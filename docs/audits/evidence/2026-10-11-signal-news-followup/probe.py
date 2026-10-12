"""Read-only diagnosis; no addresses, credentials, or notification bodies collected."""
import json
from sqlalchemy import text
from shared.db import SessionLocal
queries = {
    'clock': 'SELECT now() AS observed_at',
    'news_event': """SELECT source,symbol,headline,url,published_at,ingested_at,sentiment_label,is_material,category,classification_deferred_reason FROM realtime_news_items WHERE published_at >= '2026-10-05' AND (headline ILIKE '%Surface Laptop Ultra%' OR headline ILIKE '%RTX Spark%') ORDER BY published_at LIMIT 70""",
    'news_sources': """SELECT source,count(*) rows,count(DISTINCT url) articles,max(ingested_at) latest FROM realtime_news_items WHERE ingested_at>=now()-interval '7 days' GROUP BY source""",
    'preferences': """SELECT alert_type,enabled,count(*) users FROM alert_preferences WHERE alert_type IN ('options_flow','option_opportunity','signal_change','earnings') GROUP BY 1,2""",
    'outbox': 'SELECT alert_type,state,count(*) n,max(created_at) latest FROM notification_outbox GROUP BY 1,2',
    'flow': """SELECT fired_date,direction,count(*) n FROM options_flow_alert_outcomes WHERE fired_date>=CURRENT_DATE-10 GROUP BY 1,2 ORDER BY 1,2""",
    'ledger': "SELECT count(*) captures FROM option_strategy_captures",
    'watchlist': """SELECT s.symbol,s.name,s.active,count(w.user_id) watchers FROM stocks s LEFT JOIN watchlist_items w ON w.stock_id=s.id WHERE s.symbol IN ('MSFT','NVDA','INTC','MS') GROUP BY 1,2,3""",
    'signals': """SELECT DISTINCT ON(s.stock_id,s.horizon) st.symbol,s.horizon,s.ts,s.signal,s.confidence,s.bullish_probability,s.reasons->>'ml_test_auc' auc,s.reasons->>'ml_oos_suppressed' low_oos,s.reasons->>'compression_cap_applied' compression_cap,s.reasons->>'weekly_gate_fired' weekly_gate FROM signals s JOIN stocks st ON st.id=s.stock_id WHERE st.symbol IN ('MSFT','NVDA','INTC') ORDER BY s.stock_id,s.horizon,s.ts DESC""",
}
out={}
with SessionLocal() as session:
    for name, query in queries.items():
        try:
            session.execute(text('SET TRANSACTION READ ONLY'))
            session.execute(text("SET LOCAL statement_timeout='20s'"))
            out[name]=[dict(r) for r in session.execute(text(query)).mappings()]
        except Exception as exc:
            out[name]={'error':str(exc).split('\n')[0]}
        finally:
            session.rollback()
print(json.dumps(out, default=str, indent=2))
