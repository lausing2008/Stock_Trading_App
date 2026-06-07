"""Congressional trading data.

Primary source (free, no API key):
  House Stock Watcher  — housestockwatcher.com/api/transactions
  Senate Stock Watcher — senatestockwatcher.com/api/transactions
  Both scrape official STOCK Act disclosures from congress.gov.

Optional upgrade:
  Quiver Quantitative  — quiverquant.com (paid, $30/mo) — richer metadata,
  used when quiver_api_key is configured in Settings.

All sources are normalised to the same CongressTrade schema so the frontend
needs no changes.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from common.logging import get_logger
from .auth import get_current_user

log = get_logger("congress")
router = APIRouter(prefix="/congress", tags=["congress"])

_quiver_api_key: str | None = None


def set_quiver_key(key: str) -> None:
    global _quiver_api_key
    _quiver_api_key = key


def get_quiver_key() -> str | None:
    return _quiver_api_key


# ── Amount parsing (House/Senate return strings like "$1,001 - $15,000") ──────

_AMT_MAP = {
    "$1,001 - $15,000":        (1_001,    15_000),
    "$15,001 - $50,000":       (15_001,   50_000),
    "$50,001 - $100,000":      (50_001,  100_000),
    "$100,001 - $250,000":    (100_001,  250_000),
    "$250,001 - $500,000":    (250_001,  500_000),
    "$500,001 - $1,000,000":  (500_001, 1_000_000),
    "$1,000,001 - $5,000,000": (1_000_001, 5_000_000),
    "over $5,000,000":        (5_000_001, None),
    "$1,000,001 +":           (1_000_001, None),
}

def _parse_amount(s: str | None) -> tuple[int | None, int | None]:
    if not s:
        return None, None
    key = s.strip()
    if key in _AMT_MAP:
        return _AMT_MAP[key]
    # Generic regex: grab all integers, use first two as min/max
    nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", key)]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], None
    return None, None


def _cutoff_date(days: int) -> str:
    """ISO date string for `days` ago."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


# ── Free source: House Stock Watcher ─────────────────────────────────────────

async def _fetch_house(client: httpx.AsyncClient, days: int) -> list[dict]:
    try:
        resp = await client.get(
            "https://housestockwatcher.com/api/transactions",
            headers={"User-Agent": "StockAI/1.0"},
            timeout=20,
        )
        if not resp.is_success:
            log.warning("congress.house_error", status=resp.status_code)
            return []
        raw: list[dict] = resp.json()
    except Exception as exc:
        log.warning("congress.house_fetch_failed", error=str(exc))
        return []

    cutoff = _cutoff_date(days)
    out = []
    for t in raw:
        date_str = (t.get("transaction_date") or t.get("disclosure_date") or "")[:10]
        if date_str < cutoff:
            continue
        ticker = (t.get("ticker") or "").strip().upper()
        if not ticker or ticker in ("--", "N/A"):
            continue
        tx_raw = (t.get("type") or "").lower()
        tx = "Purchase" if "purchase" in tx_raw or "buy" in tx_raw else "Sale"
        lo, hi = _parse_amount(t.get("amount"))
        out.append({
            "Ticker": ticker,
            "Date": date_str,
            "Politician": t.get("representative") or t.get("owner") or "—",
            "Transaction": tx,
            "Min": lo,
            "Max": hi,
            "Party": (t.get("party") or "")[:1].upper() or None,
            "State": t.get("state") or None,
            "Chamber": "House",
            "ReportDate": (t.get("disclosure_date") or "")[:10] or None,
        })
    return out


# ── Free source: Senate Stock Watcher ────────────────────────────────────────

async def _fetch_senate(client: httpx.AsyncClient, days: int) -> list[dict]:
    try:
        resp = await client.get(
            "https://senatestockwatcher.com/api/transactions",
            headers={"User-Agent": "StockAI/1.0"},
            timeout=20,
        )
        if not resp.is_success:
            log.warning("congress.senate_error", status=resp.status_code)
            return []
        raw: list[dict] = resp.json()
    except Exception as exc:
        log.warning("congress.senate_fetch_failed", error=str(exc))
        return []

    cutoff = _cutoff_date(days)
    out = []
    for t in raw:
        date_str = (t.get("transaction_date") or t.get("disclosure_date") or "")[:10]
        if date_str < cutoff:
            continue
        ticker = (t.get("ticker") or "").strip().upper()
        if not ticker or ticker in ("--", "N/A"):
            continue
        tx_raw = (t.get("type") or "").lower()
        tx = "Purchase" if "purchase" in tx_raw or "buy" in tx_raw else "Sale"
        lo, hi = _parse_amount(t.get("amount"))
        out.append({
            "Ticker": ticker,
            "Date": date_str,
            "Politician": t.get("senator") or t.get("owner") or "—",
            "Transaction": tx,
            "Min": lo,
            "Max": hi,
            "Party": None,  # Senate Watcher doesn't return party
            "State": None,
            "Chamber": "Senate",
            "ReportDate": (t.get("ptr_link") or t.get("disclosure_date") or "")[:10] or None,
        })
    return out


# ── Paid source: Quiver Quantitative ────────────────────────────────────────

async def _fetch_quiver(client: httpx.AsyncClient, key: str) -> list[dict]:
    try:
        resp = await client.get(
            "https://api.quiverquant.com/beta/live/congresstrading",
            headers={
                "Authorization": f"Token {key}",
                "Accept": "application/json",
                "X-CSRFToken": "null",
            },
            timeout=20,
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Invalid Quiver API key.")
        if not resp.is_success:
            raise HTTPException(status_code=502, detail=f"Quiver API returned {resp.status_code}.")
        return resp.json()
    except HTTPException:
        raise
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach Quiver API: {exc}")


# ── Main endpoint ─────────────────────────────────────────────────────────────

@router.get("/trades")
async def congress_trades(
    days: int = Query(90, le=365),
    politician: str | None = None,
    ticker: str | None = None,
    transaction: str | None = None,
    _user=Depends(get_current_user),
):
    """Fetch recent congressional trades.

    Uses House Stock Watcher + Senate Stock Watcher (free, no key needed) by
    default. Falls back to Quiver Quantitative if a key is configured in
    Settings — Quiver has richer metadata but costs $30/mo.
    """
    async with httpx.AsyncClient() as client:
        key = _quiver_api_key
        if key:
            # Paid path — Quiver returns everything in one call
            data: list[dict] = await _fetch_quiver(client, key)
            # Quiver returns all history; apply days filter client-side
            cutoff = _cutoff_date(days)
            data = [t for t in data if (t.get("Date") or "")[:10] >= cutoff]
        else:
            # Free path — merge House + Senate watchers
            house, senate = await _fetch_house(client, days), await _fetch_senate(client, days)
            data = house + senate

    # Shared filters
    if politician:
        name_lower = politician.lower()
        data = [t for t in data if name_lower in (t.get("Politician") or "").lower()]
    if ticker:
        tk = ticker.upper()
        data = [t for t in data if (t.get("Ticker") or "").upper() == tk]
    if transaction:
        tx = transaction.lower()
        data = [t for t in data if tx in (t.get("Transaction") or "").lower()]

    # Sort newest first
    data.sort(key=lambda t: (t.get("Date") or ""), reverse=True)
    return data
