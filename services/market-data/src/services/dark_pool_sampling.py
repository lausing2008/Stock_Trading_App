"""Bounded provider-poll journal, separate from the sampled-print median (R10).

Redis retains 21 days. Cache hits are not polls. Telemetry failure never blocks a feed
request and is exposed as unknown, never as continuous tape coverage.
"""
import json
from bisect import bisect_left
from datetime import datetime, timedelta, timezone

RETENTION = 21 * 86400
CADENCE = 900
# AUD-DP-COVERAGE-DRIFT (2026-11-10): a poll happens when the 900 s cache has expired AND the
# next 1-minute job tick arrives, so real polls are 900-960 s apart and drift across the fixed
# 900 s slot grid. Measured on a full session: steady 930-960 s polling counted 1-2 slots as
# missing every day, so coverage read "degraded" with nothing wrong. A slot is now covered by a
# poll up to two job ticks after its end. A real outage (any gap of 2+ cadences) still counts.
SLOT_TOLERANCE_SECONDS = 120


def key(symbol):
    return f"stockai:darkpool:polls:v1:{symbol.upper()}"


def record(redis, symbol, status, count=0, limit=50, *, now=None, identified=None):
    now = now or datetime.now(timezone.utc)
    try:
        item = json.dumps({"at": now.isoformat(), "status": status, "count": count,
                           "saturated": count >= limit, "limit": limit, "identified": identified}, sort_keys=True)
        pipe = redis.pipeline()
        pipe.zadd(key(symbol), {item: now.timestamp()})
        pipe.zremrangebyscore(key(symbol), "-inf", now.timestamp() - RETENTION)
        pipe.zremrangebyrank(key(symbol), 0, -4097)
        pipe.expire(key(symbol), RETENTION)
        pipe.execute()
    except Exception:
        pass


def summarize(rows, *, now=None):
    from common.market_calendar import session_bounds
    now = now or datetime.now(timezone.utc)
    good = sorted(rows, key=lambda r: r["at"])
    successful = [r for r in good if r["status"] in ("ok", "empty")]
    times = sorted(datetime.fromisoformat(r["at"]) for r in successful)
    failures = len(good) - len(successful)
    saturated = sum(bool(r["saturated"]) for r in successful)
    unidentified = sum(max(0,r.get("count",0)-(r.get("identified") or 0)) for r in successful)
    # Only assess complete expected slots after observation began, during regular sessions.
    missing = 0
    if good:
        start = datetime.fromisoformat(good[0]["at"])
        day = start.date()
        while day <= now.date():
            bounds = session_bounds("US", day)
            if bounds:
                slot, close = bounds
                while slot + timedelta(seconds=CADENCE) <= min(close, now):
                    end = slot + timedelta(seconds=CADENCE)
                    covered_until = end + timedelta(seconds=SLOT_TOLERANCE_SECONDS)
                    if slot >= start and (bisect_left(times, slot) == bisect_left(times, covered_until)):
                        missing += 1
                    slot = end
            day += timedelta(days=1)
    return {"status": "unobserved" if not good else "degraded" if failures or saturated or missing or unidentified else "observed_sample",
            "polls": len(good), "failed_polls": failures, "unidentified_events": unidentified, "saturated_windows": saturated,
            "missing_session_slots": missing, "expected_cadence_seconds": CADENCE,
            "observed_since": good[0]["at"] if good else None,
            "last_poll": good[-1]["at"] if good else None,
            "identity_status": "provider_tracking_id_v2",
            "note": "Captured windows only. The identity ledger excludes legacy tuples and unidentified prints; continuous tape coverage is not established."}


def coverage(redis, symbol, *, now=None, window_seconds=RETENTION):
    """`window_seconds` should match what the figure is shown beside (AUD-DP-COVERAGE-WINDOW:
    the route showed 21 days of poll history next to a 14-day baseline)."""
    now = now or datetime.now(timezone.utc)
    window_seconds = min(window_seconds, RETENTION)
    try:
        raw = redis.zrangebyscore(key(symbol), now.timestamp() - window_seconds, "+inf")
        return summarize([json.loads(r) for r in raw], now=now)
    except Exception:
        return {"status": "query_failed", "note": "Poll history unavailable; sampling quality is unknown.",
                "identity_status": "provider_tracking_id_v2"}
