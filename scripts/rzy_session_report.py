"""Read-only session evidence: intraday bar revisions and the Little RZY research-alert path.

Run inside the market-data container after a session, then copy OUT to durable storage:

    docker cp scripts/rzy_session_report.py stockai-market-data-1:/tmp/
    docker exec -e RZY_RELEASE=<image label> -w /app stockai-market-data-1 python /tmp/rzy_session_report.py 2026-10-21 /tmp/rzy-session
    docker cp stockai-market-data-1:/tmp/rzy-session /home/ec2-user/rzy-session-evidence/2026-10-21

Writes report.json, manifest.json (release, config hash, code sha256, row counts) and raw CSVs:
bar_revisions.csv, bar_provenance.csv, rzy_alert_path.csv, scan_runs.csv, outbox.csv.
Nothing is written to the database (READ ONLY transaction).

Reading rules built into the report:
- Completed-bar revisions and forming-bar updates are separate sections; only completed-bar
  revisions bear on the restatement question.
- previous_provider_unknown is its own category and counts toward NEITHER hypothesis
  (cross-provider replacement vs same-provider correction).
- Latency covers every emailable 5m event, mailed or not, with median / p90 / p95, alongside the
  counts of expired, suppressed, too-late and paused cases; delivered-only figures would hide
  missed alerts. Provider acceptance is not confirmed inbox delivery.
"""
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path[:0] = [os.environ.get('RZY_EVAL_ROOT', '/app'), '/app/shared']
from sqlalchemy import select, text  # noqa: E402
from db import SessionLocal, NotificationOutbox, Stock  # noqa: E402
from src.services.bar_provenance import BarProvenance, BarRevision  # noqa: E402
from src.services.rzy_models import RzyEvent, RzyStream, RzyStreamGeneration, RzyScanRun, RzyOccurrence, RzyRebuild  # noqa: E402
from src.services.rzy_workflow import emailable, current_config, MINUTES  # noqa: E402
from src.services.little_rzy import VERSION  # noqa: E402

STEP = {'5m': 5, '15m': 15, '1h': 60}


def pct(values):
    v = sorted(x for x in values if x is not None)
    if not v:
        return dict(n=0)
    q = lambda p: round(v[min(len(v) - 1, int(len(v) * p))], 2)
    return dict(n=len(v), median=q(.5), p90=q(.9), p95=q(.95), max=round(v[-1], 2))


def age_bucket(m):
    return '<15m' if m < 15 else '<60m' if m < 60 else '<6h' if m < 360 else '>=6h'


def revision_section(revs):
    return dict(
        total=len(revs),
        by_classification=dict(Counter(r.classification.replace('_forming_bar', '') for r in revs)),
        by_timeframe=dict(Counter(r.timeframe for r in revs)),
        provider_pairs=Counter(f'{r.previous_provider or "unknown"}->{r.provider}' for r in revs).most_common(12),
        bar_age_minutes=pct([r.bar_age_minutes for r in revs]),
        by_classification_and_age=dict(Counter(f"{r.classification.replace('_forming_bar', '')}:{age_bucket(r.bar_age_minutes)}" for r in revs)),
        max_rel_change_pct=pct([r.max_rel_change * 100 for r in revs]),
        changed_fields=Counter(','.join(r.changed_fields) for r in revs).most_common(8),
        symbols=len({r.symbol for r in revs}), top_symbols=Counter(r.symbol for r in revs).most_common(10))


def main(day, out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    start = datetime.fromisoformat(day + 'T08:00:00'); end = start + timedelta(hours=16)   # 04:00-24:00 ET (EDT)
    report = dict(session_day=day, window_utc=[start.isoformat(), end.isoformat()], strategy_version=VERSION,
                  note='Provider acceptance is not confirmed inbox delivery. previous_provider_unknown counts toward neither hypothesis.')
    with SessionLocal() as s:
        s.execute(text('SET TRANSACTION READ ONLY'))
        revs = s.scalars(select(BarRevision).where(BarRevision.fetched_at >= start, BarRevision.fetched_at < end).order_by(BarRevision.fetched_at)).all()
        completed = [r for r in revs if not r.classification.endswith('_forming_bar')]
        forming = [r for r in revs if r.classification.endswith('_forming_bar')]
        tally = Counter(r.classification for r in completed)
        report['completed_bar_revisions'] = revision_section(completed)
        report['completed_bar_hypothesis_tally'] = dict(
            cross_provider=tally.get('cross_provider', 0), same_provider=tally.get('same_provider', 0),
            previous_provider_unknown_not_counted=tally.get('previous_provider_unknown', 0),
            reading='cross_provider supports vendor replacement; same_provider supports corrections within one vendor; unknown supports neither.')
        report['forming_bar_updates'] = revision_section(forming)
        prov = s.scalars(select(BarProvenance).where(BarProvenance.ts >= start, BarProvenance.ts < end)).all()
        report['bar_provenance'] = dict(
            bars=len(prov), provider_of_last_write=dict(Counter(p.provider for p in prov)),
            off_grid_by_provider=dict(Counter(p.provider for p in prov if p.off_grid)),
            bars_with_completed_revisions=len({(r.stock_id, r.timeframe, r.ts) for r in completed}),
            volume_only_revisions=sum(p.volume_only_revisions or 0 for p in prov),
            provider_switches_same_values=sum(p.provider_switches_same_values or 0 for p in prov),
            close_to_first_ingest_minutes_5m=pct([(p.first_written_at - (p.ts + timedelta(minutes=5))).total_seconds() / 60
                                                  for p in prov if p.timeframe == '5m' and not p.off_grid]))
        runs = s.scalars(select(RzyScanRun).where(RzyScanRun.scan_time >= start, RzyScanRun.scan_time < end).order_by(RzyScanRun.scan_time)).all()
        session_runs = [r for r in runs if r.mode == 'session']
        gaps = [(b.scan_time - a.scan_time).total_seconds() / 60 for a, b in zip(session_runs, session_runs[1:])]
        report['scans'] = dict(
            runs=len(runs), session_runs=len(session_runs), duration_seconds=pct([r.duration_seconds for r in session_runs]),
            minutes_between_session_scans=pct(gaps),
            stream_status_totals=dict(sum((Counter(r.statuses) for r in session_runs), Counter())),
            paused_stream_scans=sum(r.statuses.get('restatement_pending', 0) for r in session_runs),
            too_late_to_mail=sum(r.too_late_to_mail for r in session_runs), new_emailable_events=sum(r.new_emailable_events for r in session_runs),
            queued=sum(r.queued for r in session_runs))
        # F08: the append-only rebuild log survives generation pruning; archives do not.
        log_rows = s.scalars(select(RzyRebuild).where(RzyRebuild.rebuilt_at >= start, RzyRebuild.rebuilt_at < end)).all()
        if log_rows:
            report['rzy_rebuilds'] = dict(source='little_rzy_rebuilds (append-only)', total=len(log_rows),
                by_reason=dict(Counter(g.reason for g in log_rows)), by_timeframe=dict(Counter(g.timeframe for g in log_rows)))
        else:
            gens = s.scalars(select(RzyStreamGeneration).where(RzyStreamGeneration.archived_at >= start, RzyStreamGeneration.archived_at < end)).all()
            report['rzy_rebuilds'] = dict(source='retained generation archives (LOWER BOUND: older ones are pruned)', total=len(gens),
                by_reason=dict(Counter(g.reason for g in gens)), by_timeframe=dict(Counter(g.stream_key.split(':')[1] for g in gens)))
        report['rzy_streams_paused_at_report_time'] = sum(1 for r in s.scalars(select(RzyStream)).all() if r.checkpoint.get('restatement_pending'))
        occs = s.scalars(select(RzyOccurrence).where(RzyOccurrence.first_seen_at >= start, RzyOccurrence.first_seen_at < end)).all()
        outbox = s.scalars(select(NotificationOutbox).where(NotificationOutbox.alert_type.in_(('little_rzy', 'short_term_digest')),
                                                            NotificationOutbox.created_at >= start, NotificationOutbox.created_at < end)).all()
        mail = {}
        for r in outbox:
            if r.alert_type == 'little_rzy':
                mail.setdefault(r.event_id.split(':')[-1], []).append(r)
        path_rows = []
        if occs:
            # F08: first observation comes from the immutable occurrence record. Replays found
            # while rebuilding/bootstrapping are counted separately, never dropped by an age guess.
            report['occurrence_source'] = 'little_rzy_occurrences (first observation, survives pruning)'
            report['occurrences_by_observation'] = dict(Counter(o.observed_in for o in occs))
            for o in occs:
                p = dict(o.first_figures or {}, event_type=o.event_type)
                if o.signal_time is None or not emailable(p) or o.observed_in != 'incremental':
                    continue
                path_rows.append((o.occurrence_id, o.first_seen_at, o.signal_time, o.symbol, o.timeframe, o.event_type))
        else:
            report['occurrence_source'] = ('earliest SURVIVING event version (pre-occurrence-record data): pruned versions can '
                                           'move first observation later; events recorded >2 h after their signal are excluded as replay')
            events = s.scalars(select(RzyEvent).where(RzyEvent.created_at >= start, RzyEvent.created_at < end)).all()
            first = {}
            for e in events:
                occ = e.payload.get('occurrence_id', e.event_id)
                if occ not in first or e.created_at < first[occ][0]:
                    first[occ] = (e.created_at, e.payload)
            for occ, (recorded, p) in first.items():
                if not p.get('signal_time') or not emailable(p) or recorded - datetime.fromisoformat(p['signal_time']).replace(tzinfo=None) > timedelta(hours=2):
                    continue
                path_rows.append((occ, recorded, datetime.fromisoformat(p['signal_time']).replace(tzinfo=None), p['symbol'], p['timeframe'], p['event_type']))
        rows_out = []
        for occ, recorded, close, symbol, timeframe, event_type in path_rows:
            rows = mail.get(occ, [])
            q = min((r.created_at for r in rows), default=None); a = min((r.accepted_at for r in rows if r.accepted_at), default=None)
            late = recorded - close > timedelta(minutes=MINUTES.get(timeframe, 5) + 3)
            rows_out.append(dict(occurrence_id=occ, symbol=symbol, timeframe=timeframe, event_type=event_type,
                candle_close=close.isoformat(), recorded=recorded.isoformat(), queued=q.isoformat() if q else None, accepted=a.isoformat() if a else None,
                close_to_recorded_min=round((recorded - close).total_seconds() / 60, 2),
                close_to_accepted_min=round((a - close).total_seconds() / 60, 2) if a else None,
                outcome=('accepted' if a else ','.join(sorted({r.state for r in rows})) if rows else 'recorded_too_late_not_queued' if late else 'not_queued')))
        path_rows = rows_out
        def stage(tf):
            rows = [r for r in path_rows if r['timeframe'] == tf]
            return dict(signals=len(rows), close_to_recorded_minutes=pct([r['close_to_recorded_min'] for r in rows]),
                        close_to_provider_accepted_minutes=pct([r['close_to_accepted_min'] for r in rows]),
                        outcomes=dict(Counter(r['outcome'] for r in rows)))
        report['research_alert_path'] = {tf: stage(tf) for tf in ('5m', '15m', '1h')}
        report['outbox_states'] = dict(Counter(f'{r.alert_type}:{r.state}' for r in outbox))
        digest = [r for r in outbox if r.alert_type == 'short_term_digest']
        report['digest'] = [dict(created=r.created_at.isoformat(), accepted=r.accepted_at.isoformat() if r.accepted_at else None, state=r.state) for r in digest]
        symbols = {st.id: st.symbol for st in s.scalars(select(Stock)).all()}
        # No explicit rollback: it would expire the loaded rows used below. Closing the session
        # discards the READ ONLY transaction and keeps the loaded values on the detached objects.
    def write(name, rows, fields):
        with open(out / name, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    write('bar_revisions.csv', [dict(symbol=r.symbol, timeframe=r.timeframe, ts=r.ts.isoformat(), fetched_at=r.fetched_at.isoformat(), bar_age_minutes=round(r.bar_age_minutes, 2),
          previous_provider=r.previous_provider, provider=r.provider, classification=r.classification, changed_fields=','.join(r.changed_fields),
          max_rel_change=r.max_rel_change, before=json.dumps(r.before), after=json.dumps(r.after)) for r in revs],
          ['symbol', 'timeframe', 'ts', 'fetched_at', 'bar_age_minutes', 'previous_provider', 'provider', 'classification', 'changed_fields', 'max_rel_change', 'before', 'after'])
    write('bar_provenance.csv', [dict(symbol=symbols.get(p.stock_id), timeframe=p.timeframe, ts=p.ts.isoformat(), provider=p.provider, first_written_at=p.first_written_at.isoformat(),
          last_written_at=p.last_written_at.isoformat(), ohlc_revisions=p.ohlc_revisions, volume_only_revisions=p.volume_only_revisions,
          provider_switches_same_values=p.provider_switches_same_values, off_grid=p.off_grid) for p in prov],
          ['symbol', 'timeframe', 'ts', 'provider', 'first_written_at', 'last_written_at', 'ohlc_revisions', 'volume_only_revisions', 'provider_switches_same_values', 'off_grid'])
    write('rzy_alert_path.csv', path_rows, list(path_rows[0]) if path_rows else ['occurrence_id'])
    write('scan_runs.csv', [dict(scan_time=r.scan_time.isoformat(), mode=r.mode, duration_seconds=r.duration_seconds, streams=r.streams, statuses=json.dumps(r.statuses),
          new_emailable_events=r.new_emailable_events, too_late_to_mail=r.too_late_to_mail, queued=r.queued) for r in runs],
          ['scan_time', 'mode', 'duration_seconds', 'streams', 'statuses', 'new_emailable_events', 'too_late_to_mail', 'queued'])
    write('outbox.csv', [dict(alert_type=r.alert_type, state=r.state, created_at=r.created_at.isoformat(), accepted_at=r.accepted_at.isoformat() if r.accepted_at else None,
          expires_at=r.expires_at.isoformat() if r.expires_at else None, subject=r.subject) for r in outbox],
          ['alert_type', 'state', 'created_at', 'accepted_at', 'expires_at', 'subject'])
    (out / 'report.json').write_text(json.dumps(report, indent=1, default=str))
    root = Path(os.environ.get('RZY_EVAL_ROOT', '/app'))
    code = {f: hashlib.sha256((root / 'src/services' / f).read_bytes()).hexdigest() for f in
            ('bar_provenance.py', 'ingestion.py', 'little_rzy.py', 'rzy_data.py', 'rzy_workflow.py')}
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), session_day=day, release=os.environ.get('RZY_RELEASE', 'unknown'),
                    config_hash=current_config().hash, strategy_version=VERSION, code_sha256=code,
                    files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.glob('*')) if p.name != 'manifest.json'},
                    rows=dict(bar_revisions=len(revs), bar_provenance=len(prov), scan_runs=len(runs), alert_path=len(path_rows), outbox=len(outbox)))
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1))
    print(json.dumps(dict(report_dir=str(out), rows=manifest['rows'])))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else datetime.utcnow().date().isoformat(),
         sys.argv[2] if len(sys.argv) > 2 else '/tmp/rzy-session')
