"""Read-only report over the durable measurement records (2026-10-30).

Run inside market-data:

    docker exec -i -w /app stockai-market-data-1 python - 2026-11-02 2026-11-06 < scripts/measurement_report.py

Two sections, both READ ONLY:

PRICE-RISK TRIAL. A US session counts toward the five-session acceptance trial only when every
scheduled US scan for it is present as a COMPLETED symbol-scope record, all under one
configuration hash and one release, with delivery off. The scheduled US scans are 10:05, 12:05,
14:05 and 15:40 ET in the session plus the 20:10 ET settled-bar scan. A session missing any of
them is reported incomplete with the missing slots named; it is never counted.

ENTRY RECEIPTS (fresh-mark decision). Attempts by outcome and freshness. An attempt that stopped
before the exposure reservation never valued a position (`not_evaluated`). If no attempt reached
the reservation, the answer is "no opportunity to evaluate", never a pass.
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path[:0] = ['/app', '/app/shared']
from sqlalchemy import select, text  # noqa: E402
from db import SessionLocal  # noqa: E402
from common.market_calendar import session_bounds  # noqa: E402
from src.services.receipts.models import paper_entry_receipts as R, price_risk_scans as P  # noqa: E402

NY = ZoneInfo('America/New_York')
US_SLOTS = (time(10, 5), time(12, 5), time(14, 5), time(15, 40), time(20, 10))
#: A scheduled fire may start late by up to the job's misfire grace (15 min).
SLOT_WINDOW = timedelta(minutes=20)


def slot_of(started_utc):
    et = started_utc.replace(tzinfo=timezone.utc).astimezone(NY)
    for s in US_SLOTS:
        start = datetime.combine(et.date(), s, tzinfo=NY)
        if start <= et < start + SLOT_WINDOW:
            return et.date(), s.strftime('%H:%M')
    return et.date(), None


def main(first: date, last: date):
    lo = datetime.combine(first, time(0), tzinfo=NY).astimezone(timezone.utc).replace(tzinfo=None)
    hi = datetime.combine(last + timedelta(days=1), time(6), tzinfo=NY).astimezone(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as s:
        s.execute(text('SET TRANSACTION READ ONLY'))
        scans = s.execute(select(P.c.scan_id, P.c.scope, P.c.status, P.c.mode, P.c.started_at, P.c.subjects,
                                 P.c.measured, P.c.excluded, P.c.events, P.c.config_hash, P.c.release_sha,
                                 P.c.code_fingerprint, P.c.delivery_enabled, P.c.us_regular_session,
                                 P.c.coverage, P.c.quote_refusals, P.c.suppressions, P.c.error)
                          .where(P.c.started_at >= lo, P.c.started_at < hi).order_by(P.c.started_at)).mappings().all()
        receipts = s.execute(select(R).where(R.c.decided_at >= lo, R.c.decided_at < hi)).mappings().all()

    sessions = []
    by_day = defaultdict(dict)
    off_slot = []
    for r in scans:
        if r['scope'] != 'symbol':
            continue
        day, slot = slot_of(r['started_at'])
        if slot is None:
            off_slot.append(r['started_at'].isoformat())
            continue
        by_day[day].setdefault(slot, []).append(r)
    d = first
    while d <= last:
        if session_bounds('US', d):
            runs = by_day.get(d, {})
            missing = [s.strftime('%H:%M') for s in US_SLOTS if s.strftime('%H:%M') not in runs]
            failed = [k for k, v in runs.items() if not any(x['status'] == 'completed' for x in v)]
            done = [x for v in runs.values() for x in v if x['status'] == 'completed']
            hashes = {x['config_hash'] for x in done}
            releases = {(x['release_sha'], x['code_fingerprint']) for x in done}
            delivery_on = any(x['delivery_enabled'] for x in done)
            complete = not missing and not failed and len(hashes) == 1 and len(releases) == 1 and not delivery_on
            sessions.append(dict(
                session=d.isoformat(), complete=complete, missing_slots=missing, failed_slots=failed,
                config_hashes=sorted(h for h in hashes if h), releases=sorted(f'{a}/{b[:12]}' for a, b in releases),
                delivery_enabled_seen=delivery_on,
                events=sum(x['events'] or 0 for x in done),
                excluded_per_scan=[x['excluded'] for x in done],
                quote_refusals=sum(sum((x['quote_refusals'] or {}).get('counts', {}).values()) for x in done),
                suppressions=dict(sum((Counter((x['suppressions'] or {}).get('counts', {})) for x in done), Counter()))))
        d += timedelta(days=1)
    complete = [x['session'] for x in sessions if x['complete']]

    by = Counter((r['outcome'], r['freshness']) for r in receipts)
    evaluated = [r for r in receipts if r['freshness'] != 'not_evaluated']
    with_fallback = [r for r in evaluated if r['freshness'] == 'fallback_marks']
    would_change = [r for r in evaluated if r['decision_would_change']]
    fresh_mark = (dict(answer='no opportunity to evaluate',
                       why=f'{len(receipts)} attempt(s), none reached the exposure reservation')
                  if not evaluated else
                  dict(answer='measured', evaluated_attempts=len(evaluated),
                       with_fallback_marks=len(with_fallback),
                       enforcement_would_change_outcome=len(would_change),
                       rate_would_change=f'{len(would_change)} / {len(evaluated)} evaluated attempts'))
    print(json.dumps(dict(
        window=[first.isoformat(), last.isoformat()],
        price_risk_trial=dict(complete_sessions=complete, complete_count=len(complete),
                              trial_status=('five complete sessions' if len(complete) >= 5
                                            else f'{len(complete)} of 5 complete sessions'),
                              sessions=sessions, off_slot_scans=off_slot,
                              failed_scans=[dict(at=r['started_at'].isoformat(), scope=r['scope'], error=r['error'])
                                            for r in scans if r['status'] == 'failed']),
        entry_receipts=dict(attempts=len(receipts),
                            by_outcome_and_freshness={f'{o}:{f}': n for (o, f), n in sorted(by.items())},
                            rejection_reasons=dict(Counter(r['reason'] for r in receipts if r['outcome'] == 'rejected')),
                            candidate_price_sources=dict(Counter(r['candidate_price_source'] for r in receipts)),
                            releases=sorted({r['release_sha'] for r in receipts}),
                            fresh_mark=fresh_mark)), indent=1, default=str))


if __name__ == '__main__':
    a = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today() - timedelta(days=7)
    b = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    main(a, b)
