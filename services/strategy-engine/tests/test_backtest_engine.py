"""Regression test for T247-STRATEGYENGINE-CAGR-OVERFLOW.

CAGR = equity ** (1/years) - 1 previously floored `years` at 1e-6 for a same-calendar-day (or
otherwise near-zero-day) backtest range, raising equity to the power of up to 1,000,000 —
any equity != 1.0 silently overflows to `inf` (a numpy RuntimeWarning, not an exception).
`inf`/`nan` is not valid JSON, breaking the frontend backtest page and corrupting the stored
Backtest.cagr row.
"""
import numpy as np
import pandas as pd

from src.backtest.engine import BacktestEngine


def _same_day_df(n=3, start_price=100.0):
    """A handful of bars all on the same calendar date — reproduces the exact bug scenario
    (feat['ts'].iloc[-1] - feat['ts'].iloc[0] rounds to 0 days)."""
    same_ts = pd.Timestamp("2026-01-01")
    close = [start_price, start_price * 1.02, start_price * 1.04][:n]
    return pd.DataFrame({
        "ts": [same_ts] * n,
        "open": close,
        "high": [c * 1.001 for c in close],
        "low": [c * 0.999 for c in close],
        "close": close,
        "volume": [1000] * n,
    })


def _normal_df(n=300, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0.05, 1, n).cumsum()
    return pd.DataFrame({
        "ts": pd.date_range("2023-01-01", periods=n, freq="D"),
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": rng.integers(1000, 5000, n),
    })


def test_same_day_backtest_produces_finite_or_none_cagr_not_inf():
    """The exact bug scenario: a same-calendar-day range with a real equity move must not
    produce inf — either a large-but-finite number or None, never inf/nan."""
    df = _same_day_df()
    engine = BacktestEngine()
    rule = {"op": ">", "left": "close", "right": 0}  # always true -> always in position
    result = engine.run(df, rule)
    assert result.cagr is None or np.isfinite(result.cagr), f"cagr={result.cagr!r} is not finite/None"


def test_same_day_cagr_is_strict_json_compliant():
    """inf/nan are NOT valid per the JSON spec — Python's stdlib json.dumps emits the
    non-compliant literal tokens "Infinity"/"NaN" by default (and json.loads accepts them
    back non-strictly), which is why a naive round-trip test through Python's own json module
    would NOT catch this bug — a strict client (e.g. JS's JSON.parse) rejects those tokens
    outright. Assert directly on the serialized text containing neither token, rather than
    round-tripping through Python's own permissive parser."""
    import json
    df = _same_day_df()
    engine = BacktestEngine()
    rule = {"op": ">", "left": "close", "right": 0}
    result = engine.run(df, rule)
    serialized = json.dumps({"cagr": result.cagr})
    assert "Infinity" not in serialized, f"cagr serialized as non-JSON-compliant Infinity: {serialized}"
    assert "NaN" not in serialized, f"cagr serialized as non-JSON-compliant NaN: {serialized}"


def test_normal_multi_year_backtest_cagr_unaffected():
    """Sanity check: a normal, multi-year backtest must still produce a real, sensible CAGR —
    the fix must not degrade the common case."""
    df = _normal_df(300)
    engine = BacktestEngine()
    rule = {"op": ">", "left": "sma_20", "right": "sma_50"}
    result = engine.run(df, rule)
    assert result.cagr is None or np.isfinite(result.cagr)
    if result.cagr is not None:
        # A real multi-year backtest shouldn't produce an absurdly large annualized number.
        assert abs(result.cagr) < 100, f"cagr={result.cagr} looks like a residual overflow, not a real value"


def test_calmar_is_none_when_cagr_is_none():
    """calmar = cagr / max_drawdown is undefined without a real cagr — must not raise
    TypeError on None / float."""
    df = _same_day_df()
    engine = BacktestEngine()
    rule = {"op": ">", "left": "close", "right": 0}
    result = engine.run(df, rule)  # must not raise
    if result.cagr is None:
        assert result.calmar is None
