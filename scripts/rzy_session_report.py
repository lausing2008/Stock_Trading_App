"""Read-only session evidence: intraday bar revisions and the Little RZY delivery path.

Run inside the market-data container after a session (no writes; READ ONLY transaction):

    docker cp scripts/rzy_session_report.py stockai-market-data-1:/tmp/
    docker exec -w /app stockai-market-data-1 python /tmp/rzy_session_report.py 2026-10-21 > report.json

Bar revisions (from bar_provenance / bar_revisions, recorded since the trace was deployed):
classification counts (cross_provider vs same_provider vs previous_provider_unknown, plus
_forming_bar), provider pairs, bar age at revision, magnitude, changed fields, off-grid rows by
provider, and the provider mix that wrote the session's bars.

Delivery path for 5m research alerts, per stage in minutes: candle close -> first ingested
(provenance) -> event recorded (little_rzy_events) -> queued (outbox created_at) -> provider
accepted (outbox accepted_at). Provider acceptance is NOT confirmed inbox delivery. Also counts
outbox states, rebuilds by reason, and streams paused on restatements.
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path[:0] = ['/app', '/app/shared']
from sqlalchemy import select, text  # noqa: E402
from db import SessionLocal, NotificationOutbox  # noqa: E402
from src.services.bar_provenance import BarProvenance, BarRevision  # noqa: E402
from src.services.rzy_models import RzyEvent, RzyStream, RzyStreamGeneration  # noqa: E402


def pct(values, ps=(10, 50, 90, 99)):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    return {f'p{p}': round(v[min(len(v) - 1, int(len(v) * p / 100))], 2) for p in ps} | {'n': len(v), 'max': round(v[-1], 2)}


def main(day):
    start = datetime.fromisoformat(day + 'T13:00:00'); end = start + timedelta(hours=8)
    out = dict(session_utc=[start.isoformat(), end.isoformat()], note='Provider acceptance is not confirmed inbox delivery.')
    with SessionLocal() as s:
        s.execute(text('SET TRANSACTION READ ONLY'))
        revs = s.scalars(select(BarRevision).where(BarRevision.fetched_at >= start, BarRevision.fetched_at < end)).all()
        out['bar_revisions'] = dict(
            total=len(revs),
            by_classification=Counter(r.classification for r in revs),
            by_timeframe=Counter(r.timeframe for r in revs),
            provider_pairs=Counter(f'{r.previous_provider}->{r.provider}' for r in revs).most_common(10),
            completed_bar_age_minutes=pct([r.bar_age_minutes for r in revs if not r.classification.endswith('_forming_bar')]),
            max_rel_change_pct=pct([r.max_rel_change * 100 for r in revs], (50, 90, 99)),
            changed_fields=Counter(','.join(r.changed_fields) for r in revs).most_common(6),
            symbols=len({r.symbol for r in revs}),
            top_symbols=Counter(r.symbol for r in revs).most_common(10),
            by_classification_and_age=Counter(f"{r.classification}:{'<15m' if r.bar_age_minutes < 15 else '<60m' if r.bar_age_minutes < 60 else '<6h' if r.bar_age_minutes < 360 else '>6h'}" for r in revs),
        )
        prov = s.scalars(select(BarProvenance).where(BarProvenance.ts >= start, BarProvenance.ts < end)).all()
        step = {'5m': 5, '15m': 15, '1h': 60}
        out['bar_provenance'] = dict(
            bars=len(prov), provider_of_last_write=Counter(p.provider for p in prov),
            off_grid_by_provider=Counter(p.provider for p in prov if p.off_grid),
            bars_with_ohlc_revisions=sum(1 for p in prov if p.ohlc_revisions),
            volume_only_revisions=sum(p.volume_only_revisions or 0 for p in prov),
            provider_switches_same_values=sum(p.provider_switches_same_values or 0 for p in prov),
            close_to_first_ingest_minutes=pct([(p.first_written_at - (p.ts + timedelta(minutes=step[p.timeframe]))).total_seconds() / 60
                                               for p in prov if p.timeframe == '5m' and not p.off_grid]),
        )
        gens = s.scalars(select(RzyStreamGeneration).where(RzyStreamGeneration.archived_at >= start, RzyStreamGeneration.archived_at < end)).all()
        out['rzy_rebuilds'] = dict(total=len(gens), by_reason=Counter(g.reason for g in gens),
                                   by_timeframe=Counter(g.stream_key.split(':')[1] for g in gens))
        streams = s.scalars(select(RzyStream)).all()
        out['rzy_streams_paused_now'] = sum(1 for r in streams if r.checkpoint.get('restatement_pending'))
        events = s.scalars(select(RzyEvent).where(RzyEvent.created_at >= start, RzyEvent.created_at < end)).all()
        first_seen = {}
        for e in events:
            occ = e.payload.get('occurrence_id', e.event_id)
            if occ not in first_seen or e.created_at < first_seen[occ][0]:
                first_seen[occ] = (e.created_at, e.payload)
        rows = s.scalars(select(NotificationOutbox).where(NotificationOutbox.alert_type == 'little_rzy',
                                                          NotificationOutbox.created_at >= start, NotificationOutbox.created_at < end)).all()
        stages = defaultdict(list)
        for r in rows:
            occ = r.event_id.split(':')[-1]
            if occ not in first_seen:
                continue
            recorded, payload = first_seen[occ]
            close = datetime.fromisoformat(payload['signal_time']).replace(tzinfo=None) if payload.get('signal_time') else None
            if close is None:
                continue
            tf = payload.get('timeframe')
            stages[f'{tf}:close_to_recorded'].append((recorded - close).total_seconds() / 60)
            stages[f'{tf}:recorded_to_queued'].append((r.created_at - recorded).total_seconds() / 60)
            if r.accepted_at:
                stages[f'{tf}:queued_to_accepted'].append((r.accepted_at - r.created_at).total_seconds() / 60)
                stages[f'{tf}:close_to_accepted'].append((r.accepted_at - close).total_seconds() / 60)
        all_5m = [(created - datetime.fromisoformat(p['signal_time']).replace(tzinfo=None)).total_seconds() / 60
                  for created, p in first_seen.values() if p.get('timeframe') == '5m' and p.get('signal_time')]
        out['rzy_delivery'] = dict(
            outbox_states=Counter(r.state for r in rows),
            stages_minutes={k: pct(v) for k, v in sorted(stages.items())},
            all_5m_events_close_to_recorded=pct([x for x in all_5m if x < 120]),
            five_minute_events_recorded_too_late_to_mail=sum(1 for x in all_5m if 8 < x < 120),
        )
        s.rollback()
    print(json.dumps(out, indent=1, default=str))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else datetime.utcnow().date().isoformat())
