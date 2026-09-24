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


async def _get_latest_13f(client: httpx.AsyncClient, fund_cik: str) -> tuple[str, date] | None:
    """Find the most recent 13F-HR accession number and its real reporting period-end for a
    fund. Returns (accession, report_date) or None."""
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
        report_dates = filings.get("reportDate", [])
        for form, acc, rdate in zip(forms, accessions, report_dates):
            if form in ("13F-HR", "13F-HR/A"):
                return acc, date.fromisoformat(rdate)
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

    async with httpx.AsyncClient() as client:
        for fund_name, fund_cik in _TRACKED_FUNDS:
            await asyncio.sleep(0.12)
            # EI-BUG: period_date was `date.today().replace(day=1)` — the sync's RUN date, not
            # the filing's actual reporting period. 13F filings are quarterly (this module's own
            # docstring says so) and report a `reportDate` field (the real period-end, e.g.
            # 2026-03-31) via the same submissions.json endpoint already being queried here. Using
            # today's month-start meant period_date stayed frozen at the sync's first-ever run
            # date for the rest of that calendar month regardless of the real filing period, and
            # (since period_date is part of uq_inst_holding's conflict key) every later run that
            # month just updated the same rows in place — MAX(period_date) never advanced until
            # the calendar flipped months, masquerading as "sync is stale" when it was running fine.
            latest = await _get_latest_13f(client, fund_cik)
            if not latest:
                continue
            accession, period_date = latest
            await asyncio.sleep(0.12)
            holdings = await _parse_13f_holdings(client, fund_cik, accession)

            with SessionLocal() as s:
                # T237-INST-TXN-NEVER-WRITTEN: capture the PREVIOUS period's holdings for this
                # fund BEFORE upserting the new period's rows — needed to diff against below.
                # Uses stock_id as the diff key (not cusip/name) since that's what
                # InstitutionalHolding itself is keyed on.
                previous_period_row = s.execute(
                    select(InstitutionalHolding.period_date)
                    .where(
                        InstitutionalHolding.fund_cik == fund_cik,
                        InstitutionalHolding.period_date < period_date,
                    )
                    .order_by(InstitutionalHolding.period_date.desc())
                    .limit(1)
                ).scalar_one_or_none()
                previous_holdings_by_stock: dict[int, InstitutionalHolding] = {}
                if previous_period_row is not None:
                    prev_rows = s.execute(
                        select(InstitutionalHolding).where(
                            InstitutionalHolding.fund_cik == fund_cik,
                            InstitutionalHolding.period_date == previous_period_row,
                        )
                    ).scalars().all()
                    previous_holdings_by_stock = {h.stock_id: h for h in prev_rows}

                current_holdings_by_stock: dict[int, dict] = {}
                for h in holdings:
                    filing_name = h["name"] or ""
                    normalized = _normalize_company_name(filing_name)
                    stock_id = stocks_by_name.get(normalized) if normalized else None
                    if stock_id is None:
                        continue
                    current_holdings_by_stock[stock_id] = h

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

                _write_institutional_transactions(
                    s, fund_name, fund_cik, period_date,
                    previous_holdings_by_stock, current_holdings_by_stock,
                )
                s.commit()

    return {"funds_processed": len(_TRACKED_FUNDS), "holdings_upserted": total_holdings}


def _write_institutional_transactions(
    session,
    fund_name: str,
    fund_cik: str,
    period_date: date,
    previous_holdings_by_stock: dict[int, "InstitutionalHolding"],
    current_holdings_by_stock: dict[int, dict],
) -> int:
    """T237-INST-TXN-NEVER-WRITTEN: compare this period's holdings against the fund's most
    recent PRIOR period's holdings and write one InstitutionalTransaction row per real change —
    a new position ("initiate"), a fully-closed position ("exit"), or a share-count change on an
    existing position ("add"/"trim"). Unchanged positions (identical share count) are skipped —
    a real transaction table should only record real changes, not every quarter's re-affirmation
    of an unchanged position. Returns the number of transaction rows written.
    """
    written = 0
    all_stock_ids = set(previous_holdings_by_stock) | set(current_holdings_by_stock)

    for stock_id in all_stock_ids:
        prev = previous_holdings_by_stock.get(stock_id)
        curr = current_holdings_by_stock.get(stock_id)
        prev_shares = prev.shares if prev is not None else None
        prev_value = prev.value_usd if prev is not None else None
        diff = _diff_holding(
            prev_shares=prev_shares, prev_value=prev_value,
            curr_shares=curr["shares"] if curr is not None else None,
            curr_value=curr["value_usd"] if curr is not None else None,
            had_previous=prev is not None, has_current=curr is not None,
        )
        if diff is None:
            continue  # no real change to record (or shares unknown on either side)
        change_type, shares_change, value_change = diff

        stmt = (
            pg_insert(InstitutionalTransaction)
            .values(
                fund_name=fund_name,
                fund_cik=fund_cik,
                stock_id=stock_id,
                period_date=period_date,
                change_type=change_type,
                shares_change=shares_change,
                value_change_usd=value_change,
            )
            .on_conflict_do_update(
                constraint="uq_inst_txn",
                set_=dict(change_type=change_type, shares_change=shares_change, value_change_usd=value_change),
            )
        )
        result = session.execute(stmt)
        written += result.rowcount

    return written


def _diff_holding(
    prev_shares: int | None,
    prev_value: float | None,
    curr_shares: int | None,
    curr_value: float | None,
    had_previous: bool,
    has_current: bool,
) -> tuple[str, int, float | None] | None:
    """Pure diff logic for one (fund, stock) pair across two periods — factored out of
    _write_institutional_transactions() so it's directly unit-testable without a DB session.
    Returns (change_type, shares_change, value_change) or None if there's nothing to record.

    shares_change/value_change are always (new - old), treating an absent side as 0 —
    "initiate" naturally yields the full new position, "exit" naturally yields the negative of
    the full old position, "add"/"trim" yield the real delta. Uses explicit `is not None`
    checks throughout, NOT `or 0` — a genuine 0-share/0-value holding is a real value, not an
    absent one, and `or 0` would silently coerce it the same way the T237-EI2 None-vs-falsy bug
    did earlier this session.
    """
    if had_previous and has_current:
        if prev_shares is None or curr_shares is None or prev_shares == curr_shares:
            return None
        change_type = "add" if curr_shares > prev_shares else "trim"
    elif has_current and not had_previous:
        change_type = "initiate"
    elif had_previous and not has_current:
        change_type = "exit"
    else:
        return None  # neither previous nor current — nothing to diff

    shares_change = (curr_shares if curr_shares is not None else 0) - (prev_shares if prev_shares is not None else 0)
    value_change = (
        (curr_value if curr_value is not None else 0.0) - (prev_value if prev_value is not None else 0.0)
        if (prev_value is not None or curr_value is not None) else None
    )
    return change_type, shares_change, value_change


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


# ── AUD-INSTFOLLOW: "who actually moved, and could you have followed them?" ────
#
# Distinct from the EDGAR path above, which scrapes 13F XML and name-matches issuers against our
# own Stock table — that is why it holds 4 Berkshire positions when Berkshire reports ~40. UW's
# /api/institution/{name}/activity returns the ticker directly, so no name matching is needed,
# and it carries `units_change` (the signed position delta) plus BOTH dates and the price at
# each. This function does not touch the EDGAR tables; it is a read-side report.
#
# THE ONE THING THIS REPORT MUST NOT LET A READER BELIEVE: that 13F is tradeable. It is a
# quarter-END SNAPSHOT, filed up to 45 days later. It shows no intra-quarter round trips, no
# shorts, and no options unless separately reported. By the time you read it the manager may
# have exited entirely. So every row carries its own report_date, filing_date and staleness, and
# the return is measured from the FILING date — the first moment the position was public.
#
# `already_moved_pct` exists for the same reason the congress report quotes trade-vs-disclosure:
# it is the average move between the quarter-end the position reflects and the day it became
# public. That is edge that was gone before anyone outside could act on it, and stating it is
# the difference between a report and an advertisement.

_INST_HORIZON_BARS = 21
_INST_MIN_POSITIONS = 8
_INST_CACHE_TTL = 21600  # 6h; 13F changes quarterly, nothing here moves intraday.

# Curated rather than "top N by AUM": the largest 13F filers are index complexes (BlackRock,
# Vanguard, State Street) whose holdings reflect fund flows, not a view. Every name below is a
# discretionary or systematic manager whose position changes represent a decision. UW's exact
# name string is the API key, so it is stored verbatim next to the display name.
_TRACKED_INSTITUTIONS: list[tuple[str, str]] = [
    ("Berkshire Hathaway (Buffett)", "BERKSHIRE HATHAWAY INC"),
    ("Pershing Square (Ackman)", "PERSHING SQUARE CAPITAL MANAGEMENT, L.P."),
    ("Citadel (Griffin)", "CITADEL ADVISORS LLC"),
    ("Point72 (Cohen)", "POINT72 ASSET MANAGEMENT, L.P."),
    ("Bridgewater (Dalio)", "BRIDGEWATER ASSOCIATES, LP"),
    ("Renaissance Technologies", "RENAISSANCE TECHNOLOGIES LLC"),
    ("Tiger Global", "TIGER GLOBAL MANAGEMENT LLC"),
    ("ARK Invest (Wood)", "ARK INVESTMENT MANAGEMENT LLC"),
    ("Two Sigma", "TWO SIGMA INVESTMENTS, LP"),
    ("Millennium", "MILLENNIUM MANAGEMENT LLC"),
    ("AQR Capital", "AQR CAPITAL MANAGEMENT LLC"),
    ("Coatue", "COATUE MANAGEMENT LLC"),
    ("Soros Fund Management", "SOROS FUND MANAGEMENT LLC"),
    ("Appaloosa (Tepper)", "APPALOOSA LP"),
    ("Duquesne (Druckenmiller)", "DUQUESNE FAMILY OFFICE LLC"),
    ("Scion (Burry)", "SCION ASSET MANAGEMENT, LLC"),
]


def _uw_institution_activity(uw_name: str, limit: int = 500) -> list[dict]:
    """One institution's reported position changes. Redis-cached 6h; fails open to []."""
    import json as _json
    import urllib.parse

    from common.ai_keys import get_unusual_whales_key, is_unusual_whales_enabled
    from common.redis_client import get_redis

    if not (is_unusual_whales_enabled() and get_unusual_whales_key()):
        return []
    cache_key = f"stockai:uw:inst_activity:{uw_name}"
    try:
        cached = get_redis().get(cache_key)
        if cached:
            return _json.loads(cached)
    except Exception:
        pass
    try:
        r = httpx.get(
            f"https://api.unusualwhales.com/api/institution/{urllib.parse.quote(uw_name)}/activity",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {get_unusual_whales_key()}",
                     "Accept": "application/json"},
            timeout=45,
        )
        if r.status_code != 200:
            log.warning("institutional.uw_activity_status", name=uw_name, status=r.status_code)
            return []
        body = r.json()
        rows = body.get("data") if isinstance(body, dict) else body
        rows = rows if isinstance(rows, list) else []
    except Exception as exc:
        log.warning("institutional.uw_activity_failed", name=uw_name, error=str(exc))
        return []
    try:
        get_redis().setex(cache_key, _INST_CACHE_TTL, _json.dumps(rows))
    except Exception:
        pass
    return rows


def _to_f(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def get_institutional_followers(min_positions: int = _INST_MIN_POSITIONS) -> dict:
    """Per-manager follow-return on newly-added/increased 13F positions, entered at FILING.

    Returns one row per tracked institution with its own report/filing dates and staleness, so a
    fund whose latest filing is a year old (UW's Scion data, for one) cannot be read as current.
    """
    from datetime import date as _date

    from sqlalchemy import text as _text

    today = _date.today()
    _bench_cache: dict[str, float | None] = {}
    with SessionLocal() as s:
        ticker_map: dict[str, int] = {
            sym.upper(): sid for sid, sym in s.execute(select(Stock.id, Stock.symbol)).all()
        }

        funds = []
        for display, uw_name in _TRACKED_INSTITUTIONS:
            rows = _uw_institution_activity(uw_name)
            if not rows:
                # Built from the same key template as a measured row. Hand-writing this dict is
                # how it previously shipped without `alpha_vs_spy_pct`, and since the sort reads
                # that key unconditionally, ONE unreachable fund raised KeyError and took the
                # whole 16-fund report down with it.
                funds.append(_fund_row(display, unavailable=True))
                continue

            # One filing at a time: a fund's rows can span quarters, and mixing them would
            # average returns entered on different dates into a single meaningless figure.
            latest_filing = max((r.get("filing_date") or "") for r in rows)
            cur = [r for r in rows if (r.get("filing_date") or "") == latest_filing]
            report_date = max((r.get("report_date") or "") for r in cur) or None

            # UW returns one row per security line, so a ticker recurs (share classes, or the
            # same name listed twice). Left as-is it inflates the position count — Citadel read
            # as 300 buys — and lets one holding be measured repeatedly. First row per ticker
            # wins; they carry identical prices anyway.
            def _dedupe(rows_in: list[dict]) -> list[dict]:
                seen: set[str] = set()
                out_rows = []
                for r in rows_in:
                    t = (r.get("ticker") or "").upper()
                    if not t or t in seen:
                        continue
                    seen.add(t)
                    out_rows.append(r)
                return out_rows

            buys = _dedupe([r for r in cur if (_to_f(r.get("units_change")) or 0) > 0])
            sells = _dedupe([r for r in cur if (_to_f(r.get("units_change")) or 0) < 0])

            # NOT REPORTED: an "already moved between quarter-end and filing" figure. UW
            # populates price_on_report and price_on_filing IDENTICALLY on every row checked
            # (278 of 278 Citadel buys), so the computed value is structurally 0.00% for every
            # fund. Publishing that would assert the disclosure lag costs nothing — a stronger
            # and more wrong claim than omitting it. The lag is still real; we simply have no
            # honest measurement of its cost from this feed.

            pairs = [
                (ticker_map[t], latest_filing) for r in buys
                if (t := (r.get("ticker") or "").upper()) in ticker_map
            ]
            avg_pct = pct_up = alpha_pct = None
            n_measured = 0
            if pairs:
                res = s.execute(_text("""
                    WITH picks AS (
                      SELECT * FROM unnest(CAST(:sids AS int[]), CAST(:fdates AS date[]))
                                AS t(stock_id, filing_date)
                    ),
                    px AS (
                      SELECT stock_id, ts::date AS d, close,
                             LEAD(close, :horizon) OVER (PARTITION BY stock_id ORDER BY ts) AS fwd
                      FROM prices
                      WHERE timeframe = 'D1'
                        AND stock_id IN (SELECT stock_id FROM picks)
                    ),
                    e AS (
                      SELECT p.stock_id,
                        (SELECT x.close FROM px x WHERE x.stock_id=p.stock_id AND x.d >= p.filing_date
                          ORDER BY x.d LIMIT 1) AS entry,
                        (SELECT x.fwd FROM px x WHERE x.stock_id=p.stock_id AND x.d >= p.filing_date
                          ORDER BY x.d LIMIT 1) AS exit
                      FROM picks p
                    )
                    SELECT count(*) n,
                           avg(100.0*(exit-entry)/entry) avg_pct,
                           100.0*count(*) FILTER (WHERE exit > entry)/NULLIF(count(*),0) pct_up
                    FROM e WHERE entry IS NOT NULL AND exit IS NOT NULL AND entry > 0
                """), {
                    "sids": [p[0] for p in pairs],
                    "fdates": [p[1] for p in pairs],
                    "horizon": _INST_HORIZON_BARS,
                }).one()
                n_measured = res.n or 0
                if n_measured:
                    avg_pct = round(float(res.avg_pct), 2)
                    pct_up = round(float(res.pct_up), 0)
                    # AUD-ALPHAEVAL's lesson, applied here: a raw return over a window in which
                    # the market fell 2.44% says almost nothing about the manager. Every fund in
                    # the first run of this report was "negative" purely because the window was.
                    bench = _bench_return_21d(s, latest_filing, _bench_cache)
                    if bench is not None:
                        alpha_pct = round(avg_pct - bench, 2)

            fdate = _date.fromisoformat(latest_filing) if latest_filing else None
            rdate = _date.fromisoformat(report_date) if report_date else None
            funds.append(_fund_row(
                display,
                n_buys=len(buys),
                n_sells=len(sells),
                n_measured=n_measured,
                avg_21d_pct=avg_pct,
                alpha_vs_spy_pct=alpha_pct,
                benchmark_21d_pct=_bench_cache.get(latest_filing),
                pct_up=pct_up,
                report_date=report_date or None,
                filing_date=latest_filing or None,
                disclosure_lag_days=(fdate - rdate).days if fdate and rdate else None,
                staleness_days=(today - rdate).days if rdate else None,
                # Measured positions, not reported ones: a fund can report 40 buys of which we
                # price only 3. NOTE this means "enough positions to average over", NOT
                # "enough evidence to judge the manager" — every row here is a single quarter
                # observed over a single window, and no position count fixes that.
                sample_is_adequate=n_measured >= min_positions,
            ))

    # Ranked on ALPHA, since that is the column that means something.
    ranked = sorted(
        funds,
        key=lambda f: (f["alpha_vs_spy_pct"] is None, -(f["alpha_vs_spy_pct"] or 0)),
    )
    return {
        "horizon_days": _INST_HORIZON_BARS,
        "min_positions_for_adequacy": min_positions,
        "entry_basis": "filing_date",
        "funds": ranked,
        "n_followable": sum(1 for f in ranked if f["sample_is_adequate"]),
        "caveats": [
            "A 13F is a quarter-END SNAPSHOT filed up to 45 days later — not a trade feed. It "
            "shows no intra-quarter round trips, no short positions, and no options unless "
            "separately reported. The manager may have exited before you ever saw it.",
            "Returns are entered at the FILING date, the first moment the position was public. "
            "Entering at the quarter-end the filing describes would be unreachable by anyone.",
            "Returns are benchmark-relative: the market's own move over the identical window "
            "is subtracted, because a quarter in which everything fell is not a manager being "
            "wrong. The raw return is shown alongside so both are visible.",
            "Staleness is per fund and varies enormously. Check each row's own dates rather "
            "than assuming the table is current.",
            "Only positions on stocks this platform prices are measured, so n_measured is "
            "usually far below the fund's reported position count.",
            "THIS IS ONE QUARTER OVER ONE 21-DAY WINDOW, NOT A TRACK RECORD. Every fund here "
            "shares essentially the same window, so a single market episode drives much of the "
            "spread between them. It says how the latest disclosed adds happened to do; it "
            "does not measure skill, and ranking managers on it would be a mistake.",
            "A 13F shows only the LONG book. For multi-strategy and quantitative funds — "
            "Citadel, Millennium, Two Sigma, AQR, Renaissance — the disclosed longs are one leg "
            "of a hedged position whose shorts and derivatives are invisible here, so a large "
            "negative alpha for them may be the hedge working exactly as intended rather than a "
            "bad call. Concentrated long-only managers are the ones this measurement fits.",
        ],
    }


def _bench_return_21d(session, filing_date: str, cache: dict) -> float | None:
    """SPY's own return over the same 21 bars from the same entry date, as a percent.

    Cached per filing_date because most managers file on the identical deadline — 14 of 16
    tracked funds share 2026-08-14 — so this is one query, not one per fund.

    Mirrors analytics.py's `_bench_return()` in intent. US-only by design: 13F is an SEC filing,
    so every position in it is a US-reporting holding and there is no HK cohort to mis-benchmark
    the way AUD-ALPHAEVAL found elsewhere.
    """
    from sqlalchemy import text as _text

    if filing_date in cache:
        return cache[filing_date]
    try:
        row = session.execute(_text("""
            WITH px AS (
              SELECT p.ts::date AS d, p.close,
                     LEAD(p.close, 21) OVER (ORDER BY p.ts) AS fwd
              FROM prices p JOIN stocks st ON st.id = p.stock_id
              WHERE st.symbol = 'SPY' AND p.timeframe = 'D1'
            )
            SELECT close, fwd FROM px
            WHERE d >= CAST(:fd AS date) AND fwd IS NOT NULL
            ORDER BY d LIMIT 1
        """), {"fd": filing_date}).one_or_none()
    except Exception:
        row = None
    val = None
    if row and row.close and float(row.close) > 0 and row.fwd is not None:
        val = round(100.0 * (float(row.fwd) - float(row.close)) / float(row.close), 2)
    cache[filing_date] = val
    return val


def _fund_row(
    name: str,
    *,
    n_buys: int = 0,
    n_sells: int = 0,
    n_measured: int = 0,
    avg_21d_pct: float | None = None,
    alpha_vs_spy_pct: float | None = None,
    benchmark_21d_pct: float | None = None,
    pct_up: float | None = None,
    report_date: str | None = None,
    filing_date: str | None = None,
    disclosure_lag_days: int | None = None,
    staleness_days: int | None = None,
    sample_is_adequate: bool = False,
    unavailable: bool = False,
) -> dict:
    """The single definition of a fund row's shape.

    Exists because the measured and unavailable rows were built as two separate hand-written
    dicts, drifted, and the unavailable one lost `alpha_vs_spy_pct` — which the ranking sort
    reads unconditionally, so a SINGLE fund UW could not serve raised KeyError and took the
    entire sixteen-fund report down. Defaults here mean a new field can never again be present
    on one path and missing on the other.
    """
    return {
        "name": name,
        "n_buys": n_buys,
        "n_sells": n_sells,
        "n_measured": n_measured,
        "avg_21d_pct": avg_21d_pct,
        "alpha_vs_spy_pct": alpha_vs_spy_pct,
        "benchmark_21d_pct": benchmark_21d_pct,
        "pct_up": pct_up,
        "report_date": report_date,
        "filing_date": filing_date,
        "disclosure_lag_days": disclosure_lag_days,
        "staleness_days": staleness_days,
        "sample_is_adequate": sample_is_adequate,
        "unavailable": unavailable,
    }
