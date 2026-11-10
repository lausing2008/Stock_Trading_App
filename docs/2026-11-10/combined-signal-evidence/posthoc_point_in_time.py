"""POST-HOC (written after combined-results.txt was read): how much of the momentum and pullback
results comes from choosing stocks with hindsight?

Runs the same code with each month's universe limited to stocks that were in the S&P 500 AT
THAT TIME (fja05680/sp500 historical components, sha256 in the results), and, for contrast, to
today's S&P 500 members. Former members without Yahoo prices (mostly delisted or acquired) are
counted: they are the part of the problem this free data cannot fix.

Usage: posthoc_point_in_time.py <1d> <earnings> <analyst> <universe.json> <sp500_hist.csv>
"""
import json, os, sys
import numpy as np, pandas as pd
import combined_study as C

bars, earn, analyst, uni, hist = sys.argv[1:6]
h = pd.read_csv(hist); h['date'] = pd.to_datetime(h['date'])
members = [set(t.replace('.', '-') for t in str(x).split(',')) for x in h['tickers']]
dates = h['date'].values
today = set(json.load(open(uni))['sp500'])
have = set(pd.read_json(os.path.join('out', 'months-composite3.csv'), lines=False) if False else [])


def at(d):
    k = np.searchsorted(dates, np.datetime64(d), side='right') - 1
    return members[k]


coverage = []


def point_in_time(d, syms):
    m = at(d); s = set(syms)
    coverage.append(dict(month=str(d.date()), members=len(m), with_prices=len(m & s)))
    return np.array([x in m for x in syms])


def todays(d, syms):
    return np.array([x in today for x in syms])


C.main(bars, earn, analyst, uni, 'out-point-in-time', point_in_time)
cov = pd.DataFrame(coverage).drop_duplicates('month')
cov['missing_share'] = 1 - cov.with_prices / cov.members
cov.to_csv('out-point-in-time/membership-coverage.csv', index=False)
print('\nS&P 500 members at the time with Yahoo prices: '
      + ', '.join(f"{y}: {100 * (1 - g.missing_share.mean()):.0f}%" for y, g in cov.groupby(cov.month.str[:4]) if y in ('2006', '2010', '2015', '2020', '2024', '2026')))
print('\n==== contrast: today\'s S&P 500 members only ====')
C.main(bars, earn, analyst, uni, 'out-todays-sp500', todays)
