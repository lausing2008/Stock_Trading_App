"""Transparent reverse proxy — frontend hits /stocks/*, /signals/*, etc.
through the gateway without knowing about internal service hosts.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from common.config import get_settings

router = APIRouter(tags=["proxy"])
_settings = get_settings()

# Route-prefix → upstream URL
_ROUTES = {
    "stocks": _settings.market_data_url,
    "admin": _settings.market_data_url,
    "ta": _settings.technical_analysis_url,
    "ml": _settings.ml_prediction_url,
    "rankings": _settings.ranking_engine_url,
    "signals": _settings.signal_engine_url,
    "strategies": _settings.strategy_engine_url,
    "backtest": _settings.strategy_engine_url,
    "portfolio": _settings.portfolio_optimizer_url,
    "watchlist": _settings.market_data_url,
    "auth": _settings.market_data_url,
}


def _upstream(path: str) -> str | None:
    head = path.strip("/").split("/", 1)[0]
    return _ROUTES.get(head)


@router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def reverse_proxy(full_path: str, request: Request):
    if full_path in ("health", "docs", "openapi.json", "redoc"):
        raise HTTPException(404)
    upstream = _upstream(full_path)
    if not upstream:
        raise HTTPException(404, f"No route for /{full_path}")

    url = f"{upstream}/{full_path}"
    body = await request.body()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.request(
            request.method,
            url,
            params=dict(request.query_params),
            content=body,
            headers={k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")},
        )
    try:
        return JSONResponse(r.json(), status_code=r.status_code)
    except ValueError:
        return JSONResponse({"raw": r.text}, status_code=r.status_code)
