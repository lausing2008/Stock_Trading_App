import numpy as np
import pandas as pd

from src.indicators.core import bollinger_bands, macd, rsi, sma, supertrend
from src.patterns.recognizer import detect_patterns


def _series(n=200, seed=0):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1, n).cumsum()
    return pd.Series(100 + steps)


def _ohlc_df(n=400, seed=42):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 0.5, n)))
    high = close + np.abs(rng.normal(0, 0.3, n))
    low = close - np.abs(rng.normal(0, 0.3, n))
    return pd.DataFrame({"high": high, "low": low, "close": close})


def test_sma_window():
    s = _series()
    import pytest
    assert sma(s, 20).iloc[-1] == pytest.approx(s.tail(20).mean(), rel=1e-9)


def test_rsi_in_range():
    s = _series()
    r = rsi(s, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_macd_columns():
    s = _series()
    out = macd(s)
    assert set(out.columns) == {"macd", "signal", "hist"}


def test_bollinger_bands_order():
    s = _series()
    bb = bollinger_bands(s).dropna()
    assert (bb["bb_upper"] >= bb["bb_mid"]).all()
    assert (bb["bb_mid"] >= bb["bb_lower"]).all()


def test_supertrend_produces_non_nan_values_past_warmup():
    """T247-TA-SUPERTREND-NAN-SEED regression guard: supertrend() previously never seeded its
    final_upper/final_lower band-carry once ATR first became valid, leaving the ENTIRE output
    100% NaN for every symbol regardless of input length. Only the ATR warmup window (the
    first `period` bars) should be NaN — everything after must be real."""
    df = _ohlc_df(n=400)
    result = supertrend(df, period=10)
    # ATR uses min_periods=period, so index `period - 1` (the 10th bar, 0-indexed 9) is the
    # first bar with a real (non-NaN) ATR/basic-band value.
    first_valid = 10 - 1
    assert result["supertrend"].iloc[:first_valid].isna().all()
    assert not result["supertrend"].iloc[first_valid:].isna().any()


def test_supertrend_trend_is_not_stuck_at_initial_value():
    """Before the fix, `trend` never left its initial +1 for the entire series because the
    NaN-poisoned final_upper/final_lower made every post-warmup comparison degenerate."""
    df = _ohlc_df(n=400)
    result = supertrend(df, period=10)
    trend_values = set(result["trend"].dropna().unique())
    assert trend_values == {1.0, -1.0}, (
        f"expected both trend directions to appear over 400 bars, got {trend_values}"
    )


def test_supertrend_cross_signals_can_fire():
    """cross_up/cross_down are computed from trend transitions — if trend is stuck, these can
    never be True. A realistic 400-bar series should show at least one crossing each way."""
    df = _ohlc_df(n=400)
    result = supertrend(df, period=10)
    assert result["cross_up"].any()
    assert result["cross_down"].any()


def test_supertrend_first_valid_bar_matches_basic_band_not_nan():
    """Direct check of the seed fix: at the first bar where ATR becomes valid (i=period), the
    supertrend value must come from that bar's own basic upper/lower band, not carry forward
    the NaN from the warmup region."""
    df = _ohlc_df(n=50)
    result = supertrend(df, period=10)
    first_valid_idx = 10 - 1  # ATR min_periods=10, so index 9 is the first non-NaN basic band
    assert not pd.isna(result["supertrend"].iloc[first_valid_idx])


def test_patterns_run():
    n = 200
    df = pd.DataFrame(
        {
            "close": np.linspace(100, 120, n) + np.random.default_rng(0).normal(0, 1, n),
            "high": np.linspace(101, 121, n),
            "low": np.linspace(99, 119, n),
            "volume": [1000] * n,
        }
    )
    patterns = detect_patterns(df)
    assert isinstance(patterns, list)
