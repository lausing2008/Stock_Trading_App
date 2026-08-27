"""K-Score: 0-100 composite of Technical / Momentum / Value / Growth / Volatility.

Value and Growth are real sector-relative fundamental percentiles when available
(passed in as value_score/growth_score). When a stock lacks fundamentals data,
those factors are excluded from the weighted composite entirely (T234-RANK-KSCORE-
PROXY-MIXING) rather than backfilled with a price-derived proxy — the composite
score only ever reflects factors it has real data for.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from common.indicators import atr as _canon_atr, rsi as _canon_rsi


@dataclass
class KScoreComponents:
    technical: float
    momentum: float
    value: float | None       # None when price proxy used (no real fundamentals)
    growth: float | None      # None when price proxy used (no real fundamentals)
    volatility: float
    score: float
    fair_price: float | None = None
    relative_strength: float | None = None


_WEIGHTS = {
    "technical": 0.22,
    "momentum": 0.23,
    "value": 0.13,
    "growth": 0.14,
    "volatility": 0.18,
    "relative_strength": 0.10,
}

_KSCORE_WEIGHTS_REDIS_KEY = "stockai:kscore_weights"

# T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B: the 3 curve-shape constants #17 (RSI-to-score
# piecewise mapping), #18 (ADX-boost normalization), #19 (volatility scale factor) — each has a
# hardcoded default matching the ORIGINAL literal, read via a `cfg` dict so a future walk-forward
# sweep (POST /tune_kscore_curve, see routes.py) can vary them against real historical Price
# data, exactly mirroring how compute_score()'s own constants were made cfg-driven for Group A.
# Every existing caller (compute_kscore() with no cfg, the default {} below) is byte-identical
# to before this change.
_CURVE_DEFAULTS = {
    # #17: RSI-to-score piecewise mapping. rsi_low/rsi_mid/rsi_high are the 3 breakpoints
    # (30/50/70); score_at_low/score_at_mid/score_at_high are the score anchors at those exact
    # breakpoints (50/90/100) — the piecewise SLOPES between them are always DERIVED from these
    # 6 values (never swept independently), so a candidate can never produce a discontinuous
    # function. score_ceiling (100.0) and the >rsi_high decay rate (2.5) stay linked to
    # score_at_high the same way the original hardcoded formula did.
    "rsi_low": 30.0, "rsi_mid": 50.0, "rsi_high": 70.0,
    "score_at_low": 50.0, "score_at_mid": 90.0, "score_at_high": 100.0,
    "rsi_overbought_decay_per_point": 2.5,  # how fast score falls off above rsi_high
    # #18: ADX-boost normalization — original literal formula is
    # clip((adx - adx_center) / adx_divisor, -1, 1) * adx_boost_scale. adx_divisor is NOT the
    # same thing as "the ceiling where the boost saturates" (a real distinction caught during
    # implementation: at the hardcoded defaults, adx=40 is where the clip actually saturates at
    # +10, not adx=25 — the comment's own "strong trend >25" prose is a loose description, not
    # the literal saturation point). Keep the true 3 independent knobs the original math uses.
    "adx_center": 15.0, "adx_divisor": 25.0, "adx_boost_scale": 10.0,
    # #19: volatility scale factor (higher = harsher penalty per unit of realized vol).
    "volatility_scale": 1500.0,
}


_KSCORE_CURVE_REDIS_KEY = "stockai:kscore_curve"


def _load_active_curve_params() -> dict:
    """T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B: read a validated curve-shape override
    written by POST /rankings/tune_kscore_curve, falling back to _CURVE_DEFAULTS on any
    absence/parse/connection failure — mirrors _load_active_weights()'s exact fail-open
    convention. A partial override (only some of the 11 keys present) is allowed here, unlike
    the weights override — each of #17/#18/#19's own constants is independently meaningful
    (unlike weights, which only mean something as a complete set summing to 1.0), so a
    real, promoted single-parameter override should apply on its own without requiring every
    other curve constant to be re-specified too.
    """
    try:
        from common.redis_client import get_redis
        raw = get_redis().get(_KSCORE_CURVE_REDIS_KEY)
        if not raw:
            return dict(_CURVE_DEFAULTS)
        import json
        override = json.loads(raw)
        if not isinstance(override, dict):
            return dict(_CURVE_DEFAULTS)
        return {
            **_CURVE_DEFAULTS,
            **{k: float(v) for k, v in override.items() if k in _CURVE_DEFAULTS},
        }
    except Exception:
        return dict(_CURVE_DEFAULTS)


def _curve_params(cfg: dict | None) -> dict:
    """Merge cfg overrides onto the CURRENTLY ACTIVE curve params (the live Redis override if
    tune_kscore_curve has ever promoted one, else the hardcoded _CURVE_DEFAULTS — matching
    _load_active_weights()'s own "None means live, not hardcoded" semantics exactly). cfg=None
    or {} (the real ranking-refresh path) resolves to whatever is currently live. A sweep
    wanting the PURE hardcoded defaults as its own baseline (never the live override, which may
    already differ from the defaults) should pass _CURVE_DEFAULTS explicitly rather than None —
    see tune_kscore_curve()'s own current_params variable for exactly this distinction."""
    active = _load_active_curve_params()
    if not cfg:
        return active
    return {**active, **{k: v for k, v in cfg.items() if k in _CURVE_DEFAULTS}}


def _load_active_weights() -> dict:
    """T288-KSCORE-WEIGHT-SWEEP: read a validated weight override written by
    POST /rankings/tune_kscore_weights, falling back to the hardcoded _WEIGHTS on any
    absence/parse/connection failure — the exact fail-open-to-hardcoded-default convention
    every other Redis-tuned parameter in this codebase already uses (e.g. signal-engine's
    _get_style_tuned_param). The override is a single JSON blob of all 6 weights together
    (not 6 independent keys) since they only ever mean something as a complete set that sums
    to 1.0 — a partial override (e.g. only "momentum" changed) would silently corrupt the
    other 5 factors' effective share of the composite.
    """
    # Always returns a FRESH dict, never the module-level _WEIGHTS object itself — a caller
    # that mutates its own local copy (compute_kscore() does, via del) must never be able to
    # corrupt the hardcoded default for every subsequent call in the process.
    try:
        from common.redis_client import get_redis
        raw = get_redis().get(_KSCORE_WEIGHTS_REDIS_KEY)
        if not raw:
            return dict(_WEIGHTS)
        import json
        override = json.loads(raw)
        if not isinstance(override, dict) or set(override.keys()) != set(_WEIGHTS.keys()):
            return dict(_WEIGHTS)
        return {k: float(v) for k, v in override.items()}
    except Exception:
        return dict(_WEIGHTS)


def _rsi(close: pd.Series, w: int = 14) -> pd.Series:
    """T233-ARCH-INDICATOR-DEDUP: now delegates to the canonical Wilder's RSI in
    shared/common/indicators.py instead of a standalone reimplementation.

    T233-KSCORE-RSI1: the old version had no min_periods on its .ewm() calls, so it produced
    numerically real-looking RSI values from bar 0 onward — a stock with only 5 bars of real
    history (a recent IPO/watchlist addition) could already show RSI=96, well before the 14-bar
    window has enough data to mean anything. `.fillna(100)` then conflated that warmup case
    with the genuinely-different "no down days at all" case (both real RSI=100 situations,
    but for entirely different reasons) — same bug class as T232-TA1, already fixed in the
    canonical rsi(), just not previously ported here. The canonical version correctly returns
    NaN during warmup; see _technical_score() below for the explicit NaN handling this requires.
    """
    return _canon_rsi(close, window=w)


def _adx_value(df: pd.DataFrame, period: int = 14) -> float | None:
    """Return ADX scalar, or None if insufficient data.

    AUD232-014: previously fell back to 20.0 (not None) on insufficient data — the exact
    bug signal-engine's own _adx() already fixed ("C3 FIX"): a 20.0 fallback silently
    passed as a real (non-neutral) value into _technical_score()'s adx_boost formula,
    granting a fixed +2.0 boost to every short-history stock (np.clip((20-15)/25,0,1)*10 = 2.0)
    instead of being treated as unknown. Returns None so the caller can explicitly skip the
    ADX-derived boost rather than silently misapplying it.
    """
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)

    up_move   = high.diff()
    down_move = (-low.diff())
    dm_plus  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    dm_minus = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    # AUD232-071: was tr.ewm(...).mean() with no min_periods — computed a real-looking ATR
    # from bar 0 (before `period` true-range bars have accumulated), reintroducing the exact
    # warmup-NaN bug class T237-TA-ATR-MINPERIODS already fixed in the canonical version. Using
    # common.indicators.atr() correctly propagates NaN through di_plus/di_minus/dx/adx during
    # warmup instead of computing on too few bars — consistent with this function's own None
    # return for genuinely insufficient data (AUD232-014).
    atr_val  = _canon_atr(high, low, close, period=period)
    di_plus  = 100 * dm_plus.ewm(alpha=1 / period, adjust=False).mean() / atr_val.replace(0, np.nan)
    di_minus = 100 * dm_minus.ewm(alpha=1 / period, adjust=False).mean() / atr_val.replace(0, np.nan)

    dx  = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    return float(adx) if not pd.isna(adx) else None


def _technical_raw_inputs(df: pd.DataFrame) -> dict:
    """The RAW indicator values _technical_score() combines — split out specifically so a
    walk-forward sweep (POST /tune_kscore_curve) can compute these ONCE per (stock, as_of) and
    cheaply re-apply many candidate curve-shape parameters to the SAME raw values, instead of
    re-running the expensive RSI/ADX EWM computations (the dominant cost, profiled directly
    before this split — ~6ms/call, ~68s for a single full-window candidate, ~800s for a full
    one-parameter-at-a-time sweep pool) once per candidate per row. Curve-shape parameters
    (#17/#18/#19) only change how these raw values are MAPPED to a 0-100 score, never what the
    raw values themselves are — so this split changes zero behavior, only where the cfg-
    dependent step begins."""
    close = df["close"]
    sma50  = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    _s50_ok  = not pd.isna(sma50)
    _s200_ok = not pd.isna(sma200)
    # Use 0.5 (neutral) when SMA is NaN — stocks with < 50/200 bars of history
    # (IPOs, new additions) otherwise score 0/1 for each missing component,
    # systematically underranking them relative to stocks with full history.
    above_sma50        = (1 if close.iloc[-1] > sma50  else 0) if _s50_ok               else 0.5
    above_sma200       = (1 if close.iloc[-1] > sma200 else 0) if _s200_ok              else 0.5
    sma50_above_sma200 = (1 if sma50 > sma200           else 0) if (_s50_ok and _s200_ok) else 0.5
    r = _rsi(close).iloc[-1]
    adx = _adx_value(df)
    return {
        "above_sma50": above_sma50, "above_sma200": above_sma200,
        "sma50_above_sma200": sma50_above_sma200,
        "rsi": None if pd.isna(r) else float(r),
        "adx": adx,
    }


def _technical_score_from_raw(raw: dict, cfg: dict | None = None) -> float:
    """Apply the #17 (RSI-to-score piecewise mapping) / #18 (ADX-boost normalization)
    curve-shape parameters to already-computed raw indicator values. See
    _technical_raw_inputs()'s own docstring for why this split exists."""
    p = _curve_params(cfg)
    r = raw["rsi"]
    # T233-KSCORE-RSI1: canonical rsi() correctly returns NaN during the 14-bar warmup window
    # (a stock with <14 bars of real history) instead of a fabricated real-looking value.
    # T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B item #17: the piecewise mapping's 3 breakpoints
    # and score anchors are now cfg-driven (see _CURVE_DEFAULTS) — the SLOPES between segments
    # are always DERIVED from the breakpoint/anchor pairs below, never swept independently, so a
    # candidate can never produce a discontinuous function. At the hardcoded defaults
    # (30/50/70 breakpoints, 50/90/100 anchors, 2.5 overbought decay) this reproduces the
    # ORIGINAL literal formula (50→90 as RSI 30→50, slope 2.0; 90→100 as RSI 50→70, slope 0.5;
    # 100→62.5 as RSI 70→85+, decay 2.5) byte-for-byte.
    _neutral_fallback = (p["score_at_low"] + p["score_at_high"]) / 2  # 75.0 at defaults
    if r is None:
        rsi_score = _neutral_fallback
    # Asymmetric: optimal zone is rsi_low-rsi_high (bullish momentum). Oversold (<rsi_low) and
    # very overbought (>rsi_high) penalised. A trending RSI=70 scores higher than RSI=40.
    elif r <= p["rsi_low"]:
        rsi_score = p["score_at_low"]
    elif r <= p["rsi_mid"]:
        _slope_lo_mid = (p["score_at_mid"] - p["score_at_low"]) / (p["rsi_mid"] - p["rsi_low"])
        rsi_score = p["score_at_low"] + (r - p["rsi_low"]) * _slope_lo_mid
    elif r <= p["rsi_high"]:
        _slope_mid_hi = (p["score_at_high"] - p["score_at_mid"]) / (p["rsi_high"] - p["rsi_mid"])
        rsi_score = p["score_at_mid"] + (r - p["rsi_mid"]) * _slope_mid_hi
    else:
        rsi_score = p["score_at_high"] - (r - p["rsi_high"]) * p["rsi_overbought_decay_per_point"]

    adx = raw["adx"]
    # ADX boost: trend strength above adx_center lifts score; below it drags it, ramping via
    # adx_divisor and capped at +-adx_boost_scale.
    # AUD232-014: skip entirely (no boost, positive or negative) when ADX is unknown
    # (insufficient history) rather than treating "unknown" as a real, non-neutral value.
    # T247-RANKINGENGINE-ADXBOOST-FLOOR: `np.clip(..., 0, 1)` floored the boost at 0, so a
    # weak/choppy trend only ever contributed a NEUTRAL 0, never the penalty a "very weak trend
    # drags it" framing implies — an ADX=5 stock scored identically to an ADX=15 stock. Clip to
    # [-1, 1] so a below-center ADX genuinely drags the score below neutral, symmetric with the
    # above-center boost.
    # T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B item #18: adx_center/adx_divisor/
    # adx_boost_scale are now cfg-driven — reproduces the original (adx - 15) / 25 * 10 formula
    # byte-for-byte at the hardcoded defaults (the clip only actually saturates at +-10 when
    # |adx - 15| >= 25, i.e. adx<=-10 or adx>=40 — NOT at adx=25, despite the original comment's
    # "strong trend >25" framing; caught and corrected during this parameterization, see
    # _CURVE_DEFAULTS' own comment for the full explanation).
    adx_boost = (
        np.clip((adx - p["adx_center"]) / p["adx_divisor"], -1, 1) * p["adx_boost_scale"]
        if adx is not None else 0.0
    )  # -adx_boost_scale..+adx_boost_scale

    base = (raw["above_sma50"] + raw["above_sma200"] + raw["sma50_above_sma200"]) / 3 * 60 + rsi_score * 0.4
    return float(np.clip(base + adx_boost, 0, 100))


def _technical_score(df: pd.DataFrame, cfg: dict | None = None) -> float:
    return _technical_score_from_raw(_technical_raw_inputs(df), cfg)


def _momentum_score(df: pd.DataFrame) -> float:
    c = df["close"]
    if len(c) < 127:
        return 50.0
    r1m = c.iloc[-1] / c.iloc[-22]  - 1
    r3m = c.iloc[-1] / c.iloc[-64]  - 1
    r6m = c.iloc[-1] / c.iloc[-127] - 1
    raw = 0.5 * r3m + 0.3 * r6m + 0.2 * r1m
    return float(np.clip(50 + raw * 150, 0, 100))


def _volatility_raw_input(df: pd.DataFrame) -> float | None:
    """The raw realized-vol value _volatility_score() scales — split out for the same reason as
    _technical_raw_inputs() (see its own docstring): a walk-forward sweep can compute this ONCE
    per (stock, as_of) and cheaply re-scale it under many candidate volatility_scale values."""
    ret = df["close"].pct_change()
    vol = ret.rolling(60).std().iloc[-1]
    return None if pd.isna(vol) else float(vol)


def _volatility_score_from_raw(vol: float | None, cfg: dict | None = None) -> float:
    """Apply the #19 (volatility scale factor) curve-shape parameter to an already-computed
    raw realized-vol value."""
    if vol is None:
        return 50.0
    p = _curve_params(cfg)
    return float(np.clip(100 - vol * p["volatility_scale"], 0, 100))


def _volatility_score(df: pd.DataFrame, cfg: dict | None = None) -> float:
    """Lower realized vol → higher score.

    T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B item #19: volatility_scale is now cfg-driven,
    defaulting to the original hardcoded 1500 literal.
    """
    return _volatility_score_from_raw(_volatility_raw_input(df), cfg)


def compute_kscore(
    df: pd.DataFrame,
    rs_score: float | None = None,
    value_score: float | None = None,
    growth_score: float | None = None,
    curve_cfg: dict | None = None,
) -> KScoreComponents:
    """Compute K-Score composite.

    value_score / growth_score (0-100): when provided, these are sector-relative
    percentile ranks from real fundamental data (PE, PB, EV/EBITDA, revenue growth,
    ROE, etc.) and are returned as-is. When None, the composite score uses price
    proxies internally but value/growth are returned as None (displayed as "—") so
    the UI does not mislead traders with price data labeled as fundamental quality.

    curve_cfg: optional override for the #17/#18/#19 curve-shape constants (see
    _CURVE_DEFAULTS). The live ranking-refresh path passes None — matching _load_active_
    weights()'s own "None means whatever is currently live" convention, this resolves to a
    validated Redis override if POST /tune_kscore_curve has ever promoted one, else the
    hardcoded defaults (see _curve_params()/_load_active_curve_params()). A sweep candidate
    passes a real, non-empty curve_cfg to layer its own candidate ON TOP of whatever is
    already live, to recompute _technical_score()/_volatility_score() under that candidate.
    """
    tech = _technical_score(df, curve_cfg)
    mom  = _momentum_score(df)
    vol  = _volatility_score(df, curve_cfg)

    # T234-RANK-KSCORE-PROXY-MIXING: value_score/growth_score used to silently fall back to
    # _value_proxy(df)/_growth_proxy(df) (both monotonic transforms of trailing price return,
    # like _momentum_score) and feed the proxy into the weighted composite as if it were a
    # real fundamental percentile — while KScoreComponents.value/.growth correctly returned
    # None. Two stocks could show an IDENTICAL K-Score while one was fundamentals-grounded and
    # the other a pure momentum artifact wearing a value/growth label internally, and when
    # fundamentals are missing (common for smaller/newer names), close to half the composite
    # became the same underlying signal (recent price action) counted three times under three
    # factor names. Fixed by excluding value/growth from the weighted sum entirely when the
    # real fundamental is unavailable, redistributing their weight to the remaining factors —
    # the same pattern already used just below for a missing rs_score.
    #
    # T288-KSCORE-WEIGHT-SWEEP: reads a validated, Redis-overridden weight set if
    # tune_kscore_weights has ever promoted one, falling back to the hardcoded _WEIGHTS above
    # otherwise. This is the ONLY thing tune_kscore_weights' promotion path can actually change
    # — the redistribution logic below (excluding a None factor, renormalizing the rest) stays
    # byte-identical either way, so a promoted weight set changes WHICH weights apply, never
    # HOW they're combined.
    # dict(...) here is load-bearing, not decorative: _load_active_weights() can return the
    # module-level _WEIGHTS dict directly (its own fallback path), and this function mutates
    # its local copy via del a few lines below — without the copy, the FIRST call with any
    # factor missing would permanently delete that key from _WEIGHTS itself, corrupting every
    # later call in the same process (a real bug caught by test_kscore.py's own pre-existing
    # test failing when run after this file's Redis-override tests, in the SAME session).
    _active_weights = dict(_load_active_weights())
    if value_score is None:
        del _active_weights["value"]
    if growth_score is None:
        del _active_weights["growth"]
    if rs_score is None:
        del _active_weights["relative_strength"]

    w_sum = sum(_active_weights.values())
    _factor_values = {
        "technical": tech, "momentum": mom, "volatility": vol,
        "value": value_score, "growth": growth_score, "relative_strength": rs_score,
    }
    score = sum(
        (weight / w_sum) * _factor_values[factor]
        for factor, weight in _active_weights.items()
    )

    sma200 = df["close"].rolling(200).mean().iloc[-1]
    fair   = float(sma200) if not pd.isna(sma200) else None

    return KScoreComponents(
        technical=round(tech, 2),
        momentum=round(mom, 2),
        value=round(value_score, 2) if value_score is not None else None,
        growth=round(growth_score, 2) if growth_score is not None else None,
        volatility=round(vol, 2),
        score=round(score, 2),
        fair_price=round(fair, 2) if fair else None,
        relative_strength=round(rs_score, 2) if rs_score is not None else None,
    )
