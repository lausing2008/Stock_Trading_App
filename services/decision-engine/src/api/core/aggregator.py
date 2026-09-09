"""Async fan-out — fetches signal, research, and game plan data in parallel."""
from __future__ import annotations

import asyncio
import time as _time
from concurrent.futures import ThreadPoolExecutor

import httpx
import structlog
from jose import jwt as _jwt

from common.config import get_settings

_yf_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yf_price")

# AUD250-DECISIONENGINE-GAMEPLAN-SHARED-EXECUTOR: abuild_game_plan()'s blocking
# _get_style_params() httpx.get() (on a cold _STYLE_PARAMS_CACHE) previously shared
# _yf_executor with the unrelated yfinance-price-fallback path above — a distinct kind of
# blocking work contending for the same small 4-worker pool undercuts the parallelism a
# batch POST /decide/batch request is supposed to get (tasks queue behind each other on the
# shared pool rather than stalling the event loop outright). Dedicated pool, matching
# regime.py's own _regime_executor fix for the identical cross-purpose-contention pattern.
_game_plan_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="game_plan")

log = structlog.get_logger()
_settings = get_settings()

_svc_token_cache: str = ""


def _svc_token() -> str:
    global _svc_token_cache
    if _svc_token_cache:
        return _svc_token_cache
    payload = {
        "sub": "decision-engine",
        "jti": str(__import__("uuid").uuid4()),
        "exp": int(_time.time()) + 365 * 86400,
    }
    _svc_token_cache = _jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")
    return _svc_token_cache


# ── Default style game-plan parameters ────────────────────────────────────────
#
# T232-DL-STYLEPARAMS3X: this dict was previously an independent third copy of
# scheduler.py/paper_trading_engine.py's _STYLE_PARAMS, with WRONG GROWTH values
# (stop -16%/target +60% here vs the real engine's -12%/+35%) and two dead styles
# (SCALP/INCOME) that don't exist in the real trading engine — while SHORT and LONG,
# which DO exist and ARE requested by real portfolios (paper_trading_engine.py passes
# cfg["trading_style"] verbatim to POST /decide/{symbol}), were MISSING entirely and
# silently fell back to SWING's parameters. Fixed 2026-07-04: fetch the canonical
# values from market-data instead of maintaining a separate copy.

_STYLE_PARAMS_FALLBACK = {
    "SHORT":  {"entry2_pct": 0.985, "breakout_pct": 1.010, "stop_pct": 0.970, "default_tp_pct": 1.05, "atr_stop_mult": 2.0},
    "SWING":  {"entry2_pct": 0.965, "breakout_pct": 1.020, "stop_pct": 0.945, "default_tp_pct": 1.12, "atr_stop_mult": 2.0},
    "LONG":   {"entry2_pct": 0.950, "breakout_pct": 1.030, "stop_pct": 0.900, "default_tp_pct": 1.25, "atr_stop_mult": 2.0},
    "GROWTH": {"entry2_pct": 0.940, "breakout_pct": 1.035, "stop_pct": 0.880, "default_tp_pct": 1.35, "atr_stop_mult": 3.0},
}

_STYLE_PARAMS_CACHE: dict | None = None
_STYLE_PARAMS_TS: float = 0.0
_STYLE_PARAMS_TTL = 900  # 15 minutes — matches regime.py's cache window


def _get_style_params() -> dict:
    """Fetch the canonical _STYLE_PARAMS from market-data, with a local cache + hardcoded
    fallback if market-data is unreachable (fail-open — a stale/fallback game plan is better
    than blocking the decide endpoint entirely)."""
    global _STYLE_PARAMS_CACHE, _STYLE_PARAMS_TS
    if _STYLE_PARAMS_CACHE and (_time.time() - _STYLE_PARAMS_TS) < _STYLE_PARAMS_TTL:
        return _STYLE_PARAMS_CACHE
    try:
        r = httpx.get(f"{_settings.market_data_url}/stocks/style-params", timeout=5.0)
        r.raise_for_status()
        _STYLE_PARAMS_CACHE = r.json()
        _STYLE_PARAMS_TS = _time.time()
        return _STYLE_PARAMS_CACHE
    except Exception as exc:
        log.warning("decision.style_params_fetch_failed", error=str(exc))
        return _STYLE_PARAMS_CACHE if _STYLE_PARAMS_CACHE else _STYLE_PARAMS_FALLBACK


# AUD-SIGALERT-RRUNREACHABLE: how much R:R the default game plan should aim for.
#
# Must track the gate's own calibrated `min_rr_ratio`, or the two silently diverge again the
# next time calibrate_min_rr_ratio() promotes a new floor. _RR_TARGET_MARGIN keeps a candidate
# sitting exactly at the floor from being rejected by rounding (rr_ratio is round(...,2)).
_RR_TARGET_MARGIN = 0.15
_RR_TARGET_FLOOR = 2.0   # never aim LOWER than the historical 2:1, even if the floor drops


def _target_rr_multiple(style: str, market: str = "US") -> float:
    """The reward:risk multiple _default_game_plan aims for, derived from the live gate floor.

    Reads the SAME `min_rr_ratio` the R:R hard reject reads (via the same cached
    _get_entry_gate_params used elsewhere in this file), so supply and demand cannot silently
    drift apart the next time calibrate_min_rr_ratio() promotes a new floor.

    `market` defaults to "US" because _default_game_plan() is not given one and threading it
    through every call site would be a wider change than this fix warrants. That is safe here:
    this only sets how ambitious the DEFAULT target is, and check_hard_rejects still re-checks
    the resulting rr_ratio against the caller's own real per-market floor. A US-derived target
    that is too low for HK is rejected there exactly as it would have been anyway — it cannot
    let a candidate through that the real floor would refuse.

    Fails safe to the historical 2.0 if params are unavailable: that restores the old behaviour
    rather than inventing a number, and the gate blocks as it does today rather than passing
    something unvalidated.
    """
    try:
        params = _get_entry_gate_params(style, market) or {}
        # AUD-SIGALERT-RRREGIMEFLOOR: must consider BOTH floors. check_hard_rejects does
        #     min_rr = cfg["min_rr_ratio"]
        #     if regime_state in ("choppy", "risk_off"):
        #         min_rr = max(min_rr, cfg["regime_min_rr_ratio"])
        # so in a choppy/risk_off regime the ENFORCED floor is the regime value (3.38), not the
        # base one (2.25). My first version of this fix read only `min_rr_ratio`, so it aimed at
        # 2.40 and the gate still rejected everything with "R:R 2.40:1 below minimum 3.4:1" —
        # the outage persisted for exactly the regimes where it matters most.
        #
        # _default_game_plan is not given `regime_state` (routes.py resolves it separately), and
        # threading it through would be a wider change. Taking the max of both floors is safe in
        # EVERY regime: in a calm regime it aims slightly higher than strictly required, which
        # the style target cap still bounds, and it can never aim BELOW what the gate enforces.
        _floors = [
            float(v) for v in (params.get("min_rr_ratio"), params.get("regime_min_rr_ratio"))
            if v is not None and float(v) > 0
        ]
        if _floors:
            return max(_RR_TARGET_FLOOR, max(_floors) + _RR_TARGET_MARGIN)
    except Exception:  # noqa: BLE001 — never let a params lookup break game-plan construction
        pass
    return _RR_TARGET_FLOOR


def _default_game_plan(live_price: float, style: str, atr_14: float | None = None) -> dict:
    style_params = _get_style_params()
    p_raw = style_params.get(style.upper(), style_params.get("SWING", _STYLE_PARAMS_FALLBACK["SWING"]))
    # market-data's dict uses "default_tp_pct" (not "target_pct") — normalize the key here so
    # the rest of this function doesn't need to know which source it came from.
    p = {**p_raw, "target_pct": p_raw.get("target_pct", p_raw.get("default_tp_pct"))}
    fixed_stop = live_price * p["stop_pct"]
    if atr_14 and atr_14 > 0:
        # AUD-DUPLOGIC: atr_stop_mult now read from the same style-params fetch as
        # entry/breakout/stop/target percentages above (both the live market-data response and
        # _STYLE_PARAMS_FALLBACK always carry this key now), rather than its own hardcoded
        # literal — this used to independently say 2.5 for GROWTH while paper_trading_engine.py's
        # real, authoritative _build_game_plan_for_style() said 3.0, an undocumented drift with
        # no comment ever explaining why the two disagreed.
        atr_mult = p_raw.get("atr_stop_mult", 2.0)
        atr_stop = live_price - atr_mult * atr_14
        stop = max(atr_stop, fixed_stop)
        # AUD-SIGALERT-RRUNREACHABLE: this used to hardcode a 2.0 R:R target:
        #     rr_target = live_price + 2.0 * (live_price - stop)
        # which pinned the resulting rr_ratio at <= 2.00 BY CONSTRUCTION (the min() below can
        # only lower it), with no market input at all. That was fine when min_rr_ratio was also
        # 2.0 — but `calibrate_min_rr_ratio` later raised the live floor to 2.25 (3.38 in
        # choppy/risk_off) from real trade data, and NOTHING moved the target. Two independently
        # correct mechanisms, jointly lethal: 2.00 < 2.25 means check_hard_rejects' R:R gate
        # rejects EVERY candidate, always.
        #
        # This is the path that gates SIGNAL ALERTS: check_signal_alerts() POSTs /decide with
        # only {style, market} and no game_plan, and DE's build_game_plan() requires
        # entry2+stop+take_profit in signal reasons — which ZERO of 1,365 production BUY signals
        # carry — so it always lands here. Measured over 72h: 123 conviction passes, 0 DE-gate
        # passes, and BUY alerts last actually sent 2026-09-04 while WAIT/HOLD/SELL kept
        # flowing. A total, silent outage of the platform's primary user-facing output.
        #
        # Fixed by deriving the target multiple from the SAME calibrated floor the gate demands,
        # with a margin so a candidate at exactly the floor is not rejected by float noise. The
        # style cap below still applies, so this never invents a target the style disallows —
        # it only stops manufacturing one that is guaranteed to fail.
        _rr_mult = _target_rr_multiple(style)
        rr_target = live_price + _rr_mult * (live_price - stop)
        take_profit = min(rr_target, live_price * p["target_pct"])  # cap at style max
    else:
        stop = fixed_stop
        take_profit = live_price * p["target_pct"]
    risk = live_price - stop
    reward = take_profit - live_price
    rr_ratio = round(reward / risk, 2) if risk > 0 else None
    return {
        "entry2":      round(live_price * p["entry2_pct"],   4),
        "breakout":    round(live_price * p["breakout_pct"], 4),
        "stop":        round(stop,        4),
        "take_profit": round(take_profit, 4),
        "target_1":    round(live_price + (take_profit - live_price) * 0.5, 4),
        "rr_ratio":    rr_ratio,
    }


async def _fetch_signal(client: httpx.AsyncClient, symbol: str, style: str) -> dict | None:
    try:
        url = f"{_settings.signal_engine_url}/signals/{symbol}?style={style}&live=false"
        r = await client.get(url, headers={"Authorization": f"Bearer {_svc_token()}"}, timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            # Endpoint returns a list (all signals) or a single dict
            if isinstance(data, list):
                # T247-DECISIONENGINE-FETCHSIGNAL-MISATTRIBUTION: the previous
                # `data[0] if data else None` fallback silently returned an ARBITRARY,
                # unrelated symbol's signal whenever no entry matched the requested symbol —
                # currently unreachable (signal-engine's /signals/{symbol}?style=... never
                # actually returns a bare list for this query shape), but if that upstream
                # response shape ever changes, this would have scored the wrong symbol's
                # signal data instead of correctly falling through to the "no signal" path.
                matching = [s for s in data if s.get("symbol", "").upper() == symbol.upper()]
                return matching[0] if matching else None
            return data
    except Exception as exc:
        log.warning("decision.signal_fetch_failed", symbol=symbol, error=str(exc))
    return None


_RESEARCH_MAX_AGE_SEC = 86_400  # discard research older than 24h


async def _fetch_research(client: httpx.AsyncClient, symbol: str) -> dict | None:
    try:
        url = f"{_settings.research_engine_url}/research/{symbol}/summary"
        r = await client.get(url, headers={"Authorization": f"Bearer {_svc_token()}"}, timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            generated_at = data.get("generated_at")
            if generated_at:
                try:
                    from datetime import datetime, timezone
                    ts = datetime.fromisoformat(generated_at.rstrip("Z")).replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - ts).total_seconds() > _RESEARCH_MAX_AGE_SEC:
                        log.info("decision.research_stale", symbol=symbol, generated_at=generated_at)
                        return None
                except Exception:
                    pass
            return data
    except Exception as exc:
        log.warning("decision.research_fetch_failed", symbol=symbol, error=str(exc))
    return None


def _yf_last_price(symbol: str) -> float | None:
    """Fetch latest close price from yfinance. Runs in a thread pool."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d", interval="1d", auto_adjust=True)
        if hist.empty:
            return None
        price = float(hist["Close"].iloc[-1])
        return price if price > 0 else None
    except Exception as exc:
        log.warning("decision.yf_price_failed", symbol=symbol, error=str(exc))
        return None


async def _fetch_price_fallback(symbol: str) -> float | None:
    """Async wrapper: fetch price via yfinance in executor thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_yf_executor, _yf_last_price, symbol)


async def fetch_all(symbol: str, style: str) -> tuple[dict | None, dict | None, float | None]:
    """Fan-out: fetch signal + research in parallel; yfinance only if signal has no price."""
    async with httpx.AsyncClient() as client:
        signal_data, research_data = await asyncio.gather(
            _fetch_signal(client, symbol, style),
            _fetch_research(client, symbol),
        )
    # Only call yfinance when signal reasons carry no usable price (avoids 400-1200ms on every call)
    reasons = (signal_data or {}).get("reasons") or {}
    signal_price = reasons.get("last_price") or reasons.get("price") or reasons.get("close")
    yf_price: float | None = None
    if not signal_price or float(signal_price) <= 0:
        yf_price = await _fetch_price_fallback(symbol)
    return signal_data, research_data, yf_price


def extract_live_price(signal_data: dict | None, yf_price: float | None = None) -> float | None:
    """Pull last known price from signal reasons; fall back to yfinance price."""
    if signal_data:
        reasons = signal_data.get("reasons") or {}
        price = reasons.get("last_price") or reasons.get("price") or reasons.get("close")
        if price and float(price) > 0:
            return float(price)
    return yf_price


def build_game_plan(live_price: float, style: str, signal_data: dict | None) -> dict:
    """Build game plan from signal reasons if available, else use ATR-aware style defaults."""
    atr_14: float | None = None
    if signal_data:
        reasons = signal_data.get("reasons") or {}
        atr_14 = reasons.get("atr_14")
        gp = {
            "entry2":      reasons.get("entry2"),
            "breakout":    reasons.get("breakout"),
            "stop":        reasons.get("stop"),
            "take_profit": reasons.get("take_profit"),
            "target_1":    reasons.get("target_1"),
        }
        if all(v is not None for v in [gp["entry2"], gp["stop"], gp["take_profit"]]):
            gp["breakout"] = gp["breakout"] or live_price * 1.035
            gp["target_1"] = gp["target_1"] or (live_price + (gp["take_profit"] - live_price) * 0.5)
            return {k: float(v) for k, v in gp.items()}
    return _default_game_plan(live_price, style, atr_14)


async def abuild_game_plan(live_price: float, style: str, signal_data: dict | None) -> dict:
    """T247-DECISIONENGINE-STYLEPARAMS-BLOCKING: build_game_plan() is called directly
    (unawaited) from async def _decide() (routes.py); on the signal-reasons-missing path it
    calls _default_game_plan() -> _get_style_params(), which does a blocking httpx.get() to
    market-data on a cache miss. Same event-loop-stall class as regime.py's get_regime() bug
    (T247-DECISIONENGINE-REGIME-BLOCKING) — compounds with it when both 15-minute caches are
    cold simultaneously, serializing what asyncio.gather in /decide/batch was meant to
    parallelize. build_game_plan() itself is fast/pure whenever signal_data already has full
    game-plan reasons (the common case — no network call at all), so wrap the whole function
    in the executor rather than threading async through every internal call site."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_game_plan_executor, build_game_plan, live_price, style, signal_data)


# ── T234-CONFIG-DECIDE-DEFAULT-MISMATCH: real entry-gate defaults (min_confidence etc.) ──
#
# AUD-ENTRYGATEFALLBACK-NOSTYLE (2026-09-06 deep audit): this used to be a single flat dict
# with min_confidence=62.0 — a literal matching NO real style (real per-style min_confidence is
# LONG=40.0, GROWTH=45.0/SHORT=45.0 [both inherit the base default], SWING=50.0, and 65.0 only
# under the HK market override). The T234 fix above (fetching the real value from market-data)
# was itself failing CLOSED onto this same disconnected literal: on a market-data timeout with
# no prior cache, _get_entry_gate_params() returned _ENTRY_GATE_FALLBACK, applying a hard floor
# of 62.0*0.90=55.8 to every style for the full 900s TTL — a 19.8-point over-tightening for
# LONG (real floor 36.0) and 15.3 for GROWTH/SHORT (real floor 40.5). Replaced with a per-style
# (and HK-market-aware) table mirroring paper_trading_engine.py's OWN real merge order
# (_DEFAULT_CONFIG -> _STYLE_OVERRIDES[style] -> _HK_MARKET_OVERRIDES if market=="HK", the same
# order resolve_entry_gate_params() uses) — hand-transcribed since decision-engine cannot import
# market-data's Python module directly (separate service/container), so this needs its OWN
# correctly-valued fallback, not a single global default.
_ENTRY_GATE_FALLBACK_BASE = {
    "min_confidence": 45.0, "min_kscore": 48.0, "min_entry_score": 4,
    "min_ta_score": 0.0, "min_rr_ratio": 2.0,
}
_ENTRY_GATE_FALLBACK_STYLE_OVERRIDES: dict[str, dict] = {
    "GROWTH": {"min_confidence": 45.0, "min_kscore": 48.0},
    "SWING":  {"min_confidence": 50.0, "min_kscore": 52.0, "min_ta_score": 0.65, "min_entry_score": 5},
    "LONG":   {"min_confidence": 40.0, "min_kscore": 50.0},
    "SHORT":  {},  # inherits the base defaults unchanged — no style-specific gate override
}
_ENTRY_GATE_FALLBACK_HK_OVERRIDE = {
    "min_entry_score": 6, "min_confidence": 65.0, "min_ta_score": 0.65,
}


def _entry_gate_fallback_for(style: str, market: str) -> dict:
    cfg = {**_ENTRY_GATE_FALLBACK_BASE, **_ENTRY_GATE_FALLBACK_STYLE_OVERRIDES.get(style.upper(), {})}
    if market.upper() == "HK":
        cfg = {**cfg, **_ENTRY_GATE_FALLBACK_HK_OVERRIDE}
    return cfg


_ENTRY_GATE_CACHE: dict[tuple[str, str], dict] = {}
_ENTRY_GATE_TS: dict[tuple[str, str], float] = {}
_ENTRY_GATE_TTL = 900  # matches _get_style_params()'s own 15-minute cache window


def _get_entry_gate_params(style: str, market: str) -> dict:
    """Fetch the real per-style/market entry-gate defaults from market-data, with a local
    cache + a per-style/market-aware fallback if market-data is unreachable — same fail-open
    shape as _get_style_params() above (a stale/fallback default is better than blocking the
    decide endpoint entirely). Cached per (style, market) pair since the resolved values
    genuinely differ across both dimensions (HK overrides several keys on top of the style
    baseline)."""
    key = (style.upper(), market.upper())
    cached = _ENTRY_GATE_CACHE.get(key)
    if cached and (_time.time() - _ENTRY_GATE_TS.get(key, 0.0)) < _ENTRY_GATE_TTL:
        return cached
    try:
        r = httpx.get(
            f"{_settings.market_data_url}/stocks/entry-gate-params",
            params={"style": key[0], "market": key[1]}, timeout=5.0,
        )
        r.raise_for_status()
        _ENTRY_GATE_CACHE[key] = r.json()
        _ENTRY_GATE_TS[key] = _time.time()
        return _ENTRY_GATE_CACHE[key]
    except Exception as exc:
        log.warning("decision.entry_gate_params_fetch_failed", error=str(exc))
        return cached if cached else _entry_gate_fallback_for(key[0], key[1])


async def aget_entry_gate_params(style: str, market: str) -> dict:
    """Async wrapper matching abuild_game_plan()'s own executor pattern — _get_entry_gate_params()
    does a blocking httpx.get() on a cache miss, which must never run directly on the shared
    event loop (same T247-DECISIONENGINE-STYLEPARAMS-BLOCKING class this whole file already
    guards against for game-plan params). Reuses _game_plan_executor rather than a third
    dedicated pool — this is the same kind of infrequent, short-lived cache-refresh call as
    the game-plan fetch, not a new class of contention."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_game_plan_executor, _get_entry_gate_params, style, market)


# ── T232-DL-DUALSCORER-DEBT item #23: calibrated-logistic entry-weight fetch ──────────────

_ENTRY_WEIGHTS_CACHE: dict | None = None
_ENTRY_WEIGHTS_TS: float = 0.0
_ENTRY_WEIGHTS_TTL = 900  # matches _get_style_params()/_get_entry_gate_params()'s own 15-min cache


def _get_entry_weights() -> dict:
    """Fetch _should_enter()'s own calibrated logistic-regression weights (PT-3) from
    market-data, with a local cache + fail-open-to-empty-dict fallback if market-data is
    unreachable — same shape as _get_style_params()/_get_entry_gate_params() above. An empty
    dict (or a fetch failure) is a SAFE degrade here, not just a fallback default: the caller's
    own `weights.get("n_trades", 0) >= 100` gate correctly treats {} as "no calibration data,
    use the plain additive threshold instead" — the exact same behavior _should_enter() itself
    falls back to when its own local file is missing."""
    global _ENTRY_WEIGHTS_CACHE, _ENTRY_WEIGHTS_TS
    if _ENTRY_WEIGHTS_CACHE is not None and (_time.time() - _ENTRY_WEIGHTS_TS) < _ENTRY_WEIGHTS_TTL:
        return _ENTRY_WEIGHTS_CACHE
    try:
        r = httpx.get(f"{_settings.market_data_url}/stocks/entry-weights", timeout=5.0)
        r.raise_for_status()
        _ENTRY_WEIGHTS_CACHE = r.json()
        _ENTRY_WEIGHTS_TS = _time.time()
        return _ENTRY_WEIGHTS_CACHE
    except Exception as exc:
        log.warning("decision.entry_weights_fetch_failed", error=str(exc))
        return _ENTRY_WEIGHTS_CACHE if _ENTRY_WEIGHTS_CACHE is not None else {}


async def aget_entry_weights() -> dict:
    """Async wrapper matching aget_entry_gate_params()'s own executor pattern — reuses
    _game_plan_executor for the same reason (an infrequent, short-lived cache-refresh call,
    not a new class of contention)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_game_plan_executor, _get_entry_weights)
