"""Economic Calendar — FRED API + hardcoded FOMC/HKMA dates."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone, timedelta

import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.config import get_settings
from db import get_session, SessionLocal, EconomicEvent

log = structlog.get_logger()
_settings = get_settings()

# FOMC meeting dates 2025–2027 (from Federal Reserve public calendar)
_FOMC_DATES: list[tuple[str, str, str]] = [
    # (date_str, title, importance)
    ("2025-01-29", "FOMC Meeting", "high"),
    ("2025-03-19", "FOMC Meeting", "high"),
    ("2025-05-07", "FOMC Meeting", "high"),
    ("2025-06-18", "FOMC Meeting", "high"),
    ("2025-07-30", "FOMC Meeting", "high"),
    ("2025-09-17", "FOMC Meeting", "high"),
    ("2025-10-29", "FOMC Meeting", "high"),
    ("2025-12-10", "FOMC Meeting", "high"),
    ("2026-01-28", "FOMC Meeting", "high"),
    ("2026-03-18", "FOMC Meeting", "high"),
    ("2026-04-29", "FOMC Meeting", "high"),
    ("2026-06-17", "FOMC Meeting", "high"),
    ("2026-07-29", "FOMC Meeting", "high"),
    ("2026-09-16", "FOMC Meeting", "high"),
    ("2026-10-28", "FOMC Meeting", "high"),
    ("2026-12-09", "FOMC Meeting", "high"),
    # 2027 — approximate dates following standard 8-per-year pattern
    ("2027-01-27", "FOMC Meeting", "high"),
    ("2027-03-17", "FOMC Meeting", "high"),
    ("2027-04-28", "FOMC Meeting", "high"),
    ("2027-06-16", "FOMC Meeting", "high"),
    ("2027-07-28", "FOMC Meeting", "high"),
    ("2027-09-15", "FOMC Meeting", "high"),
    ("2027-10-27", "FOMC Meeting", "high"),
    ("2027-12-08", "FOMC Meeting", "high"),
]

# FRED series IDs → (event_type, title, importance)
_FRED_SERIES: list[tuple[str, str, str, str]] = [
    ("CPIAUCSL",    "cpi",            "CPI (Consumer Price Index)",    "high"),
    ("CPILFESL",    "cpi_core",       "Core CPI (ex Food & Energy)",   "high"),
    ("PPIACO",      "ppi",            "PPI (Producer Price Index)",    "high"),
    ("GDP",         "gdp",            "GDP (Quarterly)",               "high"),
    ("PAYEMS",      "nfp",            "Nonfarm Payrolls",              "high"),
    ("UNRATE",      "unemployment",   "Unemployment Rate",             "high"),
    ("RSXFS",       "retail_sales",   "Retail Sales",                  "medium"),
    ("NAPM",        "ism_mfg",        "ISM Manufacturing PMI",         "medium"),
    ("UMCSENT",     "consumer_conf",  "Consumer Confidence",           "medium"),
    ("HOUST",       "housing_starts", "Housing Starts",                "medium"),
    ("ICSA",        "jobless_claims", "Initial Jobless Claims",        "medium"),
    ("FEDFUNDS",    "fed_funds",      "Fed Funds Rate",                "high"),
]


def _seed_fomc() -> int:
    """Insert hardcoded FOMC dates if not already present."""
    inserted = 0
    with SessionLocal() as s:
        for date_str, title, importance in _FOMC_DATES:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=14, minute=0, tzinfo=timezone.utc
            )
            stmt = (
                pg_insert(EconomicEvent)
                .values(
                    event_type="fomc_meeting",
                    title=title,
                    country="US",
                    event_date=dt,
                    importance=importance,
                    source="fed_calendar",
                )
                .on_conflict_do_nothing(constraint="uq_economic_event")
            )
            result = s.execute(stmt)
            inserted += result.rowcount
        s.commit()
    return inserted


async def sync_fred(lookback_days: int = 365) -> dict:
    """Fetch FRED release data for configured series and upsert into economic_events."""
    api_key = getattr(_settings, "fred_api_key", "")
    if not api_key:
        log.info("economic.fred_skip", reason="FRED_API_KEY not set")
        fomc = _seed_fomc()
        return {"fomc_seeded": fomc, "fred_series": 0, "skipped": "no_api_key"}

    fomc = _seed_fomc()
    base_url = "https://api.stlouisfed.org/fred"
    observation_start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    upserted = 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        for series_id, event_type, title, importance in _FRED_SERIES:
            try:
                r = await client.get(
                    f"{base_url}/series/observations",
                    params={
                        "series_id": series_id,
                        "api_key": api_key,
                        "file_type": "json",
                        "observation_start": observation_start,
                        "sort_order": "desc",
                        "limit": 24,
                    },
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                observations = data.get("observations", [])
                with SessionLocal() as s:
                    for obs in observations:
                        try:
                            dt = datetime.strptime(obs["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                            actual = float(obs["value"]) if obs["value"] not in (".", "") else None
                            stmt = (
                                pg_insert(EconomicEvent)
                                .values(
                                    event_type=event_type,
                                    title=title,
                                    country="US",
                                    event_date=dt,
                                    actual_value=actual,
                                    importance=importance,
                                    source="fred",
                                )
                                .on_conflict_do_update(
                                    constraint="uq_economic_event",
                                    set_=dict(actual_value=actual),
                                )
                            )
                            result = s.execute(stmt)
                            upserted += result.rowcount
                        except Exception:
                            continue
                    s.commit()
                await asyncio.sleep(0.1)  # FRED rate limit: 120/min
            except Exception as exc:
                log.warning("economic.fred_error", series=series_id, error=str(exc))

    return {"fomc_seeded": fomc, "fred_series": upserted, "skipped": None}


def get_upcoming_economic_events(days: int = 14, country: str = "US") -> list[dict]:
    """Return upcoming economic events from DB, sorted by date."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days)
    with SessionLocal() as s:
        rows = s.execute(
            select(EconomicEvent)
            .where(
                EconomicEvent.country == country,
                EconomicEvent.event_date >= now,
                EconomicEvent.event_date <= cutoff,
            )
            .order_by(EconomicEvent.event_date)
        ).scalars().all()
        return [
            {
                "id": e.id,
                "event_type": e.event_type,
                "event_name": e.title,        # matches TypeScript EconomicEvent.event_name
                "market": e.country,           # matches TypeScript EconomicEvent.market
                "event_date": e.event_date.isoformat(),
                "event_time": None,
                "actual_value": e.actual_value,
                "forecast_value": e.expected_value,  # matches TypeScript EconomicEvent.forecast_value
                "previous_value": e.previous_value,
                "impact_level": e.importance,  # matches TypeScript EconomicEvent.impact_level
                "notes": None,
            }
            for e in rows
        ]


def get_recent_economic_events(days: int = 30, country: str = "US") -> list[dict]:
    """Return recently released economic data."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    with SessionLocal() as s:
        rows = s.execute(
            select(EconomicEvent)
            .where(
                EconomicEvent.country == country,
                EconomicEvent.event_date >= since,
                EconomicEvent.event_date <= now,
                EconomicEvent.importance == "high",
            )
            .order_by(EconomicEvent.event_date.desc())
        ).scalars().all()
        return [
            {
                "event_type": e.event_type,
                "title": e.title,
                "event_date": e.event_date.isoformat(),
                "actual_value": e.actual_value,
                "expected_value": e.expected_value,
                "importance": e.importance,
            }
            for e in rows
        ]


def days_to_next_fomc() -> int | None:
    """Return days until next FOMC meeting, or None if not in DB."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as s:
        row = s.execute(
            select(EconomicEvent)
            .where(
                EconomicEvent.event_type == "fomc_meeting",
                EconomicEvent.event_date >= now,
            )
            .order_by(EconomicEvent.event_date)
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return (row.event_date.replace(tzinfo=timezone.utc) - now).days
