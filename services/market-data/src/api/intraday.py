"""Intraday research records, read-only and admin-only (design §11).

Under /stocks/intraday so the gateway's existing `stocks` route serves it (no new gateway prefix).
Stage C has no generator: these endpoints return empty pages and say production generation is
disabled. Records are platform-wide over the US universe and carry no user data.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from common.horizons import UnsupportedMarket
from db import get_session

from ..services.intraday import read as R
from ..services.intraday import schema as S
from .auth import get_admin_user

router = APIRouter(prefix="/stocks/intraday", tags=["intraday"])


def _call(session, fn, **kw):
    if S.SCHEMA_STATUS != "ok":
        raise HTTPException(503, {"error": "schema_unavailable", "detail": S.SCHEMA_STATUS})
    try:
        return fn(session.connection(), **kw)
    except UnsupportedMarket as exc:
        raise HTTPException(422, {"error": "unsupported_market", "market": exc.market,
                                  "detail": "Intraday v1 is US-only"}) from exc
    except R.BadRequest as exc:
        raise HTTPException(422, {"error": "bad_request", "detail": str(exc)}) from exc


@router.get("/identities")
def identities(session_date: date = Query(..., alias="session"), mode: str = Query(...), run_key: str | None = None,
               market: str = "US", _admin=Depends(get_admin_user), session=Depends(get_session)):
    return _call(session, R.list_identities, market=market, mode=mode, run_key=run_key, session_date=session_date)


@router.get("/evaluations")
def evaluations(session_date: date = Query(..., alias="session"), mode: str = Query(...), identity: str = Query(...),
                run_key: str | None = None, symbol: str | None = None, cursor: str | None = None,
                limit: int | None = None, market: str = "US", _admin=Depends(get_admin_user),
                session=Depends(get_session)):
    return _call(session, R.list_evaluations, market=market, mode=mode, run_key=run_key, identity=identity,
                 session_date=session_date, symbol=symbol, cursor=cursor, limit=limit)


@router.get("/setups")
def setups(session_date: date = Query(..., alias="session"), mode: str = Query(...), identity: str = Query(...),
           run_key: str | None = None, symbol: str | None = None, cursor: str | None = None, limit: int | None = None,
           market: str = "US", _admin=Depends(get_admin_user), session=Depends(get_session)):
    return _call(session, R.list_setups, market=market, mode=mode, run_key=run_key, identity=identity,
                 session_date=session_date, symbol=symbol, cursor=cursor, limit=limit)


@router.get("/unresolved")
def unresolved(mode: str = Query(...), run_key: str | None = None, cursor: str | None = None, limit: int | None = None,
               market: str = "US", _admin=Depends(get_admin_user), session=Depends(get_session)):
    return _call(session, R.list_unresolved, market=market, mode=mode, run_key=run_key, cursor=cursor, limit=limit)
