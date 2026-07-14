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

from src.services.paper_trading_engine import _composite_priority


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

