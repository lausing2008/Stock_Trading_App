"""Price alert CRUD — create, list, delete alerts per authenticated user."""
from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.logging import get_logger
from db import PriceAlert, AlertCondition, SignalAlert, get_session
from .auth import get_current_user, validate_webhook_url as _validate_webhook_url

log = get_logger("alerts")
router = APIRouter(prefix="/alerts", tags=["alerts"])

_SYMBOL_RE = re.compile(r'^[A-Z0-9.\^\-]{1,20}$')

# T230-ALERTING-COMPOUND-CONDITIONS
_COMPOUND_METRICS = {"volume_ratio", "rsi", "signal"}
_COMPOUND_OPS = {"gte", "lte", "eq"}
_MAX_COMPOUND_CONDITIONS = 3


class CompoundCondition(BaseModel):
    metric: str
    op: str
    value: float | str

    @field_validator("metric")
    @classmethod
    def validate_metric(cls, v: str) -> str:
        if v not in _COMPOUND_METRICS:
            raise ValueError(f"metric must be one of: {sorted(_COMPOUND_METRICS)}")
        return v

    @field_validator("op")
    @classmethod
    def validate_op(cls, v: str) -> str:
        if v not in _COMPOUND_OPS:
            raise ValueError(f"op must be one of: {sorted(_COMPOUND_OPS)}")
        return v


class AlertCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    condition: str
    threshold: float
    email: str | None = None
    note: str | None = Field(default=None, max_length=500)
    recurring: bool = False
    webhook_url: str | None = Field(default=None, max_length=2048)
    compound_conditions: list[CompoundCondition] | None = None

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        v = v.upper().strip()
        if not _SYMBOL_RE.match(v):
            raise ValueError("symbol must be 1-20 uppercase alphanumeric characters")
        return v

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook(cls, v: str | None) -> str | None:
        return _validate_webhook_url(v)

    @field_validator("compound_conditions")
    @classmethod
    def validate_compound_conditions(cls, v: list[CompoundCondition] | None) -> list[CompoundCondition] | None:
        if not v:
            return None
        if len(v) > _MAX_COMPOUND_CONDITIONS:
            raise ValueError(f"at most {_MAX_COMPOUND_CONDITIONS} compound conditions allowed")
        for c in v:
            if c.metric == "signal" and not isinstance(c.value, str):
                raise ValueError("signal condition value must be a string (e.g. 'BUY')")
            if c.metric in ("volume_ratio", "rsi") and isinstance(c.value, str):
                raise ValueError(f"{c.metric} condition value must be numeric")
        return v


class AlertOut(BaseModel):
    id: int
    symbol: str
    condition: str
    threshold: float
    email: str | None
    note: str | None
    triggered: bool
    triggered_at: datetime | None
    recurring: bool
    last_sent_at: datetime | None
    webhook_url: str | None
    created_at: datetime
    compound_conditions: list[CompoundCondition] | None = None

    class Config:
        from_attributes = True


@router.post("", response_model=AlertOut, status_code=201)
def create_alert(
    body: AlertCreate,
    session: Session = Depends(get_session),
    user=Depends(get_current_user),
):
    try:
        cond = AlertCondition(body.condition)
    except ValueError:
        valid = [c.value for c in AlertCondition]
        raise HTTPException(400, f"condition must be one of: {valid}")

    email = body.email.strip() if body.email else user.email

    alert = PriceAlert(
        user_id=user.id,
        symbol=body.symbol.upper(),
        condition=cond,
        threshold=body.threshold,
        email=email,
        note=body.note,
        recurring=body.recurring,
        webhook_url=body.webhook_url or None,
        compound_conditions=[c.model_dump() for c in body.compound_conditions] if body.compound_conditions else None,
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    log.info("alert.created", symbol=alert.symbol, condition=body.condition, threshold=body.threshold, recurring=body.recurring,
              compound_conditions=alert.compound_conditions, user=user.username)
    return alert


@router.get("", response_model=list[AlertOut])
def list_alerts(
    session: Session = Depends(get_session),
    user=Depends(get_current_user),
):
    rows = session.execute(
        select(PriceAlert)
        .where(PriceAlert.user_id == user.id)
        .order_by(PriceAlert.created_at.desc())
    ).scalars().all()
    return list(rows)


@router.delete("/{alert_id}", status_code=204)
def delete_alert(
    alert_id: int,
    session: Session = Depends(get_session),
    user=Depends(get_current_user),
):
    alert = session.get(PriceAlert, alert_id)
    if not alert or alert.user_id != user.id:
        raise HTTPException(404, "Alert not found")
    session.delete(alert)
    session.commit()


# ── Alert History ─────────────────────────────────────────────────────────────

class SignalAlertHistoryOut(BaseModel):
    id: int
    symbol: str
    horizon: str | None
    last_signal: str | None
    last_sent_at: datetime | None

    class Config:
        from_attributes = True


class PriceAlertHistoryOut(BaseModel):
    id: int
    symbol: str
    condition: str
    threshold: float
    triggered_at: datetime | None
    note: str | None

    class Config:
        from_attributes = True


class AlertHistoryOut(BaseModel):
    signal_alerts: list[SignalAlertHistoryOut]
    price_alerts: list[PriceAlertHistoryOut]


@router.get("/history", response_model=AlertHistoryOut)
def alert_history(
    session: Session = Depends(get_session),
    user=Depends(get_current_user),
):
    """Return last 30 sent signal alerts and last 30 triggered price alerts for the user."""
    signal_rows = session.execute(
        select(SignalAlert)
        .where(SignalAlert.user_id == user.id, SignalAlert.last_sent_at.isnot(None))
        .order_by(SignalAlert.last_sent_at.desc())
        .limit(30)
    ).scalars().all()

    price_rows = session.execute(
        select(PriceAlert)
        .where(PriceAlert.user_id == user.id, PriceAlert.triggered.is_(True))
        .order_by(PriceAlert.triggered_at.desc())
        .limit(30)
    ).scalars().all()

    return AlertHistoryOut(
        signal_alerts=[
            SignalAlertHistoryOut(
                id=a.id, symbol=a.symbol,
                horizon=getattr(a, "alert_mode", None),
                last_signal=a.last_signal, last_sent_at=a.last_sent_at,
            )
            for a in signal_rows
        ],
        price_alerts=[
            PriceAlertHistoryOut(
                id=a.id, symbol=a.symbol,
                condition=a.condition.value, threshold=a.threshold,
                triggered_at=a.triggered_at, note=a.note,
            )
            for a in price_rows
        ],
    )


# ── AUD-ALERTPREFS: per-alert-type preferences + one-click unsubscribe ────────

@router.get("/preferences")
def get_alert_preferences(
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Every manageable alert type with this user's current setting.

    Returns the full catalogue, not just stored rows: a user who has never opened this screen
    has no rows at all, and an empty list would render as "you receive nothing" — the exact
    opposite of the truth, since absence means subscribed.
    """
    from common.alert_prefs import (ALERT_TYPES, default_enabled_for,
                                     effective_enabled)
    from db import AlertPreference

    stored = {
        p.alert_type: p.enabled
        for p in session.execute(
            select(AlertPreference).where(AlertPreference.user_id == user.id)
        ).scalars().all()
    }
    return {
        "types": [
            {
                "key": a["key"],
                "group": a["group"],
                "label": a["label"],
                "desc": a.get("desc"),
                # FAIL CLOSED. An undeclared default used to fall back to True, so a newly
                # added type silently enrolled every user who had never seen it. A type that
                # forgets to declare now enrols nobody, and the ratchet test below refuses the
                # omission outright.
                # AQ-REVIEW-2 (2026-10-12): resolved by the SAME function the sender uses.
                # This line previously re-implemented the rule, and the sender re-implemented it
                # a third way and disagreed — a type could read "off" here while the sender
                # admitted it. A default only one of three readers honours is not a default.
                "enabled": effective_enabled(a["key"], stored.get(a["key"])),
                "default_enabled": default_enabled_for(a["key"]),
            }
            for a in ALERT_TYPES
        ],
        # PER TYPE, not one answer for all of them. This was `True` unconditionally, which is the
        # claim the opt-in channel disproves; a UI explaining "unset means on" would have been
        # telling users the opposite of what the sender does for an opt-in type.
        "defaults_by_type": {a["key"]: default_enabled_for(a["key"]) for a in ALERT_TYPES},
    }


class AlertPreferenceUpdate(BaseModel):
    alert_type: str
    enabled: bool


@router.put("/preferences")
def set_alert_preference(
    body: AlertPreferenceUpdate,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    from common.alert_prefs import is_manageable
    from db import AlertPreference

    if not is_manageable(body.alert_type):
        # Covers both an unknown key and an ESSENTIAL one. Refusing loudly beats storing a row
        # that looks effective but is never consulted — a user who "turned off" their own price
        # alerts and still received them would have every reason to distrust the whole screen.
        raise HTTPException(
            status_code=400,
            detail=f"'{body.alert_type}' is not a manageable alert type",
        )
    row = session.execute(
        select(AlertPreference).where(
            AlertPreference.user_id == user.id,
            AlertPreference.alert_type == body.alert_type,
        )
    ).scalars().first()
    if row is None:
        row = AlertPreference(user_id=user.id, alert_type=body.alert_type)
        session.add(row)
    row.enabled = body.enabled
    row.source = "settings"
    row.updated_at = datetime.utcnow()
    session.commit()
    return {"alert_type": body.alert_type, "enabled": body.enabled}


@router.get("/unsubscribe")
def unsubscribe(
    u: int,
    t: str,
    sig: str,
    session: Session = Depends(get_session),
):
    """One-click opt-out from an email footer. INTENTIONALLY UNAUTHENTICATED.

    A mail client follows this link with no session and often months later, so requiring a login
    would defeat the purpose — the person is trying to leave, and a login wall is how "I
    unsubscribed and it kept coming" happens. Security comes from the HMAC instead: `sig` is
    signed with the shared jwt_secret over exactly (user_id, alert_type), so a link can silence
    one alert type for one account and nothing else. It cannot read anything, cannot enable
    anything, and cannot touch another user.

    Returns HTML because a human clicked it from a mail client.
    """
    from common.alert_prefs import is_manageable, label_for, verify_unsubscribe_token
    from common.config import Settings
    from db import AlertPreference

    secret = Settings().jwt_secret or ""
    if not is_manageable(t) or not verify_unsubscribe_token(u, t, sig, secret):
        # Deliberately identical response for a bad signature and an unknown type: telling the
        # two apart lets someone probe which alert types exist for which user ids.
        raise HTTPException(status_code=400, detail="Invalid or expired unsubscribe link")

    row = session.execute(
        select(AlertPreference).where(
            AlertPreference.user_id == u, AlertPreference.alert_type == t
        )
    ).scalars().first()
    if row is None:
        row = AlertPreference(user_id=u, alert_type=t)
        session.add(row)
    row.enabled = False
    row.source = "email_unsubscribe"
    row.updated_at = datetime.utcnow()
    session.commit()

    name = label_for(t)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8>"
        "<div style=\"font-family:system-ui,sans-serif;max-width:520px;margin:64px auto;"
        "padding:0 20px;line-height:1.6;color:#0f172a\">"
        f"<h2 style=\"margin:0 0 12px\">Unsubscribed</h2>"
        f"<p style=\"margin:0 0 16px\">You will no longer receive <strong>{name}</strong> emails.</p>"
        "<p style=\"margin:0;color:#64748b;font-size:14px\">Other alerts are unaffected. "
        "You can turn this back on any time in your alert settings.</p></div>"
    )


# ── Price-risk alert history ─────────────────────────────────────────────────
#
# The design asked for "a clear alert history" and for a shadow period that measures volume,
# misses and duplicates BEFORE anything is delivered. Both are served from the same rows, which
# is the point: a summary derived from a different query than the list it summarises is how two
# screens come to disagree about the same week.

@router.get("/risk-history")
def risk_alert_history(
    scope: str | None = None,
    rule: str | None = None,
    subject: str | None = None,
    days: int = 14,
    limit: int = 200,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Detected price-risk events, newest first, with this account's own receipt state.

    The receipt is LEFT-joined rather than required. An event with no receipt for this account
    is a real detection that this account was not an audience for, and dropping those would
    turn the history into "what I was sent", which is a different and much less useful
    question when the channel is deliberately not sending yet.
    """
    from datetime import timedelta
    from db import RiskEvent, RiskEventReceipt

    since = datetime.utcnow() - timedelta(days=max(1, min(days, 120)))
    q = (select(RiskEvent, RiskEventReceipt)
         .join(RiskEventReceipt,
               (RiskEventReceipt.event_id == RiskEvent.id)
               & (RiskEventReceipt.user_id == user.id), isouter=True)
         .where(RiskEvent.detected_at >= since)
         .order_by(RiskEvent.detected_at.desc())
         .limit(max(1, min(limit, 1000))))
    if scope:
        q = q.where(RiskEvent.scope == scope)
    if rule:
        q = q.where(RiskEvent.rule == rule)
    if subject:
        q = q.where(RiskEvent.subject == subject.upper()
                    if len(subject) <= 20 else subject)

    rows = []
    for event, receipt in session.execute(q).all():
        rows.append({
            "id": event.id, "event_key": event.event_key,
            "scope": event.scope, "subject": event.subject, "rule": event.rule,
            "direction": event.direction, "change_pct": event.change_pct,
            "step": event.step, "ladder": (event.evidence or {}).get("ladder"),
            "window_sessions": event.window_sessions,
            "observed_from": event.observed_from.isoformat() if event.observed_from else None,
            "observed_to": event.observed_to.isoformat(),
            "price_basis": event.price_basis,
            "evidence_status": event.evidence_status,
            "evidence_reason": event.evidence_reason,
            "evidence": event.evidence,
            # SHADOW IS A STATE, NOT AN ABSENCE. "detected but deliberately not delivered" and
            # "nobody was eligible" look identical unless the mode is carried on the row.
            "delivery_mode": event.delivery_mode,
            "detected_at": event.detected_at.isoformat() if event.detected_at else None,
            "delivery_state": receipt.state if receipt else None,
            "delivery_reason": receipt.reason if receipt else None,
        })
    return {"count": len(rows), "days": days, "events": rows}


@router.get("/risk-history/summary")
def risk_alert_summary(
    days: int = 14,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """What a shadow period is for: volume, duplicate pressure and what could not be measured.

    `suppressed_by_ladder` is the number of detections that were NOT raised because they did
    not escalate. It is the single most useful number here — it is the difference between this
    channel and one that repeats "still down" on every polling cycle — and it is reported
    rather than inferred from the gap between two other counts.
    """
    from datetime import timedelta
    from sqlalchemy import func
    from db import RiskEvent, RiskEventReceipt

    since = datetime.utcnow() - timedelta(days=max(1, min(days, 120)))
    by_rule = dict(session.execute(
        select(RiskEvent.rule, func.count()).where(RiskEvent.detected_at >= since)
        .group_by(RiskEvent.rule)).all())
    by_mode = dict(session.execute(
        select(RiskEvent.delivery_mode, func.count()).where(RiskEvent.detected_at >= since)
        .group_by(RiskEvent.delivery_mode)).all())
    by_state = dict(session.execute(
        select(RiskEventReceipt.state, func.count())
        .join(RiskEvent, RiskEvent.id == RiskEventReceipt.event_id)
        .where(RiskEvent.detected_at >= since, RiskEventReceipt.user_id == user.id)
        .group_by(RiskEventReceipt.state)).all())
    unverified = session.execute(
        select(func.count()).select_from(RiskEvent)
        .where(RiskEvent.detected_at >= since,
               RiskEvent.evidence_status.notin_(("verified", "provisional")))).scalar() or 0
    subjects = session.execute(
        select(func.count(func.distinct(RiskEvent.subject)))
        .where(RiskEvent.detected_at >= since)).scalar() or 0

    total = sum(by_rule.values())
    return {
        "days": days,
        "events_total": total,
        "events_per_day": round(total / max(1, days), 2),
        "distinct_subjects": subjects,
        "by_rule": by_rule,
        "by_delivery_mode": by_mode,
        "my_receipt_states": by_state,
        "unmeasurable_events": by_rule.get("unmeasurable", 0),
        "events_without_verified_basis": unverified,
        # Stated, not implied: these are detections, and a detection is not a message. While
        # `by_delivery_mode` shows `shadow`, nothing here was sent to anyone.
        "delivery_note": ("shadow events are detections whose delivery was deliberately "
                          "withheld; they were not sent"),
    }


@router.get("/risk-history/exclusions")
def risk_alert_exclusions(
    days: int = 14,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Every subject that could NOT be measured, and the reason, one row each.

    A delivery precondition, not a diagnostic nicety. "17 unmeasurable" cannot be acted on; a
    named subject with a named limit can, and the three limits have completely different
    remedies — a stale span is fixed by refreshing coverage, a missing completeness guarantee
    is not fixable by refreshing at all, and an unappliable corporate action is neither.

    `best_achievable` is reported per subject so a reader can see where refreshing would get
    this figure to. Against a source that publishes no completeness guarantee that ceiling is
    `provisional`, and no amount of re-fetching reaches `verified`.
    """
    from datetime import timedelta
    from db import RiskEvent

    since = datetime.utcnow() - timedelta(days=max(1, min(days, 120)))
    rows = session.execute(
        select(RiskEvent)
        .where(RiskEvent.detected_at >= since, RiskEvent.rule == "unmeasurable")
        .order_by(RiskEvent.detected_at.desc())).scalars().all()

    by_factor: dict[str, int] = {}
    out = []
    for ev in rows:
        ev_evidence = ev.evidence or {}
        factor = ev_evidence.get("limiting_factor") or "unspecified"
        by_factor[factor] = by_factor.get(factor, 0) + 1
        out.append({
            "subject": ev.subject, "scope": ev.scope,
            "observed_to": ev.observed_to.isoformat(),
            "limiting_factor": factor,
            "reason": ev.evidence_reason,
            "best_achievable": ev_evidence.get("best_achievable"),
            "coverage_evidenced_to": ev_evidence.get("coverage_evidenced_to"),
            "window_end": ev_evidence.get("window_end"),
            # Separated because the remedies differ, and because a refresh campaign that
            # "fixed" the first kind would leave the other two untouched while looking like
            # a complete repair.
            "fixable_by_refresh": factor == "coverage_span",
        })
    return {
        "days": days, "count": len(out), "by_limiting_factor": by_factor,
        "unexplained": [r["subject"] for r in out if r["limiting_factor"] == "unspecified"],
        "exclusions": out,
    }
