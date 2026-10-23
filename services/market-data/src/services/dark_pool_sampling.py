"""Bounded provider-poll journal, separate from the sampled-print median (R10).

Redis retains 21 days. Cache hits are not polls. Telemetry failure never blocks a feed
request and is exposed as unknown, never as continuous tape coverage.
"""
import json
from bisect import bisect_left
from datetime import datetime, timedelta, timezone

RETENTION = 21 * 86400
CADENCE = 900


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
                    if slot >= start and (bisect_left(times, slot) == bisect_left(times, end)):
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


def coverage(redis, symbol, *, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        raw = redis.zrangebyscore(key(symbol), now.timestamp() - RETENTION, "+inf")
        return summarize([json.loads(r) for r in raw], now=now)
    except Exception:
        return {"status": "query_failed", "note": "Poll history unavailable; sampling quality is unknown.",
                "identity_status": "provider_tracking_id_v2"}
