"""Regression test for T247-MARKETDATA-KSCORE-FALSY.

_composite_priority()'s K-Score term used `rank_r.score or 50.0` / `if rank_r and
rank_r.score` — a real K-Score of exactly 0.0 (a valid, clipped [0,100] value per
ranking-engine's kscore.py) is falsy in Python, so it was silently treated as an
unranked/neutral 50 instead of the true rock-bottom score, inflating a genuinely terrible
candidate's sort priority and letting it out-rank a real, correctly-scored mediocre candidate
for one of the day's limited entry slots.
"""
from types import SimpleNamespace

import pytest

from src.services.paper_trading_engine import _composite_priority, _slipped_position_value


def _row(confidence=70.0, score=None, sr_context="neutral", has_ranking=True):
    sig_r = SimpleNamespace(confidence=confidence, reasons={"sr_context": sr_context})
    rank_r = SimpleNamespace(score=score) if has_ranking else None
    return (sig_r, None, rank_r)


def test_kscore_of_exactly_zero_is_not_treated_as_neutral_50():
    """The exact bug scenario: a genuinely rock-bottom K-Score of 0.0 must contribute 0.0
    to the composite priority, not the neutral-unranked default of 0.5 (50/100)."""
    zero_score_row = _row(confidence=70.0, score=0.0)
    neutral_row = _row(confidence=70.0, has_ranking=False)  # no ranking at all -> genuinely 0.5

    zero_priority = _composite_priority(zero_score_row)
    neutral_priority = _composite_priority(neutral_row)

    # Same confidence/breakout context; only the K-Score term differs (0.0 vs 0.5 normalized).
    # 0.3 weight x 0.5 delta = 0.15 — the exact inflation the audit finding described.
    assert neutral_priority - zero_priority == pytest.approx(0.15)


def test_kscore_of_zero_scores_lower_than_a_real_mediocre_score():
    """A real K-Score of 0 must rank BELOW a real K-Score of 40 — the exact scenario from
    the audit finding (a rock-bottom candidate should never out-rank a mediocre one)."""
    terrible = _composite_priority(_row(confidence=70.0, score=0.0))
    mediocre = _composite_priority(_row(confidence=70.0, score=40.0))
    assert terrible < mediocre


def test_missing_ranking_falls_back_to_neutral_50():
    """rank_r is None (genuinely no ranking available) — the 0.5 fallback is correct here,
    this is the ONE case that should still hit the neutral default."""
    row = _row(confidence=70.0, has_ranking=False)
    priority = _composite_priority(row)
    # conf=0.7*0.5=0.35, kscore=0.5*0.3=0.15, breakout=0 -> 0.50
    assert priority == pytest.approx(0.50)


def test_ranking_present_but_score_none_falls_back_to_neutral_50():
    """rank_r exists but .score itself is None (e.g. insufficient fundamentals data per
    KS-4) — still a genuinely missing value, so 0.5 fallback is correct."""
    row = _row(confidence=70.0, score=None, has_ranking=True)
    priority = _composite_priority(row)
    assert priority == pytest.approx(0.50)


def test_real_high_kscore_scores_higher_than_zero():
    high = _composite_priority(_row(confidence=70.0, score=90.0))
    zero = _composite_priority(_row(confidence=70.0, score=0.0))
    assert high > zero


# ── T247-MARKETDATA-CASHGATE-PRESLIPPAGE ──────────────────────────────────────────
#
# The cash-sufficiency gate previously compared PRE-slippage position_value (at live_price)
# against current_cash, while the actual cash deduction recomputed position_value at the
# higher SLIPPED price a few lines later — the check and the charge used two different
# values, so a candidate could pass the gate and still overdraw cash.

def test_slipped_position_value_is_higher_than_the_pre_slippage_value():
    """The exact bug scenario: the slipped (real, charged) value must be strictly greater
    than the naive live_price * shares value whenever slippage is positive."""
    shares, live_price, slippage = 100.0, 50.0, 0.02  # 2% slippage
    pre_slippage_value = round(shares * live_price, 2)
    slipped_value = _slipped_position_value(shares, live_price, slippage)
    assert slipped_value > pre_slippage_value
    assert slipped_value == pytest.approx(5100.0)  # 100 * 50 * 1.02


def test_cash_gate_using_pre_slippage_value_would_have_passed_when_it_should_not():
    """Reproduces the audit finding directly: at a cash balance that is JUST ABOVE the
    pre-slippage cost but BELOW the real (slipped) cost, the OLD check (pre-slippage vs cash)
    would incorrectly pass, while the FIXED check (slipped vs cash) correctly blocks it."""
    shares, live_price, slippage = 100.0, 50.0, 0.02
    # cash*0.98 must be >= pre-slippage value (5000) but < slipped value (5100):
    # cash in (5102.04, 5204.08) — 5150 sits inside that window.
    current_cash = 5150.0

    pre_slippage_value = round(shares * live_price, 2)
    assert pre_slippage_value <= current_cash * 0.98  # old gate: would have let this through

    slipped_value = _slipped_position_value(shares, live_price, slippage)
    assert slipped_value > current_cash * 0.98  # fixed gate: correctly blocks it


def test_zero_slippage_leaves_position_value_unchanged():
    shares, live_price = 100.0, 50.0
    assert _slipped_position_value(shares, live_price, 0.0) == round(shares * live_price, 2)

