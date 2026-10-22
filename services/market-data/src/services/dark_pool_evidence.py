"""Options review Phase B4/B5: off-exchange print timing, side estimate and baseline status.

TIME (review F7). The freshness check only asked `parsed >= cutoff`, so a print stamped a day
in the FUTURE passed as fresh forever, and a timestamp without a zone was silently assumed UTC.
Every print time now gets a status, and only `ok` may count as evidence:

  ok                  parsed, zone-aware, not later than now + CLOCK_TOLERANCE
  future              later than now + CLOCK_TOLERANCE (a clock or feed error, never fresh)
  ambiguous_timezone  no offset: which instant it names is unknown, so it is quarantined
  missing / unparseable

Measured 2026-10-22 04:50 UTC: UW sends `...Z` on every print, and none of 779,482 stored prints was
future-dated, so the quarantine removes nothing real today; it exists for the day the feed
changes.

SIDE. Where a print lands in the NBBO spread is an ESTIMATE of the aggressor side, not a fact:
quote timing, late reporting, trade conditions and complex orders can all move it.

BASELINE (review F8). The relative bar compares a print with the median of CAPTURED prints for
the symbol (bounded provider windows fetched periodically), not with all market activity. When
it cannot be computed the alert falls back to the absolute floor — and why it could not be
computed is now stated instead of collapsed into one None.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

CLOCK_TOLERANCE = timedelta(minutes=2)

SIDE_BASIS = ("estimate from where the print fell inside the NBBO at execution (above 60% of the spread "
              "= buyer-initiated, below 40% = seller-initiated, between = unknown); quote timing, late "
              "reports and trade conditions can make it wrong")
VENUE_NOTE = ("off-exchange print; a reporting-facility code does not establish which dark pool or "
              "ATS executed it")


def print_time(raw, now: datetime | None = None) -> tuple[datetime | None, str]:
    """(UTC-aware instant or None, status). Never raises."""
    now = now or datetime.now(timezone.utc)
    if raw is None or raw == "":
        return None, "missing"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")) if isinstance(raw, str) else raw
        if not isinstance(dt, datetime):
            return None, "unparseable"
    except (ValueError, TypeError, AttributeError):
        return None, "unparseable"
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None, "ambiguous_timezone"
    dt = dt.astimezone(timezone.utc)
    if dt > now + CLOCK_TOLERANCE:
        return dt, "future"
    return dt, "ok"


def is_recent(raw, cutoff: datetime, now: datetime | None = None) -> bool:
    """Fresh evidence only: a dated, zone-aware, non-future print at or after `cutoff`."""
    dt, status = print_time(raw, now)
    return status == "ok" and dt >= cutoff


def baseline_status(n: int | None, median: float | None, min_prints: int) -> str:
    """For a query that RAN; a failed query is `query_failed`, set by the caller's except."""
    if n is None or n < min_prints:
        return "insufficient_history"
    if median is None or median <= 0:
        return "no_positive_premium"
    return "ok"


BASELINE_TEXT = {
    "ok": "relative bar active: compared with the median premium of captured prints",
    "insufficient_history": "absolute floor only: too few captured prints for a relative baseline",
    "no_positive_premium": "absolute floor only: captured prints carry no positive premium",
    "query_failed": "absolute floor only: the baseline query FAILED (not the same as no history)",
}
