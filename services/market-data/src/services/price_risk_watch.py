"""Detect price-risk events for followed subjects and record who, if anyone, is told.

SHADOW BY DEFAULT. Detection runs and every decision is recorded; nothing is delivered unless
the operator turns delivery on. That is the sequence the design asked for — measure volume,
misses and duplicates against real data first — and it is also the only honest way to introduce
a channel whose thresholds are admittedly unvalidated.

The measurement and the audience are separate steps on purpose. `price_risk` decides what
happened to a price; this decides who was watching it and what they were told. Keeping the
second out of the first is what lets the first be tested without a database.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from common.outcome_price_basis import classify_observation_window, resolve_window
from db import (CorporateAction, CorporateActionCoverage, Price, RiskEvent, RiskEventReceipt,
                Stock, TimeFrame, User, WatchlistItem)

from . import price_risk

#: Redis flag. ABSENT MEANS SHADOW. A channel that starts delivering because a flag could not
#: be read is the same failure as a preference that defaults to subscribed.
DELIVERY_FLAG = "stockai:admin:feature:price_risk_delivery_enabled"

ALERT_TYPE_EMAIL = "price_risk"
ALERT_TYPE_IN_APP = "price_risk_in_app"

#: Enough history for the pullback window plus the warm-up a split check needs.
_LOOKBACK_SESSIONS = 15
_LOOKBACK_DAYS = 40


def delivery_enabled(redis_client) -> bool:
    """True only on an explicit affirmative. Any other outcome is shadow."""
    if redis_client is None:
        return False
    try:
        return (redis_client.get(DELIVERY_FLAG) or b"").decode().strip() in ("1", "true", "yes")
    except Exception:  # noqa: BLE001
        return False


def _bars_for(session, stock_ids: list[int], since: date) -> dict[int, list]:
    rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close, Price.open)
        .where(Price.stock_id.in_(stock_ids), Price.timeframe == TimeFrame.D1,
               Price.ts >= datetime.combine(since, datetime.min.time()))
        .order_by(Price.stock_id, Price.ts)).all()
    out: dict[int, list] = {}
    for row in rows:
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        out.setdefault(row.stock_id, []).append((d, float(row.close),
                                                 None if row.open is None else float(row.open)))
    return out


def _evidence_for(session, symbols: set[str]):
    actions: dict[str, list] = {}
    coverage: dict[str, object] = {}
    if not symbols:
        return actions, coverage
    for a in session.execute(select(CorporateAction).where(
            CorporateAction.symbol.in_(symbols))).scalars():
        actions.setdefault(a.symbol, []).append(a)
    for c in session.execute(select(CorporateActionCoverage).where(
            CorporateActionCoverage.symbol.in_(symbols))).scalars():
        prior = coverage.get(c.symbol)
        if prior is None or c.retrieved_at > prior.retrieved_at:
            coverage[c.symbol] = c
    return actions, coverage


def _prior_state(session, scope: str, subject: str, rule: str, before: date) -> dict | None:
    """The most recent earlier event for this subject+rule, as `should_emit` expects it.

    `worst_pct` is the worst magnitude seen in the run of recent events rather than only the
    last one, so a decline that eased slightly and then resumed is still the same episode.
    """
    rows = session.execute(
        select(RiskEvent).where(RiskEvent.scope == scope, RiskEvent.subject == subject,
                                RiskEvent.rule == rule, RiskEvent.observed_to < before)
        .order_by(RiskEvent.observed_to.desc()).limit(10)).scalars().all()
    if not rows:
        return None
    worst = max(abs(r.change_pct or 0.0) for r in rows)
    latest = rows[0]
    return {"step": latest.step, "worst_pct": worst,
            "episode_closed": bool((latest.evidence or {}).get("episode_closed"))}


def detect(session, *, now: datetime | None = None, redis_client=None,
           symbols: list[str] | None = None) -> dict:
    """Run detection over followed subjects. Returns a summary; writes events and receipts."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    live = delivery_enabled(redis_client)
    mode = "live" if live else "shadow"

    watched = {r[0] for r in session.execute(
        select(Stock.symbol).join(WatchlistItem, WatchlistItem.stock_id == Stock.id)).all()}
    universe = sorted(set(symbols) if symbols else watched)
    theme_members = {sym for members in price_risk_themes().values() for sym in members}
    universe = sorted(set(universe) | (theme_members & watched)) if universe else sorted(theme_members)
    if not universe:
        return {"mode": mode, "subjects": 0, "events": 0, "unmeasurable": 0, "receipts": 0}

    stocks = {s.symbol: s for s in session.execute(
        select(Stock).where(Stock.symbol.in_(universe))).scalars()}
    bars = _bars_for(session, [s.id for s in stocks.values()], today - timedelta(days=_LOOKBACK_DAYS))
    actions, coverage = _evidence_for(session, set(stocks))

    created = unmeasurable = receipts = 0
    moves_by_symbol: dict[str, float | None] = {}

    for symbol in universe:
        stock = stocks.get(symbol)
        series = bars.get(stock.id, []) if stock else []
        if len(series) < 2:
            moves_by_symbol[symbol] = None
            continue
        closes = [(d, c) for d, c, _ in series][-(_LOOKBACK_SESSIONS + 1):]
        opens = {d: o for d, _, o in series if o is not None}
        window_start, window_end = closes[0][0], closes[-1][0]
        resolved = resolve_window(closes, actions.get(symbol, []), coverage.get(symbol),
                                  start=window_start, end=window_end)
        verdict = classify_observation_window(resolved, actions.get(symbol, []),
                                              start=window_start, end=window_end)
        usable = resolved["prices"] or closes
        candidates = price_risk.evaluate(symbol, "symbol", list(usable), verdict, opens=opens)
        moves_by_symbol[symbol] = None
        for cand in candidates:
            if cand["rule"] == price_risk.UNMEASURABLE:
                unmeasurable += 1
                _persist(session, cand, mode, today, now)
                continue
            if cand["rule"] == "session_decline":
                moves_by_symbol[symbol] = cand["change_pct"]
            prior = _prior_state(session, "symbol", symbol, cand["rule"], cand["as_of"])
            emit, why = price_risk.should_emit(prior, cand)
            if not emit:
                continue
            event = _persist(session, cand, mode, today, now, note=why)
            if event is not None:
                created += 1
                receipts += _record_receipts(session, event, symbol, live, now)

    # Fill in the session move for symbols with no candidate, so breadth sees non-decliners
    # as observed rather than as missing — the bias `basket_breadth` refuses to hide.
    for symbol in universe:
        if moves_by_symbol.get(symbol) is None and stocks.get(symbol):
            series = bars.get(stocks[symbol].id, [])
            closes = [(d, c) for d, c, _ in series]
            move = price_risk.session_move(closes) if len(closes) >= 2 else None
            moves_by_symbol[symbol] = move["change_pct"] if move else None

    for theme, members in price_risk_themes().items():
        present = [m for m in members if m in moves_by_symbol]
        if not present:
            continue
        breadth = price_risk.basket_breadth({m: moves_by_symbol.get(m) for m in present},
                                            len(members))
        if not breadth["triggered"]:
            continue
        cand = {"scope": "theme", "subject": theme, "rule": "basket_breadth", "step": 1,
                "direction": "down", "as_of": today, "from_date": today,
                "change_pct": -abs(breadth["threshold_pct"]),
                "evidence_status": "measured_from_member_prices",
                "reason": None, "breadth": breadth}
        prior = _prior_state(session, "theme", theme, "basket_breadth", today)
        emit, why = price_risk.should_emit(prior, cand)
        if not emit:
            continue
        event = _persist(session, cand, mode, today, now, note=why)
        if event is not None:
            created += 1
            for member in breadth["declined_symbols"]:
                receipts += _record_receipts(session, event, member, live, now)

    return {"mode": mode, "subjects": len(universe), "events": created,
            "unmeasurable": unmeasurable, "receipts": receipts}


def price_risk_themes() -> dict[str, list[str]]:
    """Maintained baskets, from the one dependency-free module that defines them.

    DELIBERATELY NOT WRAPPED IN A try/except. It was, briefly, importing from `theme_signals` —
    and that module drags an LLM client, Redis and the ORM, so the import failed in contexts
    this one works in and the handler returned {}. Breadth detection was therefore switched off
    entirely while looking identical to "no theme fell". An import that cannot succeed is a
    deployment fault and should say so, not degrade into silence.
    """
    from .theme_baskets import THEMES
    return dict(THEMES)


def _persist(session, cand: dict, mode: str, today: date, now: datetime,
             note: str | None = None) -> RiskEvent | None:
    as_of = cand.get("as_of") or today
    key = price_risk.event_key(cand["scope"], cand["subject"], cand["rule"], as_of)
    if session.execute(select(RiskEvent).where(RiskEvent.event_key == key)).scalars().first():
        return None
    evidence = {k: v for k, v in cand.items()
                if k not in ("scope", "subject", "rule", "step", "direction", "change_pct")}
    evidence = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in evidence.items()}
    if note:
        evidence["emit_reason"] = note
    event = RiskEvent(
        event_key=key, scope=cand["scope"], subject=cand["subject"], rule=cand["rule"],
        direction=cand.get("direction") or "down", change_pct=cand.get("change_pct"),
        step=int(cand.get("step") or 0), window_sessions=cand.get("window_sessions"),
        observed_from=cand.get("from_date") if isinstance(cand.get("from_date"), date) else None,
        observed_to=as_of, price_basis=cand.get("basis"),
        evidence_status=str(cand.get("evidence_status") or "unverified")[:32],
        evidence_reason=cand.get("reason"), evidence=evidence,
        delivery_mode=mode, detected_at=now.replace(tzinfo=None))
    session.add(event)
    session.flush()
    return event


def _record_receipts(session, event: RiskEvent, symbol: str, live: bool, now: datetime) -> int:
    """One row per watching user. In shadow nothing is queued, and the row says so."""
    from .alert_audience import may_notify, may_send

    users = session.execute(
        select(User).join(WatchlistItem, WatchlistItem.user_id == User.id)
        .join(Stock, Stock.id == WatchlistItem.stock_id)
        .where(Stock.symbol == symbol, User.is_active.is_(True)).distinct()).scalars().all()
    written = 0
    for user in users:
        if session.execute(select(RiskEventReceipt).where(
                RiskEventReceipt.event_id == event.id,
                RiskEventReceipt.user_id == user.id)).scalars().first():
            continue
        if not live:
            state, reason = "shadow_suppressed", "delivery disabled; detection recorded only"
        elif not may_notify(session, user, ALERT_TYPE_IN_APP):
            state, reason = "preference_disabled", "in-app price-risk notices are off"
        elif not may_send(session, user, ALERT_TYPE_EMAIL):
            state, reason = "in_app_only", (
                "no recipient email" if not (user.email or "").strip()
                else "email price-risk notices are off")
        else:
            state, reason = "eligible_for_email", None
        session.add(RiskEventReceipt(
            event_id=event.id, user_id=user.id, matched_via="watchlist",
            matched_symbols=[symbol], state=state, reason=reason,
            decided_at=now.replace(tzinfo=None)))
        written += 1
    return written
