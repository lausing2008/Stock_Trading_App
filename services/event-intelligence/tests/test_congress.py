"""Tests for congress.py's compute_congress_score().

EI-DOC1: the docstring previously claimed a "0-100" range, contradicting the real
min(100.0, max(-100.0, score)) clamp — a sell-heavy trade history legitimately produces a
negative score. This is the exact false assumption that caused the real T237-EI1 bug
elsewhere (signal-engine) once already; the regression test below proves the negative range
is real and reachable here, not just a defensive clamp that never triggers.
"""
from unittest.mock import patch

from src.services.congress import compute_congress_score


def _score(trades):
    with patch("src.services.congress.get_congress_for_symbol", return_value=trades):
        return compute_congress_score(stock_id=1)


def test_no_trades_scores_zero():
    assert _score([]) == 0.0


def test_pure_buying_scores_positive():
    trades = [{"transaction_type": "purchase"}] * 2
    assert _score(trades) == 24  # 2 * 12, purchases=2 doesn't clear the >2 cluster bonus


def test_ei_doc1_sell_heavy_history_produces_a_real_negative_score():
    """Proves the -100..100 range is genuinely reachable, not just defensively clamped."""
    trades = [{"transaction_type": "sale"}] * 4
    assert _score(trades) == -20  # 4 * -5, below zero


def test_clustered_buying_over_5_gets_the_larger_bonus():
    trades = [{"transaction_type": "purchase"}] * 6
    assert _score(trades) == 6 * 12 + 20


def test_clustered_buying_between_3_and_5_gets_the_smaller_bonus():
    trades = [{"transaction_type": "purchase"}] * 3
    assert _score(trades) == 3 * 12 + 10


def test_mixed_buys_and_sells_net_out():
    trades = [{"transaction_type": "purchase"}, {"transaction_type": "sale"}]
    assert _score(trades) == 12 - 5


def test_score_clamped_at_positive_100():
    trades = [{"transaction_type": "purchase"}] * 20
    assert _score(trades) == 100.0


def test_score_clamped_at_negative_100():
    trades = [{"transaction_type": "sale"}] * 50
    assert _score(trades) == -100.0


def test_unknown_transaction_type_contributes_nothing():
    trades = [{"transaction_type": "exchange"}]
    assert _score(trades) == 0.0
