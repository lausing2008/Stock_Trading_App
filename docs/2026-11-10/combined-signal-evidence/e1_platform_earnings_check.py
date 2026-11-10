"""E1: why the platform's "+4.19% over 5 days after the open" (big beats) does not replicate.

The platform (event-intelligence earnings.get_earnings_surprise_impact) measures "after open"
from the close of the FIRST BAR ON OR AFTER report_date. For a company that reports after the
close, that bar's close is BEFORE the announcement, so the figure still contains the overnight
gap. This script takes the platform's own events (platform-earnings-events.csv, read from
production 2026-11-10), finds each one's announcement time in Yahoo's calendar, and measures:

  A. the platform's definition (close on report date -> close 5 sessions later, raw)
  B. the same split by announcement time (before the open vs after the close)
  C. a truly post-announcement entry (first OPEN after the announcement -> close 5 sessions
     later counting the entry session), raw and net of the equal-weight universe

Usage: e1_platform_earnings_check.py <events.csv> <1d.jsonl.gz> <earnings.json.gz>
"""
import csv, gzip, json, sys
import numpy as np, pandas as pd
sys.path.insert(0, __file__.rsplit('/', 2)[0] + '/signal-research-evidence')
from signal_study import load

events, bars, earn = sys.argv[1:4]
d = load(bars); o, c, v = d['open'], d['close'], d['volume']
eligible = (c.notna().rolling(260).sum() >= 260) & ((c * v).rolling(50).median() >= 5e6)
sessions = c.index; col = {s: k for k, s in enumerate(c.columns)}
opens = pd.DatetimeIndex([pd.Timestamp(f'{x.date()} 09:30', tz='America/New_York') for x in sessions])
cal = json.load(gzip.open(earn, 'rt'))
fwd5 = c.shift(-4).values / o.values - 1            # open of row k -> close 4 rows later (5 sessions)
bench = np.nanmean(np.where(eligible.shift(1).fillna(False).values, fwd5, np.nan), axis=1)
rows = []
for sym, rd, sur, r1, r5 in csv.reader(open(events)):
    sur = float(sur)
    if sym not in col or sur <= 10:
        continue
    rd = pd.Timestamp(rd)
    # Yahoo's announcement on the same calendar day (ET), if any.
    stamps = [pd.Timestamp(t).tz_convert('America/New_York') for t, *_ in cal.get(sym, [])]
    same = [t for t in stamps if t.date() == rd.date()]
    when = 'unknown' if not same else ('before open' if same[0].hour < 9 or (same[0].hour == 9 and same[0].minute < 30) else
                                       'after close' if same[0].hour >= 16 else 'during session')
    j = col[sym]; k0 = sessions.searchsorted(rd)                       # first session on/after report date
    if k0 + 5 >= len(sessions) or np.isnan(c.values[k0, j]) or np.isnan(c.values[k0 + 5, j]):
        continue
    platform_def = c.values[k0 + 5, j] / c.values[k0, j] - 1
    k = opens.searchsorted(same[0], side='right') if same else None
    true_after = excess = None
    if k is not None and k + 4 < len(sessions) and not np.isnan(fwd5[k, j]):
        true_after = fwd5[k, j]; excess = fwd5[k, j] - bench[k]
    rows.append(dict(symbol=sym, date=rd.date(), when=when, stored_5d=float(r5), platform_def=platform_def,
                     true_after_open=true_after, true_after_open_excess=excess))
df = pd.DataFrame(rows)
pct = lambda s: f'{100 * s.mean():+.2f}% (n={s.notna().sum()})'
print(f'Platform events with surprise > +10% and US daily bars here: {len(df)}')
print('A. platform definition, all:', pct(df.platform_def), '| stored post_earnings_return_5d (incl. gap):', pct(df.stored_5d))
for w, g in df.groupby('when'):
    print(f'B. {w:15s} platform definition {pct(g.platform_def)}  |  first open after announcement: raw {pct(g.true_after_open.dropna())}, net of universe {pct(g.true_after_open_excess.dropna())}')
t = df.dropna(subset=['true_after_open'])
print('C. first open after announcement, all known times: raw', pct(t.true_after_open), '| net of universe', pct(t.true_after_open_excess))
df.to_csv('e1-events.csv', index=False)
