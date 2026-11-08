"""Read-only: RZY partial-candle fixes A and B for one US session (AUD-RZY-PARTIALCANDLE).

Run inside the market-data container, like rzy_session_report.py:

    docker cp scripts/rzy_ab_read.py stockai-market-data-1:/tmp/
    docker exec -w /app stockai-market-data-1 python /tmp/rzy_ab_read.py 2026-11-09

A bar is HELD when its first observed post-close write came after the first session scan that
could have consumed it (close + the 2-minute settle). bar_post_close_writes keeps the earliest
such write per bar for 35 days; later revisions do not move it. Before B (release 75a6f97c) a held
bar was consumed while partial; from B on it waits for that write. The same classification is
applied to every day, so a pre-B day gives the baseline for the same kind of event.

Latency is reported separately for events whose signal candle had a held bar and events without,
never only pooled: a few long holds can hide in a flat median. A 15m/1h candle counts as held when
any of its 5m bars was. Events whose bars have no post-close record are 'unknown', not unaffected.
Nothing is written (READ ONLY transaction). A's retry is read from the saved log, not here.
"""
import json
import os
import sys
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path[:0] = [os.environ.get('RZY_EVAL_ROOT', '/app'), '/app/shared']
from sqlalchemy import select, text  # noqa: E402
from db import SessionLocal, NotificationOutbox, Stock  # noqa: E402
from src.services.bar_ingest_observations import BarPostCloseWrite  # noqa: E402
from src.services.rzy_models import RzyOccurrence, RzyRebuild, RzyScanRun  # noqa: E402
from src.services.rzy_workflow import emailable  # noqa: E402

STEP = {'5m': 5, '15m': 15, '1h': 60}
SETTLE = timedelta(minutes=2)
NY = ZoneInfo('America/New_York')


def pct(values):
    v = sorted(x for x in values if x is not None)
    if not v:
        return dict(n=0)
    q = lambda p: round(v[min(len(v) - 1, int(len(v) * p))], 2)
    return dict(n=len(v), median=q(.5), p90=q(.9), max=round(v[-1], 2))


def utc(day, hour, minute=0):
    d = datetime.strptime(day, '%Y-%m-%d')
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=NY).astimezone(timezone.utc).replace(tzinfo=None)


def main(day):
    start, end = utc(day, 4), utc(day, 20)
    open_, close = utc(day, 9, 30), utc(day, 16)
    out = dict(session_day=day, release=os.environ.get('STOCKAI_RELEASE_SHA', 'unknown'),
               rule='held = first post-close write after the first session scan past close + 2 min')
    with SessionLocal() as s:
        s.execute(text('SET TRANSACTION READ ONLY'))
        s.execute(text("SET LOCAL statement_timeout='60000'"))
        runs = s.scalars(select(RzyScanRun).where(RzyScanRun.scan_time >= start, RzyScanRun.scan_time < end,
                                                  RzyScanRun.mode == 'session').order_by(RzyScanRun.scan_time)).all()
        scans = [r.scan_time for r in runs]
        written = {(sym, done): seen for sym, done, seen in s.execute(
            select(Stock.symbol, BarPostCloseWrite.completed_at, BarPostCloseWrite.observed_at)
            .join(Stock, Stock.id == BarPostCloseWrite.stock_id)
            .where(BarPostCloseWrite.timeframe == '5m', BarPostCloseWrite.completed_at > open_,
                   BarPostCloseWrite.completed_at <= close))}
        occs = s.scalars(select(RzyOccurrence).where(RzyOccurrence.first_seen_at >= start, RzyOccurrence.first_seen_at < end)).all()
        outbox = s.scalars(select(NotificationOutbox).where(NotificationOutbox.alert_type == 'little_rzy',
                                                            NotificationOutbox.created_at >= start, NotificationOutbox.created_at < end)).all()
        rebuilds = s.scalars(select(RzyRebuild).where(RzyRebuild.rebuilt_at >= start, RzyRebuild.rebuilt_at < end)).all()
        triggers = s.execute(text("""select timeframe, bar_time, checkpoint_last_bar, release_sha from rzy_rebuild_triggers
            where outcome='rebuilt' and observed_at >= :a and observed_at < :b"""), dict(a=open_, b=close)).all()

    def first_scan(t):
        i = bisect_left(scans, t)
        return scans[i] if i < len(scans) else None

    # ── bars: how many were held, and for how long after their close ──
    bar_hold, held_bars = [], 0
    for (_, done), seen in written.items():
        due = first_scan(done + SETTLE)
        if due is not None and seen > due:
            held_bars += 1
            bar_hold.append((seen - done).total_seconds() / 60)
    out['bars'] = dict(regular_5m_bars_with_post_close_write=len(written), held=held_bars,
                       held_minutes_after_close=pct(bar_hold))

    # ── events: held vs unaffected, latency from candle close ──
    mail = {}
    for r in outbox:
        mail.setdefault(r.event_id.split(':')[-1], []).append(r)
    groups = {'held': [], 'unaffected': [], 'unknown': []}
    for o in occs:
        if o.signal_time is None or o.observed_in != 'incremental' or not emailable(dict(o.first_figures or {}, event_type=o.event_type)):
            continue
        c, m = o.signal_time, STEP.get(o.timeframe)
        if m is None or not open_ < c <= close:
            continue
        seen = [written.get((o.symbol, c - timedelta(minutes=5 * k))) for k in range(m // 5)]
        due = first_scan(c + SETTLE)
        if any(x is None for x in seen) or due is None:
            kind, release = 'unknown', None
        else:
            release = max(seen)
            kind = 'held' if release > due else 'unaffected'
        acc = min((r.accepted_at for r in mail.get(o.occurrence_id, []) if r.accepted_at), default=None)
        groups[kind].append(dict(tf=o.timeframe, recorded=(o.first_seen_at - c).total_seconds() / 60,
                                 accepted=(acc - c).total_seconds() / 60 if acc else None,
                                 hold=(release - c).total_seconds() / 60 if release else None,
                                 waited=bool(release and o.first_seen_at >= release)))
    out['events'] = {k: dict(
        n=len(v), by_timeframe=dict(Counter(x['tf'] for x in v)),
        close_to_recorded_minutes=pct([x['recorded'] for x in v]),
        close_to_accepted_minutes=pct([x['accepted'] for x in v]), mailed=sum(x['accepted'] is not None for x in v),
        release_write_minutes_after_close=pct([x['hold'] for x in v]),
        recorded_after_release_write=sum(x['waited'] for x in v)) for k, v in groups.items()}
    out['events_reading'] = ('Before B a held event is recorded before its release write (consumed partial); from B on '
                             'recorded_after_release_write should equal n for held events.')

    # ── streams and rebuilds ──
    out['session_scans'] = dict(n=len(runs), stale_stream_scans=sum(r.statuses.get('stale', 0) for r in runs),
                                stream_scans=sum(r.streams for r in runs))
    in_session = [g for g in rebuilds if open_ <= g.rebuilt_at < close]
    out['rebuilds'] = dict(total=len(rebuilds), in_session=len(in_session),
                           in_session_by_timeframe=dict(Counter(g.timeframe for g in in_session)),
                           before_open_by_timeframe=dict(Counter(g.timeframe for g in rebuilds if g.rebuilt_at < open_)))
    t5 = [t for t in triggers if t.timeframe == '5m']
    out['in_session_5m_triggers'] = dict(rebuilt=len(t5), changed_bar_is_last_consumed=sum(t.bar_time == t.checkpoint_last_bar for t in t5),
                                         releases=dict(Counter(t.release_sha[:8] for t in triggers)))
    print(json.dumps(out, indent=1, default=str))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else datetime.now(NY).date().isoformat())
