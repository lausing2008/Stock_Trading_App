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
    q('confidence_pooled_5d', '''SELECT floor(confidence/10)*10 AS band, count(*) AS n, count(*) FILTER(WHERE is_correct_5d) AS wins, min(confidence) AS lo, max(confidence) AS hi FROM signal_outcomes WHERE confidence IS NOT NULL AND return_5d IS NOT NULL GROUP BY 1 ORDER BY 1''')
    band = "CASE WHEN confidence>=0 AND confidence<40 THEN '0-40' WHEN confidence<55 AND confidence>=40 THEN '40-55' WHEN confidence<70 AND confidence>=55 THEN '55-70' WHEN confidence<85 AND confidence>=70 THEN '70-85' WHEN confidence<101 AND confidence>=85 THEN '85+' ELSE 'outside' END"
    for cohort, predicate in [('180d', "o.signal_date >= current_date-180"), ('since_sep01', "o.signal_date >= DATE '2026-09-01'")]:
        q('confidence_'+cohort, f'''SELECT o.horizon, o.signal_direction, s.market, {band} AS band, count(*) AS n, count(*) FILTER(WHERE is_correct) AS wins, count(DISTINCT o.symbol) AS symbols, count(DISTINCT signal_date) AS dates, avg(pct_return) AS raw_return FROM signal_outcomes o JOIN stocks s ON s.id=o.stock_id WHERE is_correct IS NOT NULL AND {predicate} GROUP BY 1,2,3,4 ORDER BY 1,2,3,4''')
    q('confidence_pooled_market', f'''SELECT horizon, signal_direction, {band} AS band, count(*) AS n, count(*) FILTER(WHERE is_correct) AS wins FROM signal_outcomes WHERE is_correct IS NOT NULL AND signal_date>=current_date-180 GROUP BY 1,2,3 ORDER BY 1,2,3''')
    q('paper_feedback_flags', "SELECT style, config->>'calibration_feedback_enabled' AS feedback, count(*) AS portfolios FROM paper_portfolios GROUP BY 1,2")
    for h in (5,10):
        for grain, cols in [('direction','direction'),('side','direction, option_type, ask_side_dominant')]:
            q(f'flow_{h}d_{grain}', f'''SELECT {cols}, count(*) AS n, count(return_{h}d) AS resolved, count(*) FILTER(WHERE is_correct_{h}d) AS wins, count(*) FILTER(WHERE return_{h}d>0.005) AS up, count(*) FILTER(WHERE return_{h}d < -0.005) AS down, count(*) FILTER(WHERE return_{h}d BETWEEN -0.005 AND 0.005) AS neutral, avg(return_{h}d) AS mean_return, percentile_cont(0.5) WITHIN GROUP(ORDER BY return_{h}d) AS median_return, count(DISTINCT symbol) FILTER(WHERE return_{h}d IS NOT NULL) AS symbols, count(DISTINCT fired_date) FILTER(WHERE return_{h}d IS NOT NULL) AS dates, count(DISTINCT (symbol,fired_date)) FILTER(WHERE return_{h}d IS NOT NULL) AS symbol_dates, min(fired_date) AS first_date, max(fired_date) AS last_date FROM options_flow_alert_outcomes GROUP BY {cols} ORDER BY {cols}''')
        q(f'flow_{h}d_clusters',f'''SELECT direction,symbol,fired_date, count(*) AS contracts, avg(return_{h}d) AS ret, min(entry_date) AS entry_date, avg(CASE WHEN is_correct_{h}d THEN 1.0 ELSE 0 END) AS win_fraction FROM options_flow_alert_outcomes WHERE return_{h}d IS NOT NULL GROUP BY 1,2,3 ORDER BY 1,2,3''')
    q('dark_summary', '''SELECT count(*) AS prints, count(DISTINCT symbol) AS symbols, min(executed_at) AS earliest, max(executed_at) AS latest FROM dark_pool_prints''')
    q('dark_daily', '''WITH p AS (SELECT (executed_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York')::date AS day, count(*) AS prints, count(DISTINCT symbol) AS observed_symbols FROM dark_pool_prints WHERE executed_at>=current_date-21 GROUP BY 1), a AS (SELECT fired_date AS day, count(*) AS alerts, count(DISTINCT symbol) AS alert_symbols FROM dark_pool_alert_outcomes WHERE fired_date>=current_date-21 GROUP BY 1) SELECT coalesce(p.day,a.day) AS day, prints,observed_symbols,alerts,alert_symbols FROM p FULL JOIN a USING(day) ORDER BY day''')
    q('gex_by_type', '''SELECT alert_type,gex_corroborated,count(*) AS n,count(return_5d) AS resolved,count(DISTINCT symbol) FILTER(WHERE return_5d IS NOT NULL) AS symbols,count(DISTINCT fired_date) FILTER(WHERE return_5d IS NOT NULL) AS dates,avg(return_5d) AS avg_return FROM squeeze_alert_outcomes GROUP BY 1,2 ORDER BY 1,2''')
    q('prebreakout', '''SELECT symbol,count(*) AS n,count(return_5d) AS resolved_5d,count(*) FILTER(WHERE is_correct_5d) AS wins_5d,avg(return_5d) AS ret_5d,count(return_20d) AS resolved_20d,avg(return_20d) AS ret_20d FROM prebreakout_alert_outcomes GROUP BY 1 ORDER BY 2 DESC''')
    c.rollback()
out['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
print('CHECKPOINT_JSON_BEGIN')
print(json.dumps(out,default=str,indent=2))
