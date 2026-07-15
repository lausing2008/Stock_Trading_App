"""CAPE (Shiller cyclically-adjusted P/E) — macro valuation context for the AI-bubble-warning
indicator.

Source is multpl.com, NOT Yale's own ie_data.xls. That file is real but was found stale
(Last-Modified Oct 2023) when this feature was investigated, and Shiller's site was mid-
migration to a new Yale SOM page with no working direct download found. multpl.com publishes
a genuine Atom feed per indicator (multpl.com/{indicator}/atom — confirmed identical pattern
across multiple indicator pages, not a one-off) plus a stable `id="datatable"` HTML table
(multpl.com/shiller-pe/table/by-month) for historical backfill, both verified live and current
at investigation time. Still an unofficial third-party source — same fragility CLASS as the
dead housestockwatcher/senatestockwatcher congress-data incident, just a more stable access
pattern — so staleness is monitored the same way (dq_check:cape_reading), not assumed reliable
forever.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import httpx
import structlog
from lxml import etree, html as lxml_html
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import SessionLocal, CapeReading

log = structlog.get_logger()

_ATOM_URL = "https://www.multpl.com/shiller-pe/atom"
_TABLE_URL = "https://www.multpl.com/shiller-pe/table/by-month"
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# Historical CAPE bands, sourced from real peaks (not guessed):
#   - Long-run mean/median (1871-present): ~16-17
#   - 1929 pre-crash peak: ~32-33
#   - 2021 post-COVID peak: ~38.6
#   - Dec 1999 dot-com peak (all-time high): 44.19
_CAPE_BANDS = [
    (30.0, "normal"),
    (35.0, "elevated"),
    (40.0, "high"),
]
_CAPE_EXTREME_LABEL = "extreme"


def cape_band(cape_value: float) -> str:
    """Classify a CAPE reading into a warning band using real historical peaks as anchors."""
    for threshold, label in _CAPE_BANDS:
        if cape_value < threshold:
            return label
    return _CAPE_EXTREME_LABEL


def _parse_atom(content_bytes: bytes) -> tuple[date, float]:
    """Parse a multpl.com indicator Atom feed response into (reading_date, cape_value).
    Raises on any parse failure — callers translate that into a structured skip result."""
    root = etree.fromstring(content_bytes)
    entry = root.find("a:entry", _ATOM_NS)
    content = entry.find("a:content", _ATOM_NS).text
    updated = entry.find("a:updated", _ATOM_NS).text
    m = re.search(r"</b>\s*([\d.]+)", content)
    if not m:
        raise ValueError("no_value_match")
    cape_value = float(m.group(1))
    reading_date = datetime.fromisoformat(updated).date()
    return reading_date, cape_value


def _parse_table(content_bytes: bytes, months: int) -> list[tuple[date, float]]:
    """Parse multpl.com's by-month table response into [(reading_date, cape_value), ...],
    newest first, capped at `months` rows. Skips any row that doesn't parse cleanly rather
    than failing the whole batch."""
    tree = lxml_html.fromstring(content_bytes)
    table = tree.xpath('//table[@id="datatable"]')
    if not table:
        raise ValueError("no_table")
    rows = table[0].xpath(".//tr")[1:]  # skip header row

    parsed: list[tuple[date, float]] = []
    for row in rows[:months]:
        cells = [c.strip() for c in row.xpath(".//td//text()") if c.strip()]
        if len(cells) < 2:
            continue
        try:
            reading_date = datetime.strptime(cells[0], "%b %d, %Y").date()
            # multpl's value cell includes a leading &#x2002; (Unicode en space) entity
            # before the actual number — strip everything except digits/dot before parsing.
            value_str = re.sub(r"[^\d.]", "", cells[1])
            cape_value = float(value_str)
        except (ValueError, IndexError):
            continue
        parsed.append((reading_date, cape_value))
    return parsed


async def sync_cape_current() -> dict:
    """Fetch today's CAPE reading from the Atom feed and upsert it."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.get(_ATOM_URL, headers={"User-Agent": "StockAI/1.0"})
        except Exception as exc:
            log.warning("valuation.cape_fetch_error", error=str(exc))
            return {"synced": False, "reason": str(exc)}

    if r.status_code != 200:
        log.warning("valuation.cape_fetch_failed", status=r.status_code)
        return {"synced": False, "reason": f"http_{r.status_code}"}

    try:
        reading_date, cape_value = _parse_atom(r.content)
    except Exception as exc:
        log.warning("valuation.cape_parse_error", error=str(exc))
        return {"synced": False, "reason": str(exc)}

    _upsert_reading(reading_date, cape_value)
    return {"synced": True, "reading_date": reading_date.isoformat(), "cape_value": cape_value}


async def sync_cape_history(months: int = 24) -> dict:
    """Backfill recent monthly CAPE history from the by-month table. Safe to re-run — upserts
    on the unique reading_date, so re-running just refreshes recent values in place."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(_TABLE_URL, headers={"User-Agent": "StockAI/1.0"})
        except Exception as exc:
            log.warning("valuation.cape_history_fetch_error", error=str(exc))
            return {"synced": 0, "reason": str(exc)}

    if r.status_code != 200:
        log.warning("valuation.cape_history_fetch_failed", status=r.status_code)
        return {"synced": 0, "reason": f"http_{r.status_code}"}

    try:
        parsed = _parse_table(r.content, months)
    except Exception as exc:
        log.warning("valuation.cape_history_parse_error", error=str(exc))
        return {"synced": 0, "reason": str(exc)}

    for reading_date, cape_value in parsed:
        _upsert_reading(reading_date, cape_value)

    return {"synced": len(parsed)}


def _upsert_reading(reading_date: date, cape_value: float) -> None:
    with SessionLocal() as s:
        stmt = (
            pg_insert(CapeReading)
            .values(reading_date=reading_date, cape_value=cape_value, source="multpl")
            .on_conflict_do_update(
                index_elements=["reading_date"],
                set_={"cape_value": cape_value, "source": "multpl"},
            )
        )
        s.execute(stmt)
        s.commit()


def get_latest_cape() -> dict | None:
    """Return the most recent CAPE reading with its warning band, or None if never synced."""
    with SessionLocal() as s:
        row = s.execute(
            select(CapeReading).order_by(CapeReading.reading_date.desc()).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        age_days = (date.today() - row.reading_date).days
        return {
            "reading_date": row.reading_date.isoformat(),
            "cape_value": row.cape_value,
            "band": cape_band(row.cape_value),
            "source": row.source,
            "age_days": age_days,
            "stale": age_days > 45,  # monthly cadence + buffer; flagged rather than hidden
        }


def get_cape_history(months: int = 24) -> list[dict]:
    """Return recent CAPE readings, newest first, for a historical context chart."""
    with SessionLocal() as s:
        rows = s.execute(
            select(CapeReading).order_by(CapeReading.reading_date.desc()).limit(months)
        ).scalars().all()
        return [
            {"reading_date": r.reading_date.isoformat(), "cape_value": r.cape_value, "band": cape_band(r.cape_value)}
            for r in rows
        ]
