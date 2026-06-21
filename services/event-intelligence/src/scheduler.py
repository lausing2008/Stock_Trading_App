"""Background scheduler — daily sync jobs for all event intelligence data."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from common.config import get_settings

from .services import economic, earnings, insider, congress, institutional, political, catalyst

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


async def job_recompute_catalyst():
    # Fetch latest ta_score per stock from signals table
    from db import SessionLocal
    from sqlalchemy import text
    _tech_scores = {}
    try:
        with SessionLocal() as _s:
            rows = _s.execute(text(
                "SELECT DISTINCT ON (stock_id) stock_id, (reasons->>'ta_score')::float AS ta_score "
                "FROM signals WHERE reasons->>'ta_score' IS NOT NULL "
                "ORDER BY stock_id, ts DESC"
            )).fetchall()
            _tech_scores = {r[0]: float(r[1]) for r in rows if r[1] is not None}
    except Exception:
        pass
    await _run("recompute_catalyst", catalyst.recompute_all(technical_scores=_tech_scores))


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

    # Daily sync jobs (UTC times)
    _scheduler.add_job(job_sync_economic,      "cron", hour=6,  minute=0,  id="sync_economic")
    _scheduler.add_job(job_sync_earnings,      "cron", hour=6,  minute=30, id="sync_earnings")
    _scheduler.add_job(job_sync_insider,       "cron", hour=7,  minute=0,  id="sync_insider")
    _scheduler.add_job(job_sync_congress,      "cron", hour=7,  minute=30, id="sync_congress")
    _scheduler.add_job(job_sync_political,     "cron", hour=8,  minute=0,  id="sync_political")
    _scheduler.add_job(job_recompute_catalyst, "cron", hour=0,  minute=0,  id="recompute_catalyst_midnight")
    _scheduler.add_job(job_recompute_catalyst, "cron", hour=6,  minute=0,  id="recompute_catalyst_morning")
    _scheduler.add_job(job_recompute_catalyst, "cron", hour=12, minute=0,  id="recompute_catalyst_noon")
    _scheduler.add_job(job_recompute_catalyst, "cron", hour=18, minute=0,  id="recompute_catalyst_evening")

    # Institutional: weekly on Sunday
    _scheduler.add_job(job_sync_institutional, "cron", day_of_week="sun", hour=8, minute=0, id="sync_institutional")

    _scheduler.start()
    log.info("event_sched.started", jobs=len(_scheduler.get_jobs()))
