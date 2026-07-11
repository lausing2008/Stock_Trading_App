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
    # T237-TA-HS-CLOSE-HIGH-MISMATCH: pivots were found on `close` but then compared/scored
    # using `high` at those same indices. A close-based local max is not guaranteed to line up
    # with the bar's high, so the "shoulder"/"head" pivot indices could silently point at bars
    # that aren't actually local highs at all. Find pivots on `high` directly since this pattern
    # is a peak (resistance) formation.
    highs_idx, _ = _find_pivots(df["high"], order=5)
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
    """Detect double-top and double-bottom reversal patterns with neckline, target, and volume confirmation.

    Double Bottom (BUY reversal):
      - Two troughs within ±1.5% of each other separated by 5-40 bars
      - Volume on 2nd trough <= 1.1× volume on 1st trough (buyers less panicked = exhaustion)
      - Neckline = highest close between the two troughs
      - Entry trigger = current price > neckline (breakout confirmation)
      - Target = neckline + (neckline - trough_avg)  [measured move]
      - Confidence boosted if breakout bar volume > 1.2× 20-bar avg (institutional buying)

    Double Top (SELL / avoid signal):
      - Two peaks within ±1.5% of each other separated by 5-40 bars
      - Volume on 2nd peak <= 0.9× volume on 1st peak (bulls losing conviction = distribution)
      - Neckline = lowest close between the two peaks
      - Entry trigger = current price < neckline (breakdown confirmation)
      - Target = neckline - (peak_avg - neckline)  [measured move down]
    """
    hits: list[PatternHit] = []
    if len(df) < 30:
        return hits

    close  = df["close"].astype(float)
    high   = df["high"].astype(float)
    low    = df["low"].astype(float)
    volume = df["volume"].astype(float) if "volume" in df.columns else None
    current_price = float(close.iloc[-1])
    vol20_avg = float(volume.rolling(20).mean().iloc[-1]) if volume is not None else None

    # TA-DTB1 (same bug class as T237-TA-HS-CLOSE-HIGH-MISMATCH above): pivots were found on
    # `close` but the trough/peak price levels were then read from `low`/`high` at those same
    # indices. A close-based local min/max isn't guaranteed to coincide with that bar's actual
    # low/high (e.g. a long wick) — find pivots on the actual series being measured instead.
    _, lows_idx = _find_pivots(low, order=5)
    highs_idx, _ = _find_pivots(high, order=5)

    # ── Double Bottom ─────────────────────────────────────────────────────────
    # AUD232-015: previously `break`d after the first structurally-valid pair (newest-first),
    # so only ONE candidate per pattern type was ever built — the mutual-exclusion check below
    # could only ever compare the single newest bottom vs the single newest top. If an older,
    # genuinely-overlapping pair existed further back, it was skipped over and never even
    # constructed, so a real conflict could silently slip through undetected. Now collects every
    # structurally-valid pair per type, and mutual exclusion (below) checks ALL bottom/top pairs
    # against each other, not just the newest of each.
    bottom_hits: list[PatternHit] = []
    if len(lows_idx) >= 2:
        for i in range(len(lows_idx) - 1, 0, -1):
            b_idx = int(lows_idx[i])
            a_idx = int(lows_idx[i - 1])
            gap = b_idx - a_idx
            if gap < 5 or gap > 60:
                continue
            trough_a = float(low.iloc[a_idx])
            trough_b = float(low.iloc[b_idx])
            if _pct(trough_a, trough_b) > 0.015:  # troughs must be within 1.5%
                continue
            # Neckline = highest close between the two troughs
            neckline = float(close.iloc[a_idx:b_idx + 1].max())
            trough_avg = (trough_a + trough_b) / 2
            target = round(neckline + (neckline - trough_avg), 2)
            stop   = round(min(trough_a, trough_b) * 0.995, 2)

            # Volume confirmation: 2nd trough volume <= 1.1x 1st trough (exhaustion)
            vol_confirmed = True
            vol_boost = False
            if volume is not None:
                vol_a = float(volume.iloc[a_idx])
                vol_b = float(volume.iloc[b_idx])
                vol_confirmed = vol_b <= vol_a * 1.10  # second trough should not be higher volume
                # Breakout on high volume = institutional buying
                if current_price > neckline and vol20_avg and float(volume.iloc[-1]) > vol20_avg * 1.20:
                    vol_boost = True

            # Entry trigger: has price broken out above neckline?
            neckline_broken = current_price > neckline * 1.002  # small buffer
            base_conf = 0.70 if vol_confirmed else 0.55
            conf = min(0.92, base_conf + (0.10 if neckline_broken else 0.0) + (0.08 if vol_boost else 0.0))

            bottom_hits.append(PatternHit(
                "double_bottom", a_idx, b_idx, round(conf, 2),
                {
                    "trough_a": trough_a, "trough_b": trough_b,
                    "neckline": neckline, "target": target, "stop": stop,
                    "neckline_broken": neckline_broken, "vol_confirmed": vol_confirmed,
                }
            ))

    # ── Double Top ────────────────────────────────────────────────────────────
    top_hits: list[PatternHit] = []
    if len(highs_idx) >= 2:
        for i in range(len(highs_idx) - 1, 0, -1):
            b_idx = int(highs_idx[i])
            a_idx = int(highs_idx[i - 1])
            gap = b_idx - a_idx
            if gap < 5 or gap > 60:
                continue
            peak_a = float(high.iloc[a_idx])
            peak_b = float(high.iloc[b_idx])
            if _pct(peak_a, peak_b) > 0.015:
                continue
            neckline = float(close.iloc[a_idx:b_idx + 1].min())
            peak_avg = (peak_a + peak_b) / 2
            target = round(neckline - (peak_avg - neckline), 2)

            # Volume confirmation: 2nd peak volume <= 0.9x 1st peak (distribution, bulls fading)
            vol_confirmed = True
            if volume is not None:
                vol_a = float(volume.iloc[a_idx])
                vol_b = float(volume.iloc[b_idx])
                vol_confirmed = vol_b <= vol_a * 0.90

            neckline_broken = current_price < neckline * 0.998
            base_conf = 0.70 if vol_confirmed else 0.55
            conf = min(0.92, base_conf + (0.10 if neckline_broken else 0.0))

            top_hits.append(PatternHit(
                "double_top", a_idx, b_idx, round(conf, 2),
                {
                    "peak_a": peak_a, "peak_b": peak_b,
                    "neckline": neckline, "target": target,
                    "neckline_broken": neckline_broken, "vol_confirmed": vol_confirmed,
                }
            ))

    # T237-TA-DTB-MUTUAL-EXCLUSION (AUD232-015 extends this to ALL pairs, not just the newest):
    # double-bottom (bullish) and double-top (bearish) are found by two independent scans over
    # disjoint pivot arrays (lows_idx vs highs_idx), so nothing stops both from firing for the
    # same window — e.g. a choppy W-M consolidation produces a real local low pair AND a real
    # local high pair, yielding a BUY-reversal and a SELL-reversal hit at once. For every
    # overlapping (bottom, top) pair across the full candidate sets, drop the lower-confidence
    # one — a stock can't be both bottoming and topping in the same window.
    dropped: set[int] = set()  # id() of PatternHit objects to exclude
    for bot in bottom_hits:
        for top in top_hits:
            if id(bot) in dropped or id(top) in dropped:
                continue
            overlaps = bot.start_idx <= top.end_idx and top.start_idx <= bot.end_idx
            if overlaps:
                dropped.add(id(top) if bot.confidence >= top.confidence else id(bot))

    # Keep only the most recent surviving hit per type (matches prior behavior of reporting
    # at most one double_bottom + one double_top), now chosen from a conflict-free candidate set.
    # Both loops above append newest-pair-first (they iterate lows_idx/highs_idx from the end
    # backwards), so the most recent surviving hit is at index 0, not -1.
    surviving_bottoms = [h for h in bottom_hits if id(h) not in dropped]
    surviving_tops = [h for h in top_hits if id(h) not in dropped]
    if surviving_bottoms:
        hits.append(surviving_bottoms[0])
    if surviving_tops:
        hits.append(surviving_tops[0])

    return hits


def detect_triangle(df: pd.DataFrame, window: int = 60) -> list[PatternHit]:
    """Ascending/descending/symmetric triangles via converging pivot slopes."""
    sub = df.tail(window)
    # _find_pivots returns 0-based indices within the sub-window slice; convert
    # to absolute df positions by adding the slice offset.
    offset = len(df) - len(sub)
    # TA-DTB1/TA-TRI1 (same bug class as T237-TA-HS-CLOSE-HIGH-MISMATCH): pivots must be found
    # on the same series they're later read from — a close-based local max/min isn't guaranteed
    # to be the same bar as that bar's actual high/low (e.g. a long wick).
    highs_idx, _ = _find_pivots(sub["high"], order=3)
    _, lows_idx = _find_pivots(sub["low"], order=3)
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
    start = offset + int(min(highs_idx[0], lows_idx[0]))
    end   = offset + int(max(highs_idx[-1], lows_idx[-1]))
    return [PatternHit(kind, start, end, 0.6, {"high_slope": float(hs), "low_slope": float(ls)})]


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
