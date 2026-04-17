"""Rule DSL evaluator — safe boolean trees over precomputed feature series.

Supported leaf ops: >, >=, <, <=, ==, crosses_above, crosses_below.
Internal nodes: and, or, not.

Example rule (JSON tree):
  {"op": "and", "nodes": [
     {"op": "<", "left": "rsi_14", "right": 30},
     {"op": ">", "left": "close", "right": "sma_50"}
  ]}

No `eval()` — we walk the tree, so arbitrary-code execution isn't possible.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Extend OHLCV with standard indicators used by the DSL."""
    out = df.copy()
    close = out["close"]
    out["sma_20"] = close.rolling(20).mean()
    out["sma_50"] = close.rolling(50).mean()
    out["sma_200"] = close.rolling(200).mean()
    out["ema_12"] = close.ewm(span=12, adjust=False).mean()
    out["ema_26"] = close.ewm(span=26, adjust=False).mean()

    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    out["rsi_14"] = 100 - 100 / (1 + rs)

    macd = out["ema_12"] - out["ema_26"]
    out["macd"] = macd
    out["macd_signal"] = macd.ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    return out


def _resolve(node: Any, df: pd.DataFrame) -> pd.Series | float:
    if isinstance(node, (int, float)):
        return float(node)
    if isinstance(node, str):
        if node not in df.columns:
            raise ValueError(f"Unknown feature: {node}")
        return df[node]
    raise ValueError(f"Bad operand: {node!r}")


def _cmp(left: pd.Series | float, op: str, right: pd.Series | float) -> pd.Series:
    ops = {
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
    }
    if op in ops:
        return ops[op](left, right).fillna(False)
    if op == "crosses_above":
        diff = left - right
        return (diff.shift(1) <= 0) & (diff > 0)
    if op == "crosses_below":
        diff = left - right
        return (diff.shift(1) >= 0) & (diff < 0)
    raise ValueError(f"Unsupported op: {op}")


def evaluate_rule(rule: dict, df: pd.DataFrame) -> pd.Series:
    op = rule.get("op")
    if op in ("and", "or"):
        sub = [evaluate_rule(n, df) for n in rule["nodes"]]
        result = sub[0]
        for s in sub[1:]:
            result = (result & s) if op == "and" else (result | s)
        return result.fillna(False)
    if op == "not":
        return ~evaluate_rule(rule["node"], df)
    left = _resolve(rule["left"], df)
    right = _resolve(rule["right"], df)
    if isinstance(left, float):
        left = pd.Series([left] * len(df), index=df.index)
    return _cmp(left, op, right)
