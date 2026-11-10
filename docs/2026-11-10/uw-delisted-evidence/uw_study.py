"""Re-run the combined-signal test with the gap filled from Unusual Whales.

Definitions fixed in docs/2026-11-10/uw-delisted-plan.md before the pulled data was read.

Steps
1. Merge: Yahoo daily bars (adjusted) plus UW daily bars for former S&P 500 members Yahoo lacks.
   UW bars are regular-session rows only. Splits are removed from UW bars: an overnight ratio
   open(t)/close(t-1) within 3% of 1/2, 1/3, 1/4, 1/5, 1/10, 2/3, 3/2, 2, 3, 4, 5 or 10 is
   treated as a split and every earlier price is scaled by it (volume inversely). Dividends are
   not adjusted (a few tenths of a percent a year). Series under 60 bars are dropped.
2. Earnings from UW for every symbol: time = report date 08:00 ET for "premarket", 16:30 ET for
   "postmarket"; any other report_time is dropped. Surprise % = (actual - estimate) / |estimate|.
3. Coverage: each month-end, the share of that day's S&P 500 members with a price.
4. The combined test (combined_study.py, unchanged rules) on the point-in-time S&P 500,
   2012-01 to 2026-10, periods 2012-2016, 2017-2021, 2022-2026, with UW earnings as the
   surprise input. The same run with Yahoo-only prices is reported beside it.
5. Earnings drift (earnings_study.py rules) on the merged prices with UW timestamps.

Usage: uw_study.py <yahoo 1d.jsonl.gz> <uw out dir> <analyst.json.gz> <universe.json> <sp500 history csv> <work dir>
"""
import gzip, json, os, sys
from datetime import datetime, timedelta
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..', 'combined-signal-evidence'), os.path.join(HERE, '..', 'signal-research-evidence')]
import combined_study as C

SPLITS = (1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 2 / 3, 3 / 2, 2, 3, 4, 5, 10)
PERIODS = (('2012-2016', '2012-01-01', '2016-12-31'), ('2017-2021', '2017-01-01', '2021-12-31'), ('2022-2026', '2022-01-01', '2026-12-31'))


def split_adjust(rows):
    """rows: [date, o, h, l, c, v] ascending. Returns adjusted rows and the splits found."""
    rows = [list(r) for r in rows]; found = []
    for k in range(len(rows) - 1, 0, -1):
        ratio = rows[k][1] / rows[k - 1][4] if rows[k - 1][4] else 1
        hit = next((s for s in SPLITS if abs(ratio / s - 1) < 0.03), None)
        if hit:
            found.append((rows[k][0], hit))
            for r in rows[:k]:
                r[1:5] = [x * hit for x in r[1:5]]; r[5] = r[5] / hit
    return rows, found


def merge(yahoo, uw_dir, path):
    pulled = [json.loads(l) for l in open(os.path.join(uw_dir, 'bars.jsonl'))]
    report = dict(pulled=len(pulled), with_bars=0, kept=0, splits=[])
    with gzip.open(path, 'wt') as out:
        with gzip.open(yahoo, 'rt') as f:
            for line in f:
                out.write(line)
        for p in pulled:
            if p['bars']:
                report['with_bars'] += 1
            if len(p['bars']) < 60:
                continue
            rows, found = split_adjust(p['bars'])
            report['splits'] += [[p['symbol'], d, s] for d, s in found]
            bars = [[f'{r[0]}T00:00:00+00:00', datetime.fromisoformat(f'{r[0]}T16:00:00-05:00').isoformat(), *r[1:6], 'uw_split_adjusted']
                    for r in rows]
            out.write(json.dumps(dict(symbol=p['symbol'], timeframe='1d', bars=bars)) + '\n'); report['kept'] += 1
    return report


def uw_earnings(uw_dir, path):
    out, stats = {}, dict(rows=0, kept=0, no_time=0)
    for line in open(os.path.join(uw_dir, 'earnings.jsonl')):
        p = json.loads(line)
        for r in p['rows']:
            stats['rows'] += 1
            when = {'premarket': '08:00', 'postmarket': '16:30'}.get(r.get('report_time'))
            try:
                est, act = float(r['street_mean_est']), float(r['actual_eps'])
            except (TypeError, ValueError, KeyError):
                continue
            if when is None:
                stats['no_time'] += 1; continue
            if est == 0:
                continue
            ts = pd.Timestamp(f"{r['report_date']} {when}", tz='America/New_York').isoformat()
            out.setdefault(p['symbol'], []).append([ts, est, act, 100 * (act - est) / abs(est)]); stats['kept'] += 1
    with gzip.open(path, 'wt') as f:
        json.dump(out, f)
    return stats


def main(yahoo, uw_dir, analyst, universe, hist, work):
    os.makedirs(work, exist_ok=True)
    merged, earn = os.path.join(work, 'merged-1d.jsonl.gz'), os.path.join(work, 'uw-earnings.json.gz')
    report = dict(merge=merge(yahoo, uw_dir, merged), earnings=uw_earnings(uw_dir, earn))
    h = pd.read_csv(hist); h['date'] = pd.to_datetime(h['date'])
    members = [set(t.replace('.', '-') for t in str(x).split(',')) for x in h['tickers']]; dates = h['date'].values
    for label, bars in (('uw_filled', merged), ('yahoo_only', yahoo)):
        coverage = []
        def mask(d, syms, coverage=coverage):
            m = members[np.searchsorted(dates, np.datetime64(d), side='right') - 1]
            coverage.append(dict(month=str(d.date()), members=len(m), with_prices=len(m & set(syms))))
            return np.array([s in m for s in syms])
        C.PERIODS = PERIODS
        C.main(bars, earn, analyst, universe, os.path.join(work, label), mask, '2012-01')
        cov = pd.DataFrame(coverage).drop_duplicates('month'); cov = cov[cov.month >= '2011-12-31']
        cov['share'] = cov.with_prices / cov.members
        report[f'coverage_{label}'] = dict(mean=float(cov.share.mean()), min=float(cov.share.min()),
                                           by_year={y: float(g.share.mean()) for y, g in cov.groupby(cov.month.str[:4])})
    json.dump(report, open(os.path.join(work, 'uw-study.json'), 'w'), indent=1, default=str)
    print(json.dumps({k: v for k, v in report.items() if k != 'merge'}, indent=1, default=str))
    print('merge:', {k: (v if k != 'splits' else len(v)) for k, v in report['merge'].items()})


if __name__ == '__main__':
    main(*sys.argv[1:7])
