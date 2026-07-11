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
