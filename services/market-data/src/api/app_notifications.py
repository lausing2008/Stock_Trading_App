"""App Notifications — per-user in-app notification storage."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db import AppNotification, User, get_session
from .auth import get_current_user

router = APIRouter(prefix="/app-notifications", tags=["app-notifications"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class NotificationIn(BaseModel):
    alert_id: str
    symbol: str
    message: str
    triggered_at: datetime
    current_value: float | None = None


class NotificationOut(BaseModel):
    id: int
    alert_id: str
    symbol: str
    message: str
    triggered_at: str
    read: bool
    current_value: float | None


def _out(n: AppNotification) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        alert_id=n.alert_id,
        symbol=n.symbol,
        message=n.message,
        triggered_at=n.triggered_at.isoformat(),
        read=n.read,
        current_value=n.current_value,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[NotificationOut])
def list_notifications(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(AppNotification)
        .where(AppNotification.user_id == current.id)
        .order_by(AppNotification.triggered_at.desc())
        .limit(100)
    ).scalars().all()
    return [_out(n) for n in rows]


@router.post("", response_model=NotificationOut)
def create_notification(
    body: NotificationIn,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    n = AppNotification(
        user_id=current.id,
        alert_id=body.alert_id,
        symbol=body.symbol,
        message=body.message,
        triggered_at=body.triggered_at.replace(tzinfo=None),
        current_value=body.current_value,
    )
    session.add(n)
    session.commit()
    session.refresh(n)
    return _out(n)


@router.put("/read-all")
def mark_all_read(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    session.execute(
        update(AppNotification)
        .where(AppNotification.user_id == current.id, AppNotification.read == False)  # noqa: E712
        .values(read=True)
    )
    session.commit()
    return {"status": "ok"}


@router.delete("")
def clear_notifications(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(AppNotification).where(AppNotification.user_id == current.id)
    ).scalars().all()
    for n in rows:
        session.delete(n)
    session.commit()
    return {"status": "cleared"}
