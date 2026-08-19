"""Economic Calendar — FRED API + hardcoded FOMC/HKMA dates."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone, timedelta

import httpx
import structlog
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.config import get_settings
from db import get_session, SessionLocal, EconomicEvent, CrossAssetReading

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
    ("PCEPI",       "pce",            "PCE Price Index",               "high"),
]

# T249-MARKETMOVER-P0: FRED release_id → (event_type, title, importance) for the REAL release
# calendar (when BLS/BEA actually PUBLISHES data), distinct from _FRED_SERIES above which
# populates event_date with the observation's REFERENCE PERIOD (e.g. "2026-06-01" for June CPI
# data) — a real release date is a different axis entirely (e.g. "2026-07-14", when June's CPI
# was actually published) and any "alert me the moment CPI drops" feature needs THIS axis as
# its trigger schedule, not the reference-period rows. Release IDs found via FRED's own
# fred/series/release endpoint (services/event-intelligence/src/services/economic.py history —
# verified directly against the live API, not guessed). ISM Manufacturing PMI (NAPM) has no
# FRED release_id — ISM is a private organization, not government-sourced, so it's intentionally
# absent here (same as it was already absent from FOMC's own dedicated _FOMC_DATES handling).
_FRED_RELEASES: list[tuple[int, str, str, str]] = [
    (10, "cpi_release",       "CPI Release",        "high"),
    (46, "ppi_release",       "PPI Release",        "high"),
    (53, "gdp_release",       "GDP Advance Estimate", "medium"),
    (50, "nfp_release",       "Jobs Report (NFP)",  "high"),
    (9,  "retail_sales_release", "Retail Sales Release", "medium"),
    (91, "consumer_conf_release", "Consumer Sentiment Release", "medium"),
    (27, "housing_starts_release", "Housing Starts Release", "medium"),
    (180, "jobless_claims_release", "Jobless Claims Release", "medium"),
    (18, "fed_funds_release", "Fed Funds Rate Release", "high"),
    (54, "pce_release",       "PCE Inflation Release", "high"),
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


async def sync_fred_release_dates(lookback_days: int = 180, lookahead_days: int = 180) -> dict:
    """T249-MARKETMOVER-P0: sync the REAL release-date calendar (when data is actually
    published) from FRED's fred/release/dates endpoint, distinct from sync_fred()'s
    reference-period rows. Writes both past release dates (lookback_days, so a "most recent
    CPI release" lookup works immediately) and future scheduled release dates
    (lookahead_days, so pre-market/calendar features have a real forward-looking schedule
    instead of a hand-maintained list). Each release gets its own `{event_type}_release`
    event_type (see _FRED_RELEASES above) so these rows never collide with sync_fred()'s
    reference-period rows under the same uq_economic_event(event_type, country, event_date)
    constraint — they're intentionally different rows, not updates to the same row.

    include_release_dates_with_no_data=true is required to see FUTURE scheduled dates —
    without it FRED only returns dates that already have data attached, which excludes
    every date that hasn't happened yet.
    """
    api_key = getattr(_settings, "fred_api_key", "")
    if not api_key:
        log.info("economic.fred_release_dates_skip", reason="FRED_API_KEY not set")
        return {"synced": 0, "skipped": "no_api_key"}

    base_url = "https://api.stlouisfed.org/fred"
    realtime_start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    realtime_end = (datetime.now() + timedelta(days=lookahead_days)).strftime("%Y-%m-%d")
    upserted = 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        for release_id, event_type, title, importance in _FRED_RELEASES:
            try:
                r = await client.get(
                    f"{base_url}/release/dates",
                    params={
                        "release_id": release_id,
                        "api_key": api_key,
                        "file_type": "json",
                        "realtime_start": realtime_start,
                        "realtime_end": realtime_end,
                        "include_release_dates_with_no_data": "true",
                        "sort_order": "asc",
                        "limit": 100,
                    },
                )
                if r.status_code != 200:
                    log.warning("economic.fred_release_dates_failed", release_id=release_id, status=r.status_code)
                    continue
                data = r.json()
                release_dates = data.get("release_dates", [])
                with SessionLocal() as s:
                    for rd in release_dates:
                        try:
                            dt = datetime.strptime(rd["date"], "%Y-%m-%d").replace(
                                hour=8, minute=30, tzinfo=timezone.utc
                            )
                            stmt = (
                                pg_insert(EconomicEvent)
                                .values(
                                    event_type=event_type,
                                    title=title,
                                    country="US",
                                    event_date=dt,
                                    importance=importance,
                                    source="fred_release_calendar",
                                )
                                .on_conflict_do_nothing(constraint="uq_economic_event")
                            )
                            result = s.execute(stmt)
                            upserted += result.rowcount
                        except Exception:
                            continue
                    s.commit()
                await asyncio.sleep(0.1)  # FRED rate limit: 120/min
            except Exception as exc:
                log.warning("economic.fred_release_dates_error", release_id=release_id, error=str(exc))

    return {"synced": upserted, "skipped": None}


# IF-04: FRED series ID -> CrossAssetReading column. Deliberately just 5 series (yield curve +
# credit spread + dollar index) — see CrossAssetReading's own docstring for why gold/oil/VIX
# term structure are a separate follow-on, not this slice. All 5 series IDs were verified live
# against the real FRED API before being hardcoded here (2026-08-19) — not guessed.
_CROSS_ASSET_SERIES: list[tuple[str, str]] = [
    ("DGS10",        "yield_10y"),
    ("DGS2",         "yield_2y"),
    ("T10Y2Y",       "yield_curve_2s10s"),
    ("BAMLH0A0HYM2", "hy_spread"),
    ("DTWEXBGS",     "dxy"),
]


async def sync_cross_asset(lookback_days: int = 30) -> dict:
    """IF-04: fetch the latest cross-asset readings and upsert one row per calendar day into
    cross_asset_readings. Reuses the SAME FRED api_key/rate-limit/error-handling pattern as
    sync_fred() above rather than a second, independently-written HTTP client.

    Each series is fetched independently (a failure on one series must not block the others —
    matching sync_fred()'s own per-series try/except), and each observation is upserted keyed
    on its OWN date, updating only that series' column via on_conflict_do_update — so fetching
    5 series across 5 separate calls correctly accumulates into one row per day rather than
    5 series each creating (and then immediately violating the unique constraint on) their own
    row.
    """
    api_key = getattr(_settings, "fred_api_key", "")
    if not api_key:
        log.info("economic.cross_asset_skip", reason="FRED_API_KEY not set")
        return {"synced": 0, "skipped": "no_api_key"}

    base_url = "https://api.stlouisfed.org/fred"
    observation_start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    upserted = 0

    async with httpx.AsyncClient(timeout=15.0) as client:
        for series_id, column in _CROSS_ASSET_SERIES:
            try:
                r = await client.get(
                    f"{base_url}/series/observations",
                    params={
                        "series_id": series_id,
                        "api_key": api_key,
                        "file_type": "json",
                        "observation_start": observation_start,
                        "sort_order": "asc",
                        "limit": 100,
                    },
                )
                if r.status_code != 200:
                    log.warning("economic.cross_asset_fetch_failed", series=series_id, status=r.status_code)
                    continue
                observations = r.json().get("observations", [])
                with SessionLocal() as s:
                    for obs in observations:
                        if obs["value"] in (".", ""):
                            continue
                        try:
                            as_of = datetime.strptime(obs["date"], "%Y-%m-%d").date()
                            value = float(obs["value"])
                            stmt = (
                                pg_insert(CrossAssetReading)
                                .values(as_of=as_of, **{column: value})
                                .on_conflict_do_update(
                                    index_elements=["as_of"],
                                    set_={column: value},
                                )
                            )
                            result = s.execute(stmt)
                            upserted += result.rowcount
                        except Exception:
                            continue
                    s.commit()
                await asyncio.sleep(0.1)  # FRED rate limit: 120/min
            except Exception as exc:
                log.warning("economic.cross_asset_error", series=series_id, error=str(exc))

    return {"synced": upserted, "skipped": None}


def get_latest_cross_asset_reading() -> dict | None:
    """Most recent cross_asset_readings row, plus a rule-based RISK_ON/RISK_OFF/NEUTRAL read.

    The classification is deliberately simple and stated, not hidden — a real backtest of these
    specific thresholds has not been run (unlike this app's live-decision-affecting parameters,
    which all go through walk-forward validation before being trusted); this is a measured
    macro CONTEXT panel, matching the honesty convention already established for CAPE/options-
    flow sentiment elsewhere in this app, not a validated trading signal.
    """
    with SessionLocal() as s:
        row = s.execute(
            select(CrossAssetReading).order_by(CrossAssetReading.as_of.desc()).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None

        notes: list[str] = []
        risk_score = 0  # positive = risk-on, negative = risk-off
        if row.yield_curve_2s10s is not None:
            if row.yield_curve_2s10s < 0:
                risk_score -= 1
                notes.append("Yield curve inverted (2s10s < 0) — historically a recession-risk signal, though with a long and variable lead time.")
            elif row.yield_curve_2s10s > 1.0:
                risk_score += 1
                notes.append("Yield curve steep (2s10s > 1.0%) — historically associated with expansion/growth conditions.")
        if row.hy_spread is not None:
            if row.hy_spread > 5.0:
                risk_score -= 1
                notes.append(f"High-yield credit spread elevated ({row.hy_spread:.2f}%) — signals rising credit stress/risk aversion.")
            elif row.hy_spread < 3.5:
                risk_score += 1
                notes.append(f"High-yield credit spread tight ({row.hy_spread:.2f}%) — signals low credit stress.")

        if risk_score >= 1:
            direction = "RISK_ON"
        elif risk_score <= -1:
            direction = "RISK_OFF"
        else:
            direction = "NEUTRAL"

        return {
            "as_of": row.as_of.isoformat(),
            "yield_2y": row.yield_2y,
            "yield_10y": row.yield_10y,
            "yield_curve_2s10s": row.yield_curve_2s10s,
            "hy_spread": row.hy_spread,
            "dxy": row.dxy,
            "direction": direction,
            "notes": notes,
        }


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


def get_recent_economic_events(
    days: int = 30, country: str = "US", min_importance: str = "medium"
) -> list[dict]:
    """Return recently released economic data.

    AUD264-ECON-ENDPOINT-FILTERS-HIGH-ONLY: previously hardcoded to `importance == "high"`,
    silently excluding retail_sales/consumer_conf/housing_starts/jobless_claims/gdp — all
    genuinely tagged "medium" in _FRED_RELEASES/_FRED_SERIES, and all real, already-populated
    event types since AUD264-RELEASE-POLL-COVERS-4-OF-10 fixed the poll that writes their
    actual_value. `min_importance` now defaults to "medium" (includes both "high" and
    "medium" — there is no lower tier in this codebase) so every synced release type is
    visible by default; a caller that genuinely only wants FOMC/CPI/NFP-grade releases can
    still pass min_importance="high" explicitly.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    _importance_tiers = {"high": {"high"}, "medium": {"high", "medium"}}
    allowed = _importance_tiers.get(min_importance, _importance_tiers["medium"])
    with SessionLocal() as s:
        rows = s.execute(
            select(EconomicEvent)
            .where(
                EconomicEvent.country == country,
                EconomicEvent.event_date >= since,
                EconomicEvent.event_date <= now,
                EconomicEvent.importance.in_(allowed),
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
