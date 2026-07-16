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


@dataclass
class FairValueGap:
    top: float          # upper edge of the gap (always the higher price, regardless of kind)
    bottom: float       # lower edge of the gap (always the lower price)
    kind: str           # "bullish" | "bearish"
    idx: int            # index of the middle candle (the one whose range IS the gap)
    filled: bool        # has price traded back through the full gap since it formed?
    filled_idx: int | None  # index of the bar that completed the fill, or None if still open


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


def _cluster_pivots(df: pd.DataFrame, order: int, tolerance: float) -> list[Level]:
    """Cluster all pivot highs/lows in df into Level objects.

    T247-TA-CLUSTERPIVOTS-CLOSE-HIGH-MISMATCH: previously found pivot indices on `close`
    (_find_pivots(df["close"], ...)) but then read the reported price from `high`/`low` at
    those same indices — a close-based local max/min is not guaranteed to coincide with the
    bar's actual high/low (e.g. a long wick), so the reported S/R level wasn't actually a
    local extremum at all. Same bug class already fixed at every call site in
    patterns/recognizer.py (T237-TA-HS-CLOSE-HIGH-MISMATCH, TA-DTB1, TA-TRI1) but missed here
    in trendlines.py's own _find_pivots()-consuming code, which detect_support_resistance()
    (GET /ta/{symbol}/levels) actually depends on. Find pivots on the same series being read.
    """
    highs_idx, _ = _find_pivots(df["high"], order=order)
    _, lows_idx = _find_pivots(df["low"], order=order)
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
    return levels


def _fib_levels_from_range(df: pd.DataFrame) -> list[Level]:
    """Generate Fibonacci retracement levels from the recent 90-bar high/low range.

    Used as a fallback when a stock is at new highs with no nearby pivot S/R.
    Fib levels are widely watched by traders and serve as proxy entry/exit zones.
    """
    recent = df.tail(90)
    hi = float(recent["high"].max())
    lo = float(recent["low"].min())
    rng = hi - lo
    if rng < 1e-6:
        return []
    # Standard Fibonacci retracements from the swing high
    levels = []
    for ratio, kind in ((0.236, "resistance"), (0.382, "support"),
                        (0.500, "support"), (0.618, "support"), (0.786, "support")):
        price = hi - ratio * rng
        levels.append(Level(price=round(price, 4), kind=kind, strength=1))
    return levels


def detect_support_resistance(
    df: pd.DataFrame, order: int = 5, tolerance: float = 0.01, max_levels: int = 6
) -> list[Level]:
    """Cluster pivot prices into S/R levels; strength = touch count.

    Strategy:
    1. Try pivot detection on the most recent 90 bars (local structure). If 2+
       levels fall within 25% of the current price, use those.
    2. Fall back to the full df, within 35% of current price. Use if 2+ found.
    3. Synthesise Fibonacci retracement levels from the 90-bar high/low range.
       This handles stocks at new highs where no pivot S/R exists nearby
       (e.g. SMTC at $60-80 for 370 bars, then breaking out to $150 —
       the 60%-band fallback would still return the stale $63-81 pivots).
    """
    current_price = float(df["close"].iloc[-1])

    def _nearby(levels: list[Level], band: float) -> list[Level]:
        return [L for L in levels if abs(L.price - current_price) / current_price <= band]

    # 1. Local structure (last 90 bars)
    local_df = df.tail(90) if len(df) > 90 else df
    local_levels = _cluster_pivots(local_df, order=min(order, 4), tolerance=tolerance)
    nearby_local = _nearby(local_levels, 0.25)
    if len(nearby_local) >= 2:
        return nearby_local[:max_levels]

    # 2. Full history within 35% — catches established S/R that's still relevant
    all_levels = _cluster_pivots(df, order=order, tolerance=tolerance)
    candidates_35 = _nearby(all_levels, 0.35)
    if len(candidates_35) >= 2:
        return candidates_35[:max_levels]

    # 3. Fibonacci fallback — stock at new highs with no established S/R nearby
    fib = _fib_levels_from_range(df)
    return (fib + candidates_35)[:max_levels]


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


def detect_fair_value_gaps(
    df: pd.DataFrame, lookback: int = 200, min_gap_pct: float = 0.001, max_gaps: int = 20,
) -> list[FairValueGap]:
    """Detect Fair Value Gaps (FVG) — a 3-candle price-action imbalance.

    Bullish FVG: bar[i-1].high < bar[i+1].low — the gap is bar[i]'s own high-low range that
    sits entirely between the two neighbors, meaning bar[i]'s move up was so decisive that
    bar[i-1] and bar[i+1] never overlap it at all. Bearish FVG is the mirror: bar[i-1].low >
    bar[i+1].high. The "gap" itself is [bar[i-1].high, bar[i+1].low] for bullish (bar[i]'s own
    high/low are NOT the gap boundary — the two OUTER bars define it; this is the standard
    ICT/smart-money-concepts definition, not a naive single-bar gap).

    Price often "fills" (retraces back into) an FVG before continuing in the original
    direction — traders use the gap as a probable entry zone on a pullback. filled=True once
    any later bar's range fully covers [bottom, top].

    Only scans the last `lookback` bars (FVGs older than a few hundred bars are rarely still
    relevant — the same "recent structure first" reasoning detect_support_resistance() already
    uses for its 90-bar local pass). min_gap_pct filters out near-zero, noise-level gaps that
    aren't real imbalances (guards against divide-by-zero/float noise on very low-priced or
    illiquid symbols).
    """
    start = max(0, len(df) - lookback)
    highs = df["high"].values
    lows = df["low"].values
    gaps: list[FairValueGap] = []

    for i in range(max(1, start), len(df) - 1):
        prev_high, prev_low = highs[i - 1], lows[i - 1]
        next_high, next_low = highs[i + 1], lows[i + 1]

        if prev_high < next_low:
            top, bottom, kind = float(next_low), float(prev_high), "bullish"
        elif prev_low > next_high:
            top, bottom, kind = float(prev_low), float(next_high), "bearish"
        else:
            continue

        mid = (top + bottom) / 2 or 1e-9
        if (top - bottom) / abs(mid) < min_gap_pct:
            continue

        filled = False
        filled_idx = None
        for j in range(i + 2, len(df)):
            if lows[j] <= bottom and highs[j] >= top:
                filled = True
                filled_idx = j
                break

        gaps.append(FairValueGap(
            top=top, bottom=bottom, kind=kind, idx=i, filled=filled, filled_idx=filled_idx,
        ))

    return gaps[-max_gaps:]
