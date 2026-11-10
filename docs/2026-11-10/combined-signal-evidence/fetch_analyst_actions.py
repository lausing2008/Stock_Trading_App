"""Analyst rating changes and price-target changes from Yahoo (upgrades_downgrades), workstation
only. Writes analyst.json.gz: {symbol: [[iso timestamp, action, price_target_action], ...]}.
action: up / down / main / init / reit; price_target_action: Raises / Lowers / Maintains / ..."""
import gzip, hashlib, json, sys, time
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf

symbols = sorted({json.loads(l)['symbol'] for l in gzip.open(sys.argv[1], 'rt')}); out_path = sys.argv[2]


def fetch(symbol):
    for attempt in range(3):
        try:
            df = yf.Ticker(symbol).upgrades_downgrades
            if df is None or not len(df):
                return []
            return [[ts.isoformat(), str(a).lower(), str(p)] for ts, a, p in zip(df.index, df['Action'], df.get('priceTargetAction', [''] * len(df)))]
        except Exception:  # noqa: BLE001
            time.sleep(2 + 3 * attempt)
    return None


with ThreadPoolExecutor(4) as pool:
    data = dict(zip(symbols, pool.map(fetch, symbols)))
with gzip.open(out_path, 'wt') as f:
    json.dump({s: v for s, v in data.items() if v}, f)
n = [len(v) for v in data.values() if v]
print(len(n), 'symbols with actions,', sum(n), 'actions; failed', sum(v is None for v in data.values()),
      'sha256', hashlib.sha256(open(out_path, 'rb').read()).hexdigest())
