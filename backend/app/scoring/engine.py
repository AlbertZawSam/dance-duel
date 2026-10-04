"""Explainable choreography-match scoring.

    Final = 0.50 Pose + 0.25 Timing + 0.15 Dynamics + 0.10 Flow

Each component maps an error e to 100 * max(0, 1 - e / a) with a calibrated scale
a. Before any scoring, a visibility gate decides whether there is enough evidence;
an uncertain recording gets a retry reason instead of a low score.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .config import ANALYSIS_VERSION, ScoringConfig
from .normalize import PoseSequence, PreparedSequence, prepare
from .skeleton import FEATURES, wrap_angle


@dataclass(frozen=True)
class SignatureEvent:
    time_s: float
    name: str


@dataclass(frozen=True)
class ChallengeSpec:
    """Everything that binds both duel players to the same rules."""

    events: tuple[SignatureEvent, ...]
    signature_windows: tuple[tuple[float, float], ...] = ()
    mirror_policy: str = "none"  # "none" or "mirror_player"
    config: ScoringConfig = field(default_factory=ScoringConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "ChallengeSpec":
        return cls(
            events=tuple(SignatureEvent(float(e["time_s"]), str(e["name"])) for e in data.get("events", [])),
            signature_windows=tuple((float(a), float(b)) for a, b in data.get("signature_windows", [])),
            mirror_policy=data.get("mirror_policy", "none"),
            config=ScoringConfig.from_dict(data.get("config")),
        )

    def to_dict(self) -> dict:
        return {
            "events": [asdict(e) for e in self.events],
            "signature_windows": [list(w) for w in self.signature_windows],
            "mirror_policy": self.mirror_policy,
            "config": self.config.to_dict(),
        }


@dataclass
class EventResult:
    name: str
    time_s: float
    offset_ms: float | None  # positive = late; None = missed
    score: float


@dataclass
class ScoreResult:
    valid: bool
    invalid_reason: str | None = None
    invalid_detail: str | None = None
    coverage: dict = field(default_factory=dict)
    pose: float | None = None
    timing: float | None = None
    dynamics: float | None = None
    flow: float | None = None
    total: float | None = None
    events: list[EventResult] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)  # per-window pose score
    feedback: list[dict] = field(default_factory=list)
    analysis_version: str = ANALYSIS_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


class InvalidAttempt(Exception):
    def __init__(self, reason: str, detail: str, coverage: dict | None = None):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.coverage = coverage or {}


def _map(err: np.ndarray, scale: float) -> np.ndarray:
    return 100.0 * np.clip(1.0 - err / scale, 0.0, 1.0)


def _fmt_time(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    return f"{s // 60:02d}:{s % 60:02d}"


def _frame_weights(n: int, spec: ChallengeSpec) -> np.ndarray:
    cfg = spec.config
    w = np.ones(n)
    times = np.arange(n) / cfg.fps
    for a, b in spec.signature_windows:
        w[(times >= a) & (times < b)] = cfg.signature_weight
    return w


def _windows(n: int, cfg: ScoringConfig) -> list[tuple[int, int]]:
    size = max(1, cfg.samples(cfg.window_s))
    bounds = [(s, min(s + size, n)) for s in range(0, n, size)]
    # Merge a short tail into the previous window so every window carries real evidence.
    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] < size // 2:
        last = bounds.pop()
        bounds[-1] = (bounds[-1][0], last[1])
    return bounds


def _shift(arr: np.ndarray, mask: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Return arr[t + k] with out-of-range samples marked invalid."""
    out = np.zeros_like(arr)
    out_mask = np.zeros_like(mask)
    n = arr.shape[0]
    if k >= 0:
        out[: n - k] = arr[k:]
        out_mask[: n - k] = mask[k:]
    else:
        out[-k:] = arr[: n + k]
        out_mask[-k:] = mask[: n + k]
    return out, out_mask


def _masked_moving_average(x: np.ndarray, mask: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Moving average along axis 0 over valid samples; valid where the centre sample is valid."""
    if window <= 1:
        return x, mask
    m = mask.astype(float)
    while m.ndim < x.ndim:
        m = m[..., None]
    kernel = np.ones(window)

    def conv(a: np.ndarray) -> np.ndarray:
        return np.apply_along_axis(lambda v: np.convolve(v, kernel, mode="same"), 0, a)

    num = conv(x * m)
    den = conv(m)
    return np.where(den > 0, num / np.maximum(den, 1e-9), 0.0), mask


def check_visibility(ref: PreparedSequence, player: PreparedSequence, spec: ChallengeSpec) -> dict:
    """Raise InvalidAttempt when there is not enough evidence to score fairly."""
    cfg = spec.config
    n = ref.features.shape[0]
    full_body_ratio = float(player.full_body.mean()) if n else 0.0
    window_cov = []
    for a, b in _windows(n, cfg):
        denom = int(ref.feature_mask[a:b].sum())
        if denom == 0:
            continue  # reference windows are validated at publication
        num = int((ref.feature_mask[a:b] & player.feature_mask[a:b]).sum())
        window_cov.append({"start_s": a / cfg.fps, "end_s": b / cfg.fps, "coverage": num / denom})
    coverage = {
        "full_body_ratio": round(full_body_ratio, 4),
        "min_window_coverage": round(min((w["coverage"] for w in window_cov), default=0.0), 4),
        "windows": window_cov,
    }
    if player.torso_scale is None:
        raise InvalidAttempt("no_body_detected", "No complete torso was visible. Step back so your whole body is in frame.", coverage)
    if full_body_ratio < cfg.min_full_body_ratio:
        raise InvalidAttempt(
            "low_visibility",
            f"Your full body was visible in {full_body_ratio:.0%} of the attempt; "
            f"{cfg.min_full_body_ratio:.0%} is required. Check framing and lighting, then retry.",
            coverage,
        )
    for w in window_cov:
        if w["coverage"] < cfg.min_window_coverage:
            raise InvalidAttempt(
                "window_coverage",
                f"Between {_fmt_time(w['start_s'])} and {_fmt_time(w['end_s'])} only {w['coverage']:.0%} of your "
                "movement could be tracked. Keep your whole body in frame and retry.",
                coverage,
            )
    return coverage


def _pose_component(ref: PreparedSequence, pl: PreparedSequence, spec: ChallengeSpec, fw: np.ndarray):
    """Per-sample best shift within +/- tolerance, chosen on mean error over all features
    (never independently per feature). Returns score, per-sample signed errors and mask."""
    cfg = spec.config
    k_max = int(np.floor(cfg.pose_tolerance_s * cfg.fps + 1e-9))
    shifts = sorted(range(-k_max, k_max + 1), key=abs)  # prefer 0 on ties
    n, f = ref.features.shape

    best_err = np.full(n, np.inf)
    signed = np.zeros((n, f))
    used = np.zeros((n, f), dtype=bool)
    for k in shifts:
        p, pm = _shift(pl.features, pl.feature_mask, k)
        m = ref.feature_mask & pm
        d = wrap_angle(p - ref.features)
        cnt = m.sum(axis=1)
        mean = np.where(cnt > 0, (np.abs(d) * m).sum(axis=1) / np.maximum(cnt, 1), np.inf)
        better = mean < best_err - 1e-12
        best_err[better] = mean[better]
        signed[better] = d[better]
        used[better] = m[better]

    per = _map(np.abs(signed), np.radians(cfg.pose_scale_deg))
    w = fw[:, None] * used
    if w.sum() == 0:
        raise InvalidAttempt("window_coverage", "No pose features overlapped with the reference.")
    return float((per * w).sum() / w.sum()), signed, used, per


def _timing_component(ref: PreparedSequence, pl: PreparedSequence, spec: ChallengeSpec):
    cfg = spec.config
    n = ref.features.shape[0]
    h = cfg.samples(cfg.event_half_window_s)
    s_max = cfg.samples(cfg.timing_search_s)
    miss = np.radians(cfg.event_miss_deg)
    results: list[EventResult] = []
    weights = []
    for ev in spec.events:
        e = int(round(ev.time_s * cfg.fps))
        lo, hi = max(0, e - h), min(n, e + h + 1)
        if lo >= hi:
            continue
        r, rm = ref.features[lo:hi], ref.feature_mask[lo:hi]
        need = rm.sum()
        best_k, best_err = None, np.inf
        for k in sorted(range(-s_max, s_max + 1), key=abs):
            if lo + k < 0 or hi + k > n:
                continue
            p, pm = pl.features[lo + k : hi + k], pl.feature_mask[lo + k : hi + k]
            m = rm & pm
            if need == 0 or m.sum() < 0.5 * need:
                continue
            err = float((np.abs(wrap_angle(p - r)) * m).sum() / m.sum())
            if err < best_err - 1e-12:
                best_k, best_err = k, err
        if best_k is None or best_err > miss:
            results.append(EventResult(ev.name, ev.time_s, None, 0.0))
        else:
            offset = best_k / cfg.fps
            score = float(_map(np.array(abs(offset)), cfg.timing_scale_s))
            results.append(EventResult(ev.name, ev.time_s, round(offset * 1000.0, 1), score))
        in_sig = any(a <= ev.time_s < b for a, b in spec.signature_windows)
        weights.append(cfg.signature_weight if in_sig else 1.0)
    if not results:
        raise InvalidAttempt("no_events", "This challenge version has no scorable signature events.")
    w = np.array(weights)
    return float((np.array([r.score for r in results]) * w).sum() / w.sum()), results


def _motion_components(ref: PreparedSequence, pl: PreparedSequence, spec: ChallengeSpec, fw: np.ndarray):
    cfg = spec.config
    rv, rm = _masked_moving_average(ref.velocity, ref.velocity_mask, cfg.speed_window)
    pv, pm = _masked_moving_average(pl.velocity, pl.velocity_mask, cfg.speed_window)
    m = rm & pm
    if not m.any():
        raise InvalidAttempt("window_coverage", "Not enough continuous tracking to measure movement.")
    w = fw[:, None] * m

    rs, ps = np.linalg.norm(rv, axis=2), np.linalg.norm(pv, axis=2)
    dyn = _map(np.abs(ps - rs), cfg.dynamics_scale)
    dynamics = float((dyn * w).sum() / w.sum())

    r_moving, p_moving = rs > cfg.flow_speed_floor, ps > cfg.flow_speed_floor
    cross = rv[..., 0] * pv[..., 1] - rv[..., 1] * pv[..., 0]
    dot = (rv * pv).sum(axis=2)
    angle = np.abs(np.arctan2(cross, dot))
    flow_scores = np.where(
        r_moving & p_moving,
        _map(angle, np.radians(cfg.flow_scale_deg)),
        np.where(r_moving | p_moving, 0.0, 100.0),  # one moving = mismatch; both still = no penalty
    )
    flow = float((flow_scores * w).sum() / w.sum())
    return dynamics, flow


def _feedback(spec: ChallengeSpec, signed, used, per, events: list[EventResult], fw: np.ndarray) -> tuple[list[dict], list[dict]]:
    """Templated, timestamped notes from measured features. Only well-covered observations qualify."""
    cfg = spec.config
    n = signed.shape[0]
    timeline, candidates = [], []
    for a, b in _windows(n, cfg):
        u = used[a:b]
        w = fw[a:b, None] * u
        if w.sum() == 0:
            continue
        timeline.append({"start_s": a / cfg.fps, "end_s": b / cfg.fps, "pose": round(float((per[a:b] * w).sum() / w.sum()), 1)})
        for i, feat in enumerate(FEATURES):
            cnt = u[:, i].sum()
            if cnt < 0.6 * (b - a):
                continue
            mean_signed = float(signed[a:b, i][u[:, i]].mean())
            mean_abs = float(np.abs(signed[a:b, i][u[:, i]]).mean())
            candidates.append((mean_abs, a, b, feat, mean_signed))

    notes: list[dict] = []
    span = lambda a, b: f"{_fmt_time(a / cfg.fps)}-{_fmt_time(b / cfg.fps)}"  # noqa: E731

    def pose_note(mean_abs, a, b, feat, mean_signed):
        deg = int(round(np.degrees(mean_abs) / 5.0) * 5)
        if feat.kind == "angle":
            how = feat.open_phrase if mean_signed > 0 else feat.closed_phrase
            text = f"At {span(a, b)}, your {feat.label} was about {deg}° {how} than the reference."
        else:
            text = f"At {span(a, b)}, your {feat.label} pointed about {deg}° away from the reference direction."
        return {
            "kind": "improve",
            "start_s": a / cfg.fps,
            "end_s": b / cfg.fps,
            "text": text,
            "tip": f"Rehearse the {span(a, b)} section at normal tempo, focusing on your {feat.group}.",
        }

    candidates.sort(key=lambda c: -c[0])
    strong = [c for c in candidates if np.degrees(c[0]) >= 20]
    if strong:
        notes.append(pose_note(*strong[0]))

    timed = [e for e in events if e.offset_ms is None or abs(e.offset_ms) >= 120]
    timed.sort(key=lambda e: float("inf") if e.offset_ms is None else abs(e.offset_ms), reverse=True)
    if timed:
        e = timed[0]
        if e.offset_ms is None:
            text = f"The signature '{e.name}' around {_fmt_time(e.time_s)} did not match the reference closely enough to be detected."
        else:
            when = "late" if e.offset_ms > 0 else "early"
            text = f"The signature '{e.name}' at {_fmt_time(e.time_s)} arrived about {abs(int(round(e.offset_ms, -1)))} ms {when}."
        notes.append({
            "kind": "improve",
            "start_s": e.time_s,
            "end_s": e.time_s,
            "text": text,
            "tip": "Count the beats into that move and rehearse the two seconds around it at normal tempo.",
        })
    elif len(strong) > 1:
        # Prefer a second note about a different body group and time.
        other = next((c for c in strong[1:] if c[3].group != strong[0][3].group or c[1] != strong[0][1]), None)
        if other:
            notes.append(pose_note(*other))

    if timeline:
        best = max(timeline, key=lambda t: t["pose"])
        notes.append({
            "kind": "strength",
            "start_s": best["start_s"],
            "end_s": best["end_s"],
            "text": f"Strongest section: {_fmt_time(best['start_s'])}-{_fmt_time(best['end_s'])} (pose match {best['pose']:.0f}/100).",
            "tip": None,
        })
    return notes, timeline


def score_attempt(reference: PoseSequence, player: PoseSequence, spec: ChallengeSpec) -> ScoreResult:
    """Compare a player recording with the hidden reference. Both must be on the song timeline."""
    cfg = spec.config
    for seq in (reference, player):
        if abs(seq.fps - cfg.fps) > 1e-6:
            raise ValueError(f"pose sequences must be sampled at {cfg.fps} fps")

    n = len(reference)
    try:
        if len(player) < n - cfg.samples(cfg.max_short_s):
            raise InvalidAttempt(
                "recording_too_short",
                f"The recording covered {len(player) / cfg.fps:.1f}s of a {n / cfg.fps:.1f}s challenge. Dance until the song ends.",
            )
        player = player.fit_length(n)
        ref_p = prepare(reference, cfg)
        pl_p = prepare(player, cfg, mirrored=spec.mirror_policy == "mirror_player")
        coverage = check_visibility(ref_p, pl_p, spec)

        fw = _frame_weights(n, spec)
        pose, signed, used, per = _pose_component(ref_p, pl_p, spec, fw)
        timing, events = _timing_component(ref_p, pl_p, spec)
        dynamics, flow = _motion_components(ref_p, pl_p, spec, fw)
    except InvalidAttempt as exc:
        return ScoreResult(valid=False, invalid_reason=exc.reason, invalid_detail=exc.detail, coverage=exc.coverage)

    total = cfg.weight_pose * pose + cfg.weight_timing * timing + cfg.weight_dynamics * dynamics + cfg.weight_flow * flow
    feedback, timeline = _feedback(spec, signed, used, per, events, fw)
    return ScoreResult(
        valid=True,
        coverage=coverage,
        pose=pose,
        timing=timing,
        dynamics=dynamics,
        flow=flow,
        total=total,
        events=events,
        timeline=timeline,
        feedback=feedback,
    )


def validate_reference(reference: PoseSequence, spec: ChallengeSpec) -> list[str]:
    """Problems that must be fixed before a challenge version can be published."""
    cfg = spec.config
    problems = []
    ref_p = prepare(reference, cfg)
    if ref_p.torso_scale is None:
        problems.append("reference has no visible torso")
    ratio = float(ref_p.full_body.mean()) if len(reference) else 0.0
    if ratio < cfg.min_full_body_ratio:
        problems.append(f"reference full-body ratio {ratio:.0%} below {cfg.min_full_body_ratio:.0%}")
    for a, b in _windows(len(reference), cfg):
        cov = ref_p.feature_mask[a:b].mean()
        if cov < cfg.min_window_coverage:
            problems.append(f"reference window {_fmt_time(a / cfg.fps)}-{_fmt_time(b / cfg.fps)} coverage {cov:.0%}")
    if not spec.events:
        problems.append("no signature events")
    for e in spec.events:
        if not 0 <= e.time_s < reference.duration_s:
            problems.append(f"event '{e.name}' at {e.time_s}s is outside the reference")
    if spec.mirror_policy not in ("none", "mirror_player"):
        problems.append(f"unknown mirror policy {spec.mirror_policy!r}")
    return problems
