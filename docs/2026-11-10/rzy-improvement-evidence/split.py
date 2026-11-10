"""Design / confirmation split of the independent data, fixed before any result was read.

- seen: a platform symbol inside the period the 2026-11-08 snapshot covered (those trades shaped
  the earlier reviews). Excluded from both sets.
- 1d: design = signals before 2016-01-01; confirmation = 2016-01-01 onwards.
- 1h: design = signals before 2025-07-01; confirmation = 2025-07-01 onwards.
- 5m and 15m (60 days only): split by symbol, not time: sha256(symbol) even = design, odd =
  confirmation. Both halves share the same calendar days, so they are not independent in time.
"""
import hashlib, json, os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PLATFORM = set(json.load(open(os.path.join(HERE, 'universe.json')))['platform'])
SEEN_FROM = {'1d': '2023-12-26', '1h': '2026-08-04', '15m': '2026-08-04', '5m': '2026-08-04'}
TIME_CUT = {'1d': '2016-01-01', '1h': '2025-07-01'}


def even(symbol):
    return int(hashlib.sha256(symbol.encode()).hexdigest(), 16) % 2 == 0


def assign(df):
    out = pd.Series('confirm', index=df.index)
    for tf, g in df.groupby('timeframe'):
        if tf in TIME_CUT:
            out[g.index[g['signal_time'] < pd.Timestamp(TIME_CUT[tf], tz='UTC')]] = 'design'
        else:
            out[g.index[g['symbol'].map(even)]] = 'design'
        seen = g['symbol'].isin(PLATFORM) & (g['signal_time'] >= pd.Timestamp(SEEN_FROM[tf], tz='UTC'))
        out[g.index[seen]] = 'seen'
    return out
