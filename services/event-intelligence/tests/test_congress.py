"""Tests for congress.py's compute_congress_score() / _congress_score_from_trades().

EI-DOC1: the docstring previously claimed a "0-100" range, contradicting the real
min(100.0, max(-100.0, score)) clamp — a sell-heavy trade history legitimately produces a
negative score. This is the exact false assumption that caused the real T237-EI1 bug
elsewhere (signal-engine) once already; the regression test below proves the negative range
is real and reachable here, not just a defensive clamp that never triggers.

AUD264-CATALYST-NO-TIME-DECAY (2026-08-11): the original flat trade-count scoring (no recency
decay, no dollar-amount weighting, clustering bonus counted raw purchase-trade count instead
of distinct filers) was fixed in _congress_score_from_trades() — a new pure function split out
of compute_congress_score() specifically so this math is directly testable without a DB
round-trip. Every fixture below now needs a real `trade_date` (the fix's `continue`-on-missing-
date guard skips any trade without one), and expected scores are recomputed against the real
recency-decay + dollar-weight formula rather than the old flat +12/-5-per-trade arithmetic.
"""
from datetime import date, timedelta
from unittest.mock import patch

from src.services.congress import _congress_score_from_trades, compute_congress_score

_TODAY = date(2026, 8, 11)


def _trade(transaction_type, days_ago=1, amount_min=None, amount_max=None, politician=None):
    return {
        "transaction_type": transaction_type,
        "trade_date": (_TODAY - timedelta(days=days_ago)).isoformat(),
        "amount_min": amount_min,
        "amount_max": amount_max,
        "politician_name": politician,
    }


def _score(trades):
    return _congress_score_from_trades(trades, today=_TODAY)


def test_no_trades_scores_zero():
    assert _score([]) == 0.0


def test_pure_buying_scores_positive():
    trades = [_trade("purchase"), _trade("purchase")]
    assert _score(trades) > 0


def test_ei_doc1_sell_heavy_history_produces_a_real_negative_score():
    """Proves the -100..100 range is genuinely reachable, not just defensively clamped."""
    trades = [_trade("sale")] * 4
    assert _score(trades) < 0


def test_clustered_buying_over_5_distinct_politicians_gets_the_larger_bonus():
    trades = [_trade("purchase", politician=f"Rep {i}") for i in range(6)]
    score_no_bonus_equivalent = _score([_trade("purchase", politician="Rep 0")] * 6)
    # 6 distinct politicians clears the >5 threshold; a single politician appearing 6 times
    # never accumulates more than 1 distinct filer, so it must NOT get the same bonus.
    assert _score(trades) > score_no_bonus_equivalent


def test_clustered_buying_between_3_and_5_distinct_politicians_gets_the_smaller_bonus():
    trades_3 = [_trade("purchase", politician=f"Rep {i}") for i in range(3)]
    trades_2 = [_trade("purchase", politician=f"Rep {i}") for i in range(2)]
    assert _score(trades_3) > _score(trades_2)


def test_mixed_buys_and_sells_net_out():
    trades = [_trade("purchase"), _trade("sale")]
    assert _score(trades) != 0.0


def test_score_clamped_at_positive_100():
    trades = [_trade("purchase", amount_min=5_000_000, amount_max=25_000_000) for _ in range(20)]
    assert _score(trades) == 100.0


def test_score_clamped_at_negative_100():
    trades = [_trade("sale", amount_min=5_000_000, amount_max=25_000_000) for _ in range(50)]
    assert _score(trades) == -100.0


def test_unknown_transaction_type_contributes_nothing():
    trades = [_trade("exchange")]
    assert _score(trades) == 0.0


def test_missing_trade_date_is_skipped_not_crashed_or_counted():
    trades = [{"transaction_type": "purchase"}]  # no trade_date at all
    assert _score(trades) == 0.0


# ── AUD264-CATALYST-NO-TIME-DECAY: the 3 real fixes ─────────────────────────────────────────

def test_recency_decay_a_recent_purchase_scores_higher_than_an_89_day_old_one():
    """The exact bug this closes: an 89-day-old trade previously scored IDENTICALLY to a
    fresh one, inside a flat 90-day window."""
    recent = _score([_trade("purchase", days_ago=1, amount_min=1001, amount_max=15000)])
    old = _score([_trade("purchase", days_ago=89, amount_min=1001, amount_max=15000)])
    assert recent > old > 0


def test_no_cliff_at_the_old_90_day_boundary():
    """The old code fell off a cliff to exactly 0 on day 91 (outside the flat window). The
    fix's decay must produce a smooth, small-but-nonzero continuation instead — 91 days should
    score only slightly less than 89 days, not drop to zero."""
    day_89 = _score([_trade("purchase", days_ago=89, amount_min=1001, amount_max=15000)])
    day_91 = _score([_trade("purchase", days_ago=91, amount_min=1001, amount_max=15000)])
    assert day_91 > 0
    assert day_89 - day_91 < day_89 * 0.2  # a smooth few-percent step, not a cliff to zero


def test_larger_dollar_amount_scores_higher_than_a_smaller_one_at_the_same_recency():
    """The exact second bug this closes: a $1,001 purchase and a $25M purchase previously
    scored identically (a flat +12 per trade, regardless of disclosed size)."""
    small = _score([_trade("purchase", days_ago=1, amount_min=1001, amount_max=15000)])
    large = _score([_trade("purchase", days_ago=1, amount_min=5_000_000, amount_max=25_000_000)])
    assert large > small > 0


def test_missing_amount_data_still_counts_as_real_activity_at_a_floor_weight():
    """A trade with no disclosed amount at all must not vanish from the score entirely — it
    gets the floor dollar_weight (0.5), same as the smallest real disclosure band would."""
    no_amount = _score([_trade("purchase", days_ago=1)])
    assert no_amount > 0


def test_one_filer_splitting_across_many_trades_does_not_saturate_the_cluster_bonus():
    """The exact third bug this closes: 9 purchases from ONE politician (splitting a single
    position across many same-day filings) previously saturated the clustering bonus
    identically to 9 INDEPENDENT politicians genuinely agreeing — now the bonus only counts
    distinct filers, so one filer's many filings can never clear the >5/>2 distinct-filer
    thresholds no matter how many trades they make."""
    one_filer_many_trades = _score([_trade("purchase", politician="Rep Same") for _ in range(9)])
    nine_distinct_filers = _score([_trade("purchase", politician=f"Rep {i}") for i in range(9)])
    assert nine_distinct_filers > one_filer_many_trades


def test_compute_congress_score_delegates_to_the_pure_function():
    """compute_congress_score() must call the real (unmocked) date.today() internally, not a
    fixed date — so this test uses a real, current trade_date (a fixed number of days ago
    from the real today) rather than the module's own fixed _TODAY fixture constant."""
    from datetime import date as _real_date
    real_today = _real_date.today()
    trades = [{
        "transaction_type": "purchase",
        "trade_date": (real_today - timedelta(days=1)).isoformat(),
        "amount_min": 1001, "amount_max": 15000, "politician_name": None,
    }]
    with patch("src.services.congress.get_congress_for_symbol", return_value=trades):
        assert compute_congress_score(stock_id=1) == _congress_score_from_trades(trades)
