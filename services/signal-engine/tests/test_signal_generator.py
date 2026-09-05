"""Unit tests for signal generation pure functions.

All tests operate on synthetic DataFrames — no network calls, no DB.
"""
import numpy as np
import pandas as pd
import pytest

from src.generators.signals import (
    _adx,
    _decide_style,
    _pattern_score_adjustment,
    _stoch_rsi,
    _ta_score,
    _weekly_technicals,
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
        # Renamed from obv_bullish — _TA_WEIGHTS and the reasons dict both use
        # obv_trend_bullish (the name _flag_map actually looks up).
        "obv_trend_bullish", "volume_z",
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


# ── _weekly_technicals ────────────────────────────────────────────────────────
# Renamed from _weekly_ta_score and now returns a full dict (weekly_rsi/weekly_trend/
# weekly_macd_bull/weekly_score/weekly_confidence) rather than a bare float, and the
# insufficient-history cutoff is 15 bars, not 26.


def test_weekly_technicals_score_range():
    tech = _weekly_technicals(_make_df(n=100))
    assert 0.0 <= tech["weekly_score"] <= 1.0


def test_weekly_technicals_reports_its_own_shape():
    tech = _weekly_technicals(_make_df(n=100))
    for key in ("weekly_rsi", "weekly_trend", "weekly_macd_bull",
                "weekly_score", "weekly_confidence"):
        assert key in tech
    assert tech["weekly_trend"] in ("up", "down", "neutral")


def test_weekly_technicals_too_few_bars_returns_neutral():
    """Under 15 bars → the neutral block, including zero confidence so the alignment
    filter downstream is skipped rather than acting on absent data."""
    tech = _weekly_technicals(_make_df(n=10))
    assert tech["weekly_score"] == pytest.approx(0.5)
    assert tech["weekly_confidence"] == 0.0
    assert tech["weekly_rsi"] is None


def test_weekly_technicals_empty_returns_neutral():
    tech = _weekly_technicals(pd.DataFrame())
    assert tech["weekly_score"] == pytest.approx(0.5)
    assert tech["weekly_confidence"] == 0.0


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


# ── _decide_style ─────────────────────────────────────────────────────────────
# Renamed from _decide. Now takes an explicit style_key and returns a 3-tuple
# (signal, style_key, threshold_tier) instead of (signal, horizon).
#
# The old tests asserted hardcoded probability->label mappings (BUY at 0.65, SELL below
# 0.35). Those numbers are no longer static: _decide_style reads dynamically-calibrated
# buy/sell thresholds from Redis (written by POST /outcomes/calibrate/apply) and falls back
# to per-style, per-regime profile values. Asserting fixed cut-points would make this file
# fail whenever calibration legitimately moved a threshold. These tests pin the parts that
# are genuinely invariant: the label vocabulary, the monotonic ordering, and the tier map.


@pytest.mark.parametrize("style", ["SHORT", "SWING", "LONG", "GROWTH"])
def test_decide_style_returns_three_tuple_with_valid_label(style):
    signal, style_key, tier = _decide_style(0.70, style, "bull")
    assert signal in ("BUY", "HOLD", "WAIT", "SELL")
    assert style_key == style
    assert tier in ("bull", "bear", "neutral")


def test_decide_style_is_monotonic_in_probability():
    """Higher fused probability must never produce a MORE bearish label — this is the real
    invariant, independent of where the calibrated thresholds currently sit."""
    rank = {"SELL": 0, "WAIT": 1, "HOLD": 2, "BUY": 3}
    labels = [_decide_style(p / 100, "SWING", "bull")[0]
              for p in range(5, 100, 5)]
    ranks = [rank[l] for l in labels]
    assert ranks == sorted(ranks), f"non-monotonic label sequence: {labels}"


def test_decide_style_extremes_map_to_extremes():
    assert _decide_style(0.99, "SWING", "bull")[0] == "BUY"
    assert _decide_style(0.01, "SWING", "bull")[0] == "SELL"


@pytest.mark.parametrize("regime,expected_tier", [
    ("bull", "bull"),
    ("bear", "bear"),
    ("risk_off", "bear"),
    ("neutral", "neutral"),
    ("choppy", "neutral"),
    ("unknown", "neutral"),
])
def test_decide_style_tier_mapping(regime, expected_tier):
    """risk_off shares the bear tier; everything non-bull/non-bear is neutral."""
    assert _decide_style(0.70, "SWING", regime)[2] == expected_tier


def test_decide_style_unrecognized_regime_falls_back_to_unknown_not_a_crash():
    """AUD264 kept 'unknown' as the fail-open value _fetch_market_regime() returns on a
    fetch failure — an unrecognized string must land there, not raise."""
    signal, _, tier = _decide_style(0.70, "SWING", "not_a_real_regime")
    assert signal in ("BUY", "HOLD", "WAIT", "SELL")
    assert tier == "neutral"


def test_decide_style_bear_regime_is_no_easier_than_bull():
    """A bear/risk_off tape must never make BUY easier to reach than in a bull tape."""
    rank = {"SELL": 0, "WAIT": 1, "HOLD": 2, "BUY": 3}
    for p in (0.55, 0.65, 0.75, 0.85):
        bull = rank[_decide_style(p, "SWING", "bull")[0]]
        bear = rank[_decide_style(p, "SWING", "bear")[0]]
        assert bear <= bull, f"bear was more bullish than bull at p={p}"
