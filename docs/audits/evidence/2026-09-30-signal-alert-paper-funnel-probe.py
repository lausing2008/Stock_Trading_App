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
    queries = {'portfolios': "SELECT id,is_active,initial_capital,current_cash, config->>'trading_style' AS style, config->>'market' AS market, config->>'max_positions' AS max_positions,config->>'min_entry_score' AS min_score, config->>'min_confidence' AS min_confidence,config->>'min_rr_ratio' AS min_rr,config->>'decision_engine_mode' AS de_mode FROM paper_portfolios ORDER BY id", 'scan_schema': "SELECT column_name,data_type FROM information_schema.columns WHERE table_name='paper_entry_scan_logs'", 'scans': "SELECT portfolio_id,portfolio_gate,count(*) AS scans,sum(candidates_seen) AS candidate_checks,min(scanned_at) AS first,max(scanned_at) AS last FROM paper_entry_scan_logs WHERE scanned_at>='2026-09-01' AND scanned_at<'2026-10-01' GROUP BY 1,2 ORDER BY 1,3 DESC", 'skip_reasons': "SELECT l.portfolio_id,j.key AS reason,sum(j.value::bigint) AS checks FROM paper_entry_scan_logs l CROSS JOIN LATERAL jsonb_each_text(l.skip_tally::jsonb) j WHERE scanned_at>='2026-09-01' AND scanned_at<'2026-10-01' GROUP BY 1,2 ORDER BY 1,3 DESC", 'scans_daily': "SELECT scanned_at::date AS utc_day,count(*) AS scans,count(*) FILTER(WHERE portfolio_gate IS NOT NULL) AS gated FROM paper_entry_scan_logs WHERE scanned_at>='2026-09-01' AND scanned_at<'2026-10-01' GROUP BY 1 ORDER BY 1", 'entries': "SELECT portfolio_id,trading_style,count(*) AS entries,count(DISTINCT entry_date) AS entry_days,min(entry_time) AS first,max(entry_time) AS last,count(*) FILTER(WHERE stage='open') AS still_open,count(*) FILTER(WHERE stage='closed') AS closed,count(*) FILTER(WHERE stage='closed' AND pnl>0) AS wins,sum(pnl) FILTER(WHERE stage='closed') AS closed_pnl FROM paper_trades WHERE entry_date>='2026-09-01' AND entry_date<'2026-10-01' GROUP BY 1,2 ORDER BY 1", 'open_positions': "SELECT portfolio_id,count(*) AS open_positions,sum(shares*current_price) AS marked_value,min(entry_date) AS oldest_entry FROM paper_trades WHERE stage='open' GROUP BY 1 ORDER BY 1", 'subscriptions': "SELECT horizon,alert_mode,require_consensus,count(*) AS subscriptions,count(*) FILTER(WHERE email IS NULL OR email='') AS missing_email,count(*) FILTER(WHERE last_sent_at>='2026-09-01' AND last_sent_at<'2026-10-01') AS latest_send_in_september,min(last_sent_at) AS oldest_last_send,max(last_sent_at) AS newest_last_send FROM signal_alerts GROUP BY 1,2,3 ORDER BY 1", 'signal_counts': "SELECT horizon,signal,count(*) AS rows,count(DISTINCT stock_id) AS symbols,min(ts) AS first,max(ts) AS last FROM signals WHERE ts>='2026-09-01' AND ts<'2026-10-01' GROUP BY 1,2 ORDER BY 1,2", 'preferences': 'SELECT alert_type,enabled,count(*) AS n FROM alert_preferences GROUP BY 1,2'}
    for name,sql in queries.items():
        q(name,sql)
    c.rollback()
out['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
print('CHECKPOINT_JSON_BEGIN')
print(json.dumps(out,default=str,indent=2))
