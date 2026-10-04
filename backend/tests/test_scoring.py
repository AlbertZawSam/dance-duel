"""Scoring behaviour from the proposal's evaluation plan, on synthetic dancers."""

import numpy as np
import pytest

from app.scoring import ChallengeSpec, ScoringConfig, score_attempt
from app.scoring.normalize import PoseSequence, interpolate_short_gaps
from app.scoring.synthetic import SyntheticRoutine, occlude

ROUTINE = SyntheticRoutine(seed=3)
REF = ROUTINE.sample()
SPEC = ChallengeSpec(events=ROUTINE.events(), signature_windows=((4.0, 6.0),))


def score(player, spec=SPEC):
    return score_attempt(REF, player, spec)


def test_identical_performance_scores_100():
    r = score(ROUTINE.sample())
    assert r.valid
    assert r.total == pytest.approx(100.0)


def test_scale_and_position_invariance():
    """Equivalent recordings at different distances and positions score the same."""
    near, far = score(ROUTINE.sample(scale_px=180)), score(ROUTINE.sample(scale_px=80, origin_px=(200, 500)))
    assert abs(near.total - far.total) <= 0.5


def test_deterministic_repeatability():
    player = ROUTINE.sample(noise_px=3, delay_s=0.1)
    totals = {round(score(player).total, 9) for _ in range(3)}
    assert len(totals) == 1


@pytest.mark.parametrize("offset", [0.2, -0.2, 0.4, -0.4])
def test_timing_drops_when_shifted(offset):
    base = score(ROUTINE.sample(noise_px=2, seed=5))
    shifted = score(ROUTINE.sample(noise_px=2, seed=5, delay_s=offset))
    assert shifted.timing < base.timing
    assert shifted.total < base.total
    late = [e.offset_ms for e in shifted.events if e.offset_ms is not None]
    assert np.median(late) == pytest.approx(offset * 1000, abs=70)


def test_larger_offset_scores_lower():
    assert score(ROUTINE.sample(delay_s=0.4)).timing < score(ROUTINE.sample(delay_s=0.2)).timing


def test_different_choreography_scores_much_lower():
    assert score(SyntheticRoutine(seed=9).sample()).total < score(ROUTINE.sample(noise_px=3)).total - 25


def test_weights_and_range():
    r = score(ROUTINE.sample(noise_px=4, delay_s=0.15, amplitude=0.8))
    cfg = SPEC.config
    expected = cfg.weight_pose * r.pose + cfg.weight_timing * r.timing + cfg.weight_dynamics * r.dynamics + cfg.weight_flow * r.flow
    assert r.total == pytest.approx(expected)
    for v in (r.pose, r.timing, r.dynamics, r.flow, r.total):
        assert 0.0 <= v <= 100.0


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        ScoringConfig(weight_pose=0.6)


def test_occlusion_requests_retry_instead_of_low_score():
    r = score(occlude(ROUTINE.sample(), [9, 10], 5.0, 8.0))
    assert not r.valid
    assert r.invalid_reason == "low_visibility"
    assert r.total is None


def test_hiding_part_of_body_in_one_window_is_rejected():
    """Ignoring uncertain joints alone is unsafe; the window gate must catch hidden movement."""
    spec = ChallengeSpec(events=ROUTINE.events(), config=ScoringConfig(min_full_body_ratio=0.5))
    r = score(occlude(ROUTINE.sample(), [7, 8, 9, 10], 6.0, 8.0), spec)
    assert not r.valid
    assert r.invalid_reason == "window_coverage"


def test_short_gaps_are_bridged():
    r = score(occlude(ROUTINE.sample(), [9], 5.0, 5.0 + 2 / 15))
    assert r.valid and r.total > 99


def test_recording_too_short():
    r = score(ROUTINE.sample(length_s=15))
    assert not r.valid and r.invalid_reason == "recording_too_short"


def test_mirror_policy_is_fixed_not_chosen_per_frame():
    mirrored = ROUTINE.sample(mirrored=True)
    assert score(mirrored).total < 80
    spec = ChallengeSpec(events=ROUTINE.events(), mirror_policy="mirror_player")
    assert score(mirrored, spec).total == pytest.approx(100.0)
    assert score(ROUTINE.sample(), spec).total < 80


def test_signature_weighting_keeps_score_in_range():
    spec = ChallengeSpec(events=ROUTINE.events(), signature_windows=((0.0, 20.0),))
    r = score(ROUTINE.sample(noise_px=3), spec)
    assert 0 <= r.total <= 100
    assert r.total == pytest.approx(score(ROUTINE.sample(noise_px=3), ChallengeSpec(events=ROUTINE.events())).total, abs=1e-6)


def test_feedback_is_timestamped_and_specific():
    r = score(ROUTINE.sample(delay_s=0.3, amplitude=0.7, noise_px=2))
    improve = [n for n in r.feedback if n["kind"] == "improve"]
    assert 1 <= len(improve) <= 2
    assert all(":" in n["text"] for n in r.feedback)
    assert any("late" in n["text"] for n in improve)
    assert [n for n in r.feedback if n["kind"] == "strength"]


def test_interpolation_leaves_long_gaps_invalid():
    kp = np.zeros((10, 17, 2))
    kp[:, :, 0] = np.arange(10)[:, None]
    valid = np.ones((10, 17), dtype=bool)
    valid[2:4, 0] = False  # 2-sample gap: bridged
    valid[5:9, 1] = False  # 4-sample gap: stays invalid
    out, ok = interpolate_short_gaps(kp, valid, 2)
    assert ok[2:4, 0].all() and out[3, 0, 0] == pytest.approx(3.0)
    assert not ok[5:9, 1].any()


def test_pose_sequence_roundtrip(tmp_path):
    p = tmp_path / "s.npz"
    REF.save(p)
    back = PoseSequence.load(p)
    assert back.fps == REF.fps and np.allclose(back.keypoints, REF.keypoints)
