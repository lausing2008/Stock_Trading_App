"""Signal alert CRUD — subscribe to AI Signal direction changes per symbol/horizon."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common.logging import get_logger
from db import SignalAlert, get_session
from .auth import get_current_user

log = get_logger("signal_alerts")
router = APIRouter(prefix="/signal-alerts", tags=["signal-alerts"])

_VALID_HORIZONS = {"SHORT", "SWING", "LONG", "GROWTH"}
_VALID_MODES = {"all", "buy_only"}


def effects_version(effects: dict) -> str:
    """A hash of every CONSENT-RELEVANT field a preview disclosed (review S5).

    A confirmation carries this back; the write recomputes it inside its own transaction and
    refuses on a mismatch, so a change the user never saw — an email address added or removed,
    signal emails switched on or off, a subscription appearing — cannot ride on an old card.
    """
    keys = ("already_in_list", "already_subscribed", "existing_mode", "will_create",
            "email_on_file", "signal_emails_enabled", "effect", "horizon", "alert_mode")
    payload = {k: effects.get(k) for k in keys}
    sub = effects.get("signal_subscription") or {}
    payload["sub"] = {k: sub.get(k) for k in ("horizon", "already_subscribed", "will_create")}
    payload["list"] = (effects.get("list") or {}).get("id")
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]


def subscription_effects(session: Session, user, symbol: str, horizon: str,
                         mode: str = "all") -> dict:
    """What subscribing `user` to `symbol`/`horizon` signal emails WILL do, effective result first.

    Used by the signal-alert preview and create, and by the watchlist add (which subscribes as a
    side effect), so every disclosure of this subscription is computed one way.
    """
    from common.alert_prefs import effective_enabled
    from db import AlertPreference
    existing = session.execute(select(SignalAlert).where(
        SignalAlert.user_id == user.id, SignalAlert.symbol == symbol,
        SignalAlert.horizon == horizon)).scalar_one_or_none()
    pref = session.execute(
        select(AlertPreference.enabled).where(AlertPreference.user_id == user.id,
                                              AlertPreference.alert_type == "signal")).first()
    emails_on = effective_enabled("signal", pref[0] if pref else None)
    has_email = bool(user.email)
    return {"symbol": symbol, "horizon": horizon, "alert_mode": mode,
            "already_subscribed": existing is not None,
            "existing_mode": existing.alert_mode if existing is not None else None,
            "will_create": existing is None,
            "email_on_file": has_email, "signal_emails_enabled": emails_on}


def describe_subscription(e: dict) -> str:
    sym, h = e["symbol"], e["horizon"]
    if e["already_subscribed"]:
        return (f"You are already subscribed to {h} signal alerts for {sym} "
                f"({e['existing_mode']}); saving keeps that subscription unchanged.")
    if not e["email_on_file"]:
        return (f"No email address is on your account, so a {h} signal alert for {sym} cannot "
                f"send anything.")
    if not e["signal_emails_enabled"]:
        return (f"Creates a {h} signal-alert subscription for {sym}, but AI signal emails are "
                f"turned off in your alert preferences, so none will be sent unless you turn "
                f"them on.")
    return (f"Subscribes you to {h} signal-change emails for {sym}"
            f"{' (BUY only)' if e['alert_mode'] == 'buy_only' else ''} at your account address.")


@router.get("/preview")
def preview_signal_alert(
    symbol: str = Query(..., max_length=20),
    horizon: str = Query("SWING"),
    alert_mode: str = Query("all"),
    session: Session = Depends(get_session),
    user=Depends(get_current_user),
):
    """What POST /signal-alerts would do, WITHOUT doing it — with the effective email result."""
    h = horizon.upper() if horizon.upper() in _VALID_HORIZONS else "SWING"
    m = alert_mode if alert_mode in _VALID_MODES else "all"
    e = subscription_effects(session, user, symbol.upper().strip(), h, m)
    e["effect"] = describe_subscription(e)
    e["effects_version"] = effects_version(e)
    return e


class SignalAlertCreate(BaseModel):
    symbol: str
    email: str | None = None
    alert_mode: str = "all"
    horizon: str = "SWING"
    require_consensus: bool = False
    #: Review S5: the version of the preview the user confirmed. When given, the create refuses
    #: (409) if the effective result has changed since — computed inside this transaction.
    expected_effects_version: str | None = Field(default=None, max_length=64)


class SignalAlertUpdate(BaseModel):
    alert_mode: str | None = None
    require_consensus: bool | None = None


class SignalAlertOut(BaseModel):
    id: int
    symbol: str
    email: str | None
    last_signal: str | None
    last_sent_at: datetime | None
    alert_mode: str
    horizon: str
    require_consensus: bool
    created_at: datetime

    class Config:
        from_attributes = True


@router.post("", response_model=SignalAlertOut, status_code=201)
def create_signal_alert(
    body: SignalAlertCreate,
    session: Session = Depends(get_session),
    user=Depends(get_current_user),
):
    email = body.email or user.email
    if not email:
        raise HTTPException(400, "No email address — set one in Settings → Profile or provide one here")

    symbol = body.symbol.upper().strip()
    horizon = body.horizon.upper() if body.horizon.upper() in _VALID_HORIZONS else "SWING"

    existing = session.execute(
        select(SignalAlert).where(
            SignalAlert.user_id == user.id,
            SignalAlert.symbol == symbol,
            SignalAlert.horizon == horizon,
        )
    ).scalar_one_or_none()
    if existing:
        return existing          # idempotent: also how a retried confirmation reconciles

    mode = body.alert_mode if body.alert_mode in _VALID_MODES else "all"
    if body.expected_effects_version:
        e = subscription_effects(session, user, symbol, horizon, mode)
        e["effect"] = describe_subscription(e)
        if effects_version(e) != body.expected_effects_version:
            raise HTTPException(409, "what this subscription will do has changed since you "
                                     "reviewed it; review it again")
    alert = SignalAlert(
        user_id=user.id,
        symbol=symbol,
        email=email,
        alert_mode=mode,
        horizon=horizon,
        require_consensus=body.require_consensus,
    )
    session.add(alert)
    try:
        session.commit()
    except IntegrityError:
        # A concurrent create won the (user, symbol, horizon) unique constraint. Same request,
        # same outcome: serve that row rather than a 500.
        session.rollback()
        existing = session.execute(select(SignalAlert).where(
            SignalAlert.user_id == user.id, SignalAlert.symbol == symbol,
            SignalAlert.horizon == horizon)).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    session.refresh(alert)
    log.info("signal_alert.created", symbol=symbol, horizon=horizon, user=user.username)
    return alert


@router.get("", response_model=list[SignalAlertOut])
def list_signal_alerts(
    session: Session = Depends(get_session),
    user=Depends(get_current_user),
):
    rows = session.execute(
        select(SignalAlert)
        .where(SignalAlert.user_id == user.id)
        .order_by(SignalAlert.created_at.desc())
    ).scalars().all()
    return list(rows)


@router.patch("/{alert_id}", response_model=SignalAlertOut)
def update_signal_alert(
    alert_id: int,
    body: SignalAlertUpdate,
    session: Session = Depends(get_session),
    user=Depends(get_current_user),
):
    alert = session.get(SignalAlert, alert_id)
    if not alert or alert.user_id != user.id:
        raise HTTPException(404, "Alert not found")
    if body.alert_mode is not None and body.alert_mode in _VALID_MODES:
        alert.alert_mode = body.alert_mode
    if body.require_consensus is not None:
        alert.require_consensus = body.require_consensus
    session.commit()
    session.refresh(alert)
    return alert


@router.delete("/{alert_id}", status_code=204)
def delete_signal_alert(
    alert_id: int,
    session: Session = Depends(get_session),
    user=Depends(get_current_user),
):
    alert = session.get(SignalAlert, alert_id)
    if not alert or alert.user_id != user.id:
        raise HTTPException(404, "Alert not found")
    session.delete(alert)
    session.commit()
