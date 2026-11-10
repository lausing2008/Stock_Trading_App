"""Independent research bars for Little RZY from Yahoo Finance (run on a workstation, never on
the production host). Writes <out>/<tf>.jsonl.gz in the evaluation snapshot's row format:
{symbol, timeframe, bars: [[time, closed_at, open, high, low, close, volume, price_basis]]}.

  1d : from 2005-01-01, split- and dividend-adjusted
  1h : Yahoo's 730-day limit
  5m : Yahoo's 60-day limit (15m is derived from 5m by the research runner, as the platform does)

Regular session only. An intraday day whose bar count is not a full or half session ends the
current segment (never bridged). Segments under 60 bars are dropped.
"""
import gzip, hashlib, json, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pandas as pd
import yfinance as yf

OUT = sys.argv[1]; UNIVERSE = json.load(open(sys.argv[2])); TFS = sys.argv[3].split(',')
SYMBOLS = sorted(set(UNIVERSE['platform']) | set(UNIVERSE['sp500']) | set(UNIVERSE['sp400']))
SPEC = {'1d': dict(start='2005-01-01', interval='1d'), '1h': dict(period='730d', interval='1h'), '5m': dict(period='60d', interval='5m')}
FULL = {'1h': (7, 4), '5m': (78, 42)}
STEP = {'1h': timedelta(hours=1), '5m': timedelta(minutes=5)}


def fetch(symbol, tf):
    for attempt in range(4):
        try:
            df = yf.Ticker(symbol).history(auto_adjust=True, prepost=False, actions=False, **SPEC[tf])
            if df is not None and len(df):
                return df
        except Exception as exc:  # noqa: BLE001 - reported below
            err = repr(exc)[:120]
        time.sleep(2 + 3 * attempt)
    return None


def rows(df, tf):
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    df = df[(df[['Open', 'High', 'Low', 'Close']] > 0).all(axis=1)]
    idx = df.index.tz_convert('America/New_York') if df.index.tz is not None else df.index.tz_localize('America/New_York')
    out = []  # (day, start_utc_iso, closed_utc_iso, o, h, l, c, v)
    for ts, (o, h, l, c, v) in zip(idx, df.itertuples(index=False, name=None)):
        h = max(o, h, l, c); l = min(o, l, c)
        if tf == '1d':
            start = pd.Timestamp(ts.date(), tz='UTC'); closed = pd.Timestamp(f'{ts.date()} 16:00', tz='America/New_York').tz_convert('UTC')
        else:
            if not (ts.hour * 60 + ts.minute >= 570 and ts.hour < 16):
                continue
            start = ts.tz_convert('UTC'); closed = None
        out.append([ts.date(), start, closed, float(o), float(h), float(l), float(c), float(v)])
    if tf == '1d':
        return [[[r[1].isoformat(), r[2].isoformat(), *r[3:], 'adjusted'] for r in out]] if len(out) >= 60 else []
    segs, cur = [], []
    days = {}
    for r in out:
        days.setdefault(r[0], []).append(r)
    for day in sorted(days):
        d = days[day]
        # Yahoo's hourly half-day sessions carry 3 bars (the 12:30-13:00 stub is absent).
        half = (day.month, day.day) in ((7, 3), (12, 24)) or (day.month == 11 and day.weekday() == 4 and 23 <= day.day <= 29)
        counts = (FULL[tf][0],) + ((FULL[tf][1], 3) if half and tf == '1h' else (FULL[tf][1],) if half else ())
        if len(d) not in counts:
            if len(cur) >= 60: segs.append(cur)
            cur = []; continue
        end = pd.Timestamp(f'{day} {"16:00" if len(d) == FULL[tf][0] else "13:00"}', tz='America/New_York').tz_convert('UTC')
        ok = d[0][1].tz_convert('America/New_York').strftime('%H:%M') == '09:30'
        for k, r in enumerate(d):
            r[2] = min(r[1] + STEP[tf], end)
            ok = ok and (k == 0 or r[1] == d[k - 1][2])
        if not ok:
            if len(cur) >= 60: segs.append(cur)
            cur = []; continue
        cur.extend([r[1].isoformat(), r[2].isoformat(), *r[3:], 'adjusted'] for r in d)
    if len(cur) >= 60: segs.append(cur)
    return segs


for tf in TFS:
    started = time.time(); stats = dict(symbols=0, segments=0, bars=0, missing=[], first=None, last=None)
    path = f'{OUT}/{tf}.jsonl.gz'
    with ThreadPoolExecutor(6) as pool, gzip.open(path, 'wt') as out:
        for symbol, df in zip(SYMBOLS, pool.map(lambda s: fetch(s, tf), SYMBOLS)):
            segs = rows(df, tf) if df is not None else []
            if not segs:
                stats['missing'].append(symbol); continue
            stats['symbols'] += 1; stats['segments'] += len(segs); stats['bars'] += sum(map(len, segs))
            stats['first'] = min(stats['first'] or segs[0][0][0], segs[0][0][0]); stats['last'] = max(stats['last'] or segs[-1][-1][0], segs[-1][-1][0])
            for seg in segs:
                out.write(json.dumps(dict(symbol=symbol, timeframe=tf, bars=seg)) + '\n')
    stats['sha256'] = hashlib.sha256(open(path, 'rb').read()).hexdigest(); stats['seconds'] = round(time.time() - started)
    stats['fetched_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    json.dump(stats, open(f'{OUT}/{tf}.meta.json', 'w'), indent=1)
    print(tf, {k: v for k, v in stats.items() if k != 'missing'}, 'missing', len(stats['missing']), flush=True)
