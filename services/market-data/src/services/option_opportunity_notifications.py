"""Explicit-opt-in, outbox-only promotion for fully gated option opportunities."""
from __future__ import annotations

from datetime import datetime, timezone
import math
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.models import User


ALERT_TYPE = "option_opportunity"
REQUIRED = (
    "opportunity_id", "symbol", "strategy", "contract_id", "entry_deadline",
    "maximum_loss", "quantity", "confirmation_rule", "invalidation_rule",
    "measurement_status", "quote_source", "quote_as_of", "decision_fingerprint",
)


def enqueue_if_eligible(session, *, user: User, opportunity: dict,
                        decision_facts: dict) -> dict:
    """Freeze one email only after actionability and explicit opt-in are both proven.

    The caller commits the opportunity decision and outbox row in one transaction. This
    function never invokes a mail provider and absence of a preference row is opt-out.
    """
    from .option_strategy_ledger import actionable_gate, digest
    gate = actionable_gate(decision_facts)
    bound = gate.get("bound_decision")
    if gate.get("status") != "actionable" or not isinstance(bound, dict):
        return {"status": "blocked", "reason": "not_actionable", "created": False}
    missing = [key for key in REQUIRED if opportunity.get(key) in (None, "")]
    if missing:
        return {"status": "blocked", "reason": "missing_frozen_fields",
                "missing": missing, "created": False}
    fingerprint = bound.get("decision_fingerprint")
    unsigned_bound = {key: value for key, value in bound.items() if key != "decision_fingerprint"}
    if not isinstance(fingerprint, str) or digest(unsigned_bound) != fingerprint:
        return {"status": "blocked", "reason": "gate_fingerprint_invalid", "created": False}
    if opportunity.get("decision_fingerprint") != fingerprint:
        return {"status": "blocked", "reason": "decision_fingerprint_mismatch", "created": False}

    exact_fields = (
        "opportunity_id", "symbol", "strategy", "contract_id", "quote_source",
        "quote_as_of", "entry_deadline", "confirmation_rule", "invalidation_rule",
        "measurement_status",
    )
    mismatched = [key for key in exact_fields if str(opportunity.get(key)) != str(bound.get(key))]
    if mismatched:
        return {"status": "blocked", "reason": "bound_fields_mismatch",
                "fields": mismatched, "created": False}
    quantity = opportunity.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity != bound.get("quantity"):
        return {"status": "blocked", "reason": "quantity_mismatch", "created": False}
    maximum_loss = opportunity.get("maximum_loss")
    if (isinstance(maximum_loss, bool) or not isinstance(maximum_loss, (int, float))
            or not math.isfinite(maximum_loss)
            or abs(float(maximum_loss) - float(bound.get("maximum_loss", -1))) > 0.005):
        return {"status": "blocked", "reason": "maximum_loss_mismatch", "created": False}
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
    if deadline <= datetime.now(timezone.utc):
        return {"status": "blocked", "reason": "entry_window_expired", "created": False}
    expires_at = deadline.astimezone(timezone.utc).replace(tzinfo=None)
    symbol = str(opportunity["symbol"]).upper()
    strategy = str(opportunity["strategy"])
    subject = f"Option opportunity: {symbol} {strategy}"
    lines = [
        f"{symbol} — {strategy}",
        f"Contract: {opportunity['contract_id']}",
        f"Quantity: {opportunity['quantity']}",
        f"Maximum loss: ${float(opportunity['maximum_loss']):,.2f}",
        f"Quote: {opportunity['quote_source']} as of {opportunity['quote_as_of']}",
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
