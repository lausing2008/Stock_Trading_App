"""Run via stdin in market-data. SELECT-only repeatable-read snapshot; no jobs/API calls."""
import sys, json, hashlib, pathlib
from datetime import datetime, timezone
sys.path.insert(0, '/app/shared')
sys.path.insert(0, '/app')
from sqlalchemy import text
from db.session import engine
out = {'started_at_utc': datetime.now(timezone.utc).isoformat(), 'queries': {}}
for name, path in [('scheduler', '/app/src/services/scheduler.py'), ('db_models', '/app/shared/db/models.py')]:
    p = pathlib.Path(path)
    out[name + '_sha256'] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
with engine.connect() as c:
    c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    c.execute(text("SET LOCAL statement_timeout = '25s'"))
    c.execute(text("SET LOCAL lock_timeout = '2s'"))
    def q(name, sql):
        with c.begin_nested() as sp:
            try:
                rows = [dict(r) for r in c.execute(text(sql)).mappings()]
                out['queries'][name] = {'sql': sql, 'rows': rows}
            except Exception as exc:
                sp.rollback()
                out['queries'][name] = {'sql': sql, 'error_type': type(exc).__name__, 'error': str(exc).split('[SQL:')[0]}
    q('snapshot', "SELECT now() AS snapshot_at, current_date AS server_date, current_setting('transaction_read_only') AS read_only, current_setting('transaction_isolation') AS isolation")
    queries = {'skip_reasons': "SELECT l.portfolio_id,j.key AS reason,sum(j.value::bigint) AS checks FROM paper_entry_scan_logs l CROSS JOIN LATERAL jsonb_each_text(CASE WHEN jsonb_typeof(l.skip_tally::jsonb)='object' THEN l.skip_tally::jsonb ELSE '{}'::jsonb END) j WHERE scanned_at>='2026-09-01' AND scanned_at<'2026-10-01' GROUP BY 1,2 ORDER BY 1,3 DESC", 'gate_details': "SELECT portfolio_id,portfolio_gate,portfolio_gate_reason,count(*) AS occurrences FROM paper_entry_scan_logs WHERE scanned_at>='2026-09-01' AND scanned_at<'2026-10-01' AND portfolio_gate IS NOT NULL GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 35", 'last_2_days_skips': "SELECT j.key AS reason,sum(j.value::bigint) AS checks FROM paper_entry_scan_logs l CROSS JOIN LATERAL jsonb_each_text(CASE WHEN jsonb_typeof(l.skip_tally::jsonb)='object' THEN l.skip_tally::jsonb ELSE '{}'::jsonb END) j WHERE scanned_at>='2026-09-29' AND scanned_at<'2026-10-01' GROUP BY 1 ORDER BY 2 DESC"}
    for name,sql in queries.items():
        q(name,sql)
    c.rollback()
out['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
print('CHECKPOINT_JSON_BEGIN')
print(json.dumps(out,default=str,indent=2))
