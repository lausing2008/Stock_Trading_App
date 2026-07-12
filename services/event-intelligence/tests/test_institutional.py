"""Tests for institutional.py's compute_institutional_score().

Boundary coverage for the two additive components (fund count, capped at 60; total value,
single-bucket up to +40) — includes the realistic (not the pre-EI-BUG-fix times-1000)
value_usd magnitudes, since a previous ingestion bug inflated 13F values by 1000x, which
would have made every stock trivially clear the top bucket.
"""
from unittest.mock import patch

from src.services.institutional import compute_institutional_score


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
