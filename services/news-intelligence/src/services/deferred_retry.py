"""Classifying the headlines a budget ceiling deferred, once capacity returns.

WHY A REPLAY PATH IS NOT OPTIONAL. Ingestion skips URLs it has already stored, so a row saved
WITHOUT a classification is never offered to the classifier again by the normal path: the next
poll sees the URL, treats it as old, and moves on. Recording the deferral reason made that
visible; it did not make it recoverable. Without this worker a budget deferral is permanent.

FRESHNESS IS CHECKED BEFORE A RISK GATE IS TOUCHED. A headline deferred yesterday can still be
worth labelling for the record, but it must not set a hot-news flag today as though it had just
arrived — the gate exists to suppress a BUY on news the market has not yet absorbed, and a
day-old story is not that. So an old item is classified and stored, and explicitly does NOT
re-arm the gate.

NO RE-INGESTION. This updates the row that already exists, found by its own id. Nothing is
inserted, so there is no second copy and no second URL.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from common.logging import get_logger
from sqlalchemy import select

log = get_logger("news-intelligence.deferred_retry")

#: How far back to look for deferred work. Older than this and the headline is of record
#: interest only, and is left alone rather than classified at a cost for no decision.
_MAX_AGE_HOURS = int(os.getenv("NEWS_DEFERRED_MAX_AGE_HOURS", "48"))

#: Beyond this age a classification is stored but must NOT arm a hot-news gate.
_GATE_FRESHNESS_MINUTES = int(os.getenv("NEWS_GATE_FRESHNESS_MINUTES", "120"))

#: Small batches: the point is to drain gradually as capacity frees, not to re-spend the
#: ceiling the moment it resets.
_BATCH = int(os.getenv("NEWS_DEFERRED_BATCH", "8"))


def retry_deferred(limit: int = _BATCH) -> dict:
    """Classify a few budget-deferred headlines. Returns what it did, for the dashboard."""
    from db import RealtimeNewsItem, SessionLocal
    from .classify import classify_in_batches
    from common.ai_keys import get_admin_ai_key

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(hours=_MAX_AGE_HOURS)
    api_key = get_admin_ai_key("claude")
    if not api_key:
        return {"attempted": 0, "reason": "no API key configured"}

    with SessionLocal() as session:
        rows = list(session.execute(
            select(RealtimeNewsItem)
            .where(RealtimeNewsItem.classification_deferred_reason == "budget_exhausted",
                   RealtimeNewsItem.ingested_at >= cutoff)
            .order_by(RealtimeNewsItem.published_at.desc())
            .limit(limit)).scalars().all())
        if not rows:
            return {"attempted": 0, "reason": "nothing deferred within the age window"}

        deferred_again: set[int] = set()
        results = classify_in_batches(
            [r.headline for r in rows], api_key,
            urls=[r.url for r in rows],
            scopes=["tracked"] * len(rows),      # it was eligible when first deferred
            deferred_out=deferred_again,
        )

        classified = stale = 0
        for pos, (row, cls) in enumerate(zip(rows, results)):
            if cls is None:
                if pos in deferred_again:
                    row.classification_attempts = (row.classification_attempts or 0) + 1
                continue
            # UPDATE IN PLACE. The row already exists; re-ingesting would duplicate the URL.
            row.sentiment_score = cls["sentiment_score"]
            row.sentiment_label = cls["sentiment_label"]
            row.category = cls["category"]
            row.classification_deferred_reason = None
            row.classification_attempts = (row.classification_attempts or 0) + 1

            fresh = row.published_at >= now - timedelta(minutes=_GATE_FRESHNESS_MINUTES)
            if fresh:
                # Only a FRESH item may set materiality, because that is what arms the gate.
                row.is_material = bool(cls["is_material"])
                classified += 1
            else:
                # The label is recorded; the gate is NOT armed by a stale headline. An existing
                # flag is left exactly as it was — a deferral never clears one either.
                stale += 1
            session.add(row)
        session.commit()

    out = {"attempted": len(rows), "classified_fresh": classified,
           "classified_stale_no_gate": stale,
           "still_deferred": len(deferred_again),
           "gate_freshness_minutes": _GATE_FRESHNESS_MINUTES}
    log.info("news_deferred_retry.ran", **out)
    return out
