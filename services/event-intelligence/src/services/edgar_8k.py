"""EDGAR 8-K filing ingest — T208.

Fetches recent 8-K filings from SEC EDGAR for tracked US stocks.
Uses the free EDGAR REST API (no authentication required).
SEC fair-use policy: max 10 requests/second; identify via User-Agent.

HK stocks (symbol ending in .HK) are skipped automatically — EDGAR only
covers US-listed companies. The CIK lookup uses SEC's company_tickers.json
which maps ticker symbols to 10-digit zero-padded CIK numbers.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import select, text

from db import SessionLocal, Stock

log = logging.getLogger(__name__)

_EDGAR_BASE = "https://data.sec.gov"
_TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
_HEADERS = {"User-Agent": "StockAI/1.0 (research@lausing.com)"}

# 8-K items considered material for signal cross-referencing.
# 1.01 = material agreement; 2.01 = asset acquisition/disposal;
# 2.06 = material impairment; 5.02 = officer/director change;
# 8.01 = other material events.
_MATERIAL_ITEMS = {"1.01", "2.01", "2.06", "5.02", "8.01"}

# Module-level CIK map cache (populated once per process lifetime).
# Refreshed on first call each day via _cik_map_date.
_cik_map_cache: dict[str, str] = {}
_cik_map_date: date | None = None


def _get_ticker_cik_map() -> dict[str, str]:
    """Fetch complete ticker→CIK mapping from SEC. Returns {TICKER: zero-padded-CIK}.

    Cached in-process for the calendar day to avoid hammering SEC on every call.
    """
    global _cik_map_cache, _cik_map_date
    today = date.today()
    if _cik_map_cache and _cik_map_date == today:
        return _cik_map_cache
    try:
        resp = httpx.get(_TICKER_CIK_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        result = {
            entry["ticker"].upper(): str(entry["cik_str"]).zfill(10)
            for entry in data.values()
            if entry.get("ticker") and entry.get("cik_str")
        }
        _cik_map_cache = result
        _cik_map_date = today
        log.info("edgar.cik_map_loaded", extra={"ticker_count": len(result)})
        return result
    except Exception as exc:
        log.error("edgar.cik_map_failed", extra={"exc": str(exc)})
        return _cik_map_cache  # return stale cache rather than empty dict


def _get_recent_8k_filings(cik: str, days_back: int = 7) -> list[dict]:
    """Fetch recent 8-K filings for a CIK from EDGAR submissions API.

    Returns list of filing dicts with keys:
      accession, form, filed_date, report_date, description
    Stops scanning as soon as a filing is older than days_back calendar days.
    """
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    url = f"{_EDGAR_BASE}/submissions/CIK{cik}.json"
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("edgar.submissions_failed", extra={"cik": cik, "exc": str(exc)})
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filed_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    descriptions = recent.get("primaryDocument", [])

    results: list[dict] = []
    for form, acc, filed, report, desc in zip(forms, accessions, filed_dates, report_dates, descriptions):
        if form not in ("8-K", "8-K/A"):
            continue
        if filed < cutoff:
            # EDGAR returns filings sorted newest-first; once we pass the cutoff we are done.
            break
        # Normalise accession number: remove dashes for storage consistency.
        acc_clean = acc.replace("-", "")
        results.append({
            "accession": acc_clean,
            "form": form,
            "filed_date": filed,
            "report_date": report or None,
            "description": str(desc)[:512],
        })
    return results


def ingest_8k_filings(symbols: list[str], days_back: int = 7) -> dict:
    """Main ingest function — call from the /events/sync/8k endpoint.

    Fetches 8-K filings for the given symbols and stores new ones in the
    sec_filings table. Skips HK stocks (no EDGAR coverage). Skips accessions
    that are already stored (idempotent via ON CONFLICT DO NOTHING).

    Uses its own SessionLocal() context so it can commit per-filing without
    blocking the caller's transaction.

    Rate-limited to stay under SEC's 10 req/s limit (0.15s sleep per CIK).

    Returns {"processed": int, "new": int, "material": int}
    """
    cik_map = _get_ticker_cik_map()
    processed = new_count = material_count = 0

    for symbol in symbols:
        # Strip .HK suffix — EDGAR only covers US-listed companies.
        if symbol.upper().endswith(".HK"):
            continue
        base_sym = symbol.upper()
        if base_sym not in cik_map:
            log.debug("edgar.no_cik", extra={"symbol": symbol})
            continue

        cik = cik_map[base_sym]
        processed += 1

        # Cache the CIK on the stock row for fast future lookups.
        try:
            with SessionLocal() as s:
                s.execute(
                    text("UPDATE stocks SET cik = :cik WHERE symbol = :sym AND cik IS NULL"),
                    {"cik": cik, "sym": symbol},
                )
                s.commit()
        except Exception:
            pass

        filings = _get_recent_8k_filings(cik, days_back=days_back)
        for filing in filings:
            with SessionLocal() as s:
                # Skip accessions already stored (idempotent).
                exists = s.execute(
                    text("SELECT 1 FROM sec_filings WHERE accession = :acc"),
                    {"acc": filing["accession"]},
                ).fetchone()
                if exists:
                    continue

                # is_material: the submissions API doesn't directly return item codes.
                # We store is_material=False for now; a future enhancement can parse
                # the filing index page to extract item codes and set this flag.
                is_material = False

                try:
                    s.execute(
                        text("""
                            INSERT INTO sec_filings
                                (symbol, cik, accession, form, filed_date, report_date,
                                 description, is_material)
                            VALUES
                                (:sym, :cik, :acc, :form, CAST(:filed AS date),
                                 CAST(:report AS date), :desc, :mat)
                            ON CONFLICT (accession) DO NOTHING
                        """),
                        {
                            "sym": symbol,
                            "cik": cik,
                            "acc": filing["accession"],
                            "form": filing["form"],
                            "filed": filing["filed_date"],
                            "report": filing["report_date"],
                            "desc": filing["description"],
                            "mat": is_material,
                        },
                    )
                    s.commit()
                    new_count += 1
                    if is_material:
                        material_count += 1
                    log.debug("edgar.filing_stored", extra={"symbol": symbol, "accession": filing["accession"]})
                except Exception as exc:
                    log.warning("edgar.insert_failed", extra={"symbol": symbol, "exc": str(exc)})
                    s.rollback()

        # Rate-limit: stay under 10 req/s (one request per CIK above + this sleep).
        time.sleep(0.15)

    log.info(
        "edgar.ingest_complete",
        extra={"processed": processed, "new": new_count, "material": material_count},
    )
    return {"processed": processed, "new": new_count, "material": material_count}


def get_recent_filings_for_symbol(db, symbol: str, days: int = 14) -> list[dict]:
    """Return recent 8-K filings for a symbol from the DB.

    Used by the /events/8k/{symbol} API endpoint and by signal-engine to
    flag material SEC events near BUY signals.

    db: SQLAlchemy Session (FastAPI-injected via Depends(get_session)).
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = db.execute(
        text("""
            SELECT filed_date, form, description, is_material, accession
            FROM sec_filings
            WHERE symbol = :sym AND filed_date >= CAST(:cutoff AS date)
            ORDER BY filed_date DESC
            LIMIT 10
        """),
        {"sym": symbol.upper(), "cutoff": cutoff},
    ).fetchall()
    return [
        {
            "filed_date": str(r.filed_date),
            "form": r.form,
            "description": r.description,
            "is_material": r.is_material,
            "accession": r.accession,
        }
        for r in rows
    ]
