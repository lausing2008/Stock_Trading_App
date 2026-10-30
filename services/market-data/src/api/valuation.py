"""Internal-to-app explicit refresh; research freezes the returned inputs in a report."""
import redis
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from common.jwt_auth import get_current_username
from common.redis_client import get_redis
from db import SessionLocal, Stock, Market
from ..services.valuation_inputs import collect_inputs

router = APIRouter(prefix="/stocks/valuation-inputs", tags=["valuation"])
outcome_router = APIRouter(prefix="/stocks/valuation-outcome-prices", tags=["valuation"])


@outcome_router.post("")
def outcome_prices(symbols: str = Query(..., max_length=1024), _user: str = Depends(get_current_username)):
    requested = list(dict.fromkeys(symbols.split(",")))
    if not 1 <= len(requested) <= 61 or any(not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", s) for s in requested):
        raise HTTPException(400, "Between 1 and 61 valid US symbols are required")
    from ..services.valuation_outcome_prices import collect
    return collect(get_redis(), requested)


@router.post("/refresh")
def refresh_inputs(_user: str = Depends(get_current_username), priority_symbols: str | None = Query(None, max_length=200)):
    cache = get_redis()
    lock_key = "stockai:valuation:input-refresh-lock"
    import uuid
    token = uuid.uuid4().hex
    try:
        acquired = cache.set(lock_key, token, nx=True, ex=1800)
    except redis.RedisError:
        raise HTTPException(503, "Valuation input cache unavailable")
    if not acquired:
        raise HTTPException(409, "A valuation input refresh is already running")
    try:
        with SessionLocal() as session:
            tracked = set(session.execute(select(Stock.symbol).where(
                Stock.market == Market.US, Stock.active.is_(True), Stock.delisted.is_(False))).scalars().all())
        priorities = priority_symbols.split(",") if isinstance(priority_symbols, str) else []
        return collect_inputs(cache, tracked, priority_symbols=priorities)
    except ValueError as exc:
        raise HTTPException(503, str(exc))
    finally:
        # Never delete another worker's lease after ours expired.
        try:
            cache.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) end",
                       1, lock_key, token)
        except redis.RedisError:
            pass  # The lease expires; preserve the useful response or original failure.
