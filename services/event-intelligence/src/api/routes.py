"""Event Intelligence API routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from common.jwt_auth import get_current_username
from db import get_session, SessionLocal, Stock
from sqlalchemy import select

from ..services import economic, earnings, insider, congress, institutional, political, catalyst

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "service": "event-intelligence"}


# ── Economic Calendar ─────────────────────────────────────────────────────────

@router.get("/events/economic")
def get_economic(
    days: int = Query(14, ge=1, le=90),
    market: str = Query("US"),
):
    country = "US" if market.upper() == "US" else "HK"
    events = economic.get_upcoming_economic_events(days, country)
    fomc_days = economic.days_to_next_fomc()
    return {"events": events, "fomc_days_away": fomc_days}


@router.post("/events/sync/economic")
async def sync_economic(_: str = Depends(get_current_username)):
    result = await economic.sync_fred()
    return result


# ── Earnings ──────────────────────────────────────────────────────────────────

@router.get("/events/earnings/calendar")
def get_earnings_calendar(days: int = Query(14, ge=1, le=60)):
    return earnings.get_upcoming_earnings(days)


@router.get("/events/earnings")
def get_earnings_by_symbol(symbol: str = Query(...)):
    stock_id = _symbol_to_id(symbol)
    return earnings.get_earnings_for_symbol(stock_id)


@router.post("/events/sync/earnings")
async def sync_earnings(_: str = Depends(get_current_username)):
    result = await earnings.sync_all_earnings()
    return result


# ── Insider Trading ───────────────────────────────────────────────────────────
# NOTE: fixed-path routes MUST appear before {symbol} routes in FastAPI

@router.get("/events/insider/leaderboard")
def insider_leaderboard(days: int = Query(30, ge=7, le=365), limit: int = Query(20, ge=5, le=50)):
    return insider.get_insider_leaderboard(days, limit)


@router.post("/events/sync/insider")
async def sync_insider(_: str = Depends(get_current_username)):
    result = await insider.sync_all_insider()
    return result


@router.get("/events/insider/{symbol}")
def get_insider(symbol: str, days: int = Query(90, ge=30, le=365)):
    stock_id = _symbol_to_id(symbol)
    txns = insider.get_insider_for_symbol(stock_id, days)
    score = insider.compute_insider_score(stock_id, days)
    return {"symbol": symbol.upper(), "insider_score": round(score, 1), "transactions": txns}


# ── Congress Trading ──────────────────────────────────────────────────────────
# NOTE: fixed-path routes MUST appear before {symbol} routes in FastAPI

@router.get("/events/congress/leaderboard")
def congress_leaderboard(days: int = Query(90, ge=30, le=365), limit: int = Query(20, ge=5, le=50)):
    return congress.get_congress_leaderboard(days, limit)


@router.get("/events/congress/recent")
def recent_congress(days: int = Query(30, ge=7, le=90), limit: int = Query(50, ge=10, le=200)):
    return congress.get_recent_congress_trades(days, limit)


@router.post("/events/sync/congress")
async def sync_congress(_: str = Depends(get_current_username)):
    result = await congress.sync_congress_trades()
    return result


@router.get("/events/congress/{symbol}")
def get_congress(symbol: str, days: int = Query(90, ge=30, le=365)):
    stock_id = _symbol_to_id(symbol)
    trades = congress.get_congress_for_symbol(stock_id, days)
    score = congress.compute_congress_score(stock_id, days)
    return {"symbol": symbol.upper(), "congress_score": round(score, 1), "trades": trades}


# ── Institutional ─────────────────────────────────────────────────────────────
# NOTE: fixed-path routes MUST appear before {symbol} routes in FastAPI

@router.get("/events/institutional/leaderboard")
def institutional_leaderboard(limit: int = Query(20, ge=5, le=50)):
    return institutional.get_institutional_leaderboard(limit)


@router.post("/events/sync/institutional")
async def sync_institutional(_: str = Depends(get_current_username)):
    result = await institutional.sync_institutional()
    return result


@router.get("/events/institutional/{symbol}")
def get_institutional(symbol: str):
    stock_id = _symbol_to_id(symbol)
    holdings = institutional.get_institutional_for_symbol(stock_id)
    score = institutional.compute_institutional_score(stock_id)
    return {"symbol": symbol.upper(), "institutional_score": round(score, 1), "holdings": holdings}


# ── Political Events ──────────────────────────────────────────────────────────

@router.get("/events/political")
def get_political(days: int = Query(30, ge=7, le=180), symbol: str | None = None):
    stock_id = _symbol_to_id(symbol) if symbol else None
    return political.get_political_events(days, stock_id)


@router.post("/events/sync/political")
async def sync_political(_: str = Depends(get_current_username)):
    result = await political.sync_political_contracts()
    return result


# ── Catalyst Scores ───────────────────────────────────────────────────────────
# NOTE: fixed-path routes MUST appear before {symbol} routes in FastAPI

@router.get("/catalyst/leaderboard")
def catalyst_leaderboard(limit: int = Query(20, ge=5, le=50)):
    return catalyst.get_catalyst_leaderboard(limit)


@router.get("/catalyst/risk-leaderboard")
def risk_leaderboard(limit: int = Query(20, ge=5, le=50)):
    return catalyst.get_risk_leaderboard(limit)


@router.get("/catalyst/composite-leaderboard")
def composite_leaderboard(limit: int = Query(20, ge=5, le=50)):
    return catalyst.get_composite_leaderboard(limit)


@router.post("/catalyst/recompute")
async def recompute_catalyst(_: str = Depends(get_current_username)):
    result = await catalyst.recompute_all()
    return result


@router.get("/catalyst/{symbol}")
def get_catalyst(symbol: str):
    stock_id = _symbol_to_id(symbol)
    score = catalyst.get_catalyst(stock_id)
    if score is None:
        # Compute on demand if not yet cached
        score = catalyst.compute_and_store(stock_id)
    score["symbol"] = symbol.upper()
    return score


# ── Overview (used by frontend intelligence page) ─────────────────────────────

@router.get("/events/overview")
def get_overview():
    """Single endpoint returning all sections for the /intelligence page.
    Shape must match the EventIntelOverview TypeScript type in api.ts."""
    upcoming_economic = economic.get_upcoming_economic_events(14, "US")
    fomc_days = economic.days_to_next_fomc()
    upcoming_earnings = earnings.get_upcoming_earnings(14)
    insider_leaders = insider.get_insider_leaderboard(30, 10)
    congress_leaders = congress.get_congress_leaderboard(90, 10)
    return {
        "economic": {
            "upcoming_count": len(upcoming_economic),
            "fomc_days_away": fomc_days,
            "events": upcoming_economic,
        },
        "earnings": {
            "upcoming_count": len(upcoming_earnings),
            "events": upcoming_earnings,
        },
        "insider": {
            "top_buys": insider_leaders,
        },
        "congress": {
            "top_buys": congress_leaders,
            "recent": congress.get_recent_congress_trades(30, 20),
        },
        "catalyst_leaders": catalyst.get_catalyst_leaderboard(15),
        "risk_leaders": catalyst.get_risk_leaderboard(10),
        "composite_leaders": catalyst.get_composite_leaderboard(10),
        "political_events": political.get_political_events(30),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _symbol_to_id(symbol: str) -> int:
    with SessionLocal() as s:
        row = s.execute(
            select(Stock.id).where(Stock.symbol == symbol.upper())
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"Symbol {symbol.upper()} not found")
    return row
