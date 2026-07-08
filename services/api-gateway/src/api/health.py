"""GET /health/deep — fan-out ping to all upstream service /health endpoints."""
from __future__ import annotations

import asyncio
import time as _time

import httpx
from fastapi import APIRouter

from common.config import get_settings

router = APIRouter(tags=["health"])
_settings = get_settings()

_SERVICES: list[tuple[str, str]] = [
    ("market-data",         _settings.market_data_url),
    ("technical-analysis",  _settings.technical_analysis_url),
    ("ml-prediction",       _settings.ml_prediction_url),
    ("ranking-engine",      _settings.ranking_engine_url),
    ("signal-engine",       _settings.signal_engine_url),
    ("strategy-engine",     _settings.strategy_engine_url),
    ("portfolio-optimizer", _settings.portfolio_optimizer_url),
    ("research-engine",     _settings.research_engine_url),
    ("decision-engine",     _settings.decision_engine_url),
    ("event-intelligence",  _settings.event_intelligence_url),
]


async def _ping(client: httpx.AsyncClient, name: str, base_url: str) -> dict:
    url = f"{base_url}/health"
    t0 = _time.monotonic()
    try:
        r = await client.get(url, timeout=4.0)
        latency_ms = round((_time.monotonic() - t0) * 1000)
        return {"service": name, "status": "ok" if r.status_code == 200 else "error", "latency_ms": latency_ms, "code": r.status_code}
    except httpx.TimeoutException:
        latency_ms = round((_time.monotonic() - t0) * 1000)
        return {"service": name, "status": "timeout", "latency_ms": latency_ms, "code": None}
    except Exception as exc:
        latency_ms = round((_time.monotonic() - t0) * 1000)
        return {"service": name, "status": "error", "latency_ms": latency_ms, "code": None, "error": str(exc)}


@router.get("/health/deep")
async def health_deep():
    """Ping all upstream service /health endpoints in parallel. Returns latency and status for each."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_ping(client, name, url) for name, url in _SERVICES])
    total_ok = sum(1 for r in results if r["status"] == "ok")
    return {
        "gateway": "ok",
        "services_ok": total_ok,
        "services_total": len(_SERVICES),
        "results": list(results),
    }
