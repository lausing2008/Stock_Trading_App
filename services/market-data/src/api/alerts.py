"""Price alert CRUD — create, list, delete alerts per authenticated user."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.logging import get_logger
from db import PriceAlert, AlertCondition, get_session
from .auth import get_current_user

log = get_logger("alerts")
router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertCreate(BaseModel):
    symbol: str
    condition: str        # "above" | "below"
    threshold: float
    email: str | None = None   # falls back to user's account email
    note: str | None = None


class AlertOut(BaseModel):
    id: int
    symbol: str
    condition: str
    threshold: float
    email: str
    note: str | None
    triggered: bool
    triggered_at: datetime | None
    created_at: datetime

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
        raise HTTPException(400, "condition must be 'above' or 'below'")

    email = body.email or user.email
    if not email:
        raise HTTPException(400, "No email address — set one in Settings → Profile or provide one with the alert")

    alert = PriceAlert(
        user_id=user.id,
        symbol=body.symbol.upper(),
        condition=cond,
        threshold=body.threshold,
        email=email,
        note=body.note,
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    log.info("alert.created", symbol=alert.symbol, condition=body.condition, threshold=body.threshold, user=user.username)
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
