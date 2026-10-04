"""Synthetic dancers for tests and the offline demo.

Produces a procedurally animated COCO-17 skeleton so the scoring pipeline can be
exercised without any video, music or personal data.
"""

from __future__ import annotations

import numpy as np

from .engine import SignatureEvent
from .normalize import PoseSequence
from .skeleton import MIRROR_PERMUTATION, NUM_JOINTS

# Angles in radians, measured from "straight down" in body coordinates (y down).
_KEYS = ("l_ua", "l_fa", "r_ua", "r_fa", "l_th", "l_sh", "r_th", "r_sh", "lean", "sway")


def _keyframes(duration_s: float, beat_s: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    times = np.arange(0.0, duration_s + beat_s, beat_s)
    k = len(times)
    frames = np.zeros((k, len(_KEYS)))
    frames[:, 0] = rng.uniform(0.2, 2.6, k)  # left upper arm: down .. overhead
    frames[:, 1] = rng.uniform(-1.4, 0.2, k)  # left elbow bend
    frames[:, 2] = -rng.uniform(0.2, 2.6, k)
    frames[:, 3] = rng.uniform(-0.2, 1.4, k)
    frames[:, 4] = rng.uniform(-0.1, 0.6, k)  # thighs
    frames[:, 5] = rng.uniform(-0.9, 0.0, k)  # knees
    frames[:, 6] = -rng.uniform(-0.1, 0.6, k)
    frames[:, 7] = rng.uniform(0.0, 0.9, k)
    frames[:, 8] = rng.uniform(-0.2, 0.2, k)  # torso lean
    frames[:, 9] = rng.uniform(-0.4, 0.4, k)  # hip sway (torso lengths)
    return times, frames


def _interpolate(times: np.ndarray, frames: np.ndarray, t: np.ndarray) -> np.ndarray:
    idx = np.clip(np.searchsorted(times, t, side="right") - 1, 0, len(times) - 2)
    u = (t - times[idx]) / (times[idx + 1] - times[idx])
    u = (1 - np.cos(np.pi * u)) / 2  # ease in/out between keyframes
    return frames[idx] * (1 - u[:, None]) + frames[idx + 1] * u[:, None]


def _forward_kinematics(params: np.ndarray) -> np.ndarray:
    """params (T, 10) -> joints (T, 17, 2) in torso-length units, hip midpoint near origin."""
    t = params.shape[0]
    p = dict(zip(_KEYS, params.T))
    out = np.zeros((t, NUM_JOINTS, 2))

    def unit(angle):
        return np.stack([np.sin(angle), np.cos(angle)], axis=1)  # angle 0 = straight down

    hip_mid = np.stack([p["sway"], np.zeros(t)], axis=1)
    up = -unit(p["lean"])  # torso points up
    side = np.stack([-up[:, 1], up[:, 0]], axis=1)  # perpendicular, towards image right... flipped below
    sh_mid = hip_mid + up * 1.0

    l_sh, r_sh = sh_mid - side * 0.4, sh_mid + side * 0.4
    l_hip, r_hip = hip_mid - side * 0.2, hip_mid + side * 0.2
    head = sh_mid + up * 0.45

    # Image-left of the frame is the dancer's right when facing the camera; keep it simple
    # and consistent: "left" joints get the negative side.
    l_el = l_sh + unit(p["l_ua"] + p["lean"]) * 0.55
    l_wr = l_el + unit(p["l_ua"] + p["l_fa"] + p["lean"]) * 0.5
    r_el = r_sh + unit(p["r_ua"] + p["lean"]) * 0.55
    r_wr = r_el + unit(p["r_ua"] + p["r_fa"] + p["lean"]) * 0.5
    l_kn = l_hip + unit(p["l_th"]) * 0.85
    l_an = l_kn + unit(p["l_th"] + p["l_sh"]) * 0.8
    r_kn = r_hip + unit(p["r_th"]) * 0.85
    r_an = r_kn + unit(p["r_th"] + p["r_sh"]) * 0.8

    out[:, 0] = head
    out[:, 1], out[:, 2] = head - side * 0.08 + up * 0.05, head + side * 0.08 + up * 0.05
    out[:, 3], out[:, 4] = head - side * 0.15, head + side * 0.15
    out[:, 5], out[:, 6] = l_sh, r_sh
    out[:, 7], out[:, 8] = l_el, r_el
    out[:, 9], out[:, 10] = l_wr, r_wr
    out[:, 11], out[:, 12] = l_hip, r_hip
    out[:, 13], out[:, 14] = l_kn, r_kn
    out[:, 15], out[:, 16] = l_an, r_an
    return out


class SyntheticRoutine:
    """A reproducible routine; sample() renders it for any timing/camera variation."""

    def __init__(self, seed: int = 0, duration_s: float = 20.0, beat_s: float = 0.5, fps: float = 15.0):
        self.seed, self.duration_s, self.beat_s, self.fps = seed, duration_s, beat_s, fps
        self._times, self._frames = _keyframes(duration_s, beat_s, np.random.default_rng(seed))

    def events(self, every_beats: int = 4) -> tuple[SignatureEvent, ...]:
        beats = self._times[every_beats : -2 : every_beats]
        return tuple(SignatureEvent(round(float(t), 3), f"hit {i + 1}") for i, t in enumerate(beats) if t < self.duration_s - 1)

    def sample(
        self,
        delay_s: float = 0.0,
        scale_px: float = 150.0,
        origin_px: tuple[float, float] = (640.0, 360.0),
        noise_px: float = 0.0,
        amplitude: float = 1.0,
        mirrored: bool = False,
        length_s: float | None = None,
        seed: int = 1,
    ) -> PoseSequence:
        n = int(round((length_s if length_s is not None else self.duration_s) * self.fps))
        t = np.arange(n) / self.fps - delay_s  # a late dancer shows the reference pose later
        params = _interpolate(self._times, self._frames, np.clip(t, 0, self.duration_s))
        params = params * amplitude
        joints = _forward_kinematics(params)
        if mirrored:
            joints[..., 0] = -joints[..., 0]
            joints = joints[:, MIRROR_PERMUTATION]
        kp = joints * scale_px + np.array(origin_px)
        if noise_px:
            kp = kp + np.random.default_rng(seed).normal(0, noise_px, kp.shape)
        return PoseSequence(kp, np.full((n, NUM_JOINTS), 0.9), self.fps)


def occlude(seq: PoseSequence, joints: list[int], start_s: float, end_s: float) -> PoseSequence:
    scores = seq.scores.copy()
    a, b = int(round(start_s * seq.fps)), int(round(end_s * seq.fps))
    scores[a:b, joints] = 0.05
    return PoseSequence(seq.keypoints, scores, seq.fps)
