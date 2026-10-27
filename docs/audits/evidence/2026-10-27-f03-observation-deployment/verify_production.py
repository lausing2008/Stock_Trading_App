"""F03 observation deployment verification. Runs ON EC2 from the repo checkout; read-only.

Checks: (1) every backend's /app/shared and /app/src equal the checkout's (prod HEAD);
(2) the running trainer is the pre-F04 file; (3) the three F03 tables and their constraints
exist; (4) signal persistence continues after the deploy, observations are written, and no
observation failure has been logged. Prints one JSON document.
"""
import datetime, hashlib, json, pathlib, subprocess

SERVICES = ['market-data', 'signal-engine', 'ml-prediction', 'technical-analysis', 'ranking-engine',
            'strategy-engine', 'portfolio-optimizer', 'research-engine', 'decision-engine',
            'event-intelligence', 'news-intelligence', 'api-gateway']
TREE = ("import pathlib,hashlib,json,sys\nout={}\nfor name in ('src','shared'):\n"
        " p=pathlib.Path('/app')/name; out[name]={str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest()"
        " for f in sorted(p.rglob('*.py')) if '__pycache__' not in f.parts}\nprint(json.dumps(out))")


def sh(*a, timeout=60):
    return subprocess.check_output(list(a), text=True, timeout=timeout)


def local_tree(root):
    p = pathlib.Path(root)
    return {str(f.relative_to(p)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(p.rglob('*.py')) if '__pycache__' not in f.parts}


out = {'captured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
       'repo_head': sh('git', 'rev-parse', 'HEAD').strip(), 'containers': {}}
want_shared = local_tree('shared')
for svc in SERVICES:
    name = f'stockai-{svc}-1'
    got = json.loads(sh('docker', 'exec', name, 'python', '-c', TREE))
    want_src = local_tree(f'services/{svc}/src')
    c = json.loads(sh('docker', 'inspect', name))[0]
    out['containers'][svc] = {
        'started_at': c['State']['StartedAt'], 'health': c['State'].get('Health', {}).get('Status'),
        'image': c['Image'][:19],
        'shared_mismatch': sorted(k for k in set(want_shared) | set(got['shared'])
                                  if want_shared.get(k) != got['shared'].get(k)),
        'src_mismatch': sorted(k for k in set(want_src) | set(got['src'])
                               if want_src.get(k) != got['src'].get(k)),
    }

trainer = sh('docker', 'exec', 'stockai-ml-prediction-1', 'cat', '/app/src/training/trainer.py')
pre_f04 = sh('git', 'show', '28f232bd~1:services/ml-prediction/src/training/trainer.py')
out['trainer'] = {'sha256': hashlib.sha256(trainer.encode()).hexdigest(),
                  'equals_pre_f04_parent': trainer == pre_f04,
                  'f04_markers': sum(trainer.count(m) for m in ('_threshold_report_bounds', 'n_report_purge_rows'))}

started = out['containers']['signal-engine']['started_at'][:19]
probe = f"""import json
from sqlalchemy import text
from db import SessionLocal
since = '{started}'
with SessionLocal() as s:
    s.execute(text('SET TRANSACTION READ ONLY')); s.execute(text("SET LOCAL statement_timeout='20000'"))
    q = lambda sql, **p: s.execute(text(sql), p)
    out = {{}}
    out['tables'] = [r[0] for r in q("SELECT tablename FROM pg_tables WHERE tablename IN "
        "('signal_occurrences','signal_snapshots','signal_session_closes') ORDER BY 1")]
    out['indexes'] = [r[0] for r in q("SELECT indexname FROM pg_indexes WHERE tablename IN "
        "('signal_occurrences','signal_snapshots','signal_session_closes') ORDER BY 1")]
    out['signals_written_since_start'] = q("SELECT COUNT(*) FROM signals WHERE ts >= CAST(:t AS timestamp)", t=since).scalar()
    out['signals_max_ts'] = str(q("SELECT MAX(ts) FROM signals").scalar())
    out['occurrences'] = q("SELECT COUNT(*) FROM signal_occurrences").scalar()
    out['open_occurrences'] = q("SELECT COUNT(*) FROM signal_occurrences WHERE closed_at IS NULL").scalar()
    out['snapshots_by_kind'] = dict(q("SELECT kind, COUNT(*) FROM signal_snapshots GROUP BY kind").all())
    out['session_closes'] = q("SELECT COUNT(*) FROM signal_session_closes").scalar()
    s.rollback()
print(json.dumps(out, default=str))
"""
out['database'] = json.loads(sh('docker', 'exec', 'stockai-signal-engine-1', 'python', '-c', probe))
logs = subprocess.run(['docker', 'logs', '--since', started, 'stockai-signal-engine-1'],
                      capture_output=True, text=True, timeout=60)
text_all = logs.stdout + logs.stderr
out['signal_engine_log'] = {'occurrence_observe_failed': text_all.count('signal.occurrence_observe_failed'),
                            'refresh_skip': text_all.count('signals.refresh.skip'),
                            'tracebacks': text_all.count('Traceback (most recent call last)')}
print(json.dumps(out, indent=2))
