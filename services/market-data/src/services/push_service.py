"""T230-ALERTING-PUSH-NOTIFICATIONS: Web Push delivery.

Sends browser/mobile push notifications via the standard Web Push protocol (VAPID auth),
as a third delivery channel alongside email (email_service.py) and Discord/Slack webhooks
(send_webhook_notification). Push is near-instant (seconds, not the 5-15 minute latency of
email) since it's delivered directly by the browser's push service (FCM for Chrome, etc.)
rather than waiting on an SMTP/SES round-trip.

Fails open, matching every other delivery channel in this codebase: a push failure never
blocks or fails the alert as a whole — email is always the primary, guaranteed-delivery
channel; push is a fast, best-effort supplement. If VAPID keys aren't configured
(vapid_private_key/vapid_public_key both empty in settings), send_push_to_user() is a
no-op — this lets development/staging environments run without ever configuring push.
"""
from __future__ import annotations

import json

from common.config import get_settings
from common.logging import get_logger

log = get_logger(__name__)


def send_push_to_user(user, title: str, body: str, url: str = "/", tag: str | None = None) -> int:
    """Send a push notification to every subscription registered for this user (they may
    have several — one per browser/device). Returns the number of subscriptions successfully
    delivered to. A subscription that the browser has revoked (410 Gone / 404 Not Found from
    the push service) is deleted here so it stops being retried forever.

    tag: optional — browsers coalesce notifications with the same tag into one, so repeated
    alerts for the same symbol (e.g. multiple signal flips in a short window) replace rather
    than stack. Pass e.g. the symbol for signal alerts.
    """
    settings = get_settings()
    if not settings.vapid_private_key or not settings.vapid_public_key:
        return 0  # push not configured — fail open, not an error (matches email_provider="")

    subscriptions = list(getattr(user, "push_subscriptions", []) or [])
    if not subscriptions:
        return 0

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.warning("push.pywebpush_not_installed")
        return 0

    from db import SessionLocal, PushSubscription

    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
    sent = 0
    dead_subscription_ids: list[int] = []

    for sub in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh_key, "auth": sub.auth_key},
                },
                data=payload,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
                timeout=10,
            )
            sent += 1
        except WebPushException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in (404, 410):
                # Push service confirms this subscription no longer exists (user uninstalled
                # the PWA, cleared site data, or revoked notification permission) — stop
                # retrying it forever.
                dead_subscription_ids.append(sub.id)
            else:
                log.warning("push.send_failed", user_id=user.id, status_code=status_code, error=str(exc))
        except Exception as exc:
            log.warning("push.send_failed", user_id=user.id, error=str(exc))

    if dead_subscription_ids:
        try:
            with SessionLocal() as session:
                session.query(PushSubscription).filter(
                    PushSubscription.id.in_(dead_subscription_ids)
                ).delete(synchronize_session=False)
                session.commit()
        except Exception:
            pass  # cleanup is best-effort — a stale subscription just gets retried and re-pruned later

    return sent
