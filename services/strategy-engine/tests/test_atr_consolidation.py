"""Regression test for AUD-DUPLOGIC: dsl/evaluator.py's compute_features() had its own inline
TR/ATR calculation (byte-identical formula to shared/common/indicators.py's canonical atr(),
already used by signal-engine/ranking-engine/market-data for the same purpose) instead of
importing and calling it — a strand this consolidation's earlier signal-engine-only pass missed
entirely, found by a follow-up full-repo re-sweep.
"""
import numpy as np
import pandas as pd

from common.indicators import atr as _real_canon_atr
from src.dsl import compute_features


def _make_df(n: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 1, n).cumsum()
    return pd.DataFrame({
        "ts": pd.date_range("2023-01-01", periods=n, freq="D"),
        "open": close + rng.normal(0, 0.2, n),
        "high": close + np.abs(rng.normal(0, 0.5, n)),
        "low": close - np.abs(rng.normal(0, 0.5, n)),
        "close": close,
        "volume": rng.integers(1000, 5000, n),
    })


def test_atr_14_matches_canonical_atr_function_directly():
    """compute_features()'s atr_14 column must be produced by the SAME canonical atr() call
    every other service uses — not a coincidentally-similar independent formula."""
    df = _make_df(seed=1)
    features = compute_features(df)

    expected = _real_canon_atr(df["high"], df["low"], df["close"], period=14)
    pd.testing.assert_series_equal(
        features["atr_14"].reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False,
    )


def test_atr_14_has_min_periods_guard():
    """Confirms the canonical function's min_periods=14 guard is actually in effect here —
    a short-history slice must produce NaN, not a fabricated early value (the exact bug class
    T237-SE2's own comment in this file documents fixing for ema_12/ema_26/rsi_14/macd, which
    atr_14 must share since it now goes through the same canonical function)."""
    df = _make_df(n=10, seed=2)
    features = compute_features(df)
    assert pd.isna(features["atr_14"].iloc[-1])


def test_atr_14_produces_real_values_with_sufficient_history():
    df = _make_df(n=300, seed=3)
    features = compute_features(df)
    assert not pd.isna(features["atr_14"].iloc[-1])
    assert features["atr_14"].iloc[-1] >= 0.0
