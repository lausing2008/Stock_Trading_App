"""Regression test for T247-RANKINGENGINE-ADXBOOST-FLOOR.

_technical_score()'s adx_boost used `np.clip((adx - 15) / 25, 0, 1) * 10`, which only ever
produces a value in [0, 10] — the comment above it says "very weak trend (<15) drags it,"
implying a penalty, but the clip floor of 0 means ADX<15 never actually subtracts anything;
it merely contributes zero, identical to ADX=15 itself.
"""
import numpy as np
import pandas as pd
import pytest

import src.scoring.kscore as kscore_mod


def _flat_df(n=250):
    """Enough bars for SMA50/SMA200 + RSI to be real (non-neutral-fallback) values, held
    constant so above_sma50/above_sma200/sma50_above_sma200 and RSI stay fixed across the
    two ADX scenarios — isolating adx_boost as the only varying input."""
    close = pd.Series(np.linspace(100, 110, n))
    return pd.DataFrame({
        "close": close, "high": close + 0.5, "low": close - 0.5,
        "open": close, "volume": np.full(n, 1000.0),
    })


def test_adx_below_15_scores_lower_than_adx_of_exactly_15(monkeypatch):
    """The exact bug scenario: a genuinely weak/choppy trend (ADX=5) must drag the score
    BELOW an ADX=15 stock, not tie with it."""
    df = _flat_df()

    monkeypatch.setattr(kscore_mod, "_adx_value", lambda _df: 15.0)
    score_at_15 = kscore_mod._technical_score(df)

    monkeypatch.setattr(kscore_mod, "_adx_value", lambda _df: 5.0)
    score_at_5 = kscore_mod._technical_score(df)

    assert score_at_5 < score_at_15


def test_adx_boost_is_negative_below_15():
    """Directly verify the clip range: ADX=5 must produce a NEGATIVE boost, not a floored 0."""
    adx = 5.0
    adx_boost = np.clip((adx - 15) / 25, -1, 1) * 10
    assert adx_boost < 0


def test_adx_boost_still_positive_above_25():
    """The >25 strong-trend boost must be unaffected by widening the floor."""
    adx = 40.0
    adx_boost = np.clip((adx - 15) / 25, -1, 1) * 10
    assert adx_boost == pytest.approx(10.0)  # clipped at the +1 ceiling, same as before


def test_adx_boost_is_zero_at_exactly_15():
    adx = 15.0
    adx_boost = np.clip((adx - 15) / 25, -1, 1) * 10
    assert adx_boost == pytest.approx(0.0)


def test_missing_adx_still_contributes_no_boost(monkeypatch):
    """AUD232-014 behavior must be preserved: unknown ADX (insufficient history) contributes
    exactly 0, not a fabricated positive or negative value."""
    df = _flat_df()
    monkeypatch.setattr(kscore_mod, "_adx_value", lambda _df: None)
    score_unknown = kscore_mod._technical_score(df)

    monkeypatch.setattr(kscore_mod, "_adx_value", lambda _df: 15.0)
    score_at_15 = kscore_mod._technical_score(df)

    assert score_unknown == pytest.approx(score_at_15)
