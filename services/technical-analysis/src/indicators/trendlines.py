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


def detect_sr_context(df: pd.DataFrame, levels: list[Level] | None = None) -> dict:
    """Classify the current bar's position relative to support/resistance as one of
    'breakout' | 'at_resistance' | 'at_support' | 'neutral'.

    AUD-DUPLOGIC: this is a straight port of signal-engine's own `_sr_context()` classification
    logic (services/signal-engine/src/generators/signals.py) — that file's pivot-DETECTION was
    a simplified, independent reimplementation of this module's own `detect_support_resistance()`
    (60-bar/±3-window vs. this module's 3-tier 90-bar/full-history/Fibonacci-fallback strategy,
    already fixed once for a close-vs-high/low pivot mismatch — T247-TA-CLUSTERPIVOTS-
    CLOSE-HIGH-MISMATCH — that signal-engine's own copy never received). Consolidating here
    means signal-engine's breakout/at_support labeling can no longer silently disagree with
    the chart's own official S/R levels for the same symbol at the same moment.

    `levels` may be passed in (already computed by the caller, e.g. GET /ta/{symbol}/levels'
    own detect_support_resistance() call) to avoid recomputing them a second time; if omitted,
    computes them fresh with the same defaults detect_support_resistance() itself uses.
    """
    if levels is None:
        levels = detect_support_resistance(df)

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    current = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) >= 2 else current

    # 52-week high/low from historical bars (excluding today, to avoid look-ahead).
    hist_len = min(252, len(close) - 1)
    hist_high = float(high.iloc[-hist_len - 1:-1].max()) if hist_len > 0 else float(high.max())
    hist_low = float(low.iloc[-hist_len - 1:-1].min()) if hist_len > 0 else float(low.min())

    resistances = [hist_high] + [L.price for L in levels if L.kind == "resistance"]
    supports = [L.price for L in levels if L.kind == "support"]

    nearest_res = min((r for r in resistances if r > current), default=None)
    nearest_sup = max((s for s in supports if s < current), default=None)
    # A stock that decisively clears every known resistance level in one move (a genuine
    # all-time-high breakout) has no level qualifying as "nearest" once price has passed it —
    # track the highest resistance still <= current separately so that case is still recognized
    # as a breakout instead of silently falling through to "neutral".
    cleared_res = max((r for r in resistances if r <= current), default=None)
    # Mirror of cleared_res for the downside — the lowest support still >= current, i.e. the
    # support level price has actually broken BELOW (not sr_nearest_support, which is by
    # construction always < current and therefore can never be "the level just broken").
    # This function's own sr_context classification doesn't currently distinguish a genuine
    # breakdown from plain "neutral" (only breakout/at_resistance/at_support/neutral exist) —
    # exposing cleared_sup is additive data only, not a change to that existing classification.
    cleared_sup = min((s for s in supports if s >= current), default=None)

    thr = 0.015  # 1.5% proximity threshold
    sr_context = "neutral"

    if cleared_res is not None and prev < cleared_res:
        # Price closed at/above a former resistance level the prior bar was still below — a
        # freshly-confirmed breakout, not just historically having traded above it.
        sr_context = "breakout"
    elif nearest_res is not None:
        dist = (nearest_res - current) / nearest_res
        if dist <= thr:
            if prev < nearest_res * (1.0 - thr):
                sr_context = "breakout"
            else:
                sr_context = "at_resistance"
    if sr_context == "neutral" and nearest_sup is not None:
        dist = (current - nearest_sup) / current
        if dist <= thr:
            sr_context = "at_support"

    return {
        "sr_context": sr_context,
        "sr_nearest_resistance": round(nearest_res, 2) if nearest_res is not None else None,
        "sr_nearest_support": round(nearest_sup, 2) if nearest_sup is not None else None,
        "sr_52w_high": round(hist_high, 2),
        "sr_52w_low": round(hist_low, 2),
        # T258-ACCUM-DIST-BREAKOUT-QUALITY: the actual levels a breakout/breakdown check should
        # test — sr_nearest_resistance/sr_nearest_support are ALWAYS on the "not yet reached"
        # side of current price by construction, so neither can be the level a genuine
        # breakout/breakdown just cleared. cleared_res/cleared_sup are the correct levels to
        # feed into assess_breakout_quality() for the "up"/"down" directions respectively.
        "sr_cleared_resistance": round(cleared_res, 2) if cleared_res is not None else None,
        "sr_cleared_support": round(cleared_sup, 2) if cleared_sup is not None else None,
    }


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

        # AUD-FVG-SINGLEBARFILL: the old check required ONE bar's range to span the entire
        # [bottom, top] gap (lows[j] <= bottom and highs[j] >= top). A gap traded through
        # gradually over several bars — each covering only part of the range — never
        # satisfied that single-bar condition and stayed filled=False forever, showing a
        # long-dead gap as a live, actionable level. Track the CUMULATIVE union of each bar's
        # overlap with [bottom, top] instead: filled once the union of those overlaps fully
        # covers the gap. Tracked as a single [covered_lo, covered_hi] contiguous run
        # extended from either edge — correct because a gap can only ever be covered
        # starting from its bottom edge upward or its top edge downward (there is no bar
        # that touches the gap's interior without also touching at least one edge first,
        # since the gap itself is untraded territory the first bar to enter must cross an
        # edge to reach).
        filled = False
        filled_idx = None
        covered_lo = covered_hi = None  # the contiguous covered sub-range within [bottom, top], or None
        for j in range(i + 2, len(df)):
            lo, hi = max(lows[j], bottom), min(highs[j], top)
            if lo > hi:
                continue  # this bar's range doesn't overlap the gap at all
            if covered_lo is None:
                covered_lo, covered_hi = lo, hi
            else:
                # extend the covered run only if this bar's overlap is contiguous with
                # (touches or overlaps) what's already covered — otherwise it's a separate,
                # disconnected touch that doesn't bridge the remaining uncovered middle.
                if lo <= covered_hi and hi >= covered_lo:
                    covered_lo, covered_hi = min(covered_lo, lo), max(covered_hi, hi)
            if covered_lo is not None and covered_lo <= bottom and covered_hi >= top:
                filled = True
                filled_idx = j
                break

        gaps.append(FairValueGap(
            top=top, bottom=bottom, kind=kind, idx=i, filled=filled, filled_idx=filled_idx,
        ))

    # AUD-FVG-CAPORDERING: max_gaps used to be a pure gaps[-max_gaps:] slice — the most
    # RECENT gaps by bar index, mixing filled/unfilled with no regard for which are actually
    # near the current price. A genuinely nearest, still-unfilled, actionable gap formed
    # earlier than 20 other (possibly already-filled or far-away) gaps was silently dropped
    # before the frontend's nearestActionableFvg() ever saw it. Prioritize unfilled gaps over
    # filled ones, then nearest-to-current-price within each group, before capping — then
    # restore chronological order (by idx) so rendering/consumers see a stable, time-ordered
    # list, matching the pre-fix contract.
    if len(gaps) > max_gaps:
        current_price = float(df["close"].values[-1])
        def _relevance_key(g: FairValueGap) -> tuple[bool, float]:
            dist = min(abs(g.top - current_price), abs(g.bottom - current_price))
            return (g.filled, dist)  # False (unfilled) sorts before True (filled)
        gaps = sorted(gaps, key=_relevance_key)[:max_gaps]
        gaps.sort(key=lambda g: g.idx)

    return gaps


def detect_accumulation_distribution(df: pd.DataFrame, window: int = 20) -> dict:
    """T258-ACCUM-DIST-BREAKOUT-QUALITY: classify a stock as 'accumulation' | 'distribution' |
    'neutral' from price/volume PATTERN alone.

    No block-trade/dark-pool feed exists anywhere in this app — this deliberately does NOT
    claim to detect institutional accumulation directly (that would need trade-level data this
    app doesn't have access to). It's a volume-pattern-based read, framed honestly as such:
    the two component signals, both already established conventions elsewhere in this app —
    - OBV trend: cumulative (volume * price-direction) — the same construction signal-engine's
      own obv_trend_bullish already uses (generators/signals.py), a 10-bar OBV average above
      its 30-bar average means net buying pressure has been building recently.
    - Up/down-day volume ratio: total volume on up-close days vs. down-close days over
      `window` bars — a stock trading heavier on its up days than its down days is being
      bought into strength, not just drifting up on thin volume.

    Both signals must agree for a real accumulation/distribution call (`state`); one agreeing
    and one not degrades to 'neutral' rather than assigning a rough state. Returns the two
    component readings (`obv_trend_bullish`, `updown_vol_ratio`) alongside `state` so a caller
    can see the actual evidence, not just a bare label — the tracker's own honesty requirement
    for a pattern-based (not trade-level) classification.
    """
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    if len(close) < window + 1:
        return {"state": "neutral", "obv_trend_bullish": None, "updown_vol_ratio": None}

    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (volume * direction).cumsum()
    obv_trend_bullish = None
    if len(obv) >= 30:
        obv_trend_bullish = bool(obv.rolling(10).mean().iloc[-1] > obv.rolling(30).mean().iloc[-1])

    recent_dir = direction.iloc[-window:]
    recent_vol = volume.iloc[-window:]
    up_vol = float(recent_vol[recent_dir > 0].sum())
    down_vol = float(recent_vol[recent_dir < 0].sum())
    updown_vol_ratio = (up_vol / down_vol) if down_vol > 0 else (float("inf") if up_vol > 0 else None)

    state = "neutral"
    if obv_trend_bullish is True and updown_vol_ratio is not None and updown_vol_ratio > 1.2:
        state = "accumulation"
    elif obv_trend_bullish is False and updown_vol_ratio is not None and 0 < updown_vol_ratio < (1 / 1.2):
        state = "distribution"

    return {
        "state": state,
        "obv_trend_bullish": obv_trend_bullish,
        "updown_vol_ratio": round(updown_vol_ratio, 2) if updown_vol_ratio not in (None, float("inf")) else updown_vol_ratio,
    }


def assess_breakout_quality(df: pd.DataFrame, level: float, direction: str = "up", window: int = 20) -> dict | None:
    """T258-ACCUM-DIST-BREAKOUT-QUALITY: assess whether the most recent close-beyond-level
    move is a 'real' | 'failed' | 'unconfirmed' breakout, per the "poke-and-reject = false
    breakout" read already taught (manual chart read only, until now) in the Volume Profile docs.

    `level` is the price level being tested (e.g. the nearest resistance from
    detect_support_resistance(), or the game-plan breakout level) — this function does NOT
    pick the level itself, matching the FVG trade-plan precedent of keeping level-selection
    and quality-assessment as separate concerns. `direction` is "up" (breakout above
    resistance) or "down" (breakdown below support).

    Finds the FIRST bar (scanning backward from the end) whose close crossed the level in
    the given direction while the prior bar was still on the wrong side — i.e. the actual
    breakout bar, not just "today's close happens to be beyond the level" (which could be
    day 40 of an established uptrend, not a fresh break). Classification:
    - 'real': the bar AFTER the breakout bar held beyond the level too (didn't reverse back
      across) AND the breakout bar's own volume was above its `window`-bar average
      (RVOL > 1.0) — a genuine, volume-confirmed move.
    - 'failed': the bar after the breakout closed back on the wrong side of the level —
      the classic poke-and-reject the docs already describe as a manual chart read.
    - 'unconfirmed': the breakout happened on the most recent bar (no next bar exists yet
      to confirm the hold), or a next bar exists and held but the breakout itself lacked
      volume confirmation — a break without volume confirmation is real-vs-failed
      genuinely unknowable from price alone, so this deliberately doesn't guess 'real'.

    Returns None if no bar in the given `df` actually broke the level in this direction —
    there's no breakout to assess (e.g. price has been above resistance the whole window,
    or never got near the level at all).
    """
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    n = len(close)

    def _beyond(i: int) -> bool:
        return close.iloc[i] > level if direction == "up" else close.iloc[i] < level

    breakout_idx = None
    for i in range(n - 1, 0, -1):
        if _beyond(i) and not _beyond(i - 1):
            breakout_idx = i
            break
    if breakout_idx is None:
        return None

    avg_vol = float(volume.iloc[max(0, breakout_idx - window):breakout_idx].mean())
    breakout_rvol = (float(volume.iloc[breakout_idx]) / avg_vol) if avg_vol > 0 else None
    volume_confirmed = breakout_rvol is not None and breakout_rvol > 1.0

    if breakout_idx == n - 1:
        quality = "unconfirmed"  # no next bar yet to confirm the hold
    elif not _beyond(breakout_idx + 1):
        quality = "failed"       # next bar reversed back across the level
    elif volume_confirmed:
        quality = "real"
    else:
        quality = "unconfirmed"  # held, but no volume confirmation on the break itself

    return {
        "quality": quality,
        "level": round(level, 2),
        "direction": direction,
        "close": round(float(close.iloc[breakout_idx]), 2),
        "breakout_rvol": round(breakout_rvol, 2) if breakout_rvol is not None else None,
        "volume_confirmed": volume_confirmed,
    }
