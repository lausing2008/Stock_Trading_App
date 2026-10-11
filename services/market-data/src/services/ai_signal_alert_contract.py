"""AQ06/AQ07: delivery and freshness contract for selected AI signal alerts.

The signal row is a generated candidate. It becomes a delivered alert only when the sender
accepts a message containing that exact row. Provider acceptance is not inbox delivery.
Price freshness is separate: a current price cannot make old model output current.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from common.signal_time import parse_signal_instant

CONTRACT_VERSION = "ai-alert-1"
MAX_SIGNAL_AGE = timedelta(hours=72)

CANDIDATE = "candidate"
SUPPRESSED_BY_COOLDOWN = "suppressed_by_cooldown"
QUEUED_NOT_SENT = "queued_not_sent"
SENDER_ACCEPTED = "sender_accepted"
ORDER = (CANDIDATE, SUPPRESSED_BY_COOLDOWN, QUEUED_NOT_SENT, SENDER_ACCEPTED)
_RANK = {state: rank for rank, state in enumerate(ORDER)}


def merge(current: str | None, proposed: str) -> str:
    """Keep the strongest observed state across recipients and scheduler runs."""
    if proposed not in _RANK:
        raise ValueError(f"unknown AI alert delivery state: {proposed}")
    if current not in _RANK:
        return proposed
    return current if _RANK[current] >= _RANK[proposed] else proposed


def classify_selected_signal_freshness(raw_timestamp, *, decided_at: datetime | None = None) -> dict:
    """Require a readable, non-future selected-signal time no more than 72 hours old."""
    decided_at = decided_at or datetime.now(timezone.utc)
    if decided_at.tzinfo is None:
        decided_at = decided_at.replace(tzinfo=timezone.utc)
    instant = parse_signal_instant(raw_timestamp, now=decided_at)
    if not instant.usable:
        return {"eligible": False, "state": instant.state, "age_seconds": None,
                "max_age_seconds": MAX_SIGNAL_AGE.total_seconds()}
    age = decided_at - instant.at
    if age > MAX_SIGNAL_AGE:
        return {"eligible": False, "state": "stale", "age_seconds": age.total_seconds(),
                "max_age_seconds": MAX_SIGNAL_AGE.total_seconds()}
    return {"eligible": True, "state": "fresh",
            "age_seconds": max(0.0, age.total_seconds()),
            "max_age_seconds": MAX_SIGNAL_AGE.total_seconds()}
