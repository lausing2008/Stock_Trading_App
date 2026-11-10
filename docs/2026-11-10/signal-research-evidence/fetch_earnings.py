"""Earnings announcement dates, EPS estimate, reported EPS and surprise % from Yahoo Finance
(workstation only). Writes earnings.json.gz: {symbol: [[iso timestamp, estimate, reported, surprise_pct], ...]}."""
import gzip, hashlib, json, sys, time
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf

symbols = sorted({json.loads(l)['symbol'] for l in gzip.open(sys.argv[1], 'rt')}); out_path = sys.argv[2]


def fetch(symbol):
    for attempt in range(3):
        try:
            df = yf.Ticker(symbol).get_earnings_dates(limit=100)
            if df is not None and len(df):
                df = df.dropna(subset=['Reported EPS'])
                return [[ts.isoformat(), None if est != est else float(est), float(rep), None if sur != sur else float(sur)]
                        for ts, est, rep, sur in zip(df.index, df['EPS Estimate'], df['Reported EPS'], df['Surprise(%)'])]
            return []
        except Exception:  # noqa: BLE001
            time.sleep(2 + 3 * attempt)
    return None


with ThreadPoolExecutor(4) as pool:
    data = dict(zip(symbols, pool.map(fetch, symbols)))
missing = sorted(s for s, v in data.items() if not v)
with gzip.open(out_path, 'wt') as f:
    json.dump({s: v for s, v in data.items() if v}, f)
print(len(symbols) - len(missing), 'symbols,', sum(len(v) for v in data.values() if v), 'announcements; missing', len(missing),
      'sha256', hashlib.sha256(open(out_path, 'rb').read()).hexdigest())
