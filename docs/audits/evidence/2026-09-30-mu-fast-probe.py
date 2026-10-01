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
    queries = {'mu_earnings': "SELECT e.report_date,e.eps_actual,e.eps_estimate,e.revenue_actual,e.fetched_at,e.impact_generated_at,e.impact_sent_at,e.impact_direction FROM earnings_events e JOIN stocks s ON s.id=e.stock_id WHERE s.symbol='MU' AND report_date>='2026-09-01' ORDER BY report_date", 'mu_news': "SELECT source,headline,url,published_at,ingested_at,category FROM realtime_news_items WHERE (symbol='MU' OR headline ILIKE '%micron%') AND published_at>='2026-09-30' ORDER BY published_at DESC LIMIT 20", 'mu_subs': "SELECT 'earnings' AS kind,count(*) AS n FROM earnings_alert_subscriptions WHERE symbol='MU' UNION ALL SELECT 'active_price',count(*) FROM price_alerts WHERE symbol='MU' AND triggered IS FALSE UNION ALL SELECT 'signal',count(*) FROM signal_alerts WHERE symbol='MU'", 'sources': "SELECT source,count(*) AS n,max(published_at) AS latest_publication,max(ingested_at) AS latest_ingestion FROM realtime_news_items WHERE ingested_at>='2026-09-30' GROUP BY 1"}
    for name,sql in queries.items():
        q(name,sql)
    c.rollback()
out['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
from common.redis_client import get_redis
r=get_redis()
out['flags']={name:r.get('stockai:admin:feature:'+name) for name in ['earnings_llm_impact_enabled','earnings_llm_forecast_enabled']}
print('CHECKPOINT_JSON_BEGIN')
print(json.dumps(out,default=str,indent=2))
