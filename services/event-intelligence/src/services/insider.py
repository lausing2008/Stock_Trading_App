"""Insider Trading — SEC EDGAR Form 4 ingestion."""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta, timezone

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import get_session, SessionLocal, InsiderTransaction, Stock

log = structlog.get_logger()

_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_BROWSE = "https://www.sec.gov/cgi-bin/browse-edgar"
_HEADERS = {"User-Agent": "StockAI/1.0 contact@lausing.com", "Accept-Encoding": "gzip"}

_ROLE_WEIGHTS = {
    "ceo": 30, "chief executive": 30,
    "cfo": 20, "chief financial": 20,
    "president": 20, "coo": 18,
    "director": 10,
    "10%": 15, "owner": 12,
}

_TRANSACTION_CODES = {
    "P": "purchase",
    "S": "sale",
    "A": "award",
    "D": "disposition",
    "G": "gift",
    "F": "tax_withholding",
    "M": "option_exercise",
    "X": "option_expire",
}


async def _fetch_form4_filings(client: httpx.AsyncClient, ticker: str, days: int = 90) -> list[dict]:
    """Search SEC EDGAR for recent Form 4 filings for a ticker."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        r = await client.get(
            _EDGAR_BROWSE,
            params={
                "action": "getcompany",
                "company": ticker,
                "type": "4",
                "dateb": "",
                "owner": "include",
                "count": "40",
                "search_text": "",
                "output": "atom",
            },
            headers=_HEADERS,
            timeout=10.0,
        )
        if r.status_code != 200:
            return []

        content = r.text
        # Extract accession numbers from Atom feed
        accessions = re.findall(r"Accession-Number: (\d{10}-\d{2}-\d{6})", content)
        return [{"accession": acc} for acc in accessions[:20]]
    except Exception as exc:
        log.debug("insider.fetch_fail", ticker=ticker, error=str(exc))
        return []


async def _parse_form4(client: httpx.AsyncClient, accession: str) -> dict | None:
    """Download and parse a Form 4 XML filing."""
    acc_fmt = accession.replace("-", "")
    # Accession number format: {filer_cik_10digit}-{YY}-{sequence}
    # First segment is the 10-digit zero-padded filer CIK — strip leading zeros.
    entity_cik = str(int(accession.split("-")[0]))
    url = f"https://www.sec.gov/Archives/edgar/data/{entity_cik}/{acc_fmt}/{accession}-index.htm"
    try:
        r = await client.get(url, headers=_HEADERS, timeout=10.0)
        if r.status_code != 200:
            return None
        # Find the XML document link
        xml_links = re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', r.text)
        if not xml_links:
            return None
        xml_url = f"https://www.sec.gov{xml_links[0]}"
        xr = await client.get(xml_url, headers=_HEADERS, timeout=10.0)
        if xr.status_code != 200:
            return None
        return _extract_form4_data(xr.text, accession)
    except Exception as exc:
        log.debug("insider.parse_fail", accession=accession, error=str(exc))
        return None


def _extract_form4_data(xml: str, accession: str) -> dict | None:
    """Extract key fields from Form 4 XML."""
    def _tag(tag: str) -> str | None:
        m = re.search(rf"<{tag}[^>]*>([^<]+)</{tag}>", xml, re.IGNORECASE)
        return m.group(1).strip() if m else None

    insider_name = _tag("rptOwnerName") or _tag("reportingOwnerName")
    role_raw = _tag("officerTitle") or _tag("isDirector") or ""
    txn_code = _tag("transactionCode") or ""
    # Do NOT fall back to sharesOwnedFollowingTransaction — that is the insider's total
    # post-trade position (e.g. 500,000 shares), not the number of shares transacted.
    shares_str = _tag("transactionShares") or "0"
    price_str = _tag("transactionPricePerShare") or "0"
    date_str = _tag("transactionDate") or _tag("periodOfReport")

    if not insider_name or not date_str:
        return None

    try:
        txn_date = date.fromisoformat(date_str[:10])
        shares = int(float(re.sub(r"[^\d.]", "", shares_str or "0") or "0"))
        price = float(re.sub(r"[^\d.]", "", price_str or "0") or "0")
    except Exception:
        return None

    txn_type = _TRANSACTION_CODES.get(txn_code.upper(), "other")
    role = _normalize_role(role_raw)

    return {
        "accession": accession,
        "insider_name": insider_name,
        "insider_role": role,
        "transaction_type": txn_type,
        "shares": shares,
        "price_per_share": price if price > 0 else None,
        "total_value": shares * price if price > 0 else None,
        "transaction_date": txn_date,
        "filing_date": txn_date,  # approximate — actual filing date from index
    }


def _normalize_role(raw: str) -> str:
    if not raw:
        return "Officer"
    raw_lower = raw.lower()
    for key, _ in _ROLE_WEIGHTS.items():
        if key in raw_lower:
            return raw.strip()[:64]
    return raw.strip()[:64]


async def sync_insider_for_symbol(ticker: str, stock_id: int, days: int = 90) -> int:
    """Fetch Form 4 filings for a single ticker and upsert to DB. Returns rows inserted."""
    upserted = 0
    async with httpx.AsyncClient() as client:
        filings = await _fetch_form4_filings(client, ticker, days)
        for filing in filings:
            await asyncio.sleep(0.12)  # SEC rate limit: 10/sec
            data = await _parse_form4(client, filing["accession"])
            if not data:
                continue
            if data["transaction_type"] not in ("purchase", "sale"):
                continue
            with SessionLocal() as s:
                stmt = (
                    pg_insert(InsiderTransaction)
                    .values(
                        stock_id=stock_id,
                        insider_name=data["insider_name"],
                        insider_role=data["insider_role"],
                        transaction_type=data["transaction_type"],
                        shares=data["shares"],
                        price_per_share=data["price_per_share"],
                        total_value=data["total_value"],
                        transaction_date=data["transaction_date"],
                        filing_date=data["filing_date"],
                        accession_number=data["accession"],
                    )
                    .on_conflict_do_nothing(constraint="uq_insider_accession")
                )
                result = s.execute(stmt)
                upserted += result.rowcount
                s.commit()
    return upserted


async def sync_all_insider(days: int = 90) -> dict:
    """Sync insider transactions for all tracked stocks."""
    with SessionLocal() as s:
        stocks = s.execute(select(Stock.id, Stock.symbol)).all()

    total = 0
    for stock_id, symbol in stocks:
        n = await sync_insider_for_symbol(symbol, stock_id, days)
        total += n
        await asyncio.sleep(0.5)

    return {"symbols_processed": len(stocks), "rows_upserted": total}


def get_insider_for_symbol(stock_id: int, days: int = 90) -> list[dict]:
    since = date.today() - timedelta(days=days)
    with SessionLocal() as s:
        rows = s.execute(
            select(InsiderTransaction)
            .where(InsiderTransaction.stock_id == stock_id, InsiderTransaction.transaction_date >= since)
            .order_by(InsiderTransaction.transaction_date.desc())
        ).scalars().all()
        return [_txn_to_dict(t) for t in rows]


def get_insider_leaderboard(days: int = 30, limit: int = 20) -> list[dict]:
    """Stocks with most net insider buying in last N days."""
    since = date.today() - timedelta(days=days)
    with SessionLocal() as s:
        result: dict[int, dict] = {}
        all_txns = s.execute(
            select(InsiderTransaction, Stock.symbol, Stock.name)
            .join(Stock, InsiderTransaction.stock_id == Stock.id)
            .where(InsiderTransaction.transaction_date >= since)
            .order_by(InsiderTransaction.transaction_date.desc())
        ).all()
        for txn, symbol, name in all_txns:
            sid = txn.stock_id
            if sid not in result:
                result[sid] = {"stock_id": sid, "symbol": symbol, "company": name, "purchases": 0, "sales": 0, "net_value": 0.0}
            if txn.transaction_type == "purchase":
                result[sid]["purchases"] += 1
                result[sid]["net_value"] += txn.total_value or 0
            elif txn.transaction_type == "sale":
                result[sid]["sales"] += 1
                result[sid]["net_value"] -= txn.total_value or 0

        sorted_results = sorted(result.values(), key=lambda x: x["net_value"], reverse=True)
        return sorted_results[:limit]


def compute_insider_score(stock_id: int, days: int = 90) -> float:
    """0-100 insider score (negative = net selling)."""
    txns = get_insider_for_symbol(stock_id, days)
    if not txns:
        return 0.0

    score = 0.0
    purchase_count = 0
    for t in txns:
        role = (t.get("insider_role") or "").lower()
        weight = 8.0
        for key, w in _ROLE_WEIGHTS.items():
            if key in role:
                weight = w
                break
        if t["transaction_type"] == "purchase":
            score += weight
            purchase_count += 1
        elif t["transaction_type"] == "sale":
            score -= weight * 0.4

    # Cluster bonus: 3+ insiders buying
    if purchase_count >= 3:
        score *= 1.25
    return max(-100.0, min(100.0, score))


def _txn_to_dict(t: InsiderTransaction) -> dict:
    return {
        "id": t.id,
        "insider_name": t.insider_name,
        "insider_role": t.insider_role,
        "transaction_type": t.transaction_type,
        "shares": t.shares,
        "price_per_share": t.price_per_share,
        "total_value": t.total_value,
        "transaction_date": t.transaction_date.isoformat(),
        "filing_date": t.filing_date.isoformat(),
    }
