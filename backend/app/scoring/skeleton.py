"""COCO-17 skeleton definitions and normalized pose features.

Features are joint interior angles and limb directions. Both are invariant to
translation and body scale, so body size and camera distance matter less. Poses
are deliberately *not* rotated to fit the reference: that would erase a genuine
leaning error.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

JOINT_NAMES = (
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
)  # fmt: skip
NUM_JOINTS = 17

L_SH, R_SH, L_EL, R_EL, L_WR, R_WR = 5, 6, 7, 8, 9, 10
L_HIP, R_HIP, L_KN, R_KN, L_AN, R_AN = 11, 12, 13, 14, 15, 16

# Swapping left/right joints; used together with an x-flip for mirror policies.
MIRROR_PERMUTATION = np.array([0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15])

# A "full-body" sample needs all 12 limb joints (the face is not required).
BODY_JOINTS = np.arange(5, 17)

# Joints whose speed and direction feed Dynamics and Flow.
MOTION_JOINTS = np.array([L_EL, R_EL, L_WR, R_WR, L_KN, R_KN, L_AN, R_AN])


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    kind: str  # "angle": interior joint angle; "direction": segment direction in the image plane
    points: tuple[tuple[int, ...], ...]  # each point is the mean of these joints
    group: str
    open_phrase: str = ""  # angle features: wording when the player's angle is larger
    closed_phrase: str = ""  # ... and when it is smaller

    @property
    def joints(self) -> tuple[int, ...]:
        return tuple(sorted({j for p in self.points for j in p}))


def _angle(key, label, a, b, c, group, open_phrase, closed_phrase):
    return Feature(key, label, "angle", ((a,), (b,), (c,)), group, open_phrase, closed_phrase)


def _direction(key, label, start, end, group):
    return Feature(key, label, "direction", (start, end), group)


FEATURES: tuple[Feature, ...] = (
    _angle("l_elbow", "left elbow", L_SH, L_EL, L_WR, "left arm", "straighter", "more bent"),
    _angle("r_elbow", "right elbow", R_SH, R_EL, R_WR, "right arm", "straighter", "more bent"),
    _angle("l_shoulder", "left arm", L_HIP, L_SH, L_EL, "left arm", "raised further from your body", "held closer to your body"),
    _angle("r_shoulder", "right arm", R_HIP, R_SH, R_EL, "right arm", "raised further from your body", "held closer to your body"),
    _angle("l_hip", "left hip", L_SH, L_HIP, L_KN, "left leg", "more extended", "more flexed"),
    _angle("r_hip", "right hip", R_SH, R_HIP, R_KN, "right leg", "more extended", "more flexed"),
    _angle("l_knee", "left knee", L_HIP, L_KN, L_AN, "left leg", "straighter", "more bent"),
    _angle("r_knee", "right knee", R_HIP, R_KN, R_AN, "right leg", "straighter", "more bent"),
    _direction("l_upper_arm", "left upper arm", (L_SH,), (L_EL,), "left arm"),
    _direction("l_forearm", "left forearm", (L_EL,), (L_WR,), "left arm"),
    _direction("r_upper_arm", "right upper arm", (R_SH,), (R_EL,), "right arm"),
    _direction("r_forearm", "right forearm", (R_EL,), (R_WR,), "right arm"),
    _direction("l_thigh", "left thigh", (L_HIP,), (L_KN,), "left leg"),
    _direction("l_shin", "left shin", (L_KN,), (L_AN,), "left leg"),
    _direction("r_thigh", "right thigh", (R_HIP,), (R_KN,), "right leg"),
    _direction("r_shin", "right shin", (R_KN,), (R_AN,), "right leg"),
    _direction("torso", "torso", (L_HIP, R_HIP), (L_SH, R_SH), "torso"),
    _direction("shoulder_line", "shoulder line", (L_SH,), (R_SH,), "torso"),
)  # fmt: skip
FEATURE_KEYS = tuple(f.key for f in FEATURES)


def wrap_angle(x: np.ndarray) -> np.ndarray:
    """Wrap radians to [-pi, pi)."""
    return (x + np.pi) % (2 * np.pi) - np.pi


def compute_features(keypoints: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return feature values (T, F) in radians and their validity mask (T, F).

    A feature is valid only when every joint it needs is valid.
    """
    t = keypoints.shape[0]
    values = np.zeros((t, len(FEATURES)))
    mask = np.zeros((t, len(FEATURES)), dtype=bool)
    for i, feat in enumerate(FEATURES):
        pts = [keypoints[:, list(p), :].mean(axis=1) for p in feat.points]
        mask[:, i] = valid[:, list(feat.joints)].all(axis=1)
        if feat.kind == "angle":
            u, v = pts[0] - pts[1], pts[2] - pts[1]
            cross = u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]
            dot = (u * v).sum(axis=1)
            values[:, i] = np.abs(np.arctan2(cross, dot))
        else:
            d = pts[1] - pts[0]
            values[:, i] = np.arctan2(d[:, 1], d[:, 0])
        # Degenerate (zero-length) segments carry no direction.
        for a, b in zip(pts[:-1], pts[1:]):
            mask[:, i] &= np.linalg.norm(b - a, axis=1) > 1e-6
    return values, mask
