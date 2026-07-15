"""Event Intelligence API routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from common.jwt_auth import get_current_username
from db import get_session, SessionLocal, Stock, EconomicEvent
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..services import economic, earnings, insider, congress, institutional, political, catalyst, edgar_8k, valuation

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "service": "event-intelligence"}


# ── Economic Calendar ─────────────────────────────────────────────────────────

@router.get("/events/economic")
def get_economic(
    days: int = Query(14, ge=1, le=90),
    market: str = Query("US"),
    _: str = Depends(get_current_username),
):
    country = "US" if market.upper() == "US" else "HK"
    events = economic.get_upcoming_economic_events(days, country)
    fomc_days = economic.days_to_next_fomc()
    return {"events": events, "fomc_days_away": fomc_days}


@router.post("/events/sync/economic")
async def sync_economic(_: str = Depends(get_current_username)):
    result = await economic.sync_fred()
    return result


# ── CAPE / AI-Bubble-Warning Valuation Indicator ──────────────────────────────

@router.get("/events/valuation/cape")
def get_cape(months: int = Query(24, ge=1, le=600), _: str = Depends(get_current_username)):
    latest = valuation.get_latest_cape()
    if latest is None:
        raise HTTPException(404, "No CAPE data synced yet")
    history = valuation.get_cape_history(months)
    return {"latest": latest, "history": history}


@router.post("/events/sync/cape")
async def sync_cape(_: str = Depends(get_current_username)):
    current = await valuation.sync_cape_current()
    history = await valuation.sync_cape_history()
    return {"current": current, "history": history}


# ── Earnings ──────────────────────────────────────────────────────────────────

@router.get("/events/earnings/calendar")
def get_earnings_calendar(days: int = Query(14, ge=1, le=60), _: str = Depends(get_current_username)):
    return earnings.get_upcoming_earnings(days)


@router.get("/events/earnings")
def get_earnings_by_symbol(symbol: str = Query(...), _: str = Depends(get_current_username)):
    stock_id = _symbol_to_id(symbol)
    return earnings.get_earnings_for_symbol(stock_id)


@router.post("/events/sync/earnings")
async def sync_earnings(_: str = Depends(get_current_username)):
    result = await earnings.sync_all_earnings()
    return result


# ── Insider Trading ───────────────────────────────────────────────────────────
# NOTE: fixed-path routes MUST appear before {symbol} routes in FastAPI

@router.get("/events/insider/leaderboard")
def insider_leaderboard(days: int = Query(30, ge=7, le=365), limit: int = Query(20, ge=5, le=50), _: str = Depends(get_current_username)):
    return insider.get_insider_leaderboard(days, limit)


@router.post("/events/sync/insider")
async def sync_insider(_: str = Depends(get_current_username)):
    result = await insider.sync_all_insider()
    return result


@router.get("/events/insider/{symbol}")
def get_insider(symbol: str, days: int = Query(90, ge=30, le=365), _: str = Depends(get_current_username)):
    stock_id = _symbol_to_id(symbol)
    txns = insider.get_insider_for_symbol(stock_id, days)
    score = insider.compute_insider_score(stock_id, days)
    return {"symbol": symbol.upper(), "insider_score": round(score, 1), "transactions": txns}


# ── Congress Trading ──────────────────────────────────────────────────────────
# NOTE: fixed-path routes MUST appear before {symbol} routes in FastAPI

@router.get("/events/congress/leaderboard")
def congress_leaderboard(days: int = Query(90, ge=30, le=365), limit: int = Query(20, ge=5, le=50), _: str = Depends(get_current_username)):
    return congress.get_congress_leaderboard(days, limit)


@router.get("/events/congress/recent")
def recent_congress(
    days: int = Query(30, ge=7, le=365),
    limit: int = Query(50, ge=10, le=500),
    ticker: str | None = Query(None, description="Filter to one ticker, e.g. AAPL"),
    politician: str | None = Query(None, description="Case-insensitive substring match on politician name"),
    _: str = Depends(get_current_username),
):
    # T233-ARCH-CONGRESS-DEDUP: days/limit ceilings raised (was 90/200) and ticker/politician
    # filters added so this single endpoint can fully replace market-data's now-deleted
    # /congress/trades for congress.tsx/insider.tsx's full-table + screener UX.
    return congress.get_recent_congress_trades(days, limit, ticker=ticker, politician=politician)


@router.post("/events/sync/congress")
async def sync_congress(_: str = Depends(get_current_username)):
    result = await congress.sync_congress_trades()
    return result


@router.get("/events/congress/{symbol}")
def get_congress(symbol: str, days: int = Query(90, ge=30, le=365), _: str = Depends(get_current_username)):
    stock_id = _symbol_to_id(symbol)
    trades = congress.get_congress_for_symbol(stock_id, days)
    score = congress.compute_congress_score(stock_id, days)
    return {"symbol": symbol.upper(), "congress_score": round(score, 1), "trades": trades}


# ── Institutional ─────────────────────────────────────────────────────────────
# NOTE: fixed-path routes MUST appear before {symbol} routes in FastAPI

@router.get("/events/institutional/leaderboard")
def institutional_leaderboard(limit: int = Query(20, ge=5, le=50), _: str = Depends(get_current_username)):
    return institutional.get_institutional_leaderboard(limit)


@router.post("/events/sync/institutional")
async def sync_institutional(_: str = Depends(get_current_username)):
    result = await institutional.sync_institutional()
    return result


@router.get("/events/institutional/{symbol}")
def get_institutional(symbol: str, _: str = Depends(get_current_username)):
    stock_id = _symbol_to_id(symbol)
    holdings = institutional.get_institutional_for_symbol(stock_id)
    score = institutional.compute_institutional_score(stock_id)
    return {"symbol": symbol.upper(), "institutional_score": round(score, 1), "holdings": holdings}


# ── Political Events ──────────────────────────────────────────────────────────

@router.get("/events/political")
def get_political(days: int = Query(30, ge=7, le=180), symbol: str | None = None, _: str = Depends(get_current_username)):
    stock_id = _symbol_to_id(symbol) if symbol else None
    return political.get_political_events(days, stock_id)


@router.post("/events/sync/political")
async def sync_political(_: str = Depends(get_current_username)):
    result = await political.sync_political_contracts()
    return result


# ── Catalyst Scores ───────────────────────────────────────────────────────────
# NOTE: fixed-path routes MUST appear before {symbol} routes in FastAPI

@router.get("/catalyst/leaderboard")
def catalyst_leaderboard(limit: int = Query(20, ge=5, le=50), _: str = Depends(get_current_username)):
    return catalyst.get_catalyst_leaderboard(limit)


@router.get("/catalyst/risk-leaderboard")
def risk_leaderboard(limit: int = Query(20, ge=5, le=50), _: str = Depends(get_current_username)):
    return catalyst.get_risk_leaderboard(limit)


@router.get("/catalyst/composite-leaderboard")
def composite_leaderboard(limit: int = Query(20, ge=5, le=50), _: str = Depends(get_current_username)):
    return catalyst.get_composite_leaderboard(limit)


@router.post("/catalyst/recompute")
async def recompute_catalyst(_: str = Depends(get_current_username)):
    result = await catalyst.recompute_all()
    return result


@router.get("/catalyst/{symbol}")
def get_catalyst(symbol: str, technical_score: float = Query(50.0, ge=0.0, le=100.0), _: str = Depends(get_current_username)):
    stock_id = _symbol_to_id(symbol)
    score = catalyst.get_catalyst(stock_id)
    if score is None:
        # Compute on demand if not yet cached
        score = catalyst.compute_and_store(stock_id, technical_score=technical_score)
    score["symbol"] = symbol.upper()
    return score


# ── SEC EDGAR 8-K Filings (T208) ──────────────────────────────────────────────

@router.post("/events/sync/8k")
async def sync_8k(_: str = Depends(get_current_username)):
    """Trigger SEC EDGAR 8-K filing ingest for all active US stocks.

    Fetches filings from the last 7 days for each US stock tracked in the DB.
    HK stocks are skipped (no EDGAR coverage). Idempotent — existing accessions
    are skipped via ON CONFLICT DO NOTHING.
    The ingest manages its own DB sessions internally.
    """
    with SessionLocal() as s:
        symbols = list(
            s.execute(
                select(Stock.symbol).where(Stock.active.is_(True), Stock.market == "US")
            ).scalars()
        )
    result = edgar_8k.ingest_8k_filings(symbols, days_back=7)
    return result


@router.get("/events/8k/{symbol}")
def get_8k_filings(
    symbol: str,
    days: int = Query(30, ge=1, le=180),
    _: str = Depends(get_current_username),
    db: Session = Depends(get_session),
):
    """Return recent SEC 8-K filings for a symbol from the local DB.

    Returns stored filings only (no live EDGAR fetch). Populated daily by the
    market-data scheduler after US close via the EDGAR ingest job (T208).
    HK stocks will always return an empty list (no EDGAR coverage).
    """
    return edgar_8k.get_recent_filings_for_symbol(db, symbol, days=days)


# ── Overview (used by frontend intelligence page) ─────────────────────────────

@router.get("/events/overview")
def get_overview(_: str = Depends(get_current_username)):
    """Single endpoint returning all sections for the /intelligence page.
    Shape must match the EventIntelOverview TypeScript type in api.ts."""
    upcoming_economic = economic.get_upcoming_economic_events(14, "US")
    fomc_days = economic.days_to_next_fomc()
    upcoming_earnings = earnings.get_upcoming_earnings(14)
    insider_leaders = insider.get_insider_leaderboard(30, 10)
    congress_leaders = congress.get_congress_leaderboard(90, 10)

    # T249-MARKETMOVER-P2: latest macro fast-reaction, for the Overview tab's reaction card.
    latest_macro_reaction = None
    with SessionLocal() as s:
        row = s.execute(
            select(EconomicEvent)
            .where(EconomicEvent.reaction_text.isnot(None))
            .order_by(EconomicEvent.reaction_generated_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            latest_macro_reaction = {
                "event_type": row.event_type,
                "title": row.title,
                "actual_value": row.actual_value,
                "expected_value": row.expected_value,
                "previous_value": row.previous_value,
                "reaction_text": row.reaction_text,
                "generated_at": row.reaction_generated_at.isoformat() if row.reaction_generated_at else None,
            }

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
        "latest_macro_reaction": latest_macro_reaction,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _symbol_to_id(symbol: str) -> int:
    """T247-EVENTINTELLIGENCE-SYMBOLEXCHANGE: the DB's real uniqueness constraint on Stock is
    (symbol, exchange) — nothing prevents two rows sharing the same symbol on different
    exchanges. `scalar_one_or_none()` raises an unhandled MultipleResultsFound (surfaced as a
    raw HTTP 500) whenever that happens, instead of resolving deterministically. Prefer the
    active listing, then the lowest id, so a lookup by symbol alone always resolves to exactly
    one real stock rather than crashing.
    """
    with SessionLocal() as s:
        row = s.execute(
            select(Stock.id)
            .where(Stock.symbol == symbol.upper())
            .order_by(Stock.active.desc(), Stock.id.asc())
            .limit(1)
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"Symbol {symbol.upper()} not found")
    return row
