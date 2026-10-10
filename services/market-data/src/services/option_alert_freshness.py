"""Frozen event-age policy for options-flow entry alerts.

The provider discovery window may be wider than the decision window. A row can remain useful
historical evidence after it is too old to justify a current-entry notification.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


FLOW_EVENT_MAX_AGE = timedelta(minutes=15)
FLOW_EVENT_FUTURE_TOLERANCE = timedelta(seconds=5)


def classify_flow_event_freshness(event_at: datetime, decision_at: datetime) -> dict:
    if event_at.utcoffset() is None or decision_at.utcoffset() is None:
        return {"eligible": False, "state": "timezone_missing", "age_seconds": None}
    event = event_at.astimezone(timezone.utc)
    decision = decision_at.astimezone(timezone.utc)
    age = decision - event
    if age < -FLOW_EVENT_FUTURE_TOLERANCE:
        return {"eligible": False, "state": "future", "age_seconds": age.total_seconds()}
    if age > FLOW_EVENT_MAX_AGE:
        return {"eligible": False, "state": "stale", "age_seconds": age.total_seconds()}
    return {"eligible": True, "state": "fresh", "age_seconds": max(0.0, age.total_seconds())}


def partition_fresh_flow_candidates(candidates: dict[str, dict],
                                    decision_at: datetime) -> tuple[dict[str, dict], dict[str, int]]:
    """Recheck age at selection time so a slow scan cannot send an expired candidate."""
    eligible: dict[str, dict] = {}
    excluded = {"stale": 0, "future": 0, "timezone_missing": 0}
    for identity, candidate in candidates.items():
        result = classify_flow_event_freshness(candidate["event_at"], decision_at)
        if result["eligible"]:
            eligible[identity] = candidate
        else:
            excluded[result["state"]] += 1
    return eligible, excluded
