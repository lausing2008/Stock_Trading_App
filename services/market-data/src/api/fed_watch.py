"""T405-FEDWATCH — GET /fed-watch: market-implied odds of a Fed move at each upcoming FOMC.

Thin route layer. All arithmetic lives in services/fed_watch.py, which is pure and tested
directly; this file only gathers the two inputs (the FOMC calendar already in economic_events,
and 30-Day Fed Funds futures prices) and caches the result.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import structlog
import yfinance as yf
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from common.redis_client import get_redis
from db import get_session

from ..services.fed_watch import build_meeting_path, contract_symbol, implied_rate

log = structlog.get_logger()
router = APIRouter(prefix="/fed-watch", tags=["fed-watch"])

# Futures move all day but the derived probabilities move slowly; 15 min keeps the page live
# without one yfinance call per viewer. The whole response is a handful of quotes.
_CACHE_KEY = "stockai:fed_watch:v1"
_CACHE_TTL = 900
_MAX_MEETINGS = 6


def _fomc_meetings(session: Session, limit: int = _MAX_MEETINGS) -> list[date]:
    """Upcoming FOMC dates from the calendar this app already ingests (source
    `fed_calendar`) — not a hardcoded list that silently goes stale each year."""
    rows = session.execute(text("""
        SELECT event_date::date AS d FROM economic_events
        WHERE event_type = 'fomc_meeting' AND event_date::date >= CURRENT_DATE
        ORDER BY event_date LIMIT :lim
    """), {"lim": limit}).all()
    return [r.d for r in rows]


def _front_month_rate() -> float | None:
    """Today's market-implied EFFECTIVE fed funds rate, from the front-month contract.

    Deliberately NOT the official target range: ZQ settles on the effective rate, which trades
    a few bp inside the band. Reporting this as "the Fed's target" would be wrong by a few
    basis points every single day, so it is labelled as what it is.
    """
    try:
        h = yf.Ticker("ZQ=F").history(period="5d")
        if h.empty:
            return None
        return implied_rate(float(h["Close"].iloc[-1]))
    except Exception as exc:
        log.warning("fed_watch.front_month_failed", error=str(exc))
        return None


def _contract_prices(meetings: list[date]) -> dict[str, float]:
    """One quote per distinct contract month — never one per meeting, since two meetings can
    share a month and a duplicate fetch buys nothing."""
    from ..services.fed_watch import next_month
    # Both the meeting's own month AND the month after it: the preferred inference reads the
    # post-meeting rate straight off the FOLLOWING month's contract when that month has no
    # meeting of its own.
    wanted = {contract_symbol(m.year, m.month) for m in meetings}
    wanted |= {contract_symbol(*next_month(m.year, m.month)) for m in meetings}
    prices: dict[str, float] = {}
    for sym in sorted(wanted):
        try:
            h = yf.Ticker(sym).history(period="5d")
            if not h.empty:
                prices[sym] = round(float(h["Close"].iloc[-1]), 4)
        except Exception as exc:
            log.warning("fed_watch.contract_failed", contract=sym, error=str(exc))
    return prices


@router.get("")
def get_fed_watch(session: Session = Depends(get_session)):
    """Market-implied probability of a hike/cut at each upcoming FOMC meeting.

    Reached through the gateway, which authenticates every prefix except "auth"
    (_PUBLIC_PREFIXES). This response carries nothing user-specific and would be harmless to
    open up, but widening the gateway's public set is a security change and this feature does
    not need one — every viewer is signed in already.
    """
    try:
        rdb = get_redis()
        cached = rdb.get(_CACHE_KEY) if rdb else None
        if cached:
            return json.loads(cached)
    except Exception:
        rdb = None

    meetings = _fomc_meetings(session)
    if not meetings:
        return {"available": False, "reason": "no_fomc_calendar"}

    current = _front_month_rate()
    if current is None:
        return {"available": False, "reason": "no_futures_quote",
                "meetings": [m.isoformat() for m in meetings]}

    prices = _contract_prices(meetings)
    path = build_meeting_path(meetings=meetings, prices=prices, current_rate=current)

    result = {
        "available": True,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "current_implied_rate_pct": current,
        "source": "CBOT 30-Day Fed Funds futures (ZQ)",
        "meetings": path,
        # Carried in the payload, not only in the docs, so the caveats travel with the numbers
        # wherever they are rendered or copied.
        "limitations": [
            "These are MARKET EXPECTATIONS priced into futures, not a forecast by this app and "
            "not a Fed communication.",
            "ZQ settles on the EFFECTIVE fed funds rate, which trades a few basis points inside "
            "the target range — so the levels here are effective-rate expectations, not the "
            "target band itself.",
            "A meeting late in its month leaves few days to infer the post-meeting rate from; "
            "those are flagged low_precision.",
            "Futures carry a small risk premium, so implied probabilities lean slightly toward "
            "the direction of carry. CME's own FedWatch makes the same simplification.",
        ],
    }
    try:
        if rdb:
            rdb.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(result))
    except Exception:
        pass
    return result
