"""Read-only: fundamentals data coverage per active symbol, from the cache keys the endpoint uses.
fresh = 24h copy present; last_good = only the 14-day last-good copy; failing = failure remembered;
blank = nothing to serve. 'has_data' = payload carries market_cap or trailing_pe or total_revenue."""
import json, sys
from datetime import datetime, timezone
sys.path[:0] = ['/app', '/app/shared']
from sqlalchemy import text
from db import SessionLocal
from src.api.routes import _get_redis, _FUND_TTL, _FUND_LAST_KEY, _FUND_FAIL_KEY
r = _get_redis()
with SessionLocal() as s:
    s.execute(text('SET TRANSACTION READ ONLY'))
    rows = s.execute(text("SELECT symbol, market::text FROM stocks WHERE active AND NOT delisted ORDER BY 1")).all()
def has_data(p):
    return any(p.get(k) is not None for k in ('market_cap', 'trailing_pe', 'total_revenue'))
out = {}
detail = []
for sym, mkt in rows:
    S = sym.upper()
    fresh = r.get(f"stockai:fundamentals:v2:{S}")
    ttl = r.ttl(f"stockai:fundamentals:v2:{S}") if fresh else None
    last = r.get(_FUND_LAST_KEY.format(S))
    failing = bool(r.get(_FUND_FAIL_KEY.format(S)))
    if fresh:
        state, payload = 'fresh', json.loads(fresh)
    elif last:
        state, payload = 'last_good_only', json.loads(last)
    else:
        state, payload = 'nothing_cached', {}
    data = has_data(payload)
    m = out.setdefault(mkt, {})
    key = f"{state}:{'has_data' if data else 'no_data'}"
    m[key] = m.get(key, 0) + 1
    m['failure_remembered'] = m.get('failure_remembered', 0) + failing
    detail.append(dict(symbol=sym, market=mkt, state=state, has_data=data, failure_remembered=failing,
                       fresh_age_hours=round((_FUND_TTL - ttl) / 3600, 2) if ttl and ttl > 0 else None))
print(json.dumps(dict(read_at=datetime.now(timezone.utc).isoformat(), symbols=len(rows), by_market=out,
                      no_data=[d for d in detail if not d['has_data']]), indent=1, default=str))
