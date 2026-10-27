"""F03 observation: one daily capture, run ON EC2 from the repo checkout after the US close.
Read-only. Writes f03-observation/<UTC date>.json beside this file's run directory.

Captured per run, so nothing depends on container logs surviving rotation (5 x 20 MB):
  * observation_report() since the deployment session (runs, later runs, generations,
    content changes per session/market/horizon; close reasons; completed closes; table bytes);
  * observe failures and refresh skips logged since the previous capture's timestamp;
  * signals persisted per session (the persistence denominator the failures are read against);
  * PostgreSQL deadlocks since the previous capture, flagged if any involve an F03 table or the
    advisory lock (a pre-existing signals-upsert vs alert-delivery deadlock ran 2-15/day before).
Session completeness is decided at analysis time, not here: the deployment session (US
2026-10-27, and HK 2026-10-28 if the first HK generation that day began mid-session) is
excluded, as is any session in which the signal-engine container was recreated.
"""
import datetime, json, pathlib, subprocess, sys

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'f03-observation')
OUT.mkdir(parents=True, exist_ok=True)
now = datetime.datetime.now(datetime.timezone.utc)
prev = sorted(OUT.glob('*.json'))
since = json.loads(prev[-1].read_text())['captured_at'] if prev else '2026-10-27T14:00:00'

probe = """import json, datetime
from sqlalchemy import text
from db import SessionLocal
from src.api.occurrence_observer import observation_report
with SessionLocal() as s:
    s.execute(text('SET TRANSACTION READ ONLY')); s.execute(text("SET LOCAL statement_timeout='60000'"))
    rep = observation_report(s, since=datetime.date(2026, 10, 27))
    rep['signals_per_utc_day'] = [dict(r) for r in s.execute(text(
        "SELECT CAST(ts AS date) AS day, COUNT(*) AS n FROM signals "
        "WHERE ts >= '2026-10-27' GROUP BY 1 ORDER BY 1")).mappings()]
    s.rollback()
print(json.dumps(rep, default=str))
"""
report = json.loads(subprocess.check_output(
    ['docker', 'exec', 'stockai-signal-engine-1', 'python', '-c', probe], text=True, timeout=120))
logs = subprocess.run(['docker', 'logs', '--since', since[:19], 'stockai-signal-engine-1'],
                      capture_output=True, text=True, timeout=120)
txt = logs.stdout + logs.stderr
started = json.loads(subprocess.check_output(['docker', 'inspect', 'stockai-signal-engine-1'], text=True))[0]['State']['StartedAt']
pg = subprocess.run(['docker', 'logs', '--since', since[:19], 'stockai-postgres-1'],
                    capture_output=True, text=True, timeout=120)
pg_lines = (pg.stdout + pg.stderr).splitlines()
deadlocks = []
for i, line in enumerate(pg_lines):
    if 'ERROR:  deadlock detected' in line:
        ctx = '\n'.join(pg_lines[i:i + 40])
        deadlocks.append({'at': line[:23],
                          'involves_f03': any(t in ctx for t in ('signal_occurrences', 'signal_snapshots',
                                                                 'signal_session_closes', 'pg_advisory_xact_lock')),
                          'involves_alert_delivery_update': 'SET alert_delivery_state' in ctx})
doc = {'captured_at': now.isoformat(timespec='seconds'), 'log_window_since': since,
       'signal_engine_started_at': started,
       'log_counts': {'occurrence_observe_failed': txt.count('signal.occurrence_observe_failed'),
                      'refresh_skip': txt.count('signals.refresh.skip'),
                      'postgres_deadlocks': len(deadlocks)},
       'deadlocks': deadlocks,
       'failure_samples': [l[:400] for l in txt.splitlines() if 'occurrence_observe_failed' in l][:5],
       'report': report}
path = OUT / f"{now.date().isoformat()}.json"
path.write_text(json.dumps(doc, indent=2, default=str))
print(path)
