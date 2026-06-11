"""Signal alert CRUD — subscribe to AI Signal direction changes per symbol/horizon."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.logging import get_logger
from db import SignalAlert, get_session
from .auth import get_current_user

log = get_logger("signal_alerts")
router = APIRouter(prefix="/signal-alerts", tags=["signal-alerts"])

_VALID_HORIZONS = {"SHORT", "SWING", "LONG", "GROWTH"}
_VALID_MODES = {"all", "buy_only"}


class SignalAlertCreate(BaseModel):
    symbol: str
    email: str | None = None
    alert_mode: str = "all"
    horizon: str = "SWING"
    require_consensus: bool = False


class SignalAlertUpdate(BaseModel):
    alert_mode: str | None = None
    require_consensus: bool | None = None


class SignalAlertOut(BaseModel):
    id: int
    symbol: str
    email: str | None
    last_signal: str | None
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
        return existing

    mode = body.alert_mode if body.alert_mode in _VALID_MODES else "all"
    alert = SignalAlert(
        user_id=user.id,
        symbol=symbol,
        email=email,
        alert_mode=mode,
        horizon=horizon,
        require_consensus=body.require_consensus,
    )
    session.add(alert)
    session.commit()
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
