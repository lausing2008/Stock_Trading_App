"""Chart pattern recognizer — geometric heuristics on pivot points.

These are first-pass heuristic detectors. They return candidate patterns with
a confidence score that downstream ML re-ranks. Good enough for MVP signals;
swap in a CNN on OHLC images for higher precision later.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..indicators.trendlines import _find_pivots


@dataclass
class PatternHit:
    name: str
    start_idx: int
    end_idx: int
    confidence: float  # 0-1
    meta: dict


def _last_n_pivots(idx: np.ndarray, n: int) -> np.ndarray:
    return idx[-n:] if len(idx) >= n else np.array([], dtype=int)


def _pct(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-9)


def detect_head_and_shoulders(df: pd.DataFrame) -> list[PatternHit]:
    highs_idx, _ = _find_pivots(df["close"], order=5)
    highs_idx = _last_n_pivots(highs_idx, 5)
    if len(highs_idx) < 3:
        return []
    a, b, c = highs_idx[-3], highs_idx[-2], highs_idx[-1]
    ha, hb, hc = df["high"].iloc[a], df["high"].iloc[b], df["high"].iloc[c]
    if hb > ha and hb > hc and _pct(ha, hc) < 0.05:
        return [
            PatternHit(
                "head_and_shoulders",
                int(a),
                int(c),
                confidence=min(1.0, (hb - max(ha, hc)) / max(hb, 1e-9) * 5),
                meta={"left": float(ha), "head": float(hb), "right": float(hc)},
            )
        ]
    return []


def detect_double_top_bottom(df: pd.DataFrame) -> list[PatternHit]:
    hits: list[PatternHit] = []
    highs_idx, lows_idx = _find_pivots(df["close"], order=5)
    if len(highs_idx) >= 2:
        a, b = highs_idx[-2], highs_idx[-1]
        if _pct(df["high"].iloc[a], df["high"].iloc[b]) < 0.02:
            hits.append(PatternHit("double_top", int(a), int(b), 0.7, {}))
    if len(lows_idx) >= 2:
        a, b = lows_idx[-2], lows_idx[-1]
        if _pct(df["low"].iloc[a], df["low"].iloc[b]) < 0.02:
            hits.append(PatternHit("double_bottom", int(a), int(b), 0.7, {}))
    return hits


def detect_triangle(df: pd.DataFrame, window: int = 60) -> list[PatternHit]:
    """Ascending/descending/symmetric triangles via converging pivot slopes."""
    sub = df.tail(window)
    highs_idx, lows_idx = _find_pivots(sub["close"], order=3)
    if len(highs_idx) < 2 or len(lows_idx) < 2:
        return []
    hs = np.polyfit(highs_idx, sub["high"].values[highs_idx], 1)[0]
    ls = np.polyfit(lows_idx, sub["low"].values[lows_idx], 1)[0]
    if hs < -1e-3 and ls > 1e-3:
        kind = "symmetric_triangle"
    elif abs(hs) < 1e-3 and ls > 1e-3:
        kind = "ascending_triangle"
    elif hs < -1e-3 and abs(ls) < 1e-3:
        kind = "descending_triangle"
    else:
        return []
    return [PatternHit(kind, int(highs_idx[0]), int(lows_idx[-1]), 0.6, {"high_slope": float(hs), "low_slope": float(ls)})]


def detect_flag_pennant(df: pd.DataFrame, pole_window: int = 10, flag_window: int = 20) -> list[PatternHit]:
    if len(df) < pole_window + flag_window:
        return []
    pole = df.iloc[-(pole_window + flag_window) : -flag_window]
    flag = df.iloc[-flag_window:]
    pole_ret = (pole["close"].iloc[-1] - pole["close"].iloc[0]) / pole["close"].iloc[0]
    flag_range = (flag["high"].max() - flag["low"].min()) / flag["close"].mean()
    if abs(pole_ret) > 0.08 and flag_range < 0.05:
        kind = "bull_flag" if pole_ret > 0 else "bear_flag"
        return [PatternHit(kind, len(df) - pole_window - flag_window, len(df) - 1, 0.65, {"pole_return": float(pole_ret)})]
    return []


def detect_cup_and_handle(df: pd.DataFrame, window: int = 120) -> list[PatternHit]:
    if len(df) < window:
        return []
    sub = df.tail(window)["close"].values
    n = len(sub)
    left = sub[: n // 3].max()
    middle = sub[n // 3 : 2 * n // 3].min()
    right = sub[2 * n // 3 :].max()
    if _pct(left, right) < 0.05 and middle < left * 0.88:
        return [PatternHit("cup_and_handle", len(df) - window, len(df) - 1, 0.55, {"depth": float((left - middle) / left)})]
    return []


def detect_patterns(df: pd.DataFrame) -> list[dict]:
    hits: list[PatternHit] = []
    hits.extend(detect_head_and_shoulders(df))
    hits.extend(detect_double_top_bottom(df))
    hits.extend(detect_triangle(df))
    hits.extend(detect_flag_pennant(df))
    hits.extend(detect_cup_and_handle(df))
    return [asdict(h) for h in hits]
