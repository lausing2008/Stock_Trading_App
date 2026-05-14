"""Market-aware scheduler — refreshes prices, rankings, signals, and ML models
around trading hours for US (NYSE/NASDAQ) and HK (HKEX) markets.

Schedule (times are in the local market timezone, DST handled automatically):

  US (America/New_York):
    09:00  pre-open         ingest + rankings + signals
    10:45  intra-day 1      ingest + rankings + signals
    12:45  intra-day 2      ingest + rankings + signals
    14:45  intra-day 3      ingest + rankings + signals
    16:30  post-close       ingest + rankings + signals + ML retrain

  HK (Asia/Hong_Kong, UTC+8, no DST):
    09:00  pre-open         ingest + rankings + signals
    10:30  intra-day 1      ingest + rankings + signals
    14:15  intra-day 2      ingest + rankings + signals  (post-lunch)
    15:30  intra-day 3      ingest + rankings + signals
    16:30  post-close       ingest + rankings + signals + ML retrain
"""
from __future__ import annotations

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

from sqlalchemy import select

from common.config import get_settings
from common.logging import get_logger
from db import PriceAlert, SignalAlert, SessionLocal, Stock

from .ingestion import ingest_universe
from .email_service import send_price_alert_email, send_signal_alert_email

log = get_logger("scheduler")
_settings = get_settings()
_scheduler: BackgroundScheduler | None = None


def _symbols_for(market: str) -> list[str]:
    with SessionLocal() as session:
        return list(
            session.execute(
                select(Stock.symbol).where(Stock.active.is_(True), Stock.market == market)
            ).scalars()
        )


def _post(url: str, **kwargs) -> None:
    try:
        with httpx.Client(timeout=15) as client:
            client.post(url, **kwargs)
    except Exception as exc:
        log.warning("scheduler.http_error", url=url, error=str(exc))


def _refresh_market(market: str, *, post_close: bool = False) -> None:
    symbols = _symbols_for(market)
    if not symbols:
        log.info("scheduler.skip", market=market, reason="no_symbols")
        return

    log.info("scheduler.refresh_start", market=market, count=len(symbols), post_close=post_close)

    ingest_universe(symbols, "1d")

    _post(f"{_settings.ranking_engine_url}/rankings/refresh", params={"market": market})
    _post(f"{_settings.signal_engine_url}/signals/refresh", params={"market": market})

    if post_close:
        _post(f"{_settings.ml_prediction_url}/ml/train_all")

    check_signal_alerts()
    log.info("scheduler.refresh_done", market=market, post_close=post_close)


_QUALIFYING_TRANSITIONS = {
    ("SELL", "HOLD"), ("SELL", "WAIT"), ("SELL", "BUY"),
    ("WAIT", "HOLD"), ("WAIT", "BUY"),
    ("HOLD", "BUY"),
}
_BULLISH_ANALYST = {"buy", "strong_buy", "strongbuy", "outperform"}


def check_signal_alerts() -> None:
    """Fire signal-change notifications when AI Signal improves AND analyst is BUY/STRONG BUY."""
    try:
        with SessionLocal() as session:
            alerts = session.execute(select(SignalAlert)).scalars().all()
            if not alerts:
                return

            symbols = list({a.symbol for a in alerts})

            # Fetch current signals (keep full payload for reasons)
            signals: dict[str, str] = {}
            signal_details: dict[str, dict] = {}
            for sym in symbols:
                try:
                    r = httpx.get(f"{_settings.signal_engine_url}/signals/{sym}", timeout=10)
                    if r.status_code == 200:
                        payload = r.json()
                        signals[sym] = payload.get("signal", "")
                        signal_details[sym] = payload
                except Exception:
                    pass

            # Fetch analyst ratings + fundamentals (earnings, insider data)
            analyst_ratings: dict[str, str] = {}
            fundamentals_cache: dict[str, dict] = {}
            for sym in symbols:
                try:
                    r = httpx.get(f"{_settings.market_data_url}/stocks/{sym}/fundamentals", timeout=10)
                    if r.status_code == 200:
                        payload = r.json()
                        analyst_ratings[sym] = (payload.get("recommendation") or "").lower()
                        fundamentals_cache[sym] = payload
                except Exception:
                    pass

            fired = 0
            for alert in alerts:
                current = signals.get(alert.symbol)
                if not current:
                    continue

                prev = alert.last_signal

                # Always update tracked signal, even if we don't fire
                if prev == current:
                    continue

                qualifying = (prev, current) in _QUALIFYING_TRANSITIONS
                analyst_ok = analyst_ratings.get(alert.symbol, "") in _BULLISH_ANALYST

                alert.last_signal = current  # update regardless

                if not qualifying or not analyst_ok:
                    continue

                email_ok = send_signal_alert_email(
                    to=alert.email or "",
                    symbol=alert.symbol,
                    prev_signal=prev,
                    new_signal=current,
                    analyst=analyst_ratings.get(alert.symbol, "buy"),
                    signal_data=signal_details.get(alert.symbol, {}),
                    fundamentals=fundamentals_cache.get(alert.symbol),
                )
                if email_ok:
                    fired += 1
                    log.info("signal_alert.fired", symbol=alert.symbol, prev=prev, current=current)

            session.commit()
            if fired:
                log.info("signal_alert.check_done", fired=fired)
    except Exception as exc:
        log.error("signal_alert.check_error", error=str(exc))


def check_price_alerts() -> None:
    """Check all untriggered alerts against latest live prices and fire emails."""
    try:
        import yfinance as yf
        with SessionLocal() as session:
            alerts = session.execute(
                select(PriceAlert).where(PriceAlert.triggered.is_(False))
            ).scalars().all()
            if not alerts:
                return

            # Fetch live prices for all unique symbols at once
            symbols = list({a.symbol for a in alerts})
            tickers = yf.Tickers(" ".join(symbols))
            prices: dict[str, float] = {}
            for sym in symbols:
                try:
                    p = tickers.tickers[sym].fast_info.last_price
                    if p:
                        prices[sym] = float(p)
                except Exception:
                    pass

            fired = 0
            for alert in alerts:
                price = prices.get(alert.symbol)
                if price is None:
                    continue
                should_trigger = (
                    (alert.condition.value == "above" and price >= alert.threshold) or
                    (alert.condition.value == "below" and price <= alert.threshold)
                )
                if not should_trigger:
                    continue

                # Send email first; only mark triggered on success so failed
                # deliveries are retried on the next check cycle.
                email_ok = True
                if alert.email:
                    email_ok = send_price_alert_email(
                        to=alert.email,
                        symbol=alert.symbol,
                        condition=alert.condition.value,
                        threshold=alert.threshold,
                        price=price,
                        note=alert.note,
                    )

                if email_ok:
                    alert.triggered = True
                    alert.triggered_at = datetime.utcnow()
                    fired += 1
                    log.info("alert.triggered", symbol=alert.symbol, price=price, threshold=alert.threshold)
                else:
                    log.warning("alert.email_failed_will_retry", symbol=alert.symbol, email=alert.email)

            session.commit()
            if fired:
                log.info("alert.check_done", fired=fired, checked=len(alerts))
    except Exception as exc:
        log.error("alert.check_error", error=str(exc))


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")

    # ── US Market (America/New_York — DST handled automatically) ────────────
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_pre_open", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=10, minute=45, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_intra_1", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=12, minute=45, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_intra_2", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=14, minute=45, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_intra_3", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("US", post_close=True),
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_post_close", replace_existing=True,
    )

    # ── HK Market (Asia/Hong_Kong — UTC+8, no DST) ──────────────────────────
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_pre_open", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=10, minute=30, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_intra_1", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=14, minute=15, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_intra_2", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_intra_3", replace_existing=True,
    )
    _scheduler.add_job(
        lambda: _refresh_market("HK", post_close=True),
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_post_close", replace_existing=True,
    )

    # ── Price alert checker — every minute ──────────────────────────────────
    _scheduler.add_job(
        check_price_alerts,
        "interval",
        minutes=1,
        id="price_alert_check",
        replace_existing=True,
    )

    _scheduler.start()
    log.info("scheduler.started", jobs=11)
