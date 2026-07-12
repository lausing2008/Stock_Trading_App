"""Tests for institutional.py's compute_institutional_score().

Boundary coverage for the two additive components (fund count, capped at 60; total value,
single-bucket up to +40) — includes the realistic (not the pre-EI-BUG-fix times-1000)
value_usd magnitudes, since a previous ingestion bug inflated 13F values by 1000x, which
would have made every stock trivially clear the top bucket.
"""
from unittest.mock import patch

from src.services.institutional import compute_institutional_score, _diff_holding


def _score(holdings):
    with patch("src.services.institutional.get_institutional_for_symbol", return_value=holdings):
        return compute_institutional_score(stock_id=1)


def test_no_holdings_scores_zero():
    assert _score([]) == 0.0


def test_fund_count_below_cap():
    holdings = [{"value_usd": 0}] * 2
    assert _score(holdings) == 2 * 15


def test_fund_count_caps_at_60_with_4_or_more_funds():
    holdings = [{"value_usd": 0}] * 5
    assert _score(holdings) == 60


def test_value_threshold_over_1_billion():
    holdings = [{"value_usd": 1_500_000_000}]
    assert _score(holdings) == 15 + 40  # 1 fund (15) + top value bucket (40)


def test_value_threshold_over_500_million():
    holdings = [{"value_usd": 600_000_000}]
    assert _score(holdings) == 15 + 25


def test_value_threshold_over_100_million():
    holdings = [{"value_usd": 150_000_000}]
    assert _score(holdings) == 15 + 15


def test_value_threshold_over_10_million():
    holdings = [{"value_usd": 20_000_000}]
    assert _score(holdings) == 15 + 5


def test_value_below_10_million_gets_no_value_bonus():
    holdings = [{"value_usd": 1_000_000}]
    assert _score(holdings) == 15


def test_none_value_usd_treated_as_zero_not_a_crash():
    holdings = [{"value_usd": None}, {"value_usd": None}]
    assert _score(holdings) == 2 * 15


def test_score_clamped_at_100():
    holdings = [{"value_usd": 2_000_000_000}] * 6
    assert _score(holdings) == 100.0


# ── T237-INST-TXN-NEVER-WRITTEN: _diff_holding() ──────────────────────────────
# Pure diff logic factored out of _write_institutional_transactions() so it's directly
# unit-testable without a DB session — see institutional.py for the full docstring.

def test_diff_new_position_is_initiate():
    result = _diff_holding(
        prev_shares=None, prev_value=None, curr_shares=1000, curr_value=50000.0,
        had_previous=False, has_current=True,
    )
    assert result == ("initiate", 1000, 50000.0)


def test_diff_fully_closed_position_is_exit():
    result = _diff_holding(
        prev_shares=1000, prev_value=50000.0, curr_shares=None, curr_value=None,
        had_previous=True, has_current=False,
    )
    assert result == ("exit", -1000, -50000.0)


def test_diff_increased_shares_is_add():
    result = _diff_holding(
        prev_shares=1000, prev_value=50000.0, curr_shares=1500, curr_value=75000.0,
        had_previous=True, has_current=True,
    )
    assert result == ("add", 500, 25000.0)


def test_diff_decreased_shares_is_trim():
    result = _diff_holding(
        prev_shares=1000, prev_value=50000.0, curr_shares=600, curr_value=30000.0,
        had_previous=True, has_current=True,
    )
    assert result == ("trim", -400, -20000.0)


def test_diff_unchanged_shares_returns_none():
    """A real transaction table should only record real changes, not every quarter's
    re-affirmation of an unchanged position."""
    result = _diff_holding(
        prev_shares=1000, prev_value=50000.0, curr_shares=1000, curr_value=51000.0,
        had_previous=True, has_current=True,
    )
    assert result is None


def test_diff_zero_shares_prev_is_not_treated_as_absent():
    """T237-EI2-class regression guard: a genuine 0-share prior holding must be used as a
    real 0, not coerced to "absent" the way a naive `or 0`/falsy check would."""
    result = _diff_holding(
        prev_shares=0, prev_value=0.0, curr_shares=500, curr_value=25000.0,
        had_previous=True, has_current=True,
    )
    assert result == ("add", 500, 25000.0)


def test_diff_unknown_shares_on_either_side_returns_none():
    """Can't classify add vs. trim without both share counts — must not guess."""
    result = _diff_holding(
        prev_shares=None, prev_value=50000.0, curr_shares=1500, curr_value=75000.0,
        had_previous=True, has_current=True,
    )
    assert result is None


def test_diff_neither_previous_nor_current_returns_none():
    result = _diff_holding(
        prev_shares=None, prev_value=None, curr_shares=None, curr_value=None,
        had_previous=False, has_current=False,
    )
    assert result is None


def test_diff_value_change_none_when_both_sides_have_no_value():
    """value_change should stay None (not fabricate a 0.0) when neither side has a real
    value_usd — distinguishable from a genuine $0 change."""
    result = _diff_holding(
        prev_shares=1000, prev_value=None, curr_shares=1500, curr_value=None,
        had_previous=True, has_current=True,
    )
    assert result == ("add", 500, None)
