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
    q('paper_feedback_flags', "SELECT trading_style, is_active, config->>'calibration_feedback_enabled' AS feedback, count(*) AS portfolios FROM paper_portfolios GROUP BY 1,2,3")
    q('benchmark_coverage', "SELECT s.symbol,count(p.id) AS bars,min(p.ts) AS first,max(p.ts) AS last FROM stocks s LEFT JOIN prices p ON p.stock_id=s.id AND p.timeframe='D1' WHERE s.symbol IN ('SPY','QQQ','IWM') GROUP BY 1")
    q('gex_signed', "SELECT alert_type,gex_corroborated,count(return_5d) AS resolved,count(*) FILTER(WHERE is_correct_5d) AS wins,avg(CASE WHEN alert_type='gamma_unwind_puts' THEN -return_5d ELSE return_5d END) AS signed_return FROM squeeze_alert_outcomes WHERE gex_corroborated IS NOT NULL GROUP BY 1,2 ORDER BY 1,2")
    q('flow_resolved_dates', "SELECT fired_date,direction,count(return_5d) AS n5,count(return_10d) AS n10 FROM options_flow_alert_outcomes GROUP BY 1,2 ORDER BY 1,2")
    c.rollback()
out['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
print('CHECKPOINT_JSON_BEGIN')
print(json.dumps(out,default=str,indent=2))
