"""POST-HOC, exploratory (after earnings-results were read): does drift after a big beat depend
on which stocks? Platform symbols vs the rest, and by the stock's 60-day volatility third."""
import gzip, json, sys
import numpy as np, pandas as pd
from signal_study import load, month_ci, COST
bars, earn, uni = sys.argv[1:4]
PLATFORM = set(json.load(open(uni))['platform'])
d = load(bars); o, c, v = d['open'], d['close'], d['volume']
eligible = (c.notna().rolling(260).sum() >= 260) & ((c * v).rolling(50).median() >= 5e6)
vol60 = c.pct_change(fill_method=None).rolling(60).std(); volrank = vol60.where(eligible).rank(axis=1, pct=True)
sessions = c.index; col = {s: k for k, s in enumerate(c.columns)}
opens = pd.DatetimeIndex([pd.Timestamp(f'{x.date()} 09:30', tz='America/New_York') for x in sessions])
ev = []
for symbol, rows in json.load(gzip.open(earn, 'rt')).items():
    if symbol not in col: continue
    for ts, est, rep, sur in rows:
        if est is None or sur is None or abs(est) < 0.10: continue
        k = opens.searchsorted(pd.Timestamp(ts).tz_convert('America/New_York'), side='right')
        if 1 <= k < len(sessions) and bool(eligible.values[k - 1, col[symbol]]):
            ev.append((k, col[symbol], sur, symbol in PLATFORM, volrank.values[k - 1, col[symbol]]))
ev = np.array(ev, dtype=float); rows = ev[:, 0].astype(int); cols = ev[:, 1].astype(int)
sur, plat, vr = ev[:, 2], ev[:, 3] == 1, ev[:, 4]
months = np.array(sessions.strftime('%Y-%m'))[rows]; years = sessions.year.values[rows]
for H in (5, 20):
    fwd = pd.DataFrame(c.values, index=sessions).shift(-(H - 1)).values / o.values - 1
    bench = np.nanmean(np.where(eligible.shift(1).fillna(False).values, fwd, np.nan), axis=1)
    ex = fwd[rows, cols] - bench[rows] - COST
    print(f'\nH={H} sessions, beat above +10%: excess % after cost (95% interval), events, beat-benchmark rate')
    for name, m in (('platform symbols', plat), ('other symbols', ~plat), ('platform, 2024-2026', plat & (years >= 2024)),
                    ('low-volatility third', vr <= 1 / 3), ('middle third', (vr > 1 / 3) & (vr <= 2 / 3)), ('high-volatility third', vr > 2 / 3),
                    ('high-volatility third, 2021-2026', (vr > 2 / 3) & (years >= 2021))):
        m = m & (sur > 10) & ~np.isnan(ex)
        ci = month_ci(ex[m], months[m])
        print(f'  {name:34s} {100 * ex[m].mean():+6.2f} ({100 * ci[0]:+.2f} to {100 * ci[1]:+.2f})  n={int(m.sum()):6d}  beat {100 * (ex[m] > 0).mean():.1f}%')
    print('  big beat minus big miss, all stocks:', f'{100 * (ex[(sur > 10) & ~np.isnan(ex)].mean() - ex[(sur <= -10) & ~np.isnan(ex)].mean()):+.2f}')
