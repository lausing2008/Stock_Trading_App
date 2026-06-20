"""Transparent reverse proxy — frontend hits /stocks/*, /signals/*, etc.
through the gateway without knowing about internal service hosts.
"""
from __future__ import annotations

import time as _time

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from jose import JWTError, jwt

from common.config import get_settings

router = APIRouter(tags=["proxy"])
_settings = get_settings()
log = structlog.get_logger()

# Prefixes that don't require a valid JWT
_PUBLIC_PREFIXES = {"auth", "health", "docs", "openapi.json", "redoc"}

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
    "backtests": _settings.strategy_engine_url,
    "portfolio": _settings.portfolio_optimizer_url,
    "portfolio-risk": _settings.market_data_url,
    "research": _settings.research_engine_url,
    "decide":   _settings.decision_engine_url,
    "watchlist": _settings.market_data_url,
    "watchlists": _settings.market_data_url,
    "auth": _settings.market_data_url,
    "alerts": _settings.market_data_url,
    "signal-alerts": _settings.market_data_url,
    "journal": _settings.market_data_url,
    "positions": _settings.market_data_url,
    "app-notifications": _settings.market_data_url,
    "board": _settings.market_data_url,
    "congress": _settings.market_data_url,
    "paper-portfolio": _settings.market_data_url,
    "broker": _settings.market_data_url,
}


def _upstream(path: str) -> str | None:
    head = path.strip("/").split("/", 1)[0]
    return _ROUTES.get(head)


_BLACKLIST_PREFIX = "auth:blacklist:"

# In-memory fallback: populated when Redis confirms a JTI is revoked.
# When Redis is unavailable, known-revoked tokens stay blocked; unknown JTIs fail-open.
_BLACKLIST_MEM: dict[str, float] = {}   # jti → expiry unix timestamp
_BLACKLIST_MEM_TTL = 3600               # 1 hour


def _is_blacklisted(jti: str) -> bool:
    now = _time.time()
    # Check in-memory cache first
    exp = _BLACKLIST_MEM.get(jti)
    if exp is not None and exp > now:
        return True
    try:
        import redis as redis_lib
        r = redis_lib.from_url(_settings.redis_url, decode_responses=True, socket_connect_timeout=1)
        revoked = bool(r.exists(f"{_BLACKLIST_PREFIX}{jti}"))
        if revoked:
            _BLACKLIST_MEM[jti] = now + _BLACKLIST_MEM_TTL
            if len(_BLACKLIST_MEM) > 2000:   # bounded eviction
                _BLACKLIST_MEM.clear()
        return revoked
    except Exception:
        # Redis unavailable — use in-memory cache as fallback (fail-closed for known JTIs)
        return exp is not None and exp > now


def _require_auth(full_path: str, request: Request) -> None:
    """Raise HTTP 401 for protected routes that have no valid JWT."""
    prefix = full_path.strip("/").split("/", 1)[0]
    if prefix in _PUBLIC_PREFIXES:
        return
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, _settings.jwt_secret, algorithms=["HS256"])
        jti: str = payload.get("jti", "")
        if not jti:
            raise HTTPException(401, "Token missing jti claim")
        if _is_blacklisted(jti):
            raise HTTPException(401, "Token has been revoked")
    except HTTPException:
        raise
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")


@router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def reverse_proxy(full_path: str, request: Request):
    if full_path in ("health", "docs", "openapi.json", "redoc"):
        raise HTTPException(404)
    _require_auth(full_path, request)
    upstream = _upstream(full_path)
    if not upstream:
        raise HTTPException(404, f"No route for /{full_path}")

    url = f"{upstream}/{full_path}"
    body = await request.body()
    # Strip headers with illegal values (e.g. 'Bearer ' with no token)
    safe_headers = {}
    for k, v in request.headers.items():
        if k.lower() in ("host", "content-length"):
            continue
        if k.lower() == "authorization" and v.strip() in ("", "Bearer", "Bearer "):
            continue
        safe_headers[k] = v
    # Research report generation (POST /research/*) calls the AI provider and can take 2-3 min.
    # Give it a longer timeout so the gateway doesn't cut it off before the AI responds.
    prefix = full_path.strip("/").split("/", 1)[0]
    proxy_timeout = 240 if prefix == "research" and request.method == "POST" else 120
    try:
        async with httpx.AsyncClient(timeout=proxy_timeout) as client:
            r = await client.request(
                request.method,
                url,
                params=dict(request.query_params),
                content=body,
                headers=safe_headers,
            )
    except httpx.TimeoutException:
        raise HTTPException(504, "Upstream service timed out")
    except Exception as exc:
        log.warning("proxy.upstream_error", path=full_path, error=str(exc))
        raise HTTPException(502, "Service temporarily unavailable")
    content_type = r.headers.get("content-type", "application/json").split(";")[0].strip()
    return Response(content=r.content, status_code=r.status_code, media_type=content_type)
