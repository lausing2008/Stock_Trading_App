"""Background scheduler for news-intelligence — RSS/EDGAR polling on AsyncIOScheduler (this
service's FastAPI app shares one event loop, matching event-intelligence's own scheduler.py
convention) plus the long-lived Alpaca WebSocket task started once at startup.

All blocking I/O (feedparser's internal fetch) is routed through a dedicated ThreadPoolExecutor
via run_in_executor() — matching event-intelligence/src/services/macro_reaction.py's own
established fix for this exact class of bug (a blocking call inside an async def on a shared
event loop stalls every concurrent request to this service).
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .services.edgar_source import fetch_edgar_realtime
from .services.rss_sources import fetch_businesswire, fetch_pr_newswire
from .services.storage import persist_news_items
from .services.alpaca_source import run_alpaca_stream

log = structlog.get_logger()

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="news_poll")
_scheduler: AsyncIOScheduler | None = None
_alpaca_task: asyncio.Task | None = None
_alpaca_stop = asyncio.Event()


async def _poll_pr_newswire() -> None:
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(_executor, fetch_pr_newswire)
    await loop.run_in_executor(_executor, persist_news_items, items, "pr_newswire", "extract")


async def _poll_businesswire() -> None:
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(_executor, fetch_businesswire)
    await loop.run_in_executor(_executor, persist_news_items, items, "businesswire", "extract")


async def _poll_edgar() -> None:
    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(_executor, fetch_edgar_realtime)
    await loop.run_in_executor(_executor, persist_news_items, items, "sec_edgar", "cik")


async def _run_job(name: str, coro) -> None:
    try:
        await coro
        log.info("news_sched.done", job=name)
    except Exception as exc:
        log.error("news_sched.error", job=name, error=str(exc))


async def job_pr_newswire():
    await _run_job("pr_newswire", _poll_pr_newswire())


async def job_businesswire():
    await _run_job("businesswire", _poll_businesswire())


async def job_edgar():
    await _run_job("edgar", _poll_edgar())


async def start_scheduler():
    global _scheduler, _alpaca_task
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    # Both RSS sources were verified live at ~30s (PR Newswire) and near-instant (Business Wire,
    # Last-Modified within the hour of checking) observed latency — polled every minute, cheap
    # relative to that latency floor. EDGAR's real-time feed is polled every 2 minutes, matching
    # the observed cadence of new filings appearing during this rewrite's own live verification.
    _scheduler.add_job(job_pr_newswire, "interval", minutes=1, id="pr_newswire_poll", max_instances=1, coalesce=True)
    _scheduler.add_job(job_businesswire, "interval", minutes=1, id="businesswire_poll", max_instances=1, coalesce=True)
    _scheduler.add_job(job_edgar, "interval", minutes=2, id="edgar_poll", max_instances=1, coalesce=True)
    _scheduler.start()

    _alpaca_task = asyncio.create_task(run_alpaca_stream(_alpaca_stop))

    log.info("news_sched.started", jobs=len(_scheduler.get_jobs()))
