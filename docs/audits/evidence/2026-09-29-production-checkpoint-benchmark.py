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
    q('paper_feedback_flags', "SELECT is_active, config->>'calibration_feedback_enabled' AS feedback, count(*) AS portfolios FROM paper_portfolios GROUP BY 1,2")
    q('flow_5d_spy', "WITH f AS (SELECT direction,stock_id,symbol,fired_date,entry_date,avg(return_5d) AS ret,avg(price_5d) AS recorded_exit FROM options_flow_alert_outcomes WHERE return_5d IS NOT NULL GROUP BY 1,2,3,4,5), matched AS (SELECT f.*,u.ts::date AS exit_day,u.close AS observed_exit, b0.close AS bench_entry,b1.close AS bench_exit FROM f LEFT JOIN LATERAL (SELECT ts,close FROM prices WHERE stock_id=f.stock_id AND timeframe='D1' AND ts::date>=f.entry_date+5 AND ts::date<=f.entry_date+5+10 ORDER BY ts LIMIT 1) u ON true LEFT JOIN stocks s ON s.symbol='SPY' LEFT JOIN prices b0 ON b0.stock_id=s.id AND b0.timeframe='D1' AND b0.ts::date=f.entry_date LEFT JOIN prices b1 ON b1.stock_id=s.id AND b1.timeframe='D1' AND b1.ts::date=u.ts::date) SELECT direction,count(*) AS clusters,count(*) FILTER(WHERE bench_entry>0 AND bench_exit IS NOT NULL AND abs(observed_exit-recorded_exit)<0.0001) AS matched_clusters,avg(ret) FILTER(WHERE bench_entry>0 AND bench_exit IS NOT NULL AND abs(observed_exit-recorded_exit)<0.0001) AS matched_return,avg(bench_exit/bench_entry-1) FILTER(WHERE bench_entry>0 AND bench_exit IS NOT NULL AND abs(observed_exit-recorded_exit)<0.0001) AS spy_return,avg(ret-(bench_exit/bench_entry-1)) FILTER(WHERE bench_entry>0 AND bench_exit IS NOT NULL AND abs(observed_exit-recorded_exit)<0.0001) AS excess_return FROM matched GROUP BY 1 ORDER BY 1")
    q('flow_10d_spy', "WITH f AS (SELECT direction,stock_id,symbol,fired_date,entry_date,avg(return_10d) AS ret,avg(price_10d) AS recorded_exit FROM options_flow_alert_outcomes WHERE return_10d IS NOT NULL GROUP BY 1,2,3,4,5), matched AS (SELECT f.*,u.ts::date AS exit_day,u.close AS observed_exit, b0.close AS bench_entry,b1.close AS bench_exit FROM f LEFT JOIN LATERAL (SELECT ts,close FROM prices WHERE stock_id=f.stock_id AND timeframe='D1' AND ts::date>=f.entry_date+10 AND ts::date<=f.entry_date+10+10 ORDER BY ts LIMIT 1) u ON true LEFT JOIN stocks s ON s.symbol='SPY' LEFT JOIN prices b0 ON b0.stock_id=s.id AND b0.timeframe='D1' AND b0.ts::date=f.entry_date LEFT JOIN prices b1 ON b1.stock_id=s.id AND b1.timeframe='D1' AND b1.ts::date=u.ts::date) SELECT direction,count(*) AS clusters,count(*) FILTER(WHERE bench_entry>0 AND bench_exit IS NOT NULL AND abs(observed_exit-recorded_exit)<0.0001) AS matched_clusters,avg(ret) FILTER(WHERE bench_entry>0 AND bench_exit IS NOT NULL AND abs(observed_exit-recorded_exit)<0.0001) AS matched_return,avg(bench_exit/bench_entry-1) FILTER(WHERE bench_entry>0 AND bench_exit IS NOT NULL AND abs(observed_exit-recorded_exit)<0.0001) AS spy_return,avg(ret-(bench_exit/bench_entry-1)) FILTER(WHERE bench_entry>0 AND bench_exit IS NOT NULL AND abs(observed_exit-recorded_exit)<0.0001) AS excess_return FROM matched GROUP BY 1 ORDER BY 1")
    c.rollback()
out['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
print('CHECKPOINT_JSON_BEGIN')
print(json.dumps(out,default=str,indent=2))
