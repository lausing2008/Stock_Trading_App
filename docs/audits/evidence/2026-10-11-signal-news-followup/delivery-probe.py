"""Read-only aggregate recipient and sender-acceptance evidence."""
import json
from sqlalchemy import text
from shared.db import SessionLocal
queries={
'flow_delivery': """SELECT fired_date,delivery_state,count(*) n,sum(delivered_recipient_count) accepted_recipients FROM options_flow_alert_outcomes WHERE fired_date >= '2026-10-05' GROUP BY 1,2 ORDER BY 1,2""",
'recipients': """SELECT count(DISTINCT a.user_id) users_with_open_price_alerts,count(DISTINCT a.user_id) FILTER(WHERE u.email IS NOT NULL AND u.email<>'') with_email FROM price_alerts a JOIN users u ON u.id=a.user_id WHERE NOT a.triggered""",
'event_price_subscriptions': """SELECT a.symbol,count(*) open_price_alerts FROM price_alerts a WHERE NOT a.triggered AND a.symbol IN ('MSFT','NVDA','INTC') GROUP BY a.symbol""",
}
out={}
with SessionLocal() as session:
 for name,query in queries.items():
  try:
   session.execute(text('SET TRANSACTION READ ONLY'))
   session.execute(text("SET LOCAL statement_timeout='20s'"))
   out[name]=[dict(r) for r in session.execute(text(query)).mappings()]
  except Exception as exc: out[name]={'error':str(exc).split('\n')[0]}
  finally: session.rollback()
print(json.dumps(out,default=str,indent=2))
