"""Explicit-opt-in, outbox-only promotion for fully gated option opportunities."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.models import User


ALERT_TYPE = "option_opportunity"
REQUIRED = (
    "opportunity_id", "symbol", "strategy", "contract_id", "entry_deadline",
    "maximum_loss", "quantity", "confirmation_rule", "invalidation_rule",
    "measurement_status",
)


def enqueue_if_eligible(session, *, user: User, opportunity: dict,
                        gate: dict) -> dict:
    """Freeze one email only after actionability and explicit opt-in are both proven.

    The caller commits the opportunity decision and outbox row in one transaction. This
    function never invokes a mail provider and absence of a preference row is opt-out.
    """
    if gate.get("status") != "actionable" or not gate.get("quantity"):
        return {"status": "blocked", "reason": "not_actionable", "created": False}
    missing = [key for key in REQUIRED if opportunity.get(key) in (None, "")]
    if missing:
        return {"status": "blocked", "reason": "missing_frozen_fields",
                "missing": missing, "created": False}
    if int(opportunity["quantity"]) != int(gate["quantity"]):
        return {"status": "blocked", "reason": "quantity_mismatch", "created": False}
    if not user.is_active or not (user.email or "").strip():
        return {"status": "blocked", "reason": "recipient_unavailable", "created": False}

    from sqlalchemy import select
    from common.outbox import enqueue
    from db.models import AlertPreference

    preference = session.execute(select(AlertPreference).where(
        AlertPreference.user_id == user.id,
        AlertPreference.alert_type == "option_opportunity",
    )).scalar_one_or_none()
    if preference is None or preference.enabled is not True:
        return {"status": "not_opted_in", "reason": "explicit_opt_in_required",
                "created": False}

    deadline = datetime.fromisoformat(str(opportunity["entry_deadline"]).replace("Z", "+00:00"))
    if deadline.utcoffset() is None:
        return {"status": "blocked", "reason": "entry_deadline_timezone_missing",
                "created": False}
    expires_at = deadline.astimezone(timezone.utc).replace(tzinfo=None)
    symbol = str(opportunity["symbol"]).upper()
    strategy = str(opportunity["strategy"])
    subject = f"Option opportunity: {symbol} {strategy}"
    lines = [
        f"{symbol} — {strategy}",
        f"Contract: {opportunity['contract_id']}",
        f"Quantity: {int(opportunity['quantity'])}",
        f"Maximum loss: ${float(opportunity['maximum_loss']):,.2f}",
        f"Confirm: {opportunity['confirmation_rule']}",
        f"Invalidate: {opportunity['invalidation_rule']}",
        f"Measurement: {opportunity['measurement_status']}",
        "Quotes and limits are frozen evidence, not a promise of execution or profit.",
    ]
    body_text = "\n".join(lines)
    body_html = "<br>".join(escape(line) for line in lines)
    row, created = enqueue(
        session,
        event_id=f"option-opportunity:{user.id}:{opportunity['opportunity_id']}",
        recipient=user.email.strip(), subject=subject,
        body_html=body_html, body_text=body_text,
        alert_type=ALERT_TYPE, user_id=user.id, expires_at=expires_at,
    )
    return {"status": "queued" if created else "already_queued",
            "created": created, "outbox_id": row.id}
