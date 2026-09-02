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


async def job_sync_cross_asset():
    # IF-04: yield curve / credit spread / dollar index daily readings — a genuinely
    # different SHAPE from sync_fred()'s row-per-release-event rows (one row per calendar
    # day, all continuous numeric fields), so it gets its own sync + its own table.
    await _run("sync_cross_asset", economic.sync_cross_asset())


async def job_sync_earnings():
    await _run("sync_earnings", earnings.sync_all_earnings())


async def job_sync_todays_earnings():
    # AUD-EARNINGS-INTRADAY-SYNC-GAP: closes the gap where a company reporting during
    # market hours or after the close (the normal case) never had eps_actual picked up
    # until the NEXT morning's 06:30 UTC sync — see sync_todays_earnings()'s own docstring.
    await _run("sync_todays_earnings", earnings.sync_todays_earnings())


async def job_check_earnings_impact_poll():
    await _run("check_earnings_impact_poll", earnings.check_earnings_impact_poll())


async def job_backfill_post_earnings_returns():
    # AUD-EARNINGSFORECAST-EXTEND: post_earnings_return_1d/_5d were real, DEFINED columns that
    # had never been written by any job — closes that gap. A daily cron (not check_earnings_
    # impact_poll's tighter 5-min interval) since a 5-trading-day-later return genuinely can't
    # be measured any faster than real trading days elapse.
    await _run("backfill_post_earnings_returns", earnings.backfill_post_earnings_returns())


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
    # T249-MARKETMOVER-P2: 8:30am-1:00pm ET covers every BLS/BEA release time (all release-day
    # data is published at 8:30 ET), widened from the original 8:30-10:00 window
    # (BUG-CPIPOLL-WINDOWTOOSHORT) after a real CPI release still hadn't posted to FRED by
    # 9:58am ET on 2026-08-12 — the original 15-60min margin assumption was not always enough.
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
    asyncio.create_task(job_sync_cross_asset())

    # Daily sync jobs (UTC times)
    _scheduler.add_job(job_sync_economic,      "cron", hour=6,  minute=0,  id="sync_economic")
    _scheduler.add_job(job_sync_fred_release_dates, "cron", hour=6, minute=15, id="sync_fred_release_dates")
    _scheduler.add_job(job_sync_cross_asset,   "cron", hour=6,  minute=20, id="sync_cross_asset")
    _scheduler.add_job(job_sync_earnings,      "cron", hour=6,  minute=30, id="sync_earnings")
    # AUD-EARNINGS-INTRADAY-SYNC-GAP: sync_earnings above only runs once/day before the US
    # open — a report released during market hours or after the close (the normal case) sat
    # with eps_actual=NULL until the next morning otherwise, silently starving both
    # check_earnings_reactions() and check_earnings_impact_poll() of the data they need to
    # fire on. Runs every 15 min, 7am-9pm ET weekdays (covers pre-market, regular session,
    # and after-hours prints — the vast majority of real releases land in the last hour).
    # Cheap: a single indexed query, zero yfinance calls, whenever nobody unresolved is left.
    _scheduler.add_job(
        job_sync_todays_earnings,
        CronTrigger(minute="*/15", hour="7-20", day_of_week="mon-fri", timezone="America/New_York"),
        id="sync_todays_earnings",
    )
    # T249-EARNINGS-LLM-IMPACT: unlike macro's release-day-armed polls (exact release times are
    # known in advance), earnings land unpredictably per company throughout the day — a plain
    # 5-min interval poll is the simplest correct fit. Cheap no-op (one indexed query, zero
    # LLM calls) whenever there's nothing new to generate, and fails closed on the
    # earnings_llm_impact_enabled admin flag before even querying the DB.
    _scheduler.add_job(
        job_check_earnings_impact_poll, "interval", minutes=5, id="check_earnings_impact_poll",
    )
    # AUD-EARNINGSFORECAST-EXTEND: once daily is plenty — a 5-trading-day-later return can't be
    # measured any faster regardless of cadence. 06:40 UTC, right after sync_earnings (06:30)
    # so any report synced that morning has its DB row available, though the real measurement
    # for most rows won't be fillable until several days later regardless.
    _scheduler.add_job(
        job_backfill_post_earnings_returns, "cron", hour=6, minute=40, id="backfill_post_earnings_returns",
    )
    # T323-DARKPOOL: was once-daily (07:00 UTC only) despite SEC EDGAR itself indexing a filed
    # Form 4 in under 60 seconds (confirmed directly against EDGAR's own full-text-search FAQ) —
    # a real, free freshness gap this app was leaving on the table with no data-quality tradeoff
    # at all, since Form 4 IS the primary source (see T323-DARKPOOL's own scoping note: UW's own
    # insider endpoint is confirmed to be the identical Form 4 data, re-served — no vendor can be
    # faster than the SEC's own filing system). Every 4h rather than hourly: sync_all_insider()
    # loops the WHOLE tracked-stock universe with a real 0.5s sleep between EDGAR calls (see its
    # own docstring), so hourly would be a real 24x increase in daily EDGAR request volume for
    # only a marginal freshness gain (most symbols have no new Form 4 in any given hour anyway) —
    # 4h closes most of the real gap (worst case ~4h stale vs. the old ~24h) without needlessly
    # increasing load against SEC's own fair-access expectations.
    _scheduler.add_job(job_sync_insider,       "cron", hour="3,7,11,15,19,23",  minute=0,  id="sync_insider")
    _scheduler.add_job(job_sync_congress,      "cron", hour=7,  minute=30, id="sync_congress")
    _scheduler.add_job(job_sync_political,     "cron", hour=8,  minute=0,  id="sync_political")
    _scheduler.add_job(job_sync_cape,          "cron", hour=8,  minute=45, id="sync_cape")

    # T249-MARKETMOVER-P2: release-day-armed fast polls. Both are cheap no-ops on non-release
    # days (check_release_day_fast_poll/check_fomc_statement_poll each query the calendar/FOMC
    # dates first and return immediately if nothing is due). America/New_York handles DST
    # correctly without manual UTC-offset math, matching send_paper_portfolio_digest's pattern
    # in market-data's scheduler.py.
    #
    # BUG-CPIPOLL-WINDOWTOOSHORT (2026-08-12): the window was originally 8:30-9:59am ET, sized
    # around BLS's typical same-second 8:30am release time — but on 2026-08-12, FRED's own
    # realtime_start for that day's real CPI release was AFTER 9:58am ET (the poll's last check
    # that morning still found nothing), so the release sat undetected for the rest of the day
    # until manually backfilled. Widened through 12:59pm ET — still a cheap no-op on non-release
    # days (the due_today DB query gates every FRED call), so the added cost is a few more FRED
    # calls, only on the handful of real release days/month, only for as long as a release
    # genuinely remains undetected that morning.
    _scheduler.add_job(
        job_check_release_day_fast_poll,
        CronTrigger(minute="*/2", hour="8-12", day_of_week="mon-fri", timezone="America/New_York"),
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
