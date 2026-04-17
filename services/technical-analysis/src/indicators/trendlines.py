"""Automated trendline + support/resistance detection via pivot points."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Level:
    price: float
    kind: str       # "support" | "resistance"
    strength: int   # # of touches


@dataclass
class Trendline:
    slope: float
    intercept: float
    kind: str       # "uptrend" | "downtrend"
    r2: float
    anchor_idx: list[int]


def _find_pivots(series: pd.Series, order: int = 5) -> tuple[np.ndarray, np.ndarray]:
    """Simple pivot detection: local max/min within +-order bars."""
    vals = series.values
    n = len(vals)
    highs, lows = [], []
    for i in range(order, n - order):
        window = vals[i - order : i + order + 1]
        if vals[i] == window.max():
            highs.append(i)
        if vals[i] == window.min():
            lows.append(i)
    return np.array(highs), np.array(lows)


def detect_support_resistance(
    df: pd.DataFrame, order: int = 5, tolerance: float = 0.01, max_levels: int = 6
) -> list[Level]:
    """Cluster pivot prices into S/R levels; strength = touch count."""
    highs_idx, lows_idx = _find_pivots(df["close"], order=order)
    highs = df["high"].values[highs_idx]
    lows = df["low"].values[lows_idx]

    levels: list[Level] = []
    for prices, kind in ((highs, "resistance"), (lows, "support")):
        for p in prices:
            matched = False
            for L in levels:
                if L.kind == kind and abs(L.price - p) / max(L.price, 1e-9) < tolerance:
                    L.strength += 1
                    matched = True
                    break
            if not matched:
                levels.append(Level(price=float(p), kind=kind, strength=1))
    levels.sort(key=lambda L: L.strength, reverse=True)
    return levels[:max_levels]


def detect_trendlines(df: pd.DataFrame, order: int = 5) -> list[Trendline]:
    """Least-squares fit through consecutive pivot lows (uptrend) / highs (downtrend)."""
    highs_idx, lows_idx = _find_pivots(df["close"], order=order)
    out: list[Trendline] = []

    for idx, label in ((lows_idx, "uptrend"), (highs_idx, "downtrend")):
        if len(idx) < 3:
            continue
        y = df["close"].values[idx]
        x = idx.astype(float)
        slope, intercept = np.polyfit(x, y, 1)
        pred = slope * x + intercept
        ss_res = float(((y - pred) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum()) or 1e-9
        r2 = 1 - ss_res / ss_tot
        if (label == "uptrend" and slope > 0) or (label == "downtrend" and slope < 0):
            out.append(
                Trendline(
                    slope=float(slope),
                    intercept=float(intercept),
                    kind=label,
                    r2=float(r2),
                    anchor_idx=[int(i) for i in idx.tolist()],
                )
            )
    return out
