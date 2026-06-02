"""Unit tests for signal generation pure functions.

All tests operate on synthetic DataFrames — no network calls, no DB.
"""
import numpy as np
import pandas as pd
import pytest

from src.generators.signals import (
    _adx,
    _decide,
    _pattern_score_adjustment,
    _stoch_rsi,
    _ta_score,
    _weekly_ta_score,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_df(n: int = 300, trend: float = 0.05, seed: int = 0) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with a configurable up/down trend."""
    rng = np.random.default_rng(seed)
    close = 100 + (rng.normal(trend, 1.0, n)).cumsum()
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "close": close,
            "high": close + np.abs(rng.normal(0, 0.5, n)),
            "low": close - np.abs(rng.normal(0, 0.5, n)),
            "open": close + rng.normal(0, 0.3, n),
            "volume": rng.integers(500_000, 5_000_000, n).astype(float),
        }
    )


def _make_series(n: int = 200, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 + rng.normal(0, 1, n).cumsum())


# ── _stoch_rsi ───────────────────────────────────────────────────────────────


def test_stoch_rsi_range():
    s = _make_series()
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    l_ = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + g / l_.replace(0, np.nan))
    k, d_val, k_series = _stoch_rsi(rsi)
    assert 0.0 <= k <= 1.0
    assert 0.0 <= d_val <= 1.0
    assert len(k_series) == len(rsi)


def test_stoch_rsi_short_series_returns_midpoint():
    """Too few bars to compute stochastics → should not crash, returns ~0.5."""
    rsi = pd.Series([50.0] * 10)
    k, d_val, _ = _stoch_rsi(rsi)
    assert 0.0 <= k <= 1.0
    assert 0.0 <= d_val <= 1.0


# ── _adx ─────────────────────────────────────────────────────────────────────


def test_adx_returns_three_floats():
    df = _make_df()
    adx_val, di_plus, di_minus = _adx(df)
    assert isinstance(adx_val, float)
    assert isinstance(di_plus, float)
    assert isinstance(di_minus, float)


def test_adx_non_negative():
    df = _make_df()
    adx_val, di_plus, di_minus = _adx(df)
    assert adx_val >= 0
    assert di_plus >= 0
    assert di_minus >= 0


def test_adx_strong_trend():
    """A monotonically rising series should produce ADX > 25 (trending)."""
    n = 200
    close = np.linspace(100, 200, n)
    df = pd.DataFrame(
        {
            "close": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "open": close,
            "volume": np.ones(n) * 1_000_000,
        }
    )
    adx_val, _, _ = _adx(df)
    assert adx_val > 25, f"Expected strong trend (ADX > 25), got {adx_val:.1f}"


# ── _ta_score ────────────────────────────────────────────────────────────────


def test_ta_score_output_range():
    df = _make_df()
    score, reasons = _ta_score(df)
    assert 0.0 <= score <= 1.0


def test_ta_score_returns_reasons_dict():
    df = _make_df()
    _, reasons = _ta_score(df)
    expected_keys = {
        "trend_above_sma50", "sma50_above_sma200",
        "rsi", "macd_hist", "macd_rising",
        "bb_pct_b", "adx", "adx_trending",
        "obv_bullish", "volume_z",
    }
    assert expected_keys.issubset(reasons.keys())


def test_ta_score_bullish_uptrend():
    """A strong uptrend should score meaningfully above 0.5."""
    df = _make_df(n=300, trend=0.3, seed=42)
    score, _ = _ta_score(df)
    assert score > 0.5, f"Strong uptrend expected score > 0.5, got {score:.3f}"


def test_ta_score_bearish_downtrend():
    """A strong downtrend should score meaningfully below 0.5."""
    df = _make_df(n=300, trend=-0.3, seed=42)
    score, _ = _ta_score(df)
    assert score < 0.5, f"Strong downtrend expected score < 0.5, got {score:.3f}"


def test_ta_score_handles_short_data():
    """Should not raise even with minimal price history (< 50 bars)."""
    df = _make_df(n=30)
    score, _ = _ta_score(df)
    assert 0.0 <= score <= 1.0


# ── _weekly_ta_score ──────────────────────────────────────────────────────────


def test_weekly_ta_score_range():
    df = _make_df(n=100)
    score = _weekly_ta_score(df)
    assert 0.0 <= score <= 1.0


def test_weekly_ta_score_too_few_bars_returns_neutral():
    """Less than 26 bars → neutral 0.5."""
    df = _make_df(n=20)
    score = _weekly_ta_score(df)
    assert score == pytest.approx(0.5)


def test_weekly_ta_score_empty_returns_neutral():
    assert _weekly_ta_score(pd.DataFrame()) == pytest.approx(0.5)


# ── _pattern_score_adjustment ─────────────────────────────────────────────────


def test_pattern_adjustment_no_patterns():
    adj, active = _pattern_score_adjustment([], 200)
    assert adj == pytest.approx(0.0)
    assert active == []


def test_pattern_adjustment_bullish():
    patterns = [{"name": "bull_flag", "confidence": 1.0, "end_idx": 198}]
    adj, active = _pattern_score_adjustment(patterns, 200)
    assert adj > 0
    assert "bull_flag" in active


def test_pattern_adjustment_bearish():
    patterns = [{"name": "head_and_shoulders", "confidence": 1.0, "end_idx": 198}]
    adj, _ = _pattern_score_adjustment(patterns, 200)
    assert adj < 0


def test_pattern_adjustment_clipped():
    """Stacking many patterns should not exceed ±0.15."""
    many = [
        {"name": "bull_flag", "confidence": 1.0, "end_idx": 198},
        {"name": "cup_and_handle", "confidence": 1.0, "end_idx": 197},
        {"name": "double_bottom", "confidence": 1.0, "end_idx": 196},
        {"name": "ascending_triangle", "confidence": 1.0, "end_idx": 195},
    ]
    adj, _ = _pattern_score_adjustment(many, 200)
    assert -0.15 <= adj <= 0.15


def test_pattern_adjustment_stale_pattern_ignored():
    """Patterns older than 20 bars from the end should have no effect."""
    stale = [{"name": "bull_flag", "confidence": 1.0, "end_idx": 0}]
    adj, active = _pattern_score_adjustment(stale, 200)
    assert adj == pytest.approx(0.0)
    assert active == []


# ── _decide ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prob,regime,expected_signal", [
    (0.80, "bull", "BUY"),
    (0.70, "bull", "BUY"),
    (0.60, "bull", "HOLD"),
    (0.45, "bull", "WAIT"),
    (0.20, "bull", "SELL"),
    # Bear market raises the BUY threshold
    (0.70, "bear", "HOLD"),
    (0.75, "bear", "BUY"),
    (0.40, "bear", "WAIT"),
])
def test_decide_mapping(prob, regime, expected_signal):
    signal, horizon = _decide(prob, regime)
    assert signal == expected_signal
    assert horizon == "SWING"


def test_decide_boundary_bull():
    """Exactly at the BUY threshold (0.65) → BUY."""
    signal, _ = _decide(0.651, "bull")
    assert signal == "BUY"


def test_decide_boundary_sell():
    """Below 0.35 → SELL."""
    signal, _ = _decide(0.34, "bull")
    assert signal == "SELL"
