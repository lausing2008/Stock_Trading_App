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
    try:
        # CIK= accepts ticker symbols directly and returns the company's filing Atom feed.
        # Using company= searches by name and returns company entity records (not filings).
        r = await client.get(
            _EDGAR_BROWSE,
            params={
                "action": "getcompany",
                "CIK": ticker,
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

        # The Atom feed uses <accession-number> XML tags (not "Accession-Number:" text).
        accessions = re.findall(r"<accession-number>(\d{10}-\d{2}-\d{6})</accession-number>", r.text)
        # Deduplicate — the same accession number appears in both <content> and <id>/<link> tags
        seen: set[str] = set()
        unique = []
        for acc in accessions:
            if acc not in seen:
                seen.add(acc)
                unique.append(acc)
        return [{"accession": acc} for acc in unique[:20]]
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
        # Find the raw Form 4 XML — skip XSL-rendered HTML variants (xslF345X06/form4.xml
        # is linked with text "form4.html"; it returns an HTML page, not parseable XML).
        xml_links = [
            l for l in re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', r.text)
            if "xsl" not in l.lower()
        ]
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
        # Form 4 XML wraps most fields in <tag><value>content</value></tag>.
        # Try direct text first, then nested <value>, to handle both formats.
        m = re.search(rf"<{tag}[^>]*>\s*<value>\s*([^<]+?)\s*</value>", xml, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m = re.search(rf"<{tag}[^>]*>([^<]+)</{tag}>", xml, re.IGNORECASE)
        return m.group(1).strip() if m else None

    insider_name = _tag("rptOwnerName") or _tag("reportingOwnerName")
    # AUD-INSIDERROLE: `isDirector` / `isTenPercentOwner` are BOOLEAN tags whose real values are
    # "1"/"true" — falling back to one stored the literal string "1" as a person's job title.
    # 254 of 1,049 stored rows read "1" (180) or "true" (74), which is every director who filed
    # without an officer title. Resolve the flags into real role NAMES instead.
    role_raw = _tag("officerTitle") or ""
    if not role_raw.strip():
        roles = []
        if _is_true_flag(_tag("isDirector")):
            roles.append("Director")
        if _is_true_flag(_tag("isTenPercentOwner")):
            roles.append("10% Owner")
        if _is_true_flag(_tag("isOfficer")):
            roles.append("Officer")
        role_raw = ", ".join(roles)
    txn_code = _tag("transactionCode") or ""
    # Do NOT fall back to sharesOwnedFollowingTransaction — that is the insider's total
    # post-trade position (e.g. 500,000 shares), not the number of shares transacted.
    shares_str = _tag("transactionShares") or "0"
    price_str = _tag("transactionPricePerShare") or "0"
    date_str = _tag("transactionDate") or _tag("periodOfReport")
    # AUD-10B51: <aff10b5One> is a real, document-level boolean tag on every Form 4 — the
    # filer's own attestation of whether these transactions were made under a pre-scheduled
    # Rule 10b5-1 trading plan, confirmed present on 2 real live filings before this was added.
    # SEC's own real values are "0"/"1" (confirmed live), not "true"/"false" — checked first;
    # "true" is also accepted defensively in case a filing agent's software emits it that way,
    # since this tag's own literal value space isn't independently documented anywhere.
    aff10b5_raw = _tag("aff10b5One")
    is_10b5_1 = aff10b5_raw.strip() in ("1", "true") if aff10b5_raw is not None else None

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
        "is_10b5_1": is_10b5_1,
    }


def _is_true_flag(raw: str | None) -> bool:
    """Form 4 booleans are "1"/"0" in SEC's own filings; some filing agents emit "true"/"false".
    Accept both, and treat anything else — including the literal strings that used to end up in
    the role column — as false rather than truthy."""
    return (raw or "").strip().lower() in ("1", "true")


def _normalize_role(raw: str) -> str:
    # T247-EVENTINTELLIGENCE-DEADROLELOOP: the previous for-loop over _ROLE_WEIGHTS was dead
    # code — its return and the fallback below both returned the identical raw.strip()[:64],
    # so the loop could never produce a different outcome than skipping it entirely. The real
    # role-weighted scoring already happens correctly in compute_insider_score()'s own keyword
    # scan (see its `for key, w in _ROLE_WEIGHTS.items()` below) — this function only needs to
    # normalize the free-text role string for display, not weight it.
    if not raw:
        return "Officer"
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
                        is_10b5_1=data["is_10b5_1"],
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


def _build_insider_leaderboard(rows: list[dict], limit: int) -> list[dict]:
    """Pure aggregation: given already-fetched per-transaction dicts (stock_id/symbol/company/
    transaction_type/total_value), return the top `limit` stocks by net insider buying.

    AUD-INSIDERTOPBUYS-NETNEGATIVE: this is named/consumed everywhere as a "Top Buys"
    leaderboard (route name /events/insider/leaderboard, reports.tsx's "Insider Top Buys" card,
    intelligence.tsx's Overview tab) — but previously returned the top N stocks by net_value
    with NO floor at zero. A stock with heavy net SELLING (net_value < 0) could still appear
    under a "Top Buys" heading whenever fewer than `limit` stocks had genuinely positive net
    buying in the window — net-negative flow mislabeled as a buy signal. Filtering to
    net_value > 0 before truncating to `limit` means every returned row is a REAL net buyer;
    a window with fewer than `limit` genuine buyers now correctly returns fewer rows instead
    of padding out to `limit` with net sellers.
    """
    result: dict[int, dict] = {}
    for row in rows:
        sid = row["stock_id"]
        if sid not in result:
            result[sid] = {
                "stock_id": sid, "symbol": row["symbol"], "company": row["company"],
                "purchases": 0, "sales": 0, "net_value": 0.0,
            }
        if row["transaction_type"] == "purchase":
            result[sid]["purchases"] += 1
            result[sid]["net_value"] += row["total_value"] or 0
        elif row["transaction_type"] == "sale":
            result[sid]["sales"] += 1
            result[sid]["net_value"] -= row["total_value"] or 0

    net_buyers = [v for v in result.values() if v["net_value"] > 0]
    sorted_results = sorted(net_buyers, key=lambda x: x["net_value"], reverse=True)
    return sorted_results[:limit]


def get_insider_leaderboard(days: int = 30, limit: int = 20) -> list[dict]:
    """Stocks with most net insider buying in last N days — every returned row is a genuine
    net buyer (net_value > 0); see _build_insider_leaderboard()'s own docstring."""
    since = date.today() - timedelta(days=days)
    with SessionLocal() as s:
        all_txns = s.execute(
            select(InsiderTransaction, Stock.symbol, Stock.name)
            .join(Stock, InsiderTransaction.stock_id == Stock.id)
            .where(InsiderTransaction.transaction_date >= since)
            .order_by(InsiderTransaction.transaction_date.desc())
        ).all()
        rows = [
            {
                "stock_id": txn.stock_id, "symbol": symbol, "company": name,
                "transaction_type": txn.transaction_type, "total_value": txn.total_value,
            }
            for txn, symbol, name in all_txns
        ]
        return _build_insider_leaderboard(rows, limit)


_10B5_1_SALE_WEIGHT_MULT = 0.4  # AUD-10B51: a sale made under a pre-scheduled Rule 10b5-1 plan
# was decided on and locked in whenever the plan was adopted (often months earlier) — it
# reveals essentially nothing about the insider's view of the stock RIGHT NOW, unlike a
# genuinely discretionary sale. Applied on top of the existing 0.4 sale-vs-purchase asymmetry
# below (0.4 * 0.4 = 0.16 of a same-weight purchase) rather than replacing it — a scheduled
# sale should weigh less than a discretionary one, not literally zero, since insiders can and
# do cancel/amend 10b5-1 plans, and the sale still happened.


def compute_insider_score(stock_id: int, days: int = 90) -> float:
    """-100 to 100 insider score (negative = net selling).

    EI-DOC1: was previously self-contradicting ("0-100 ... negative = net
    selling" in the same sentence) — matches the real max(-100.0, min(100.0,
    score)) clamp below. Same stale-range docstring class as congress.py's
    compute_congress_score.

    AUD-10B51: a sale's own weight is now reduced further when Form 4's real <aff10b5One>
    attestation confirms it was made under a pre-scheduled trading plan — a real, previously-
    missing distinction between a scheduled sale (reveals little about current insider
    sentiment) and a genuinely discretionary one (a real, timely signal). is_10b5_1=None
    (unknown — the common case for any row ingested before this field existed) is treated
    exactly like a discretionary sale (the FULL pre-existing weight, no discount) — an unknown
    plan status must never be silently treated as "definitely scheduled," which would
    understate real insider selling pressure whenever the data simply hasn't caught up yet.
    """
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
            sale_weight = weight * 0.4
            if t.get("is_10b5_1") is True:
                sale_weight *= _10B5_1_SALE_WEIGHT_MULT
            score -= sale_weight

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
        # AUD-10B51: the filer's own real attestation of whether this transaction was made
        # under a pre-scheduled Rule 10b5-1 trading plan (from Form 4's own <aff10b5One> tag).
        # None for rows ingested before this field existed, or the rare filing it couldn't be
        # parsed from — never backfilled/guessed, matching every other nullable field here.
        "is_10b5_1": t.is_10b5_1,
    }


# ── AUD-INSIDERUW: market-wide Form 4 ingestion from Unusual Whales ───────────
#
# WHY, GIVEN THE EDGAR PATH ALREADY WORKS. It works but it barely reaches anything: it is a
# PER-TICKER, on-demand scrape, so two years of it produced 1,049 rows of which only 147 are
# open-market purchases. Measured 2026-09-24, those 147 give:
#
#     +0.69% mean 21-day alpha vs SPY, 58.0% beat rate, n=112 resolved
#     t = 0.93 naive, t = 0.60 day-clustered across 46 distinct filing days
#
# |t| < 2 means NOT YET MEASURABLE, never "no edge" (see the rule in
# docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md §7.4). The standard deviation is 7.85%
# against a 0.69% mean, so the sample — not the signal — is what is missing. UW's feed is
# market-wide at roughly 850 filings a day across ~196 tickers per filing day, which is the
# difference between answering this question next quarter and never answering it.
#
# IT IS ALSO BETTER DATA. `is_10b5_1` is populated on 500 of 500 sampled UW rows versus 11 of
# 1,049 EDGAR rows — and that flag is the whole signal/noise line for insider activity, since a
# sale scheduled six months ago reveals nothing about anyone's view today. UW also carries clean
# `officer_title` plus explicit is_officer/is_director/is_ten_percent_owner flags, against the
# EDGAR path's own role bug (AUD-INSIDERROLE).
#
# DELIBERATELY NO SCHEMA CHANGE. Everything maps onto existing columns: transaction_code through
# the same _TRANSACTION_CODES vocabulary the EDGAR parser already writes, the role flags resolved
# into `insider_role` the same way, and `accession_number` — the table's unique key — synthesised
# deterministically from the fields that identify a filing, so re-running is idempotent and the
# two sources cannot double-insert the same event.

_UW_INSIDER_URL = "https://api.unusualwhales.com/api/insider/transactions"

# UW's transaction_code values, mapped onto the vocabulary already in this table. Only P and S
# are STORED (matching the EDGAR path's own filter): an award, an option exercise or a
# tax-withholding disposal is a compensation mechanic, not a decision about the stock, and
# mixing them into "purchases" is the single fastest way to destroy this dataset's meaning.
_UW_STORED_CODES = {"P": "purchase", "S": "sale"}


def _uw_insider_role(row: dict) -> str:
    """A readable role from UW's title plus its boolean flags."""
    title = (row.get("officer_title") or "").strip()
    if title:
        return title[:128]
    roles = []
    if row.get("is_director"):
        roles.append("Director")
    if row.get("is_ten_percent_owner"):
        roles.append("10% Owner")
    if row.get("is_officer"):
        roles.append("Officer")
    return (", ".join(roles) or "Insider")[:128]


def _uw_synthetic_accession(row: dict) -> str:
    """A stable id for a UW row, since UW does not return the SEC accession number.

    Hashed over the fields that identify one person's one transaction in one security on one
    day, so the SAME filing seen twice — on a re-run, or on a later page — collides on the
    table's unique key instead of inserting again. Prefixed `uw:` so a row's provenance stays
    visible and it can never collide with a real EDGAR accession.
    """
    import hashlib

    key = "|".join(str(row.get(k) or "") for k in
                   ("ticker", "owner_name", "transaction_date", "transaction_code",
                    "amount", "price", "filing_date"))
    return "uw:" + hashlib.sha256(key.encode()).hexdigest()[:28]


def sync_insider_from_uw(limit: int = 500) -> dict:
    """Ingest the market-wide Form 4 feed. Returns counts; never raises into the scheduler."""
    from common.ai_keys import get_unusual_whales_key, is_unusual_whales_enabled

    if not (is_unusual_whales_enabled() and get_unusual_whales_key()):
        return {"skipped": "unusual_whales_unavailable"}
    try:
        r = httpx.get(
            _UW_INSIDER_URL,
            params={"limit": limit},
            headers={"Authorization": f"Bearer {get_unusual_whales_key()}",
                     "Accept": "application/json"},
            timeout=45,
        )
        if r.status_code != 200:
            log.warning("insider.uw_status", status=r.status_code)
            return {"error": f"status {r.status_code}"}
        body = r.json()
        rows = body.get("data") if isinstance(body, dict) else body
        rows = rows if isinstance(rows, list) else []
    except Exception as exc:
        log.warning("insider.uw_fetch_failed", error=str(exc))
        return {"error": str(exc)[:200]}

    if not rows:
        return {"fetched": 0, "stored": 0}

    stored = skipped_code = skipped_ticker = 0
    with SessionLocal() as s:
        ticker_map = {sym.upper(): sid for sid, sym in s.execute(select(Stock.id, Stock.symbol)).all()}
        for row in rows:
            try:
                code = (row.get("transaction_code") or "").upper()
                txn_type = _UW_STORED_CODES.get(code)
                if txn_type is None:
                    skipped_code += 1
                    continue
                stock_id = ticker_map.get((row.get("ticker") or "").upper())
                if stock_id is None:
                    skipped_ticker += 1
                    continue
                txn_date = (row.get("transaction_date") or "")[:10]
                filing_date = (row.get("filing_date") or txn_date)[:10]
                if not txn_date:
                    continue
                price = row.get("price")
                price = float(price) if price not in (None, "") else None
                # UW's `amount` is SIGNED (negative on a disposal). Shares are a magnitude here;
                # direction already lives in transaction_type, and storing a negative share
                # count would silently flip every total_value that multiplies by it.
                amount = row.get("amount")
                shares = abs(int(float(amount))) if amount not in (None, "") else None
                stmt = pg_insert(InsiderTransaction).values(
                    stock_id=stock_id,
                    insider_name=(row.get("owner_name") or "Unknown")[:255],
                    insider_role=_uw_insider_role(row),
                    transaction_type=txn_type,
                    shares=shares,
                    price_per_share=price if price and price > 0 else None,
                    total_value=(shares * price) if shares and price and price > 0 else None,
                    transaction_date=date.fromisoformat(txn_date),
                    filing_date=date.fromisoformat(filing_date),
                    accession_number=_uw_synthetic_accession(row),
                    is_10b5_1=row.get("is_10b5_1"),
                ).on_conflict_do_nothing(constraint="uq_insider_accession")
                stored += s.execute(stmt).rowcount
            except Exception:
                continue  # one malformed row must never drop the rest of a real response
        s.commit()

    log.info("insider.uw_synced", fetched=len(rows), stored=stored,
             skipped_code=skipped_code, skipped_ticker=skipped_ticker)
    return {"fetched": len(rows), "stored": stored,
            "skipped_non_open_market": skipped_code, "skipped_untracked_ticker": skipped_ticker}
