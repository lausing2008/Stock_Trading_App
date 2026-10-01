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
    queries = {'portfolio_activity': "SELECT p.id,p.is_active,p.config->>'trading_style' AS style,p.config->>'market' AS market,count(t.id) AS total_trades,max(t.entry_time) AS last_entry,count(t.id) FILTER(WHERE t.entry_date>='2026-09-09') AS entries_after_sep8,count(t.id) FILTER(WHERE t.entry_date>='2026-08-10') AS entries_after_aug9,count(t.id) FILTER(WHERE t.stage='open') AS open_positions FROM paper_portfolios p LEFT JOIN paper_trades t ON t.portfolio_id=p.id GROUP BY p.id ORDER BY p.id", 'recent_entries': "SELECT portfolio_id,trading_style,entry_date,entry_time,stage FROM paper_trades WHERE entry_date>='2026-09-09' ORDER BY entry_time DESC LIMIT 40", 'recent_gates': "SELECT portfolio_id,portfolio_gate,portfolio_gate_reason,count(*) AS scans,max(scanned_at) AS latest FROM paper_entry_scan_logs WHERE scanned_at>=now()-interval '2 days' GROUP BY 1,2,3 ORDER BY 1,4 DESC", 'recent_skips': "SELECT portfolio_id,j.key AS reason,sum(j.value::bigint) AS candidate_checks FROM paper_entry_scan_logs l CROSS JOIN LATERAL jsonb_each_text(CASE WHEN jsonb_typeof(skip_tally::jsonb)='object' THEN skip_tally::jsonb ELSE '{}'::jsonb END) j WHERE scanned_at>=now()-interval '2 days' GROUP BY 1,2 ORDER BY 1,3 DESC", 'scan_coverage': 'SELECT portfolio_id,min(scanned_at) AS first_retained,max(scanned_at) AS latest,count(*) AS rows FROM paper_entry_scan_logs GROUP BY 1 ORDER BY 1'}
    for name,sql in queries.items():
        q(name,sql)
    c.rollback()
out['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
from common.redis_client import get_redis
r=get_redis()
out['recovery_markers']={str(i):{'value':r.get('paper:consec_loss_recovery:'+str(i)), 'ttl':r.ttl('paper:consec_loss_recovery:'+str(i))} for i in [2,5]}
print('CHECKPOINT_JSON_BEGIN')
print(json.dumps(out,default=str,indent=2))
