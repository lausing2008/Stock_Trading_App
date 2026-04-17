import numpy as np
import pandas as pd

from src.dsl import compute_features, evaluate_rule


def _df(n=300):
    rng = np.random.default_rng(0)
    close = 100 + rng.normal(0, 1, n).cumsum()
    return pd.DataFrame(
        {
            "ts": pd.date_range("2023-01-01", periods=n, freq="D"),
            "open": close + rng.normal(0, 0.2, n),
            "high": close + np.abs(rng.normal(0, 0.5, n)),
            "low": close - np.abs(rng.normal(0, 0.5, n)),
            "close": close,
            "volume": rng.integers(1000, 5000, n),
        }
    )


def test_simple_rule():
    df = compute_features(_df())
    rule = {"op": "<", "left": "rsi_14", "right": 30}
    out = evaluate_rule(rule, df)
    assert out.dtype == bool
    assert len(out) == len(df)


def test_and_rule():
    df = compute_features(_df())
    rule = {
        "op": "and",
        "nodes": [
            {"op": "<", "left": "rsi_14", "right": 70},
            {"op": ">", "left": "close", "right": "sma_50"},
        ],
    }
    out = evaluate_rule(rule, df)
    assert out.dtype == bool


def test_crosses_above():
    df = compute_features(_df())
    rule = {"op": "crosses_above", "left": "macd", "right": "macd_signal"}
    out = evaluate_rule(rule, df)
    assert out.dtype == bool
