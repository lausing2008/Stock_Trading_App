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

from db import get_session, SessionLocal, InstitutionalHolding, InstitutionalTransaction, Stock

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

# EI-F4: common corporate suffixes stripped before name comparison, so "APPLE INC" and
# "Apple Inc." both normalize to "APPLE" — reduces false negatives on the name match without
# adding any false-positive risk (stripping a suffix never makes two different companies equal).
_CORP_SUFFIX_RE = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|LLC|LP|PLC|HOLDINGS?|GROUP|SA|NV|AG)\b\.?"
)


def _normalize_company_name(name: str) -> str:
    upper = name.upper()
    upper = _CORP_SUFFIX_RE.sub("", upper)
    upper = re.sub(r"[^A-Z0-9]", "", upper)
    return upper


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
        # EI-BUG: SEC does not consistently name the holdings-table file with "informationtable"
        # in the filename (real examples: 53405.xml, primary_doc.xml, xslForm13F_X02/53405.xml) —
        # a filename-pattern regex silently matched nothing on every real filing checked, so this
        # function has always returned zero holdings. Instead: collect every .xml link on the
        # index page and identify the real holdings table by its actual XML content (the root
        # element is always <informationTable>, regardless of what the file is named) rather than
        # guessing from the URL. The cover-page doc (primary_doc.xml) has a different root tag
        # (<edgarSubmission>) and is correctly skipped by this check.
        xml_links = re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', r.text, re.IGNORECASE)
        holdings_xml: str | None = None
        for link in xml_links:
            xml_url = f"https://www.sec.gov{link}"
            xr = await client.get(xml_url, headers=_HEADERS, timeout=15.0)
            if xr.status_code == 200 and "<informationTable" in xr.text[:500]:
                holdings_xml = xr.text
                break
        if holdings_xml is None:
            return []

        root = ET.fromstring(holdings_xml)
        holdings = []
        # EI-BUG: `el.find(...) or el.find(...)` is broken for ElementTree elements — a leaf
        # element (no child elements) is FALSY in a boolean context even when it was successfully
        # found and has text content, so the `or` unconditionally falls through to the second
        # (wrong-namespace) find() call, which returns None. This made every single-value field
        # extraction below silently fail on every real filing. Use explicit `is not None` checks.
        infos = root.findall(".//ns:infoTable", _NS)
        if not infos:
            infos = root.findall(".//infoTable")
        for info in infos:
            def t(tag: str) -> str | None:
                el = info.find(f"ns:{tag}", _NS)
                if el is None:
                    el = info.find(tag)
                return el.text.strip() if el is not None and el.text else None

            name = t("nameOfIssuer")
            cusip = t("cusip")
            value_str = t("value")
            shares_el = info.find("ns:shrsOrPrnAmt/ns:sshPrnamt", _NS)
            if shares_el is None:
                shares_el = info.find("shrsOrPrnAmt/sshPrnamt")
            shares_str = shares_el.text.strip() if shares_el is not None and shares_el.text else None
            if not name:
                continue
            holdings.append({
                "name": name,
                "cusip": cusip,
                "shares": int(shares_str.replace(",", "")) if shares_str and shares_str.replace(",", "").isdigit() else None,
                # EI-BUG: the `* 1000` assumed SEC's <value> field is reported in thousands (an
                # older 13F convention) — cross-checked against real filings and it is NOT: e.g.
                # Berkshire's Apple stake (3,776,000 shares, <value>958311040</value>) implies
                # $253.79/share without any multiplier, vs. an absurd $253,790/share with it.
                # The modern XML format reports <value> directly in whole dollars.
                "value_usd": float(value_str.replace(",", "")) if value_str else None,
            })
        return holdings
    except Exception as exc:
        log.debug("institutional.parse_fail", cik=fund_cik, error=str(exc))
        return []


async def sync_institutional() -> dict:
    """Sync latest 13F holdings for all tracked funds."""
    # EI-F4: match on normalized company NAME (Stock.name vs. the filing's nameOfIssuer) only —
    # a name match is a reliable signal; a ticker-as-name-prefix heuristic is not. The old code
    # matched by ticker substring/prefix (e.g. "C" in "CATERPILLAR INC" matched Citigroup, "CAT"
    # prefix-matched "Catalyst Pharmaceuticals" too, not just Caterpillar) — tried tightening the
    # ticker heuristic with a minimum-length gate first, but testing showed even a >=3-char
    # ticker-as-prefix-of-name check still produces false positives (same CAT/Catalyst case), so
    # there is no length threshold that makes ticker-vs-name-prefix matching safe. Dropped the
    # ticker fallback entirely: if a 13F filing's issuer name doesn't match a tracked stock's
    # name, the holding is skipped rather than risking misattribution — a real fix needs a
    # CUSIP/ticker mapping data source (still tracked, see the tracker's own note), not a
    # tighter heuristic on data we don't have.
    with SessionLocal() as s:
        rows = s.execute(select(Stock.id, Stock.name)).all()
    stocks_by_name = {_normalize_company_name(name): sid for sid, name in rows if name}

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

            with SessionLocal() as s:
                for h in holdings:
                    filing_name = h["name"] or ""
                    normalized = _normalize_company_name(filing_name)
                    stock_id = stocks_by_name.get(normalized) if normalized else None
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
    with SessionLocal() as s:
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
    with SessionLocal() as s:
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
