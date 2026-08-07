"""Tests for earnings.py's _compute_strength() and _match_report_dates_to_history() — both
fully pure, zero DB/pandas dependency, the strongest unit-test candidates in event-intelligence.
"""
from datetime import date

from src.services.earnings import _compute_strength, _match_report_dates_to_history


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


# ── AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH: _match_report_dates_to_history ──
#
# Fixture data below is real, live-verified yfinance output for AAPL (2026-08-06): a single
# historical earnings_history row (period-end 2025-09-30, epsActual=1.85) genuinely joins to
# an earnings_dates row (real announce date 2025-10-30, Reported EPS=1.85) — confirmed to be
# the SAME real-world event by matching the reported EPS value, since earnings_history has no
# announcement-date field of its own at all.

def test_matches_period_end_to_real_announcement_date_via_eps_value():
    hist_rows = [{"period_end": date(2025, 9, 30), "eps_actual": 1.85}]
    announce_rows = [{"announce_date": date(2025, 10, 30), "eps_actual": 1.85}]
    result = _match_report_dates_to_history(hist_rows, announce_rows)
    assert result == {"2025-09-30": date(2025, 10, 30)}


def test_multiple_quarters_all_match_independently():
    hist_rows = [
        {"period_end": date(2025, 9, 30), "eps_actual": 1.85},
        {"period_end": date(2025, 12, 31), "eps_actual": 2.84},
        {"period_end": date(2026, 3, 31), "eps_actual": 2.01},
        {"period_end": date(2026, 6, 30), "eps_actual": 2.02},
    ]
    announce_rows = [
        {"announce_date": date(2026, 7, 30), "eps_actual": 2.02},
        {"announce_date": date(2026, 4, 30), "eps_actual": 2.01},
        {"announce_date": date(2026, 1, 29), "eps_actual": 2.84},
        {"announce_date": date(2025, 10, 30), "eps_actual": 1.85},
    ]
    result = _match_report_dates_to_history(hist_rows, announce_rows)
    assert result == {
        "2025-09-30": date(2025, 10, 30),
        "2025-12-31": date(2026, 1, 29),
        "2026-03-31": date(2026, 4, 30),
        "2026-06-30": date(2026, 7, 30),
    }


def test_eps_values_are_rounded_before_matching_not_exact_float_compare():
    """earnings_history and earnings_dates report the same real EPS value but can carry
    slightly different float precision (e.g. epsActual=1.85 vs Reported EPS=1.850000001 from
    a different rounding path) — matching must tolerate this via a 2dp round, not require an
    exact float equality that would silently never match in production."""
    hist_rows = [{"period_end": date(2025, 9, 30), "eps_actual": 1.8499999}]
    announce_rows = [{"announce_date": date(2025, 10, 30), "eps_actual": 1.8500001}]
    result = _match_report_dates_to_history(hist_rows, announce_rows)
    assert result == {"2025-09-30": date(2025, 10, 30)}


def test_no_matching_announce_row_is_simply_absent_not_fabricated():
    """A period-end with no corresponding announce-side row (a data gap on one side) must be
    absent from the result — the caller falls back to the period-end date itself, never a
    fabricated or guessed date."""
    hist_rows = [{"period_end": date(2025, 9, 30), "eps_actual": 1.85}]
    announce_rows = []
    result = _match_report_dates_to_history(hist_rows, announce_rows)
    assert result == {}


def test_rows_with_none_eps_actual_are_skipped_on_both_sides():
    hist_rows = [
        {"period_end": date(2025, 9, 30), "eps_actual": None},
        {"period_end": date(2025, 12, 31), "eps_actual": 2.84},
    ]
    announce_rows = [
        {"announce_date": date(2025, 10, 30), "eps_actual": None},
        {"announce_date": date(2026, 1, 29), "eps_actual": 2.84},
    ]
    result = _match_report_dates_to_history(hist_rows, announce_rows)
    assert result == {"2025-12-31": date(2026, 1, 29)}


def test_ambiguous_duplicate_eps_values_take_the_first_announce_match():
    """Two genuinely different quarters could coincidentally report the exact same EPS value
    (a real, if rare, possibility) — the join is inherently best-effort in that case. Confirm
    the behavior is at least deterministic (first match wins) rather than crashing or picking
    randomly, since .setdefault() is the mechanism that guarantees this."""
    hist_rows = [{"period_end": date(2025, 9, 30), "eps_actual": 1.00}]
    announce_rows = [
        {"announce_date": date(2025, 10, 30), "eps_actual": 1.00},
        {"announce_date": date(2026, 1, 29), "eps_actual": 1.00},
    ]
    result = _match_report_dates_to_history(hist_rows, announce_rows)
    assert result == {"2025-09-30": date(2025, 10, 30)}


def test_missing_dict_keys_degrade_to_skipped_not_a_crash():
    hist_rows = [{"period_end": date(2025, 9, 30)}]  # no eps_actual key at all
    announce_rows = [{"announce_date": date(2025, 10, 30), "eps_actual": 1.85}]
    result = _match_report_dates_to_history(hist_rows, announce_rows)
    assert result == {}
