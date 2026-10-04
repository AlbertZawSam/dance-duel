"""Scoring configuration.

Every value here is a *starting* design choice from the CSC493 proposal. They are
stored with each published challenge version so a later calibration creates a new
version instead of silently changing an active duel.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

ANALYSIS_VERSION = "dd-score-0.1"
NORMALIZER_VERSION = "dd-norm-0.1"


@dataclass(frozen=True)
class ScoringConfig:
    # Common timeline
    fps: float = 15.0

    # Capture validity
    conf_threshold: float = 0.3  # a joint is "visible" at or above this confidence
    max_gap_samples: int = 2  # interpolate occlusions of at most this many samples
    smooth_window: int = 3  # short, fixed centered moving average
    min_full_body_ratio: float = 0.90  # share of samples where all 12 body joints are visible
    min_window_coverage: float = 0.80  # feature coverage required in every scored window
    window_s: float = 2.0
    max_short_s: float = 0.5  # recording may end this much before the reference

    # Pose (50%)
    pose_tolerance_s: float = 0.1  # small fixed local tolerance, absorbs sampling noise only
    pose_scale_deg: float = 60.0  # angular error that maps to 0

    # Timing (25%)
    timing_search_s: float = 0.6  # bounded search window around each signature event
    timing_scale_s: float = 0.5  # absolute offset that maps to 0
    event_half_window_s: float = 0.2
    event_miss_deg: float = 45.0  # best match worse than this = missed event

    # Dynamics (15%) and Flow (10%)
    speed_window: int = 5
    dynamics_scale: float = 3.0  # torso-lengths per second
    flow_speed_floor: float = 0.5  # below this a joint counts as stationary
    flow_scale_deg: float = 90.0

    # Signature windows get this weight, normalized inside each component,
    # so the final score always stays within 0-100.
    signature_weight: float = 1.5

    weight_pose: float = 0.50
    weight_timing: float = 0.25
    weight_dynamics: float = 0.15
    weight_flow: float = 0.10

    def __post_init__(self) -> None:
        total = self.weight_pose + self.weight_timing + self.weight_dynamics + self.weight_flow
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"component weights must sum to 1.0, got {total}")

    def samples(self, seconds: float) -> int:
        return int(round(seconds * self.fps))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "ScoringConfig":
        if not data:
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
