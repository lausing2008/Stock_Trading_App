"""T264-SHORTSQUEEZE-PREBREAKOUT: "coiling" detector — a straight Python port of
technical-analysis/src/indicators/trendlines.py's detect_price_compression().

This is an INDEPENDENT port, not a shared implementation with technical-analysis's own copy —
matching this repo's own established dual-implementation pattern for exactly this class of
cross-service math (see volume_area.py's own module docstring for the identical reasoning).
technical-analysis's copy is a per-symbol-page LIVE API surface (GET /ta/{symbol}/levels,
today's reading only); this copy exists because the pre-breakout alert and its labeled-
dataset generator both need to run this same math over THOUSANDS of historical (symbol, date)
windows across 3 years of daily bars — an HTTP round-trip per window would be far too slow and
would hammer technical-analysis for no reason, since market-data already has direct DB access
to the same Price table technical-analysis itself reads from. If the compression math in one
copy is ever changed, check whether the other needs the same change too.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_COMPRESSION_LOOKBACK_DAYS = 126  # ~6 real trading months, matching the user's own "6-month low" framing
_COMPRESSION_PERCENTILE = 0.20    # bb_width/ATR must sit in the LOWEST 20% of the lookback to count as "coiling"
_COMPRESSION_VOLUME_WINDOW = 20


def _bollinger_width(close: pd.Series, window: int = 20, n_std: float = 2.0) -> pd.Series:
    """(bb_upper - bb_lower) / bb_mid — identical formula to technical-analysis's own
    bollinger_bands() + the width computation inside detect_price_compression(), just
    collapsed into one series since only the width (not bb_upper/bb_lower themselves) is
    ever needed here."""
    mid = close.rolling(window, min_periods=window).mean()
    std = close.rolling(window, min_periods=window).std(ddof=1)
    width = (2 * n_std * std) / mid
    return width.replace([np.inf, -np.inf], np.nan)


def _atr_pct(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ATR, normalized by price — identical formula to technical-analysis's own
    atr(), matching its T237-TA-ATR-MINPERIODS min_periods=period fix exactly."""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return (atr / close).replace([np.inf, -np.inf], np.nan)


def detect_price_compression(close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series) -> dict:
    """Identical logic/thresholds to trendlines.py's detect_price_compression() — see that
    function's own docstring for the full design rationale. Takes plain Series (not a
    DataFrame) since this module's two callers (the historical dataset generator, the live
    rule-based alert) both already have separately-sliced close/high/low/volume series rather
    than a single combined DataFrame.
    """
    min_bars = _COMPRESSION_LOOKBACK_DAYS + 20  # +20 for the rolling-window warmup
    if len(close) < min_bars:
        return {
            "is_compressed": False, "bb_width_pctile": None, "atr_pctile": None,
            "volume_dried_up": None, "bb_width": None, "atr_pct": None,
        }

    bb_width = _bollinger_width(close)
    atr_pct = _atr_pct(high, low, close)

    lookback_bb = bb_width.iloc[-_COMPRESSION_LOOKBACK_DAYS:].dropna()
    lookback_atr = atr_pct.iloc[-_COMPRESSION_LOOKBACK_DAYS:].dropna()
    current_bb = bb_width.iloc[-1]
    current_atr = atr_pct.iloc[-1]
    if len(lookback_bb) < 30 or len(lookback_atr) < 30 or pd.isna(current_bb) or pd.isna(current_atr):
        return {
            "is_compressed": False, "bb_width_pctile": None, "atr_pctile": None,
            "volume_dried_up": None, "bb_width": None, "atr_pct": None,
        }

    bb_pctile = float((lookback_bb < current_bb).mean())
    atr_pctile = float((lookback_atr < current_atr).mean())
    is_compressed = bb_pctile <= _COMPRESSION_PERCENTILE and atr_pctile <= _COMPRESSION_PERCENTILE

    recent_avg_vol = float(volume.iloc[-_COMPRESSION_VOLUME_WINDOW:].mean())
    lookback_vol_median = float(volume.iloc[-_COMPRESSION_LOOKBACK_DAYS:].median())
    volume_dried_up = (
        bool(recent_avg_vol < lookback_vol_median) if lookback_vol_median > 0 else None
    )

    return {
        "is_compressed": is_compressed,
        "bb_width_pctile": round(bb_pctile, 3),
        "atr_pctile": round(atr_pctile, 3),
        "volume_dried_up": volume_dried_up,
        "bb_width": round(float(current_bb), 4),
        "atr_pct": round(float(current_atr), 4),
    }
