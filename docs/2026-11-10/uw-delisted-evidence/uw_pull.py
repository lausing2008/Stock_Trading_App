"""Pull Unusual Whales daily bars for former S&P 500 members that Yahoo no longer has, plus UW
earnings history for every symbol. Runs inside the market-data container (it reads the
platform's key there and never prints it).

Gentle by design: one request at a time, at least 1.2 s apart, a 60 s pause on a 429 (up to 3
tries), and it stops by itself at STOP_UTC so it never overlaps the US session's ingestion.
Resumable: progress is appended to <out>/bars.jsonl and <out>/earnings.jsonl, and finished
items are skipped on restart.

Usage: python uw_pull.py <pull-list.json> <earnings-symbols.json> <out_dir> [STOP_UTC=12:30]
"""
import json, os, sys, time
from datetime import date, datetime, timedelta, timezone
import httpx
from common.ai_keys import get_unusual_whales_key

pull, esyms, out = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), sys.argv[3]
stop = sys.argv[4] if len(sys.argv) > 4 else '12:30'
os.makedirs(out, exist_ok=True)
H = {"Authorization": f"Bearer {get_unusual_whales_key()}", "Accept": "application/json"}
B = "https://api.unusualwhales.com"
stats = dict(requests=0, rate_limited=0, errors=0)
last = [0.0]


def deadline():
    now = datetime.now(timezone.utc)
    return now.strftime('%H:%M') >= stop and now.hour < 20


def get(path, params):
    for attempt in range(3):
        wait = 1.2 - (time.time() - last[0])
        if wait > 0:
            time.sleep(wait)
        last[0] = time.time(); stats['requests'] += 1
        try:
            r = httpx.get(B + path, headers=H, params=params, timeout=30)
        except Exception:
            stats['errors'] += 1; time.sleep(5); continue
        if r.status_code == 429:
            stats['rate_limited'] += 1; time.sleep(60); continue
        if r.status_code >= 400:
            stats['errors'] += 1
            return r.status_code, []
        return 200, (r.json() or {}).get('data') or []
    return 429, []


def done(path):
    if not os.path.exists(path):
        return set()
    return {json.loads(l)['symbol'] for l in open(path)}


bars_path, earn_path = os.path.join(out, 'bars.jsonl'), os.path.join(out, 'earnings.jsonl')
finished = done(bars_path)
with open(bars_path, 'a') as f:
    for sym, (start, end) in pull.items():
        if sym in finished:
            continue
        if deadline():
            break
        rows, cursor, status = {}, date.fromisoformat(end), 200
        while cursor >= date.fromisoformat(start):
            status, data = get(f"/api/stock/{sym}/ohlc/1d", {"limit": 500, "end_date": cursor.isoformat()})
            reg = [x for x in data if x.get('market_time') == 'r']
            if not reg:
                break
            for x in reg:
                rows[x['date']] = [x['date'], float(x['open']), float(x['high']), float(x['low']), float(x['close']), float(x.get('volume') or 0)]
            cursor = date.fromisoformat(min(x['date'] for x in reg)) - timedelta(days=1)
        f.write(json.dumps(dict(symbol=sym, window=[start, end], status=status, bars=[rows[k] for k in sorted(rows)])) + '\n'); f.flush()

finished = done(earn_path)
with open(earn_path, 'a') as f:
    for sym in esyms:
        if sym in finished:
            continue
        if deadline():
            break
        status, data = get(f"/api/earnings/{sym}", {})
        keep = [{k: x.get(k) for k in ('report_date', 'report_time', 'actual_eps', 'street_mean_est', 'ending_fiscal_quarter', 'source')} for x in data]
        f.write(json.dumps(dict(symbol=sym, status=status, rows=keep)) + '\n'); f.flush()

json.dump(dict(stats, finished_at=datetime.now(timezone.utc).isoformat(), stopped_by_deadline=deadline()), open(os.path.join(out, 'pull-stats.json'), 'w'))
print(json.dumps(stats))
