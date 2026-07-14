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
    high  = out["high"]   if "high"   in out.columns else close
    low   = out["low"]    if "low"    in out.columns else close

    out["sma_20"]  = close.rolling(20).mean()
    out["sma_50"]  = close.rolling(50).mean()
    out["sma_200"] = close.rolling(200).mean()
    # T237-SE2: .ewm() with no min_periods emits a value from the very first observation —
    # unlike .rolling(N) above (which defaults min_periods=N and correctly returns NaN during
    # warmup), so ema_12/ema_26 and everything derived from them (rsi_14, macd*, atr_14) looked
    # "fully formed" after just 1-2 bars. Confirmed: a 6-bar series produced rsi_14=86.67 after
    # only 2 real price diffs, and a 30-bar backtest entered a real trade on day 3 driven by
    # this numerically meaningless value. Pass min_periods explicitly on every ewm() below.
    out["ema_12"]  = close.ewm(span=12, adjust=False, min_periods=12).mean()
    out["ema_26"]  = close.ewm(span=26, adjust=False, min_periods=26).mean()

    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = g / l.replace(0, np.nan)
    out["rsi_14"] = 100 - 100 / (1 + rs)

    macd = out["ema_12"] - out["ema_26"]
    out["macd"]        = macd
    out["macd_signal"] = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    out["macd_hist"]   = out["macd"] - out["macd_signal"]

    # ATR(14) — Wilder smoothing using EWM
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    out["atr_14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    # Bollinger Bands (20-period, 2σ)
    bb_std         = close.rolling(20).std()
    out["bb_upper"] = out["sma_20"] + 2 * bb_std
    out["bb_lower"] = out["sma_20"] - 2 * bb_std
    bb_range        = (out["bb_upper"] - out["bb_lower"]).replace(0, np.nan)
    out["bb_pct"]   = (close - out["bb_lower"]) / bb_range  # 0=lower band, 1=upper band

    # Volume (relative to 20-day average)
    if "volume" in out.columns:
        out["volume_sma_20"] = out["volume"].rolling(20).mean()
        out["volume_ratio"]  = out["volume"] / out["volume_sma_20"].replace(0, np.nan)
    else:
        out["volume_sma_20"] = np.nan
        out["volume_ratio"]  = np.nan

    return out


def _resolve(node: Any, df: pd.DataFrame) -> pd.Series | float:
    if isinstance(node, (int, float)):
        return float(node)
    if isinstance(node, str):
        if node not in df.columns:
            raise ValueError(f"Unknown feature: {node}")
        return df[node]
    raise ValueError(f"Bad operand: {node!r}")


def _mask_nan_operands(result: pd.Series, *operands: "pd.Series | float") -> pd.Series:
    """Convert `result` to nullable-boolean dtype and set positions to <NA> wherever ANY
    operand was NaN. A plain float comparison against NaN (e.g. `a > b` with b=NaN) always
    numerically evaluates to False in numpy/pandas — it never propagates as NaN on its own —
    so this has to be done explicitly by checking the operands themselves, not by casting the
    comparison's own output afterward (astype("boolean") alone does not recover the lost
    "unknown" information once the comparison has already produced a real False)."""
    result = result.astype("boolean")
    nan_mask = pd.Series(False, index=result.index)
    for op_ in operands:
        if isinstance(op_, pd.Series):
            nan_mask = nan_mask | op_.isna()
    result[nan_mask] = pd.NA
    return result


def _cmp(left: pd.Series | float, op: str, right: pd.Series | float) -> pd.Series:
    ops = {
        ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b,
    }
    if op in ops:
        # T247-STRATEGYENGINE-NOT-NAN: previously fillna(False) was applied HERE, at every
        # leaf — by the time a `not` node's `~` ran on this result, the NaN (warmup-period
        # "unknown") had already been collapsed to a real False, so `not` inverted it to True.
        # Every warmup bar was silently reported as a true entry/exit signal. Explicitly mask
        # positions where either operand was NaN to <NA> (a numpy float comparison against NaN
        # always numerically evaluates to False, never NaN, so this can't be recovered from the
        # comparison's own output — see _mask_nan_operands()) so `not`/`and`/`or` can propagate
        # "unknown" correctly. The single fillna(False) now happens only once, at
        # evaluate_rule()'s outermost call.
        return _mask_nan_operands(ops[op](left, right), left, right)
    if op == "crosses_above":
        diff = left - right
        return _mask_nan_operands((diff.shift(1) <= 0) & (diff > 0), left, right)
    if op == "crosses_below":
        diff = left - right
        return _mask_nan_operands((diff.shift(1) >= 0) & (diff < 0), left, right)
    raise ValueError(f"Unsupported op: {op}")


def _evaluate_rule_nullable(rule: dict, df: pd.DataFrame) -> pd.Series:
    """Returns a nullable-boolean Series (dtype "boolean") where NaN means "unknown" (e.g.
    still in an indicator's warmup window) — distinct from a real False. and/or/not use
    pandas' native nullable-boolean (Kleene) logic, which correctly propagates unknown:
    not(unknown) = unknown, True & unknown = unknown, True | unknown = True, etc. Only the
    public evaluate_rule() collapses "unknown" to False, and only once, at the very top."""
    op = rule.get("op")
    if op in ("and", "or"):
        nodes = rule.get("nodes")
        if not nodes:
            raise ValueError(f"'{op}' node requires a non-empty 'nodes' list")
        sub = [_evaluate_rule_nullable(n, df) for n in nodes]
        result = sub[0]
        for s in sub[1:]:
            result = (result & s) if op == "and" else (result | s)
        return result
    if op == "not":
        if "node" not in rule:
            raise ValueError("'not' node requires a 'node' child")
        return ~_evaluate_rule_nullable(rule["node"], df)
    if "left" not in rule or "right" not in rule:
        raise ValueError(f"Comparison node op='{op}' requires 'left' and 'right'")
    left = _resolve(rule["left"], df)
    right = _resolve(rule["right"], df)
    if isinstance(left, float):
        left = pd.Series([left] * len(df), index=df.index)
    return _cmp(left, op, right)


def evaluate_rule(rule: dict, df: pd.DataFrame) -> pd.Series:
    """Public entrypoint — returns a plain bool Series (NaN/unknown collapsed to False),
    matching every existing caller's expectation (e.g. backtest/engine.py's .astype(bool))."""
    return _evaluate_rule_nullable(rule, df).fillna(False).astype(bool)
