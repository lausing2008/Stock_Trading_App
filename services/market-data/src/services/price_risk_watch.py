"""Detect price-risk events for followed subjects and record who, if anyone, is told.

LIVE DELIVERY (2026-10-13). With the delivery flag set, each run queues ONE digest email per
opted-in user into the notification outbox, plus one in-app notice. Both preference types still
default OFF, so the flag alone reaches nobody; an account must opt in. The drain job on the
alerting side of the scheduler does the actual sending.

SHADOW BY DEFAULT. Detection runs and every decision is recorded; nothing is delivered unless
the operator turns delivery on. That is the sequence the design asked for — measure volume,
misses and duplicates against real data first — and it is also the only honest way to introduce
a channel whose thresholds are admittedly unvalidated.

The measurement and the audience are separate steps on purpose. `price_risk` decides what
happened to a price; this decides who was watching it and what they were told. Keeping the
second out of the first is what lets the first be tested without a database.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from common.outcome_price_basis import (best_achievable_status,
                                        classify_observation_window, resolve_window)
from common.market_calendar import is_regular_session
from db import (AppNotification, CorporateAction, CorporateActionCoverage, NotificationOutbox,
                Price, RiskEvent, RiskEventReceipt, Stock, TimeFrame, User, WatchlistItem)

from . import price_risk

#: Redis flag. ABSENT MEANS SHADOW. A channel that starts delivering because a flag could not
#: be read is the same failure as a preference that defaults to subscribed.
DELIVERY_FLAG = "stockai:admin:feature:price_risk_delivery_enabled"

ALERT_TYPE_EMAIL = "price_risk"
ALERT_TYPE_IN_APP = "price_risk_in_app"

#: Enough history for the pullback window plus the warm-up a split check needs.
_LOOKBACK_SESSIONS = 15
_LOOKBACK_DAYS = 40

LIVE_PRICES_KEY = "stockai:live_prices"
#: How closely a live quote's own previous close must match the last settled bar for the quote
#: to be accepted as TODAY's. Loose enough for rounding, far tighter than any real daily move.
QUOTE_PREV_CLOSE_TOLERANCE = 0.005
#: Oldest cached quote an intraday measurement may use. The cache refreshes every minute; ten
#: minutes tolerates a few failed refreshes without letting a morning quote stand in for the
#: afternoon.
MAX_QUOTE_AGE = timedelta(minutes=10)
#: A quote stamped slightly in the future is clock skew between hosts; far in the future is wrong.
MAX_QUOTE_CLOCK_SKEW = timedelta(minutes=2)

#: Rules whose episode is ONE SESSION. Everything else is episodic across sessions.
SESSION_RULES = ("session_decline", "basket_breadth")
EPISODIC_SYMBOL_RULES = ("pullback", "slow_decline")
#: An episodic event older than this cannot still be the same pullback: the window is 5
#: sessions, so two calendar weeks is well past it even across a holiday.
EPISODE_MAX_AGE_DAYS = 14

#: Per-user email ceiling over a rolling 24 hours. Each run sends at most one digest per user,
#: so this bounds a bad day to a handful of emails however many symbols move.
MAX_EMAILS_PER_USER_PER_DAY = 6
#: A queued digest older than this describes a market that has moved on.
DIGEST_MAX_AGE = timedelta(hours=12)

_NY = ZoneInfo("America/New_York")
_HKT = ZoneInfo("Asia/Hong_Kong")


#: Runtime overrides for the thresholds still under evaluation. JSON, all keys optional.
CONFIG_KEY = "stockai:admin:price_risk:config"
DEFAULT_CONFIG = {
    "slow_decline_sessions": price_risk.SLOW_DECLINE_SESSIONS,
    "slow_decline_steps": list(price_risk.SLOW_DECLINE_STEPS),
    "basket_slow_member_pct": price_risk.BASKET_PULLBACK_MEMBER_PCT,
}


def load_config(redis_client) -> dict:
    """Defaults, overlaid with any VALID override. An invalid value is named, never half-used.

    Returned with `errors` so a bad override shows up in the job's own log line instead of
    quietly running the defaults while the operator believes their setting is live.
    """
    cfg, errors = dict(DEFAULT_CONFIG), []
    raw = None
    if redis_client is not None:
        try:
            raw = redis_client.get(CONFIG_KEY)
        except Exception:  # noqa: BLE001
            raw = None
    if raw:
        try:
            override = json.loads(raw)
        except (TypeError, ValueError):
            override, errors = {}, ["config is not valid JSON"]
        if not isinstance(override, dict):
            override, errors = {}, ["config must be a JSON object"]
        n = override.get("slow_decline_sessions")
        if n is not None:
            if isinstance(n, int) and not isinstance(n, bool) and 2 <= n <= _LOOKBACK_SESSIONS:
                cfg["slow_decline_sessions"] = n
            else:
                errors.append(f"slow_decline_sessions must be an int 2..{_LOOKBACK_SESSIONS}")
        steps = override.get("slow_decline_steps")
        if steps is not None:
            ok = (isinstance(steps, list) and steps
                  and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0
                          for x in steps)
                  and all(a < b for a, b in zip(steps, steps[1:])))
            if ok:
                cfg["slow_decline_steps"] = [float(x) for x in steps]
            else:
                errors.append("slow_decline_steps must be a strictly ascending list of positives")
        pct = override.get("basket_slow_member_pct")
        if pct is not None:
            if isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 < pct < 50:
                cfg["basket_slow_member_pct"] = float(pct)
            else:
                errors.append("basket_slow_member_pct must be between 0 and 50")
    cfg["errors"] = errors
    return cfg


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


def _session_date(market: str, now: datetime) -> date:
    """The venue's own calendar date at `now` — never the UTC date (see utc-vs-et-date-boundary)."""
    return now.astimezone(_HKT if str(market).upper() == "HK" else _NY).date()


def live_quotes(redis_client) -> dict[str, dict]:
    """The shared `stockai:live_prices` cache, keyed by symbol. Empty on any failure.

    Empty is safe here in a way it is not elsewhere: without a quote the detector falls back to
    the settled daily bars, which is exactly what it did before intraday runs existed.
    """
    if redis_client is None:
        return {}
    try:
        rows = json.loads(redis_client.get(LIVE_PRICES_KEY) or "[]")
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(rows, list):
        return {}
    return {r["symbol"]: r for r in rows if isinstance(r, dict) and r.get("symbol")}


def with_live_quote(series: list, quote: dict | None, market: str, now: datetime):
    """Extend settled daily bars with today's live price, when — and only when — it is today's.

    Returns `(series, quote_status)`. A quote is used only if ALL of these hold:

      * the venue is in its REGULAR session (`is_regular_session`, so holidays fall out);
      * the row carries `fetched_at`, no more than `MAX_QUOTE_AGE` before `now`;
      * the row carries the provider's own `session_date`, equal to the venue's date today;
      * its previous close matches the last settled bar within `QUOTE_PREV_CLOSE_TOLERANCE`.

    The first version had only the last check. That proves the quote BELONGS to today's
    session, not that it is CURRENT: a row cached at 09:40 still matches at 15:40, and would be
    reported as the afternoon's move. A row that cannot prove its age is refused, not trusted —
    the cache key's TTL bounds the blob, not the data written into it. Each refusal has its own
    status so the job log shows which proof failed.

    Note what this does NOT prove: the provider's own delay. Yahoo's daily download carries no
    quote time, so a fresh fetch can still be a ~15-minute-delayed price. Labelled, not hidden.
    """
    if not quote or not series or not is_regular_session(market, now):
        return series, None
    try:
        price, prev = float(quote["price"]), float(quote["prev_close"])
    except (KeyError, TypeError, ValueError):
        return series, "quote_unusable"
    today = _session_date(market, now)
    try:
        fetched = datetime.fromisoformat(str(quote["fetched_at"]))
        if fetched.tzinfo is None:
            raise ValueError("naive fetched_at")
        quote_session = date.fromisoformat(str(quote["session_date"]))
    except (KeyError, TypeError, ValueError):
        return series, "quote_unproven"
    age = now - fetched
    if age > MAX_QUOTE_AGE or age < -MAX_QUOTE_CLOCK_SKEW:
        return series, "quote_stale"
    if quote_session != today:
        return series, "quote_wrong_session"
    settled = [b for b in series if b[0] < today]
    if not settled or price <= 0 or prev <= 0:
        return series, "quote_unusable"
    if abs(settled[-1][1] - prev) / prev > QUOTE_PREV_CLOSE_TOLERANCE:
        return series, "quote_mismatch"
    today_open = next((b[2] for b in series if b[0] == today), None)
    return settled + [(today, price, today_open)], "live_quote"


def _prior_state(session, scope: str, subject: str, rule: str, as_of: date, mode: str,
                 closes: list | None = None) -> dict | None:
    """The most recent earlier event for this subject+rule, as `should_emit` expects it.

    Only events of the SAME MODE count. A shadow event proves the move was detected, not that
    anybody was told, so a live run must not treat it as already reported.

    A SESSION rule compares within one session only: a -4% day and another -4% day are two
    moves, not one episode. An EPISODIC rule (pullback, basket pullback) looks back across
    sessions, and its episode is closed by recovery measured from prices, or by age.
    """
    q = select(RiskEvent).where(RiskEvent.scope == scope, RiskEvent.subject == subject,
                                RiskEvent.rule == rule, RiskEvent.delivery_mode == mode,
                                RiskEvent.observed_to <= as_of)
    if rule in SESSION_RULES:
        q = q.where(RiskEvent.observed_to == as_of)
    rows = session.execute(q.order_by(RiskEvent.observed_to.desc(), RiskEvent.step.desc())
                           .limit(10)).scalars().all()
    if not rows:
        return None
    latest = rows[0]
    closed = bool((latest.evidence or {}).get("episode_closed"))
    if rule not in SESSION_RULES and not closed:
        if latest.observed_to < as_of - timedelta(days=EPISODE_MAX_AGE_DAYS):
            closed = True
        elif closes:
            since = [c for d, c in closes if latest.observed_to < d < as_of]
            closed = price_risk.episode_closed(latest.evidence, since)
    worst = max(abs(r.change_pct or 0.0) for r in rows)
    return {"step": latest.step, "worst_pct": worst, "episode_closed": closed}


def _latest_open_event(session, scope: str, subject: str, rule: str, mode: str,
                       as_of: date) -> RiskEvent | None:
    ev = session.execute(
        select(RiskEvent).where(RiskEvent.scope == scope, RiskEvent.subject == subject,
                                RiskEvent.rule == rule, RiskEvent.delivery_mode == mode,
                                RiskEvent.observed_to < as_of)
        .order_by(RiskEvent.observed_to.desc(), RiskEvent.step.desc()).limit(1)).scalars().first()
    if ev is None or (ev.evidence or {}).get("episode_closed"):
        return None
    return ev


def _mark_closed(event: RiskEvent, on: date, why: str) -> None:
    """PERSIST the end of an episode on the event that opened it.

    The flag `should_emit` reads was, until 2026-10-13, read and never written — so a
    decline -> recovery -> second decline stayed suppressed as "the same episode" forever. The
    JSON is reassigned, not mutated in place: an in-place change to a JSON column is invisible
    to the ORM's change tracking and would silently never be saved.
    """
    event.evidence = {**(event.evidence or {}), "episode_closed": True,
                      "episode_closed_on": on.isoformat(), "episode_closed_by": why}


def close_recovered_episodes(session, subject: str, closes: list, mode: str, as_of: date) -> int:
    """For each episodic symbol rule, close the open episode if prices have recovered."""
    closed = 0
    for rule in EPISODIC_SYMBOL_RULES:
        ev = _latest_open_event(session, "symbol", subject, rule, mode, as_of)
        if ev is None:
            continue
        if ev.observed_to < as_of - timedelta(days=EPISODE_MAX_AGE_DAYS):
            _mark_closed(ev, as_of, "age"); closed += 1
            continue
        since = [c for d, c in closes if d > ev.observed_to]
        if price_risk.episode_closed(ev.evidence, since):
            _mark_closed(ev, as_of, "price_recovery"); closed += 1
    return closed


def detect(session, *, now: datetime | None = None, redis_client=None,
           symbols: list[str] | None = None, quotes: dict | None = None) -> dict:
    """Run detection over followed subjects. Returns a summary; writes events and receipts.

    In LIVE mode this also queues one digest per eligible user in the notification outbox. It
    never calls a mail provider: the drain job does, on the alerting side of the scheduler.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    live = delivery_enabled(redis_client)
    mode = "live" if live else "shadow"
    quotes = live_quotes(redis_client) if quotes is None else quotes
    cfg = load_config(redis_client)
    slow_n = cfg["slow_decline_sessions"]
    slow_steps = tuple(cfg["slow_decline_steps"])

    watched = {r[0] for r in session.execute(
        select(Stock.symbol).join(WatchlistItem, WatchlistItem.stock_id == Stock.id)).all()}
    universe = sorted(set(symbols) if symbols else watched)
    theme_members = {sym for members in price_risk_themes().values() for sym in members}
    universe = sorted(set(universe) | (theme_members & watched)) if universe else sorted(theme_members)
    if not universe:
        return {"mode": mode, "subjects": 0, "events": 0, "unmeasurable": 0, "receipts": 0,
                "config_errors": cfg["errors"]}

    stocks = {s.symbol: s for s in session.execute(
        select(Stock).where(Stock.symbol.in_(universe))).scalars()}
    bars = _bars_for(session, [s.id for s in stocks.values()], today - timedelta(days=_LOOKBACK_DAYS))
    actions, coverage = _evidence_for(session, set(stocks))

    created = unmeasurable = receipts = intraday = episodes_closed = 0
    quote_refusals: dict[str, int] = {}
    pending: dict[int, list] = {}
    #: Per symbol: {"as_of", "session", "window"} from ADJUSTED prices, or None when the symbol
    #: was unmeasurable or had no bars. Breadth reads only this — it must never re-derive a raw
    #: move for a symbol the evidence contract already refused.
    measured: dict[str, dict | None] = {}

    for symbol in universe:
        stock = stocks.get(symbol)
        series = bars.get(stock.id, []) if stock else []
        market = getattr(getattr(stock, "market", None), "value", None) or "US"
        series, quote_status = with_live_quote(series, quotes.get(symbol), market, now)
        if quote_status == "live_quote":
            intraday += 1
        elif quote_status:
            quote_refusals[quote_status] = quote_refusals.get(quote_status, 0) + 1
        if len(series) < 2:
            measured[symbol] = None
            continue
        closes = [(d, c) for d, c, _ in series][-(_LOOKBACK_SESSIONS + 1):]
        opens = {d: o for d, _, o in series if o is not None}
        window_start, window_end = closes[0][0], closes[-1][0]
        resolved = resolve_window(closes, actions.get(symbol, []), coverage.get(symbol),
                                  start=window_start, end=window_end)
        verdict = classify_observation_window(resolved, actions.get(symbol, []),
                                              start=window_start, end=window_end)
        usable = resolved["prices"] or closes
        # CASH DIVIDENDS: added back, so the mechanical ex-date drop is not reported as a loss.
        # An amount that was never recorded cannot be added back, so a window straddling it is
        # refused like an unapplied split — measured as unknown, not as a decline.
        usable, div_applied, div_unknown = price_risk.dividend_adjust(
            list(usable), verdict.get("cash_dividends") or [])
        if div_unknown and verdict.get("usable"):
            verdict = {**verdict, "usable": False, "basis": None,
                       "reason": "cash dividend in window with no recorded amount: "
                                 + ", ".join(div_unknown)}
            resolved = {**resolved, "limiting_factor": "dividend_amount_unknown"}
        elif div_applied:
            verdict = {**verdict, "basis": f"{verdict.get('basis')}+cash_dividends_added_back"}
        # The LIMITING FACTOR travels with the exclusion, because "unmeasurable" alone cannot
        # be acted on. A stale span is fixed by a refresh; a missing completeness guarantee is
        # not fixable by one; an unappliable action is neither. See `best_achievable_status`.
        verdict = {**verdict,
                   "limiting_factor": resolved.get("limiting_factor"),
                   "best_achievable": best_achievable_status(
                       getattr(coverage.get(symbol), "completeness_basis", None)),
                   "coverage_evidenced_to": (
                       getattr(coverage.get(symbol), "evidenced_to", None).isoformat()
                       if getattr(coverage.get(symbol), "evidenced_to", None) else None),
                   "window_end": window_end.isoformat()}
        if verdict.get("usable"):
            move = price_risk.session_move(list(usable))
            measured[symbol] = {"as_of": window_end,
                                "session": move["change_pct"] if move else None,
                                "window": price_risk.window_change(list(usable), slow_n)}
        else:
            measured[symbol] = None
        if verdict.get("usable"):
            episodes_closed += close_recovered_episodes(session, symbol, list(usable), mode,
                                                        window_end)
        candidates = price_risk.evaluate(symbol, "symbol", list(usable), verdict, opens=opens,
                                         slow_sessions=slow_n, slow_steps=slow_steps)
        for cand in candidates:
            cand["price_source"] = quote_status or "settled_daily_bars"
            if div_applied:
                cand["cash_dividends_added_back"] = div_applied
            if cand["rule"] == price_risk.UNMEASURABLE:
                unmeasurable += 1
                _persist(session, cand, mode, today, now)
                continue
            if cand["rule"] == "slow_decline":
                # Same move, other ladder: a pullback reported at -7% that eases to -6.5% is
                # not a NEW slow decline. Quiet while the pullback episode is still open.
                open_pullback = _prior_state(session, "symbol", symbol, "pullback",
                                             cand["as_of"], mode, closes=list(usable))
                if open_pullback is not None and not open_pullback["episode_closed"]:
                    continue
            prior = _prior_state(session, "symbol", symbol, cand["rule"], cand["as_of"], mode,
                                 closes=list(usable))
            emit, why = price_risk.should_emit(prior, cand)
            if not emit:
                continue
            event = _persist(session, cand, mode, today, now, note=why)
            if event is not None:
                created += 1
                receipts += _record_receipts(session, event, symbol, live, now, pending)

    for theme, members in price_risk_themes().items():
        present = [m for m in members if m in measured]
        if not present:
            continue
        # COMMON SESSION. A member whose latest bar is older than the basket's newest is
        # unobserved for this session, not a non-decliner — and not a decliner on old news.
        sessions = [measured[m]["as_of"] for m in present if measured[m]]
        if not sessions:
            continue
        reference = max(sessions)
        for rule, key, threshold in (("basket_breadth", "session", price_risk.BREADTH_MEMBER_DECLINE_PCT),
                                     ("basket_slow_decline", "window", cfg["basket_slow_member_pct"])):
            moves = {m: (measured[m][key] if measured[m] and measured[m]["as_of"] == reference
                         else None) for m in present}
            breadth = price_risk.basket_breadth(moves, len(members), decline_pct=threshold)
            if not breadth["triggered"]:
                # A basket episode ends when breadth has clearly gone, not on the first reading
                # under the bar — otherwise 60% -> 50% -> 60% would re-alert on noise.
                if (rule not in SESSION_RULES and breadth["sufficient_evidence"]
                        and breadth["declined_share"] < price_risk.BREADTH_FRACTION / 2):
                    ev = _latest_open_event(session, "theme", theme, rule, mode, reference)
                    if ev is not None:
                        _mark_closed(ev, reference, "breadth_receded"); episodes_closed += 1
                continue
            declined = [moves[m] for m in breadth["declined_symbols"]]
            cand = {"scope": "theme", "subject": theme, "rule": rule, "step": 1,
                    "direction": "down", "as_of": reference, "from_date": reference,
                    "change_pct": round(sum(declined) / len(declined), 2),
                    "window_sessions": 1 if key == "session" else slow_n,
                    "evidence_status": "measured_from_member_prices",
                    "reason": None, "breadth": breadth,
                    "member_moves": {m: (None if v is None else round(v, 2))
                                     for m, v in moves.items()}}
            prior = _prior_state(session, "theme", theme, rule, reference, mode)
            emit, why = price_risk.should_emit(prior, cand)
            if not emit:
                continue
            event = _persist(session, cand, mode, today, now, note=why)
            if event is not None:
                created += 1
                receipts += _record_receipts(session, event, breadth["declined_symbols"], live,
                                             now, pending)

    queued = _queue_digests(session, pending, now) if live else {"emails": 0, "in_app": 0}
    return {"mode": mode, "subjects": len(universe), "events": created,
            "unmeasurable": unmeasurable, "receipts": receipts,
            "intraday_quotes": intraday, "quote_refusals": quote_refusals,
            "episodes_closed": episodes_closed,
            "config_errors": cfg["errors"], **queued}


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
    key = price_risk.event_key(cand["scope"], cand["subject"], cand["rule"], as_of,
                               int(cand.get("step") or 0), mode)
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


def _record_receipts(session, event: RiskEvent, symbols, live: bool, now: datetime,
                     pending: dict) -> int:
    """One row per watching user, however many of `symbols` they watch.

    A theme event reaches a user through several members at once. Receipts used to be written
    per member and de-duplicated by a SELECT, which only sees rows already flushed — production
    sessions run `autoflush=False`, so a user watching NVDA, AVGO and TSM got three receipts for
    one event and the unique constraint aborted the whole run. Found by the 2026-10-13
    production dry run; the probe now uses `autoflush=False` too, so it cannot hide again.

    AUDIENCE: an account is in it if EITHER channel is on. Email and in-app are independent
    preferences, and reading only the in-app one (as this did) recorded an email-only
    subscriber as `preference_disabled`, which is not what they chose.
    """
    from .alert_audience import may_notify

    symbols = [symbols] if isinstance(symbols, str) else list(symbols)
    matched: dict[int, set[str]] = {}
    users: dict[int, User] = {}
    for user, symbol in session.execute(
            select(User, Stock.symbol).join(WatchlistItem, WatchlistItem.user_id == User.id)
            .join(Stock, Stock.id == WatchlistItem.stock_id)
            .where(Stock.symbol.in_(symbols), User.is_active.is_(True))).all():
        users[user.id] = user
        matched.setdefault(user.id, set()).add(symbol)
    already = {r[0] for r in session.execute(select(RiskEventReceipt.user_id).where(
        RiskEventReceipt.event_id == event.id)).all()}
    written = 0
    for uid in sorted(matched):
        if uid in already:
            continue
        user = users[uid]
        if not live:
            state, reason = "shadow_suppressed", "delivery disabled; detection recorded only"
        elif not (may_notify(session, user, ALERT_TYPE_EMAIL)
                  or may_notify(session, user, ALERT_TYPE_IN_APP)):
            state, reason = "preference_disabled", "price-risk email and in-app are both off"
        else:
            state, reason = "pending_digest", None
        receipt = RiskEventReceipt(
            event_id=event.id, user_id=uid, matched_via="watchlist",
            matched_symbols=sorted(matched[uid]), state=state, reason=reason,
            decided_at=now.replace(tzinfo=None))
        session.add(receipt)
        if state == "pending_digest":
            pending.setdefault(uid, []).append((event, receipt))
        written += 1
    return written


def _queue_digests(session, pending: dict, now: datetime) -> dict:
    """ONE email and ONE in-app notice per user per run, never one per event.

    A broad selloff moves dozens of followed symbols at once; an email per symbol would bury the
    one that matters and teach the reader to mute the channel. The digest's outbox identity is a
    hash of the event keys it carries, so a re-run that finds the same events cannot queue it
    twice, and the per-user 24h ceiling bounds the worst day.
    """
    from common.outbox import enqueue
    from .alert_audience import may_notify, may_send

    emails = in_app = 0
    naive_now = now.replace(tzinfo=None)
    for user_id, items in pending.items():
        user = session.get(User, user_id)
        items.sort(key=lambda er: (er[0].scope != "theme", -abs(er[0].change_pct or 0.0)))
        events = [e for e, _ in items]
        subject, text_body, html_body, summary = render_digest(events)
        if may_notify(session, user, ALERT_TYPE_IN_APP):
            session.add(AppNotification(
                user_id=user_id, alert_id=f"price-risk:{events[0].event_key}"[:64],
                symbol=_first_symbol(events), message=summary[:500],
                triggered_at=naive_now, read=False))
            in_app += 1
        if not may_send(session, user, ALERT_TYPE_EMAIL):
            reason = ("no recipient email" if not (user.email or "").strip()
                      else "email price-risk notices are off")
            for _, r in items:
                r.state, r.reason = "in_app_only", reason
            continue
        sent_today = session.execute(select(func.count(NotificationOutbox.id)).where(
            NotificationOutbox.user_id == user_id,
            NotificationOutbox.alert_type == ALERT_TYPE_EMAIL,
            NotificationOutbox.created_at >= naive_now - timedelta(hours=24))).scalar() or 0
        if sent_today >= MAX_EMAILS_PER_USER_PER_DAY:
            for _, r in items:
                r.state, r.reason = "email_capped", (
                    f"{MAX_EMAILS_PER_USER_PER_DAY} price-risk emails already in 24h; "
                    "shown in the app and the risk-alert history")
            continue
        footer_html, footer_text = _unsubscribe_footer(user_id)
        digest_id = hashlib.sha1(",".join(sorted(e.event_key for e in events)).encode()).hexdigest()[:24]
        row, _ = enqueue(session, event_id=f"price-risk:{user_id}:{digest_id}", user_id=user_id,
                         recipient=user.email.strip(), alert_type=ALERT_TYPE_EMAIL,
                         subject=subject, body_text=text_body + footer_text,
                         body_html=html_body + footer_html,
                         expires_at=naive_now + DIGEST_MAX_AGE)
        for _, r in items:
            r.state, r.reason, r.outbox_event_id = "email_queued", None, row.event_id
        emails += 1
    return {"emails": emails, "in_app": in_app}


def _unsubscribe_footer(user_id: int) -> tuple[str, str]:
    try:
        from .email_service import _unsubscribe_footer as footer
        html, text = footer(ALERT_TYPE_EMAIL, user_id)
        return html or "", text or ""
    except Exception:  # noqa: BLE001
        return "", ""


def _first_symbol(events) -> str | None:
    for e in events:
        if e.scope == "symbol":
            return e.subject
        members = ((e.evidence or {}).get("breadth") or {}).get("declined_symbols") or []
        if members:
            return members[0]
    return None


_RULE_WORDS = {
    "session_decline": "fell {pct:.1f}% in one session",
    "pullback": "is down {pct:.1f}% from its recent high",
    "basket_breadth": "fell together in one session",
    "slow_decline": "is down {pct:.1f}% over {n} trading sessions",
    "basket_slow_decline": "have fallen together over {n} sessions",
}


def describe(event: RiskEvent) -> str:
    """One plain sentence per event: what moved, by how much, between which prices and dates."""
    ev = event.evidence or {}
    pct = abs(event.change_pct or 0.0)
    if event.scope == "theme":
        b = ev.get("breadth") or {}
        moves = ev.get("member_moves") or {}
        detail = ", ".join(f"{m} {moves[m]:+.1f}%" for m in b.get("declined_symbols", [])
                           if moves.get(m) is not None)
        return (f"{event.subject}: {b.get('declined_count')} of {b.get('observed_count')} "
                f"members {_RULE_WORDS[event.rule].format(n=event.window_sessions or 1)} "
                f"(each down at least {b.get('threshold_pct')}%): {detail}.")
    line = (f"{event.subject} "
            f"{_RULE_WORDS.get(event.rule, event.rule).format(pct=pct, n=event.window_sessions or 0)}")
    if ev.get("from_price") is not None and ev.get("to_price") is not None:
        line += (f": {float(ev['from_price']):,.2f} on {ev.get('from_date')} to "
                 f"{float(ev['to_price']):,.2f} on {ev.get('as_of')}")
    if event.rule == "session_decline" and ev.get("overnight_pct") is not None:
        line += (f" (overnight gap {ev['overnight_pct']:+.1f}%, "
                 f"regular session {ev['regular_session_pct']:+.1f}%)")
    if ev.get("cash_dividends_added_back"):
        line += " (cash dividend added back)"
    if ev.get("price_source") == "live_quote":
        line += " [intraday quote, possibly delayed by the provider; the session is not over]"
    return line + "."


def render_digest(events: list) -> tuple[str, str, str, str]:
    """(subject, text, html, in-app summary). Facts only; no cause is claimed."""
    names = []
    for e in events:
        label = e.subject if e.scope == "symbol" else e.subject.split(" / ")[0]
        if label not in names:
            names.append(label)
    head = ", ".join(names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else "")
    subject = f"Price risk: {head}"
    lines = [describe(e) for e in events]
    unverified = any(e.evidence_status not in ("verified",) for e in events
                     if e.scope == "symbol")
    notes = [
        "These are MEASURED PRICE MOVES, not trade signals. The AI signal for these stocks may "
        "not have changed: it reports a direction label, and a label does not move just "
        "because a price falls.",
        "This alert does not establish WHY the price moved. Check news and filings before "
        "acting on it.",
    ]
    if unverified:
        notes.append("Some figures are not verified against corporate-action records "
                     "(splits); they are unlikely to be wrong but are labelled as such.")
    notes.append("Full history: Risk Alerts page (/risk-alerts).")
    text = "\n".join(["Price-risk alert", ""] + [f"- {l}" for l in lines] + [""] + notes)
    html = ("<h3>Price-risk alert</h3><ul>" + "".join(f"<li>{escape(l)}</li>" for l in lines)
            + "</ul>" + "".join(f"<p style=\"color:#555;font-size:13px\">{escape(n)}</p>"
                                for n in notes))
    summary = f"Price risk: {head}. " + " ".join(lines[:3])
    return subject, text, html, summary
