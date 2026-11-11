# NOT RUN (production read refused by the session permission policy, 2026-10-29).
# Read-only: aggregates the dark-pool poll journal using SCAN and ZRANGE only; no writes.
# Intended invocation: piped to `docker exec -i -w /app stockai-market-data-1 python -`.
import json, collections, sys
sys.path.insert(0, "/app/src"); sys.path.insert(0, "/app/shared"); sys.path.insert(0, "/app")
from src.services import unusual_whales as uw
r = uw._get_redis()
keys = list(r.scan_iter(match="stockai:darkpool:polls:v1:*", count=500))
status = collections.Counter(); saturated = 0; unidentified = 0; polls = 0
first = last = None; per_sym = {}
for k in keys:
    items = [json.loads(x) for x in r.zrange(k, 0, -1)]
    sym = (k.decode() if isinstance(k, bytes) else k).rsplit(":", 1)[-1]
    per_sym[sym] = len(items)
    for it in items:
        polls += 1; status[it.get("status")] += 1
        saturated += bool(it.get("saturated")); unidentified += (it.get("identified") is False)
        at = it.get("at"); first = min(first, at) if first else at; last = max(last, at) if last else at
print(json.dumps({"symbols": len(keys), "polls": polls, "status": status, "saturated_windows": saturated,
                  "unidentified_flagged": unidentified, "first": first, "last": last,
                  "polls_per_symbol_min_median_max": (min(per_sym.values()), sorted(per_sym.values())[len(per_sym)//2], max(per_sym.values())) if per_sym else None}, default=str))
