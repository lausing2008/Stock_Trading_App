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

    # Daily sync jobs (UTC times)
    _scheduler.add_job(job_sync_economic,      "cron", hour=6,  minute=0,  id="sync_economic")
    _scheduler.add_job(job_sync_earnings,      "cron", hour=6,  minute=30, id="sync_earnings")
    _scheduler.add_job(job_sync_insider,       "cron", hour=7,  minute=0,  id="sync_insider")
    _scheduler.add_job(job_sync_congress,      "cron", hour=7,  minute=30, id="sync_congress")
    _scheduler.add_job(job_sync_political,     "cron", hour=8,  minute=0,  id="sync_political")
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
