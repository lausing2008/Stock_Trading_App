"""Sourced product-event watches with durable per-user decisions.

The rule catalogue describes verified company roles. It does not translate event direction
into a trade signal or claim a revenue/stock return. A new product needs a reviewed rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlparse

from sqlalchemy import select
from db import (AppNotification, NewsImpactEvent, NewsImpactReceipt, RealtimeNewsItem,
                Stock, User, Watchlist, WatchlistItem)
from common.outbox import enqueue

ALERT_TYPE = "product_impact"
EVENT_KEY = "surface_rtx_spark_preorders_2026"
OFFICIAL_URL = "https://blogs.windows.com/devices/2026/10/07/pre-order-our-most-powerful-surface-devices-ever/"
IMPACTS = [
    {"symbol": "MSFT", "direction": "positive_watch", "role": "device_maker",
     "reason": "Surface Laptop Ultra preorders opened; demand and margin impact are unmeasured."},
    {"symbol": "NVDA", "direction": "positive_watch", "role": "cpu_gpu_supplier",
     "reason": "The device uses NVIDIA Grace CPU and Blackwell GPU; shipment economics are unmeasured."},
    {"symbol": "INTC", "direction": "negative_watch", "role": "excluded_cpu_supplier",
     "reason": "Intel does not supply this device's CPU slot; lost revenue and share impact are unmeasured."},
]
AFFECTED = frozenset(item["symbol"] for item in IMPACTS)
MAX_LIVE_AGE = timedelta(hours=4)


def recognize(row) -> bool:
    """A bounded launch-milestone rule; generic RTX/Surface mentions cannot trigger it."""
    headline = (row.headline or "").lower()
    url = (row.url or "").lower()
    published = row.published_at
    if published is None or published.date().isoformat() != "2026-10-07":
        return False
    official = (urlparse(url).hostname == "blogs.windows.com"
                and "/2026/10/07/pre-order-our-most-powerful-surface-devices-ever/" in url)
    provider = (row.source == "alpaca" and "microsoft" in headline
                and "rtx spark" in headline and "nvidia" in headline
                and ("shipping" in headline or "pre-order" in headline))
    return official or provider


def _watchers(session) -> dict[int, set[str]]:
    rows = session.execute(
        select(WatchlistItem.user_id, Watchlist.user_id, Stock.symbol)
        .join(Stock, Stock.id == WatchlistItem.stock_id)
        .outerjoin(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
        .where(Stock.symbol.in_(AFFECTED))
    ).all()
    matched: dict[int, set[str]] = {}
    for direct_user, list_user, symbol in rows:
        user_id = direct_user or list_user
        if user_id is not None:
            matched.setdefault(user_id, set()).add(symbol)
    return matched


def _body(event: NewsImpactEvent) -> str:
    lines = [event.title, "Published: " + event.published_at.isoformat(),
             "Official source: " + event.source_url,
             "Observed headline: " + (event.observed_url or "unavailable")]
    lines += [f"{i['symbol']}: {i['direction']} — {i['reason']}" for i in event.impacts]
    lines.append("Research watch only. No price move, revenue effect, or option return is established.")
    return "\n".join(lines)


def process_recent(session, *, now: datetime | None = None, may_send=None,
                   may_notify=None) -> dict:
    """Discover one reviewed event; record audience decisions and enqueue fresh watches.

    The 14-day scan makes a previously missed event visible without emailing old news.
    Re-runs are idempotent via event and receipt constraints plus the outbox event ID.
    """
    now = now or datetime.now(timezone.utc)
    # alert_audience, not scheduler: the same functions, without importing apscheduler and the
    # rest of the service to ask a preference question. That import weight is why this producer
    # was only ever tested with an injected lambda that did not match the shipped rule.
    if may_send is None:
        from .alert_audience import may_send as may_send
    if may_notify is None:
        from .alert_audience import may_notify as may_notify
    naive_now = now.replace(tzinfo=None)
    news = session.execute(
        select(RealtimeNewsItem)
        .where(RealtimeNewsItem.published_at >= naive_now - timedelta(days=14),
               RealtimeNewsItem.published_at <= naive_now,
               RealtimeNewsItem.headline.ilike("%RTX Spark%"))
        .order_by(RealtimeNewsItem.published_at.asc()).limit(100)
    ).scalars().all()
    observed = next((item for item in news if recognize(item)), None)
    if observed is None:
        return {"status": "no_verified_event", "recipients": 0}
    event = session.execute(select(NewsImpactEvent).where(
        NewsImpactEvent.event_key == EVENT_KEY)).scalar_one_or_none()
    if event is None:
        event = NewsImpactEvent(event_key=EVENT_KEY,
            title="Surface Laptop Ultra RTX Spark preorders and shipping milestone",
            source_url=OFFICIAL_URL, observed_url=observed.url,
            published_at=observed.published_at, impacts=IMPACTS,
            evidence_status="official_spec_and_provider_observation")
        session.add(event)
        session.flush()
    watchers = _watchers(session)
    created = 0
    for user_id, symbols in watchers.items():
        if session.execute(select(NewsImpactReceipt.id).where(
                NewsImpactReceipt.event_id == event.id,
                NewsImpactReceipt.user_id == user_id)).first():
            continue
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            continue
        receipt = NewsImpactReceipt(event_id=event.id, user_id=user_id,
            matched_symbols=sorted(symbols), state="observed", reason=None)
        session.add(receipt)
        event_age = now - observed.published_at.replace(tzinfo=timezone.utc)
        if event_age > MAX_LIVE_AGE:
            receipt.state, receipt.reason = "historical_no_alert", "event older than four hours when discovered"
            continue
        # AQ-REVIEW-3 (2026-10-12): AUDIENCE first, DELIVERY CHANNEL second. These were fused,
        # and `may_send` answers False for an account with no email address — so an opted-in user
        # without one was recorded as `preference_disabled`, which is not what happened, and
        # never received the in-app notice the `in_app_only` branch below exists to give them.
        if not may_notify(session, user, ALERT_TYPE):
            receipt.state, receipt.reason = "preference_disabled", "product impact preference disabled"
            continue
        body = _body(event)
        in_app = ("Surface Laptop Ultra: MSFT product launch watch; NVDA processor supplier win; "
                  "INTC excluded from this device's CPU slot. Revenue and stock effects unmeasured. "
                  "See News > Product impact watches.")
        session.add(AppNotification(user_id=user_id, alert_id=EVENT_KEY,
            symbol=sorted(symbols)[0], message=in_app, triggered_at=naive_now,
            read=False))
        if not may_send(session, user, ALERT_TYPE):
            # Reached with the preference ENABLED, so the cause is the channel, not consent.
            # Distinguished because "we may not write to you" and "you asked us not to" are
            # different facts and a receipt that conflates them misreports the audience.
            receipt.state, receipt.reason = "in_app_only", (
                "no recipient email" if not (user.email or "").strip()
                else "email delivery not available for this account")
            created += 1
            continue
        outbox_id = f"product-impact:{EVENT_KEY}:{user_id}"
        row, _ = enqueue(session, event_id=outbox_id, user_id=user_id,
            recipient=user.email.strip(), alert_type=ALERT_TYPE,
            subject=event.title, body_text=body,
            body_html="<br>".join(escape(line) for line in body.splitlines()),
            expires_at=observed.published_at + MAX_LIVE_AGE)
        receipt.state, receipt.reason = "email_queued", None
        receipt.outbox_event_id = row.event_id
        created += 1
    return {"status": "recorded", "recipients": len(watchers), "new_alerts": created}
