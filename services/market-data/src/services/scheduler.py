"""Daily scheduler — refreshes prices outside trading hours."""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from common.logging import get_logger
from db import SessionLocal, Stock

from .ingestion import ingest_universe

log = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None


def _daily_refresh() -> None:
    with SessionLocal() as session:
        symbols = list(session.execute(select(Stock.symbol).where(Stock.active.is_(True))).scalars())
    if not symbols:
        log.info("scheduler.skip", reason="empty_universe")
        return
    log.info("scheduler.run", count=len(symbols))
    ingest_universe(symbols, "1d")


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    # Daily 22:30 UTC — after US close, before HK open
    _scheduler.add_job(_daily_refresh, CronTrigger(hour=22, minute=30), id="daily_refresh", replace_existing=True)
    _scheduler.start()
    log.info("scheduler.started")
