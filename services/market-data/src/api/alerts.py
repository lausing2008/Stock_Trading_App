"""Price alert CRUD — create, list, delete alerts per authenticated user."""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.logging import get_logger
from db import PriceAlert, AlertCondition, get_session
from .auth import get_current_user

log = get_logger("alerts")
router = APIRouter(prefix="/alerts", tags=["alerts"])

_PRIVATE_NETS = [
    ipaddress.ip_network(cidr) for cidr in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7",
    )
]
_SYMBOL_RE = re.compile(r'^[A-Z0-9.\^\-]{1,20}$')


def _validate_webhook_url(url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("webhook_url must use https")
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(host)
        if any(addr in net for net in _PRIVATE_NETS):
            raise ValueError("webhook_url must not target private/internal IP ranges")
    except ValueError as exc:
        if "must" in str(exc):
            raise
        # hostname (not an IP) — allow; internal DNS not resolvable here
    return url


class AlertCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    condition: str
    threshold: float
    email: str | None = None
    note: str | None = Field(default=None, max_length=500)
    recurring: bool = False
    webhook_url: str | None = Field(default=None, max_length=2048)

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
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    log.info("alert.created", symbol=alert.symbol, condition=body.condition, threshold=body.threshold, recurring=body.recurring, user=user.username)
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
