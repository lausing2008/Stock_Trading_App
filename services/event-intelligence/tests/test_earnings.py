"""Tests for earnings.py's _compute_strength() — fully pure, zero DB dependency, the strongest
unit-test candidate in event-intelligence."""
from src.services.earnings import _compute_strength


def test_no_actual_eps_returns_none():
    """The function's own guard: without a reported eps_act there is nothing to score."""
    assert _compute_strength(1.0, None, None) is None


def test_baseline_score_with_no_surprise_data():
    """eps_act=1.0 is positive, so this also picks up the +10 positive-EPS bonus."""
    assert _compute_strength(1.0, 1.0, None) == 60.0


def test_big_beat_above_20_percent():
    assert _compute_strength(1.0, 1.3, 25.0) == 50.0 + 30 + 10  # surprise bonus + positive-EPS bonus


def test_beat_between_10_and_20_percent():
    assert _compute_strength(1.0, 1.15, 15.0) == 50.0 + 20 + 10


def test_beat_between_5_and_10_percent():
    assert _compute_strength(1.0, 1.07, 7.0) == 50.0 + 10 + 10


def test_miss_below_negative_10_percent():
    assert _compute_strength(1.0, 0.8, -15.0) == 50.0 - 20 + 10


def test_miss_between_5_and_10_percent_below():
    assert _compute_strength(1.0, 0.92, -7.0) == 50.0 - 10 + 10


def test_surprise_between_negative_5_and_5_gets_no_bucket_bonus():
    """Neither the beat nor the miss bucket applies for a surprise within [-5, 5]."""
    assert _compute_strength(1.0, 1.02, 2.0) == 50.0 + 10  # only the positive-EPS bonus


def test_positive_eps_actual_gets_bonus():
    score_with_bonus = _compute_strength(1.0, 1.0, 0.0)
    assert score_with_bonus == 60.0


def test_zero_eps_actual_does_not_get_positive_bonus():
    """`if eps_act and eps_act > 0` — eps_act == 0.0 is falsy in Python, so `0.0 and ...`
    short-circuits without ever evaluating `> 0`. An exact-zero EPS must NOT receive the
    +10 bonus, same treatment as a negative EPS (this is existing, intentional behavior —
    zero isn't "positive" — but worth locking down since it's the same falsy-vs-explicit
    pattern that caused the real T237-EI2 bug in catalyst.py)."""
    assert _compute_strength(1.0, 0.0, 0.0) == 50.0


def test_negative_eps_actual_does_not_get_positive_bonus():
    assert _compute_strength(1.0, -0.5, 0.0) == 50.0


def test_score_maximum_attainable_value():
    """50 (base) + 30 (surprise>20 bucket) + 10 (positive-EPS bonus) = 90 is the real maximum
    attainable score — no combination of inputs can exceed 100, so the source's min(100.0, ...)
    clamp is defensive headroom, not a boundary this function's own buckets can actually reach."""
    assert _compute_strength(1.0, 5.0, 500.0) == 90.0


def test_score_minimum_attainable_value():
    """50 (base) - 20 (worst surprise bucket, <-10) = 30 is the real minimum attainable score
    for any real (non-None) eps_act — there's no bucket below -20 and the positive-EPS bonus
    can only add, never subtract, so the source's max(0.0, ...) floor is likewise unreachable
    through this function's own logic."""
    assert _compute_strength(1.0, -0.01, -500.0) == 30.0
