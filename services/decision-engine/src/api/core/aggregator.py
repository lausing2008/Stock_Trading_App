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
    "SHORT":  {"entry2_pct": 0.985, "breakout_pct": 1.010, "stop_pct": 0.970, "default_tp_pct": 1.05},
    "SWING":  {"entry2_pct": 0.965, "breakout_pct": 1.020, "stop_pct": 0.945, "default_tp_pct": 1.12},
    "LONG":   {"entry2_pct": 0.950, "breakout_pct": 1.030, "stop_pct": 0.900, "default_tp_pct": 1.25},
    "GROWTH": {"entry2_pct": 0.940, "breakout_pct": 1.035, "stop_pct": 0.880, "default_tp_pct": 1.35},
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


def _default_game_plan(live_price: float, style: str, atr_14: float | None = None) -> dict:
    style_params = _get_style_params()
    p_raw = style_params.get(style.upper(), style_params.get("SWING", _STYLE_PARAMS_FALLBACK["SWING"]))
    # market-data's dict uses "default_tp_pct" (not "target_pct") — normalize the key here so
    # the rest of this function doesn't need to know which source it came from.
    p = {**p_raw, "target_pct": p_raw.get("target_pct", p_raw.get("default_tp_pct"))}
    fixed_stop = live_price * p["stop_pct"]
    if atr_14 and atr_14 > 0:
        # GROWTH stocks need wider stops — 2.5× ATR vs 2.0× for other styles.
        atr_mult = 2.5 if style.upper() == "GROWTH" else 2.0
        atr_stop = live_price - atr_mult * atr_14
        stop = max(atr_stop, fixed_stop)
        # Target: 2:1 R:R off the actual stop used
        rr_target = live_price + 2.0 * (live_price - stop)
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
                matching = [s for s in data if s.get("symbol", "").upper() == symbol.upper()]
                return matching[0] if matching else (data[0] if data else None)
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
    return await loop.run_in_executor(_yf_executor, build_game_plan, live_price, style, signal_data)
