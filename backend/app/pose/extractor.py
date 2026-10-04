"""Video -> pose sequence on the song timeline, using pretrained RTMPose.

Inference runs on the server after upload (not in the browser). rtmlib runs the
official RTMPose ONNX exports with onnxruntime, which avoids building MMCV on a
laptop. Install with:  pip install -r requirements-pose.txt
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from ..scoring.normalize import PoseSequence
from ..scoring.skeleton import BODY_JOINTS, NUM_JOINTS


class ExtractionError(Exception):
    """The recording itself is unusable (cannot decode, no frames, ...)."""


class PoseExtractor(Protocol):
    model_id: str

    def extract(
        self, video_path: Path, song_offset_s: float, duration_s: float, fps: float, recording_duration_s: float | None = None
    ) -> PoseSequence: ...

    def check_frame(self, image_bytes: bytes) -> dict: ...


@dataclass
class _Track:
    center: np.ndarray
    size: float


def _bbox(kp: np.ndarray, sc: np.ndarray, thr: float) -> tuple[np.ndarray, float] | None:
    ok = sc >= thr
    if ok.sum() < 4:
        return None
    pts = kp[ok]
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    return (lo + hi) / 2, float(np.linalg.norm(hi - lo))


def select_person(
    people_kp: np.ndarray, people_sc: np.ndarray, track: _Track | None, thr: float = 0.3
) -> tuple[int | None, _Track | None]:
    """Follow one person. Never jump to someone else when tracking is uncertain."""
    boxes = [_bbox(k, s, thr) for k, s in zip(people_kp, people_sc)]
    candidates = [(i, b) for i, b in enumerate(boxes) if b is not None]
    if not candidates:
        return None, track
    if track is None:
        i, (c, size) = max(candidates, key=lambda x: x[1][1])  # largest person starts the track
        return i, _Track(c, size)
    i, (c, size) = min(candidates, key=lambda x: np.linalg.norm(x[1][0] - track.center))
    if np.linalg.norm(c - track.center) > 0.5 * track.size or not (0.5 < size / max(track.size, 1e-6) < 2.0):
        return None, track
    return i, _Track(c, size)


def frame_times_to_samples(frame_ms: np.ndarray, song_offset_s: float, duration_s: float, fps: float) -> np.ndarray:
    """For each song-timeline sample, the index of the nearest video frame (-1 if none within half a sample)."""
    n = int(round(duration_s * fps))
    targets = (np.arange(n) / fps + song_offset_s) * 1000.0
    idx = np.clip(np.searchsorted(frame_ms, targets), 1, max(1, len(frame_ms) - 1))
    left, right = frame_ms[idx - 1], frame_ms[np.minimum(idx, len(frame_ms) - 1)]
    nearest = np.where(np.abs(targets - left) <= np.abs(right - targets), idx - 1, np.minimum(idx, len(frame_ms) - 1))
    gap = np.abs(frame_ms[nearest] - targets)
    return np.where(gap <= 500.0 / fps, nearest, -1)


class RTMPoseExtractor:
    """Defaults measured on an Apple M2 CPU: 'lightweight' with person detection every
    5th sample processes a 30 s clip in roughly 16 s; 'balanced' is about 4x slower.
    """

    def __init__(self, mode: str | None = None, det_frequency: int | None = None, device: str = "cpu"):
        try:
            from rtmlib import Body, PoseTracker  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Pose extraction needs rtmlib: pip install -r requirements-pose.txt") from exc
        mode = mode or os.environ.get("DD_POSE_MODE", "lightweight")
        det_frequency = det_frequency or int(os.environ.get("DD_DET_FREQUENCY", "5"))
        # Between detections, the tracker reuses boxes from the previous keypoints.
        self._tracker = PoseTracker(Body, det_frequency=det_frequency, mode=mode, backend="onnxruntime", device=device, tracking=False)
        self._lock = threading.Lock()  # the tracker is stateful; preflight and the worker share it
        self.model_id = f"rtmlib-body-{mode}(yolox+rtmpose,coco17,det/{det_frequency})"

    def _infer(self, frame) -> tuple[np.ndarray, np.ndarray]:
        kp, sc = self._tracker(frame)
        kp, sc = np.asarray(kp, float), np.asarray(sc, float)
        if kp.ndim != 3 or kp.shape[0] == 0:
            return np.zeros((0, NUM_JOINTS, 2)), np.zeros((0, NUM_JOINTS))
        # rtmlib scores are SimCC confidences that can exceed 1; clip to [0, 1].
        return kp, np.clip(sc, 0.0, 1.0)

    def extract(self, video_path, song_offset_s, duration_s, fps, recording_duration_s=None) -> PoseSequence:
        with self._lock:
            return self._extract(video_path, song_offset_s, duration_s, fps, recording_duration_s)

    def _extract(self, video_path, song_offset_s, duration_s, fps, recording_duration_s=None) -> PoseSequence:
        import cv2  # noqa: PLC0415

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ExtractionError("could not open the recording")
        try:
            # Pass 1: frame timestamps only (grab() does not decode pixels).
            times = []
            while cap.grab():
                times.append(cap.get(cv2.CAP_PROP_POS_MSEC))
            if not times:
                raise ExtractionError("the recording contains no frames")
            frame_ms = np.asarray(times, float)
            if len(frame_ms) > 1 and not np.all(np.diff(frame_ms) > 0):
                # Some MediaRecorder files carry no usable timestamps: spread frames evenly.
                if not recording_duration_s:
                    raise ExtractionError("the recording has no usable timestamps")
                frame_ms = np.linspace(0, recording_duration_s * 1000.0, len(frame_ms), endpoint=False)

            wanted = frame_times_to_samples(frame_ms, song_offset_s, duration_s, fps)
            need = {int(i) for i in wanted if i >= 0}

            # Pass 2: decode and run pose only on the frames the timeline needs.
            cap.release()
            cap = cv2.VideoCapture(str(video_path))
            self._tracker.reset()
            results: dict[int, tuple[np.ndarray, np.ndarray]] = {}
            track = None
            frame_idx = 0
            while need and frame_idx <= max(need):
                ok, frame = cap.read()
                if not ok:
                    break
                if frame_idx in need:
                    kp, sc = self._infer(frame)
                    i, track = select_person(kp, sc, track)
                    if i is not None:
                        results[frame_idx] = (kp[i], sc[i])
                frame_idx += 1
        finally:
            cap.release()

        n = len(wanted)
        keypoints = np.zeros((n, NUM_JOINTS, 2))
        scores = np.zeros((n, NUM_JOINTS))
        for s, f in enumerate(wanted):
            if f >= 0 and int(f) in results:
                keypoints[s], scores[s] = results[int(f)]
        return PoseSequence(keypoints, scores, fps)

    def check_frame(self, image_bytes: bytes) -> dict:
        """Camera preflight: one person, full body visible, enough light."""
        import cv2  # noqa: PLC0415

        img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise ExtractionError("could not decode the preview image")
        with self._lock:
            self._tracker.reset()  # force a fresh detection on this single frame
            kp, sc = self._infer(img)
        brightness = float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean())
        people = [i for i in range(len(kp)) if (sc[i] >= 0.3).sum() >= 6]
        full_body = bool(people) and bool((sc[max(people, key=lambda i: sc[i].sum())][BODY_JOINTS] >= 0.3).all())
        return {"people": len(people), "full_body": full_body, "brightness": round(brightness, 1), "model": self.model_id}
