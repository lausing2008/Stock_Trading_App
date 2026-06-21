"""Institutional Intelligence — SEC EDGAR 13F-HR filings (quarterly)."""
from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import get_session, InstitutionalHolding, InstitutionalTransaction, Stock

log = structlog.get_logger()

_EDGAR_FULL_TEXT = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_HEADERS = {"User-Agent": "StockAI/1.0 contact@lausing.com"}

# Top funds to track (CIK numbers from SEC EDGAR)
_TRACKED_FUNDS = [
    ("Berkshire Hathaway", "0001067983"),
    ("ARK Investment Management", "0001697748"),
    ("Bridgewater Associates", "0001350694"),
    ("Pershing Square", "0001336528"),
    ("Renaissance Technologies", "0001037389"),
    ("Tiger Global Management", "0001167483"),
    ("Coatue Management", "0001336467"),
]

_NS = {"ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}


async def _get_latest_13f(client: httpx.AsyncClient, fund_cik: str) -> str | None:
    """Find the most recent 13F-HR accession number for a fund."""
    try:
        r = await client.get(
            f"https://data.sec.gov/submissions/CIK{fund_cik.zfill(10)}.json",
            headers=_HEADERS,
            timeout=10.0,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        filings = data.get("filings", {}).get("recent", {})
        forms = filings.get("form", [])
        accessions = filings.get("accessionNumber", [])
        for form, acc in zip(forms, accessions):
            if form in ("13F-HR", "13F-HR/A"):
                return acc
        return None
    except Exception as exc:
        log.debug("institutional.get_13f_fail", cik=fund_cik, error=str(exc))
        return None


async def _parse_13f_holdings(client: httpx.AsyncClient, fund_cik: str, accession: str) -> list[dict]:
    """Parse holdings from 13F XML filing."""
    acc_fmt = accession.replace("-", "")
    idx_url = f"https://www.sec.gov/Archives/edgar/data/{int(fund_cik)}/{acc_fmt}/{accession}-index.htm"
    try:
        r = await client.get(idx_url, headers=_HEADERS, timeout=10.0)
        if r.status_code != 200:
            return []
        # Find the informationtable.xml link
        xml_links = re.findall(r'href="(/Archives/edgar/data/[^"]+informationtable[^"]*\.xml)"', r.text, re.IGNORECASE)
        if not xml_links:
            return []
        xml_url = f"https://www.sec.gov{xml_links[0]}"
        xr = await client.get(xml_url, headers=_HEADERS, timeout=15.0)
        if xr.status_code != 200:
            return []

        root = ET.fromstring(xr.text)
        holdings = []
        for info in root.findall(".//ns:infoTable", _NS) or root.findall(".//infoTable"):
            def t(tag: str) -> str | None:
                el = info.find(f"ns:{tag}", _NS) or info.find(tag)
                return el.text.strip() if el is not None and el.text else None

            name = t("nameOfIssuer")
            cusip = t("cusip")
            shares_str = t("sshPrnamt") or t("value")
            value_str = t("value")
            if not name:
                continue
            holdings.append({
                "name": name,
                "cusip": cusip,
                "shares": int(shares_str.replace(",", "")) if shares_str and shares_str.isdigit() else None,
                "value_usd": float(value_str.replace(",", "")) * 1000 if value_str else None,
            })
        return holdings
    except Exception as exc:
        log.debug("institutional.parse_fail", cik=fund_cik, error=str(exc))
        return []


async def sync_institutional() -> dict:
    """Sync latest 13F holdings for all tracked funds."""
    # Build CUSIP → stock_id lookup (approximate — we match by symbol name)
    with get_session() as s:
        stocks = {sym.upper(): sid for sid, sym in s.execute(select(Stock.id, Stock.symbol)).all()}

    total_holdings = 0
    period_date = date.today().replace(day=1)  # approximate period

    async with httpx.AsyncClient() as client:
        for fund_name, fund_cik in _TRACKED_FUNDS:
            await asyncio.sleep(0.12)
            accession = await _get_latest_13f(client, fund_cik)
            if not accession:
                continue
            await asyncio.sleep(0.12)
            holdings = await _parse_13f_holdings(client, fund_cik, accession)

            with get_session() as s:
                for h in holdings:
                    # Match by name approximation
                    stock_id = None
                    name_upper = (h["name"] or "").upper()
                    for sym, sid in stocks.items():
                        if sym in name_upper or name_upper.startswith(sym[:4]):
                            stock_id = sid
                            break
                    if stock_id is None:
                        continue

                    stmt = (
                        pg_insert(InstitutionalHolding)
                        .values(
                            fund_name=fund_name,
                            fund_cik=fund_cik,
                            stock_id=stock_id,
                            period_date=period_date,
                            shares=h["shares"],
                            value_usd=h["value_usd"],
                        )
                        .on_conflict_do_update(
                            constraint="uq_inst_holding",
                            set_=dict(shares=h["shares"], value_usd=h["value_usd"]),
                        )
                    )
                    result = s.execute(stmt)
                    total_holdings += result.rowcount
                s.commit()

    return {"funds_processed": len(_TRACKED_FUNDS), "holdings_upserted": total_holdings}


def get_institutional_for_symbol(stock_id: int) -> list[dict]:
    with get_session() as s:
        rows = s.execute(
            select(InstitutionalHolding)
            .where(InstitutionalHolding.stock_id == stock_id)
            .order_by(InstitutionalHolding.period_date.desc(), InstitutionalHolding.value_usd.desc())
        ).scalars().all()
        return [
            {
                "fund_name": h.fund_name,
                "period_date": h.period_date.isoformat(),
                "shares": h.shares,
                "value_usd": h.value_usd,
            }
            for h in rows
        ]


def compute_institutional_score(stock_id: int) -> float:
    """0-100 institutional score based on number and size of fund positions."""
    holdings = get_institutional_for_symbol(stock_id)
    if not holdings:
        return 0.0
    # Score by number of top funds holding + total value
    num_funds = len(holdings)
    total_value = sum(h["value_usd"] or 0 for h in holdings)
    score = min(num_funds * 15, 60)  # up to 60 from fund count
    if total_value > 1_000_000_000:
        score += 40
    elif total_value > 500_000_000:
        score += 25
    elif total_value > 100_000_000:
        score += 15
    elif total_value > 10_000_000:
        score += 5
    return min(100.0, score)


def get_institutional_leaderboard(limit: int = 20) -> list[dict]:
    with get_session() as s:
        all_rows = s.execute(
            select(InstitutionalHolding, Stock.symbol, Stock.name)
            .join(Stock, InstitutionalHolding.stock_id == Stock.id)
            .order_by(InstitutionalHolding.value_usd.desc())
        ).all()

    result: dict[int, dict] = {}
    for h, symbol, name in all_rows:
        sid = h.stock_id
        if sid not in result:
            result[sid] = {
                "stock_id": sid, "symbol": symbol, "company": name,
                "funds": 0, "total_value_usd": 0.0, "fund_names": [],
            }
        result[sid]["funds"] += 1
        result[sid]["total_value_usd"] += h.value_usd or 0
        result[sid]["fund_names"].append(h.fund_name)

    sorted_result = sorted(result.values(), key=lambda x: x["total_value_usd"], reverse=True)
    return sorted_result[:limit]
