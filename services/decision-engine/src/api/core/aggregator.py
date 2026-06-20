"""Async fan-out — fetches signal, research, and game plan data in parallel."""
from __future__ import annotations

import asyncio
import time as _time

import httpx
import structlog
from jose import jwt as _jwt

from common.config import get_settings

log = structlog.get_logger()
_settings = get_settings()

_svc_token_cache: str = ""


def _svc_token() -> str:
    global _svc_token_cache
    if _svc_token_cache:
        return _svc_token_cache
    payload = {
        "sub": "decision-engine",
        "jti": "decision-engine-service",
        "exp": int(_time.time()) + 365 * 86400,
    }
    _svc_token_cache = _jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")
    return _svc_token_cache


# ── Default style game-plan parameters ────────────────────────────────────────

_STYLE_PARAMS = {
    "SCALP":  {"entry2_pct": 0.990, "breakout_pct": 1.010, "stop_pct": 0.975, "target_pct": 1.040},
    "SWING":  {"entry2_pct": 0.940, "breakout_pct": 1.035, "stop_pct": 0.880, "target_pct": 1.350},
    "GROWTH": {"entry2_pct": 0.920, "breakout_pct": 1.050, "stop_pct": 0.840, "target_pct": 1.600},
    "INCOME": {"entry2_pct": 0.970, "breakout_pct": 1.015, "stop_pct": 0.930, "target_pct": 1.150},
}


def _default_game_plan(live_price: float, style: str) -> dict:
    p = _STYLE_PARAMS.get(style.upper(), _STYLE_PARAMS["SWING"])
    return {
        "entry2":      round(live_price * p["entry2_pct"],   4),
        "breakout":    round(live_price * p["breakout_pct"], 4),
        "stop":        round(live_price * p["stop_pct"],     4),
        "take_profit": round(live_price * p["target_pct"],   4),
        "target_1":    round(live_price * (1 + (p["target_pct"] - 1) * 0.5), 4),
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


async def _fetch_research(client: httpx.AsyncClient, symbol: str) -> dict | None:
    try:
        url = f"{_settings.research_engine_url}/research/{symbol}/summary"
        r = await client.get(url, headers={"Authorization": f"Bearer {_svc_token()}"}, timeout=2.0)
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        log.warning("decision.research_fetch_failed", symbol=symbol, error=str(exc))
    return None


async def fetch_all(symbol: str, style: str) -> tuple[dict | None, dict | None]:
    """Fan-out: fetch signal + research in parallel. Returns (signal_data, research_data)."""
    async with httpx.AsyncClient() as client:
        signal_task   = _fetch_signal(client, symbol, style)
        research_task = _fetch_research(client, symbol)
        signal_data, research_data = await asyncio.gather(signal_task, research_task)
    return signal_data, research_data


def extract_live_price(signal_data: dict | None) -> float | None:
    """Pull last known price from signal reasons if available."""
    if not signal_data:
        return None
    reasons = signal_data.get("reasons") or {}
    return reasons.get("last_price") or reasons.get("price") or None


def build_game_plan(live_price: float, style: str, signal_data: dict | None) -> dict:
    """Build game plan from signal reasons if available, else use style defaults."""
    if signal_data:
        reasons = signal_data.get("reasons") or {}
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
    return _default_game_plan(live_price, style)
