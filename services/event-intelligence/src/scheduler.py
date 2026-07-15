"""Background scheduler — daily sync jobs for all event intelligence data."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from common.config import get_settings

from .services import economic, earnings, insider, congress, institutional, political, catalyst, valuation, macro_reaction

log = structlog.get_logger()
_settings = get_settings()
_scheduler: AsyncIOScheduler | None = None


async def _run(name: str, coro) -> None:
    try:
        log.info("event_sched.start", job=name)
        result = await coro
        log.info("event_sched.done", job=name, result=result)
    except Exception as exc:
        log.error("event_sched.error", job=name, error=str(exc))


async def job_sync_economic():
    await _run("sync_economic", economic.sync_fred())


async def job_sync_fred_release_dates():
    # T249-MARKETMOVER-P0: distinct from sync_fred() above — that writes reference-period-
    # dated rows (e.g. event_date=2026-06-01 for June's CPI data). This writes the REAL
    # publication-date calendar (e.g. 2026-07-14, when June's CPI was actually released),
    # which is what any "alert before/after the announcement" feature needs to schedule off.
    await _run("sync_fred_release_dates", economic.sync_fred_release_dates())


async def job_sync_earnings():
    await _run("sync_earnings", earnings.sync_all_earnings())


async def job_sync_insider():
    await _run("sync_insider", insider.sync_all_insider())


async def job_sync_congress():
    await _run("sync_congress", congress.sync_congress_trades())


async def job_sync_institutional():
    await _run("sync_institutional", institutional.sync_institutional())


async def job_sync_political():
    await _run("sync_political", political.sync_political_contracts())


async def job_sync_cape():
    await _run("sync_cape_current", valuation.sync_cape_current())
    await _run("sync_cape_history", valuation.sync_cape_history())


async def job_check_release_day_fast_poll():
    # T249-MARKETMOVER-P2: 8:30-10:00 ET covers every BLS/BEA release time (all release-day
    # data is published at 8:30 ET) with margin for FRED's own 15-60min typical ingestion lag.
    await _run("check_release_day_fast_poll", macro_reaction.check_release_day_fast_poll())


async def job_check_fomc_statement_poll():
    await _run("check_fomc_statement_poll", macro_reaction.check_fomc_statement_poll())


async def job_recompute_catalyst():
    # Fetch latest ta_score per stock from signals table
    from db import SessionLocal
    from sqlalchemy import text
    _tech_scores = {}
    _atr_pcts: dict[int, float] = {}
    try:
        with SessionLocal() as _s:
            rows = _s.execute(text(
                "SELECT DISTINCT ON (stock_id) stock_id, (reasons->>'ta_score')::float AS ta_score, "
                "(reasons->>'atr_14_pct')::float AS atr_14_pct "
                "FROM signals WHERE reasons->>'ta_score' IS NOT NULL "
                "ORDER BY stock_id, ts DESC"
            )).fetchall()
            _tech_scores = {r[0]: float(r[1]) for r in rows if r[1] is not None}
            # T237-EI3: _compute_risk_score's "Volatility risk (ATR % passed from signal)" branch
            # was permanently dead — no caller anywhere ever passed a non-default atr_pct, so
            # highly volatile stocks got 0 risk points from this branch instead of the intended
            # up-to-+20. atr_14_pct lives in this same signals.reasons JSONB the ta_score query
            # already reads, so wiring it through here is a natural extension of the same query.
            _atr_pcts = {r[0]: float(r[2]) for r in rows if r[2] is not None}
    except Exception as exc:
        log.error("scheduler.tech_scores_fetch_failed", error=str(exc))
    await _run("recompute_catalyst", catalyst.recompute_all(technical_scores=_tech_scores, atr_pcts=_atr_pcts))


async def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")

    # Seed FOMC dates immediately at startup
    try:
        economic._seed_fomc()
        log.info("event_sched.fomc_seeded")
    except Exception as exc:
        log.warning("event_sched.fomc_seed_fail", error=str(exc))

    # T249-MARKETMOVER-P0: seed the real release-date calendar immediately at startup too,
    # so a fresh deploy doesn't leave the calendar empty until the next 06:15 cron run.
    asyncio.create_task(job_sync_fred_release_dates())

    # Daily sync jobs (UTC times)
    _scheduler.add_job(job_sync_economic,      "cron", hour=6,  minute=0,  id="sync_economic")
    _scheduler.add_job(job_sync_fred_release_dates, "cron", hour=6, minute=15, id="sync_fred_release_dates")
    _scheduler.add_job(job_sync_earnings,      "cron", hour=6,  minute=30, id="sync_earnings")
    _scheduler.add_job(job_sync_insider,       "cron", hour=7,  minute=0,  id="sync_insider")
    _scheduler.add_job(job_sync_congress,      "cron", hour=7,  minute=30, id="sync_congress")
    _scheduler.add_job(job_sync_political,     "cron", hour=8,  minute=0,  id="sync_political")
    _scheduler.add_job(job_sync_cape,          "cron", hour=8,  minute=45, id="sync_cape")

    # T249-MARKETMOVER-P2: release-day-armed fast polls. Both are cheap no-ops on non-release
    # days (check_release_day_fast_poll/check_fomc_statement_poll each query the calendar/FOMC
    # dates first and return immediately if nothing is due). America/New_York handles DST
    # correctly without manual UTC-offset math, matching send_paper_portfolio_digest's pattern
    # in market-data's scheduler.py.
    _scheduler.add_job(
        job_check_release_day_fast_poll,
        CronTrigger(minute="*/2", hour="8-9", day_of_week="mon-fri", timezone="America/New_York"),
        id="check_release_day_fast_poll",
    )
    _scheduler.add_job(
        job_check_fomc_statement_poll,
        CronTrigger(minute="*", hour="14", day_of_week="mon-fri", timezone="America/New_York"),
        id="check_fomc_statement_poll",
    )
    _scheduler.add_job(job_recompute_catalyst, "cron", hour=0,  minute=0,  id="recompute_catalyst_midnight")
    # EI-F10: was hour=6 (before earnings/insider/congress sync all complete by 07:30) — catalyst
    # score depends on all three (see catalyst.py compute_risk_score/compute_composite_score), so
    # the 06:00 run always used stale data for anything that changed overnight, invisible until
    # the 12:00 recompute (5.5h+ window). Moved to 08:15 — strictly after sync_congress (07:30)
    # and sync_political (08:00, not a catalyst dependency but scheduled last in this block).
    _scheduler.add_job(job_recompute_catalyst, "cron", hour=8,  minute=15, id="recompute_catalyst_morning")
    _scheduler.add_job(job_recompute_catalyst, "cron", hour=12, minute=0,  id="recompute_catalyst_noon")
    _scheduler.add_job(job_recompute_catalyst, "cron", hour=18, minute=0,  id="recompute_catalyst_evening")

    # Institutional: weekly on Sunday
    _scheduler.add_job(job_sync_institutional, "cron", day_of_week="sun", hour=8, minute=0, id="sync_institutional")

    _scheduler.start()
    log.info("event_sched.started", jobs=len(_scheduler.get_jobs()))
