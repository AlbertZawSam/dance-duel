"""Pose sequences, gap filling, smoothing, mirroring and normalization.

The same preparation runs on the hidden reference and on the player recording.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import ScoringConfig
from .skeleton import (
    BODY_JOINTS,
    L_HIP,
    L_SH,
    MIRROR_PERMUTATION,
    MOTION_JOINTS,
    NUM_JOINTS,
    R_HIP,
    R_SH,
    compute_features,
)


@dataclass
class PoseSequence:
    """Keypoints sampled on the song timeline: sample i is at song time i / fps."""

    keypoints: np.ndarray  # (T, 17, 2) image coordinates, y pointing down
    scores: np.ndarray  # (T, 17) confidence in [0, 1]
    fps: float

    def __post_init__(self) -> None:
        self.keypoints = np.asarray(self.keypoints, dtype=float)
        self.scores = np.asarray(self.scores, dtype=float)
        if self.keypoints.shape[1:] != (NUM_JOINTS, 2) or self.scores.shape != self.keypoints.shape[:2]:
            raise ValueError("expected keypoints (T, 17, 2) and scores (T, 17)")

    def __len__(self) -> int:
        return self.keypoints.shape[0]

    @property
    def duration_s(self) -> float:
        return len(self) / self.fps

    def save(self, path: str | Path) -> None:
        np.savez_compressed(path, keypoints=self.keypoints, scores=self.scores, fps=self.fps)

    @classmethod
    def load(cls, path: str | Path) -> "PoseSequence":
        with np.load(path) as data:
            return cls(data["keypoints"], data["scores"], float(data["fps"]))

    def fit_length(self, n: int) -> "PoseSequence":
        """Truncate, or pad with invisible samples, to exactly n samples."""
        if len(self) >= n:
            return PoseSequence(self.keypoints[:n], self.scores[:n], self.fps)
        pad = n - len(self)
        kp = np.concatenate([self.keypoints, np.zeros((pad, NUM_JOINTS, 2))])
        sc = np.concatenate([self.scores, np.zeros((pad, NUM_JOINTS))])
        return PoseSequence(kp, sc, self.fps)


@dataclass
class PreparedSequence:
    features: np.ndarray  # (T, F) radians
    feature_mask: np.ndarray  # (T, F)
    velocity: np.ndarray  # (T, J, 2) torso-lengths per second, J = len(MOTION_JOINTS)
    velocity_mask: np.ndarray  # (T, J)
    full_body: np.ndarray  # (T,)
    torso_scale: float | None


def interpolate_short_gaps(kp: np.ndarray, valid: np.ndarray, max_gap: int) -> tuple[np.ndarray, np.ndarray]:
    """Linearly bridge invisible runs of at most max_gap samples. Long occlusions stay invalid."""
    kp, valid = kp.copy(), valid.copy()
    t = kp.shape[0]
    for j in range(kp.shape[1]):
        i = 0
        while i < t:
            if valid[i, j]:
                i += 1
                continue
            start = i
            while i < t and not valid[i, j]:
                i += 1
            end = i  # first valid sample after the gap, or t
            if start > 0 and end < t and end - start <= max_gap:
                a, b = kp[start - 1, j], kp[end, j]
                for k in range(start, end):
                    w = (k - start + 1) / (end - start + 1)
                    kp[k, j] = a + w * (b - a)
                valid[start:end, j] = True
    return kp, valid


def smooth(kp: np.ndarray, valid: np.ndarray, window: int) -> np.ndarray:
    """Centered moving average, applied only where the whole window is valid."""
    if window <= 1:
        return kp
    half = window // 2
    out = kp.copy()
    t = kp.shape[0]
    for i in range(half, t - half):
        sl = slice(i - half, i + half + 1)
        ok = valid[sl].all(axis=0)
        out[i, ok] = kp[sl][:, ok].mean(axis=0)
    return out


def mirror(kp: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Flip horizontally and swap left/right joint labels."""
    flipped = kp.copy()
    flipped[..., 0] = -flipped[..., 0]
    return flipped[:, MIRROR_PERMUTATION], valid[:, MIRROR_PERMUTATION]


def torso_scale(kp: np.ndarray, valid: np.ndarray) -> float | None:
    """Robust body scale: median shoulder-midpoint to hip-midpoint distance."""
    ok = valid[:, [L_SH, R_SH, L_HIP, R_HIP]].all(axis=1)
    if not ok.any():
        return None
    sh = kp[ok][:, [L_SH, R_SH]].mean(axis=1)
    hip = kp[ok][:, [L_HIP, R_HIP]].mean(axis=1)
    scale = float(np.median(np.linalg.norm(sh - hip, axis=1)))
    return scale if scale > 1e-6 else None


def prepare(seq: PoseSequence, cfg: ScoringConfig, mirrored: bool = False) -> PreparedSequence:
    valid = seq.scores >= cfg.conf_threshold
    kp, valid = interpolate_short_gaps(seq.keypoints, valid, cfg.max_gap_samples)
    kp = smooth(kp, valid, cfg.smooth_window)
    if mirrored:
        kp, valid = mirror(kp, valid)

    features, feature_mask = compute_features(kp, valid)
    full_body = valid[:, BODY_JOINTS].all(axis=1)

    scale = torso_scale(kp, valid)
    t = len(seq)
    j = len(MOTION_JOINTS)
    velocity = np.zeros((t, j, 2))
    velocity_mask = np.zeros((t, j), dtype=bool)
    if scale is not None and t >= 3:
        hip_ok = valid[:, L_HIP] & valid[:, R_HIP]
        hip_mid = kp[:, [L_HIP, R_HIP]].mean(axis=1)
        rel = (kp[:, MOTION_JOINTS] - hip_mid[:, None]) / scale
        rel_ok = valid[:, MOTION_JOINTS] & hip_ok[:, None]
        # Central differences need valid neighbours on both sides.
        velocity[1:-1] = (rel[2:] - rel[:-2]) * (cfg.fps / 2)
        velocity_mask[1:-1] = rel_ok[2:] & rel_ok[:-2]

    return PreparedSequence(features, feature_mask, velocity, velocity_mask, full_body, scale)
