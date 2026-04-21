"""Aggregation endpoints — one call that fans out to multiple services.

Avoids N+1 request pattern from the frontend for common dashboard views.
"""
from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, HTTPException

from common.config import get_settings

router = APIRouter(prefix="/aggregate", tags=["aggregate"])
_settings = get_settings()


async def _get(client: httpx.AsyncClient, url: str):
    try:
        r = await client.get(url, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


@router.get("/overview/{symbol}")
async def overview(symbol: str):
    """One-shot: price tail, indicators, levels, signal, ranking, ML prediction."""
    async with httpx.AsyncClient() as client:
        tasks = {
            "price": _get(client, f"{_settings.market_data_url}/stocks/{symbol}"),
            "prices": _get(client, f"{_settings.market_data_url}/stocks/{symbol}/prices?limit=400"),
            "indicators": _get(client, f"{_settings.technical_analysis_url}/ta/{symbol}/indicators"),
            "patterns": _get(client, f"{_settings.technical_analysis_url}/ta/{symbol}/patterns"),
            "levels": _get(client, f"{_settings.technical_analysis_url}/ta/{symbol}/levels"),
            "signal": _get(client, f"{_settings.signal_engine_url}/signals/{symbol}?persist=true"),
            "ranking": _get(client, f"{_settings.ranking_engine_url}/rankings/{symbol}"),
        }
        results = await asyncio.gather(*tasks.values())

    out = dict(zip(tasks.keys(), results, strict=False))
    if all(v is None for v in out.values()):
        raise HTTPException(502, "All upstream services failed")
    return out
