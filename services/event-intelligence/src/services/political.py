"""Political Intelligence — USASpending.gov government contract awards."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import get_session, PoliticalEvent, Stock

log = structlog.get_logger()

_USASPENDING_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

# Sectors and their representative tickers for contract award tracking
_DEFENSE_TICKERS = {"LMT", "RTX", "NOC", "GD", "BA", "L3H", "HII", "TDG", "KTOS", "PLTR"}
_HEALTH_TICKERS = {"UNH", "CVS", "CI", "HUM", "CNC", "MOH", "ELV", "MCK", "ABC", "CAH"}
_TECH_TICKERS = {"MSFT", "AMZN", "GOOGL", "IBM", "SAIC", "LEIDOS", "CACI", "BOOZ"}


async def sync_political_contracts(days: int = 30) -> dict:
    """Fetch recent government contract awards from USASpending.gov."""
    today = date.today()
    since = today - timedelta(days=days)

    with get_session() as s:
        ticker_map = {sym.upper(): sid for sid, sym in s.execute(select(Stock.id, Stock.symbol)).all()}

    total = 0
    all_defense_tickers = _DEFENSE_TICKERS | _TECH_TICKERS | _HEALTH_TICKERS
    our_tickers = {t: ticker_map.get(t) for t in all_defense_tickers if t in ticker_map}

    async with httpx.AsyncClient(timeout=20.0) as client:
        for ticker, stock_id in our_tickers.items():
            try:
                payload = {
                    "filters": {
                        "time_period": [{"start_date": since.isoformat(), "end_date": today.isoformat()}],
                        "recipient_search_text": [ticker],
                        "award_type_codes": ["A", "B", "C", "D"],  # contracts
                    },
                    "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Start Date", "Description"],
                    "page": 1,
                    "limit": 20,
                    "sort": "Award Amount",
                    "order": "desc",
                }
                r = await client.post(_USASPENDING_URL, json=payload)
                if r.status_code != 200:
                    continue
                results = r.json().get("results", [])
                with get_session() as s:
                    for award in results:
                        amount = award.get("Award Amount") or 0
                        if amount < 1_000_000:  # only track awards > $1M
                            continue
                        try:
                            award_date_str = award.get("Start Date") or today.isoformat()
                            award_date = date.fromisoformat(award_date_str[:10])
                        except Exception:
                            award_date = today
                        title = f"{ticker} — {award.get('Awarding Agency', 'Federal Agency')} Contract ${amount/1e6:.1f}M"
                        s.add(PoliticalEvent(
                            stock_id=stock_id,
                            event_type="contract_award",
                            title=title[:512],
                            description=award.get("Description") or "",
                            amount_usd=float(amount),
                            agency=award.get("Awarding Agency") or "",
                            event_date=award_date,
                            impact="positive",
                            source="usaspending",
                            source_url=f"https://www.usaspending.gov/award/{award.get('Award ID', '')}",
                        ))
                        total += 1
                    s.commit()
                await asyncio.sleep(0.5)
            except Exception as exc:
                log.warning("political.contract_fail", ticker=ticker, error=str(exc))

    return {"contracts_stored": total}


def get_political_events(days: int = 30, stock_id: int | None = None) -> list[dict]:
    since = date.today() - timedelta(days=days)
    with get_session() as s:
        q = select(PoliticalEvent).where(PoliticalEvent.event_date >= since)
        if stock_id is not None:
            q = q.where(PoliticalEvent.stock_id == stock_id)
        rows = s.execute(q.order_by(PoliticalEvent.event_date.desc()).limit(100)).scalars().all()
        return [
            {
                "id": e.id,
                "stock_id": e.stock_id,
                "event_type": e.event_type,
                "title": e.title,
                "amount_usd": e.amount_usd,
                "agency": e.agency,
                "event_date": e.event_date.isoformat(),
                "impact": e.impact,
                "source": e.source,
                "source_url": e.source_url,
            }
            for e in rows
        ]
