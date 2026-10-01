import sys,json
from datetime import datetime,timezone,timedelta
sys.path[:0]=['/app/shared','/app']
from sqlalchemy import text
from db.session import engine
from common.redis_client import get_redis
r=get_redis(); now=datetime.now(timezone.utc)
out={'at':now.isoformat(),'recipients':[]}
with engine.connect() as c:
 c.execute(text('SET TRANSACTION READ ONLY'))
 rows=c.execute(text("SELECT DISTINCT u.id, (u.email IS NOT NULL AND u.email <> '') AS has_email FROM users u JOIN (SELECT user_id FROM price_alerts WHERE symbol='MU' AND triggered IS FALSE UNION SELECT user_id FROM earnings_alert_subscriptions WHERE symbol='MU') a ON a.user_id=u.id")).mappings().all()
 for row in rows:
  key=f"stockai:early_earnings_news:{row['id']}:MU:2026-09-30"
  ttl=r.ttl(key)
  out['recipients'].append({'has_email':row['has_email'],'early_marker_exists':bool(r.exists(key)),'ttl_seconds':ttl,'approx_marker_set_at_if_unrenewed':(now-timedelta(seconds=86400-ttl)).isoformat() if ttl>0 else None})
 c.rollback()
out['forecast_cache_exists']=bool(r.exists('stockai:earnings_forecast:MU'))
print(json.dumps(out,indent=2))
