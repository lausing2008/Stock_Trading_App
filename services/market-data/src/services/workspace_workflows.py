"""Account preferences and research workflows. No LLM calls or orders in this module."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from db.models import (
    WorkspacePreference,
    DailyCloseAlert,
    ResearchRelationship,
    EventConnection,
    Stock,
    User,
    Price,
    TimeFrame,
    RealtimeNewsItem,
    AppNotification,
)
from common.market_calendar import calendar_coverage, session_close


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def validate_layout(value):
    if not isinstance(value, dict) or value.get("version", 1) != 1:
        raise ValueError("Unsupported workspace layout")
    if set(value) - {"version", "order", "closed", "hidden", "widths"}:
        raise ValueError("Only layout preferences can be synchronized")
    ident = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
    order = value.get("order")
    if (
        not isinstance(order, list)
        or len(order) > 100
        or any(not isinstance(x, str) or not ident.fullmatch(x) for x in order)
    ):
        raise ValueError("Invalid widget order")
    result = {"version": 1, "order": list(dict.fromkeys(order))}
    for key in ("closed", "hidden", "widths"):
        entries = value.get(key, {})
        if not isinstance(entries, dict) or len(entries) > 100:
            raise ValueError("Invalid widget preferences")
        for k, v in entries.items():
            if not isinstance(k, str) or not ident.fullmatch(k):
                raise ValueError("Invalid widget identifier")
            if (key == "widths" and v not in ("compact", "wide", "full")) or (
                key != "widths" and not isinstance(v, bool)
            ):
                raise ValueError("Invalid widget setting")
        result[key] = dict(entries)
    return result


def save_layout(session, user_id, revision, value):
    layout = validate_layout(value)
    if revision == 0:
        try:
            with session.begin_nested():
                session.add(
                    WorkspacePreference(
                        user_id=user_id, revision=1, layout=layout, updated_at=utcnow()
                    )
                )
                session.flush()
        except IntegrityError:
            return None
        return 1
    result = session.execute(
        update(WorkspacePreference)
        .where(
            WorkspacePreference.user_id == user_id,
            WorkspacePreference.revision == revision,
        )
        .values(layout=layout, revision=revision + 1, updated_at=utcnow())
    )
    return revision + 1 if result.rowcount == 1 else None


def completed_session(market, now):
    """Latest completed regular session plus provider-settle delay. Fail closed beyond coverage.

    Conservative full-session closes defer early-close days to the usual close; they never
    admit an early daily bar. Special full-day closures still require calendar maintenance.
    """
    if market not in ("US", "HK"):
        return None
    aware = (
        now.replace(tzinfo=timezone.utc)
        if now.tzinfo is None
        else now.astimezone(timezone.utc)
    )
    d = aware.astimezone(
        ZoneInfo("Asia/Hong_Kong" if market == "HK" else "America/New_York")
    ).date()
    if d.year > calendar_coverage()["hkex" if market == "HK" else "nyse"]:
        return None
    for _ in range(12):
        end = session_close(market, d)
        if end and aware >= end.astimezone(timezone.utc) + timedelta(minutes=15):
            return d, end.astimezone(timezone.utc).replace(tzinfo=None)
        d -= timedelta(days=1)
    return None


def assess_close(alert, market, now, snapshot):
    """snapshot is fetched AFTER the session ended, never a live quote or an old DB daily row."""
    expected = completed_session(market, now)
    if expected is None:
        return {"state": "unavailable", "reason": "Exchange calendar unavailable"}
    day, closed = expected
    if closed < alert.created_at:
        return {
            "state": "waiting",
            "reason": "Waiting for a session closing after this alert was saved",
        }
    if now - closed > timedelta(hours=36):
        return {
            "state": "stale",
            "reason": "Latest completed session is too old; no retrospective trigger",
        }
    if not snapshot or snapshot.get("session") != day.isoformat():
        return {"state": "unavailable", "reason": "Completed daily bar unavailable"}
    price = snapshot.get("close")
    if (
        isinstance(price, bool)
        or not isinstance(price, (int, float))
        or not math.isfinite(price)
        or price <= 0
    ):
        return {"state": "unavailable", "reason": "Invalid closing price"}
    try:
        fetched = datetime.fromisoformat(snapshot["fetched_at"])
        if fetched.tzinfo is not None:
            fetched = fetched.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, KeyError, TypeError):
        return {
            "state": "unavailable",
            "reason": "Provider observation timestamp is invalid",
        }
    if (
        fetched < closed + timedelta(minutes=15)
        or fetched > now
        or now - fetched > timedelta(minutes=10)
    ):
        return {"state": "stale", "reason": "No fresh post-close provider observation"}
    fired = (
        price >= alert.threshold
        if alert.condition == "above"
        else price <= alert.threshold
    )
    return {
        **snapshot,
        "state": "triggered" if fired else "checked",
        "session_closed_at": closed.isoformat(),
        "basis": "unadjusted regular-session provider daily close",
        "threshold": alert.threshold,
    }


def check_close_alerts(session_factory, fetch_daily, now=None):
    realtime = now is None
    now = now or utcnow()
    with session_factory() as session:
        rows = [
            (a.id, a.symbol)
            for a in session.scalars(
                select(DailyCloseAlert).where(DailyCloseAlert.active.is_(True))
            )
        ]
    snapshots = {}
    for alert_id, symbol in rows:
        with session_factory() as session:
            alert = session.get(DailyCloseAlert, alert_id)
            stock = session.scalar(select(Stock).where(Stock.symbol == symbol))
            owner = session.get(User, alert.user_id) if alert else None
            if (
                not alert
                or not alert.active
                or not stock
                or not stock.active
                or not owner
                or not owner.is_active
            ):
                continue
            market = getattr(stock.market, "value", stock.market)
            expected = completed_session(market, now)
            if (
                expected is None
                or expected[1] < alert.created_at
                or now - expected[1] > timedelta(hours=36)
            ):
                alert.observation = {
                    "state": "waiting",
                    "reason": "No fresh session closed since enrollment",
                }
                session.commit()
                continue
            day = expected[0]
        # No pooled connection is held during the provider call.
        key = (symbol, day)
        if key not in snapshots:
            try:
                snapshots[key] = fetch_daily(symbol, market, day)
            except Exception:
                snapshots[key] = None
        with session_factory() as session:
            alert = session.get(DailyCloseAlert, alert_id)
            if not alert or not alert.active:
                continue
            observation = assess_close(
                alert, market, utcnow() if realtime else now, snapshots[key]
            )
            if observation["state"] == "triggered":
                won = session.execute(
                    update(DailyCloseAlert)
                    .where(
                        DailyCloseAlert.id == alert_id, DailyCloseAlert.active.is_(True)
                    )
                    .values(active=False, observation=observation)
                )
                if won.rowcount:
                    session.add(
                        AppNotification(
                            user_id=alert.user_id,
                            alert_id=f"close-{alert_id}",
                            symbol=symbol,
                            message=f"Daily close {day}: {symbol} {observation['close']:g}, at or {alert.condition} fixed threshold {alert.threshold:g}. In-app alert; no order placed.",
                            current_value=observation["close"],
                            triggered_at=now,
                        )
                    )
            else:
                alert.observation = observation
            session.commit()


def propose_connections(session, now=None):
    """Bounded candidate pass over approved relationships and recent ingested news. Review only."""
    now = now or utcnow()
    count = 0
    edges = session.scalars(
        select(ResearchRelationship).where(ResearchRelationship.approved.is_(True))
    ).all()
    for edge in edges:
        p = edge.payload
        if not p["valid_from"] <= now.date().isoformat() <= p["valid_to"]:
            continue
        rows = session.scalars(
            select(RealtimeNewsItem)
            .where(
                RealtimeNewsItem.symbol == edge.source_symbol,
                RealtimeNewsItem.published_at >= now - timedelta(days=2),
                RealtimeNewsItem.published_at <= now,
            )
            .order_by(RealtimeNewsItem.published_at.desc())
            .limit(100)
        ).all()
        for news in rows:
            if (
                not news.url
                or not p["valid_from"]
                <= news.published_at.date().isoformat()
                <= p["valid_to"]
            ):
                continue
            if not any(word.lower() in news.headline.lower() for word in p["keywords"]):
                continue
            event_key = hashlib.sha256(
                (news.url.strip() + "\n" + news.headline.strip()).encode()
            ).hexdigest()
            if session.scalar(
                select(EventConnection.id).where(
                    EventConnection.event_key == event_key,
                    EventConnection.relationship_id == edge.id,
                )
            ):
                continue
            previous = session.scalars(
                select(EventConnection)
                .where(EventConnection.relationship_id == edge.id)
                .order_by(EventConnection.id.desc())
                .limit(100)
            ).all()
            supersedes = next(
                (c.id for c in previous if c.payload["event"]["url"] == news.url), None
            )
            payload = {
                "supersedes": supersedes,
                "event": {
                    "headline": news.headline,
                    "url": news.url,
                    "published_at": news.published_at.isoformat(),
                    "news_id": news.id,
                },
                "relationship": {
                    "from": edge.source_symbol,
                    "to": edge.target_symbol,
                    **p,
                },
                "interpretation": "Possible downstream exposure; the relationship alone does not establish a stock-price effect.",
                "inference": True,
                "counterargument": p["counterargument"],
                "falsifier": p["falsifier"],
                "horizon_days": p["horizon_days"],
                "direction": p["direction"],
            }
            try:
                with session.begin_nested():
                    session.add(
                        EventConnection(
                            relationship_id=edge.id,
                            event_key=event_key,
                            symbol=edge.target_symbol,
                            payload=payload,
                            status="pending",
                            created_at=now,
                        )
                    )
                    session.flush()
                count += 1
            except IntegrityError:
                pass
    return count


def price_snapshot(session, symbol, cutoff):
    stock = session.scalar(select(Stock).where(Stock.symbol == symbol))
    if stock is None:
        return None
    price = session.scalar(
        select(Price)
        .where(
            Price.stock_id == stock.id,
            Price.timeframe == TimeFrame.D1,
            Price.ts <= cutoff,
        )
        .order_by(Price.ts.desc())
        .limit(1)
    )
    if (
        price is None
        or cutoff - price.ts > timedelta(days=5)
        or not math.isfinite(float(price.close))
        or price.close <= 0
    ):
        return None
    return {
        "price_id": price.id,
        "close": float(price.close),
        "ts": price.ts.isoformat(),
        "currency": stock.currency,
        "basis": "unadjusted stored daily close; not an executable entry",
    }


def resolve_connections(session, now=None):
    now = now or utcnow()
    for card in session.scalars(
        select(EventConnection).where(
            EventConnection.status.in_(("published", "superseded")),
            EventConnection.outcome.is_(None),
        )
    ):
        due = card.published_at + timedelta(days=card.payload["horizon_days"])
        if now < due:
            continue
        end = price_snapshot(session, card.symbol, due)
        base = card.baseline
        if not base or not end:
            card.outcome = {
                "state": "not_evaluable",
                "reason": "A fresh publication or horizon price was unavailable",
            }
            continue
        if (
            base["currency"] != end["currency"]
            or abs(end["close"] / base["close"] - 1) > 0.35
        ):
            card.outcome = {
                "state": "not_evaluable",
                "reason": "Currency or large unadjusted price discontinuity needs review",
            }
            continue
        raw = (end["close"] / base["close"] - 1) * 100
        card.outcome = {
            "state": "resolved",
            "raw_price_return_pct": raw,
            "baseline": base,
            "end": end,
            "benchmark": "unavailable",
            "note": "Unadjusted observational return; not proof of causation or strategy profitability",
        }
