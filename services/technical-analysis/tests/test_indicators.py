import numpy as np
import pandas as pd

from src.indicators.core import bollinger_bands, macd, rsi, sma, vwap
from src.patterns.recognizer import detect_patterns


def _series(n=200, seed=0):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1, n).cumsum()
    return pd.Series(100 + steps)


def test_sma_window():
    s = _series()
    assert sma(s, 20).iloc[-1] == s.tail(20).mean()


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


def test_vwap_finite():
    n = 100
    rng = np.random.default_rng(1)
    df = pd.DataFrame(
        {
            "high": 100 + rng.uniform(0, 2, n),
            "low": 100 - rng.uniform(0, 2, n),
            "close": 100 + rng.normal(0, 1, n),
            "volume": rng.integers(1000, 5000, n),
        }
    )
    v = vwap(df["high"], df["low"], df["close"], df["volume"])
    assert np.isfinite(v).all()


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
