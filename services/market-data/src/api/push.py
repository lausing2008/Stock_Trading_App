"""T230-ALERTING-PUSH-NOTIFICATIONS: Web Push subscription management.

A user's browser registers a push subscription (via the frontend's service worker +
PushManager.subscribe()) and POSTs it here to store. send_push_to_user() (push_service.py)
reads these rows to deliver notifications. See docs/DESIGN_T241_POSITION_SCALING_2026-07-10.md
for an unrelated feature's write-up style this follows — push has its own design note in the
tracker (T230-ALERTING-PUSH-NOTIFICATIONS).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.config import get_settings
from common.logging import get_logger
from db import PushSubscription, User, get_session
from .auth import get_current_user

log = get_logger("push")
router = APIRouter(prefix="/push", tags=["push"])
_settings = get_settings()


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=128)


class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=512)
    keys: PushSubscriptionKeys
    user_agent: str | None = Field(default=None, max_length=256)


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=512)


@router.get("/vapid-public-key")
def get_vapid_public_key() -> dict:
    """Unauthenticated — the VAPID public key is not a secret (the browser needs it to build
    a subscription, and it's inherently exposed in every push subscription's applicationServerKey
    anyway). Returns an empty string if push isn't configured, so the frontend can detect
    "push not available on this deployment" and hide the enable-notifications UI accordingly.
    """
    return {"public_key": _settings.vapid_public_key}


@router.post("/subscribe")
def subscribe(
    body: PushSubscribeRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Idempotent — re-subscribing with the same endpoint (e.g. the service worker refreshing
    its own subscription) updates the existing row rather than creating a duplicate, since
    endpoint is unique across the whole table (a push endpoint URL is a single opaque
    identifier issued by the browser's push service, never shared across users/devices).
    """
    existing = session.execute(
        select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
    ).scalar_one_or_none()

    if existing:
        existing.user_id = user.id
        existing.p256dh_key = body.keys.p256dh
        existing.auth_key = body.keys.auth
        existing.user_agent = body.user_agent
        existing.last_used_at = datetime.now(timezone.utc)
    else:
        session.add(PushSubscription(
            user_id=user.id,
            endpoint=body.endpoint,
            p256dh_key=body.keys.p256dh,
            auth_key=body.keys.auth,
            user_agent=body.user_agent,
        ))
    session.commit()
    log.info("push.subscribed", user_id=user.id)
    return {"status": "subscribed"}


@router.post("/unsubscribe")
def unsubscribe(
    body: PushUnsubscribeRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    result = session.execute(
        select(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint,
            PushSubscription.user_id == user.id,
        )
    ).scalar_one_or_none()
    if result is None:
        # Already gone (e.g. double-click, or the server-side cleanup in push_service.py
        # already pruned it after a 410) — not an error, the end state the caller wants is
        # already true.
        return {"status": "not_found"}
    session.delete(result)
    session.commit()
    log.info("push.unsubscribed", user_id=user.id)
    return {"status": "unsubscribed"}


@router.get("/status")
def get_push_status(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """How many active subscriptions this user has (browsers/devices with push enabled) —
    used by the frontend settings page to show "Push notifications: 2 devices enabled"
    rather than a binary on/off that hides multi-device reality.
    """
    count = session.execute(
        select(PushSubscription).where(PushSubscription.user_id == user.id)
    ).scalars().all()
    return {"subscription_count": len(count), "push_available": bool(_settings.vapid_public_key)}
