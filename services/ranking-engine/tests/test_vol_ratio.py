"""Regression test for T247-RANKINGENGINE-VOLRATIO-STALEWINDOW.

_compute_vol_ratio() previously filtered out zero-volume days BEFORE slicing [:5]/[:20],
which shifts every later index — a single zero-volume day (halt/bad ingestion) anywhere in
the 35-day lookback window silently pulled in a bar older than the nominal "last 5"/"last 20"
trading days, skewing vol_ratio away from what the label describes.
"""
import pytest

from src.api.routes import _compute_vol_ratio


def test_zero_volume_day_does_not_shift_the_5day_window():
    """The exact bug scenario: a zero-volume day at position 2 (3rd most recent) must NOT
    cause a 6th-most-recent real bar to be pulled into the "last 5" average."""
    # Newest-first (ts.desc()), 10 real values with a zero-volume day inserted at index 2.
    vols_with_gap = [100.0, 200.0, 0.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0]
    # OLD (buggy) behavior would have filtered the 0.0 out first, then taken [:5] of the
    # remaining 9 values — pulling in the 6th newest real bar (600.0) into what should be a
    # "last 5 calendar days" average.
    old_buggy_avg5 = sum([v for v in vols_with_gap if v > 0][:5]) / 5
    assert old_buggy_avg5 == pytest.approx((100 + 200 + 300 + 400 + 500) / 5)  # includes idx-5 value

    # NEW (fixed) behavior: literal newest 5 rows, including the zero. Assert against the
    # REAL function's output (not just a hand-computed value) so this actually exercises
    # _compute_vol_ratio()'s own implementation.
    fixed_avg5 = sum(vols_with_gap[:5]) / 5
    fixed_avg20 = sum(vols_with_gap[:min(len(vols_with_gap), 20)]) / min(len(vols_with_gap), 20)
    expected_ratio = round(fixed_avg5 / fixed_avg20, 2)
    assert _compute_vol_ratio(vols_with_gap) == pytest.approx(expected_ratio)
    assert fixed_avg5 != old_buggy_avg5  # proves the fix actually changes the computed window


def test_vol_ratio_matches_literal_last_5_and_last_20_rows():
    vols_desc = [float(i) for i in range(100, 0, -1)]  # 100 values, newest-first: 100,99,...,1
    ratio = _compute_vol_ratio(vols_desc)
    expected_avg5 = sum(vols_desc[:5]) / 5
    expected_avg20 = sum(vols_desc[:20]) / 20
    assert ratio == pytest.approx(round(expected_avg5 / expected_avg20, 2))


def test_fewer_than_5_rows_returns_none():
    assert _compute_vol_ratio([100.0, 200.0, 300.0]) is None


def test_fewer_than_20_rows_uses_all_available_for_avg20():
    vols_desc = [float(i) for i in range(15, 0, -1)]  # 15 rows, newest-first
    ratio = _compute_vol_ratio(vols_desc)
    expected_avg5 = sum(vols_desc[:5]) / 5
    expected_avg20 = sum(vols_desc) / len(vols_desc)  # all 15 rows, not 20
    assert ratio == pytest.approx(round(expected_avg5 / expected_avg20, 2))


def test_all_zero_volume_returns_none_not_a_divide_by_zero_crash():
    assert _compute_vol_ratio([0.0] * 20) is None
