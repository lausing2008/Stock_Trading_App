"""Read existing Redis calendar only; never refresh a feed or write a cache entry."""
import json
from datetime import datetime,timezone
from src.api.routes import _get_redis, _refresh_days_to_earnings
raw=_get_redis().get('stockai:fundamentals:v2:TSM')
payload=_refresh_days_to_earnings(json.loads(raw)) if raw else None
result={'checked_at':datetime.now(timezone.utc).isoformat(),'symbol':'TSM','method':'existing_redis_get_only','record_present':payload is not None,
        'calendar':({k:payload.get(k) for k in ('fetched_at','next_earnings_date','ex_dividend_date')} if payload is not None else None)}
print('CALENDAR_CACHE_JSON='+json.dumps(result,sort_keys=True))
