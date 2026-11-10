"""E3: does combining weak signals into one monthly ranking beat each signal alone?

Definitions are fixed in docs/2026-11-10/combined-signal-plan.md before any result. Research
only; reads files.

At each month-end close t (last session of a calendar month):
- Eligible: >= 260 prior daily bars, 50-day median dollar volume >= $5M, a price at the next open.
- Inputs, each a percentile rank among eligible stocks (higher = more attractive):
    momentum   12-1 month return, close(t-21) / close(t-252) - 1
    pullback   minus the 5-session return (the past week's losers rank highest)
    surprise   EPS surprise % of the latest announcement in the previous 90 days whose time is
               at or before the close of t, |estimate| >= 0.10; stocks without one get 0.5
    analyst    over the previous 90 days, (up - down) / (up + down), where up = an upgrade or a
               price-target raise and down = a downgrade or a price-target cut (one record
               counts once); stocks without any get 0.5
- composite3 = mean of momentum, pullback, surprise ranks; composite4 adds analyst.
- Portfolio: the top N by score, equal weight, bought at the next open and sold at the open
  after the next month-end. Ties broken by symbol.
- Benchmark: equal weight of every eligible stock over the same window.
- Cost: 0.05% per side on traded weight (a replaced name costs 0.10% of its weight).
- Measure: monthly excess return = portfolio - benchmark - cost.

Usage: combined_study.py <1d.jsonl.gz> <earnings.json.gz> <analyst.json.gz> <universe.json> <out_dir>
"""
import gzip, json, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, __file__.rsplit('/', 2)[0] + '/signal-research-evidence')
from signal_study import load

N, N_WIDE = 20, 50
SIDE_COST = 0.0005
PERIODS = (('2006-2015', '2006-01-01', '2015-12-31'), ('2016-2020', '2016-01-01', '2020-12-31'), ('2021-2026', '2021-01-01', '2026-12-31'))


def month_end_rows(index):
    m = index.to_period('M')
    return [k for k in range(len(index) - 1) if m[k] != m[k + 1]]


def event_table(path, kind):
    """{symbol: (times as int64 UTC ns, values)} sorted by time."""
    raw = json.load(gzip.open(path, 'rt')); out = {}
    for sym, rows in raw.items():
        t, v = [], []
        for r in rows:
            if kind == 'surprise':
                ts, est, rep, sur = r
                if est is None or sur is None or abs(est) < 0.10:
                    continue
                t.append(pd.Timestamp(ts).tz_convert('UTC').value); v.append(sur)
            else:
                ts, act, pta = r
                up = act == 'up' or pta.lower() == 'raises'; down = act == 'down' or pta.lower() == 'lowers'
                if up == down:
                    continue
                stamp = pd.Timestamp(ts); stamp = stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')
                t.append(stamp.value); v.append(1 if up else -1)
        if t:
            order = np.argsort(t, kind='stable'); out[sym] = (np.array(t)[order], np.array(v, float)[order])
    return out


def rank(values, eligible):
    s = pd.Series(values).where(eligible)
    return s.rank(pct=True).values


def main(bars, earn, analyst, universe, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    d = load(bars); o, c, v = d['open'].values, d['close'].values, d['volume']
    idx = d['close'].index; syms = list(d['close'].columns)
    elig_all = ((d['close'].notna().rolling(260).sum() >= 260) & ((d['close'] * v).rolling(50).median() >= 5e6)).values
    surprises, actions = event_table(earn, 'surprise'), event_table(analyst, 'analyst')
    platform = set(json.load(open(universe))['platform'])
    ends = month_end_rows(idx)
    months = []
    for a, b in zip(ends, ends[1:]):
        k0, k1 = a + 1, b + 1                                   # entry open, exit open
        if a < 252:
            continue
        close_t = pd.Timestamp(f'{idx[a].date()} 16:00', tz='America/New_York').tz_convert('UTC').value
        start = close_t - 90 * 86400 * 10**9
        eligible = elig_all[a] & ~np.isnan(o[k0]) & ~np.isnan(o[k1])
        ret = o[k1] / o[k0] - 1
        mom = c[a - 21] / c[a - 252] - 1
        pull = -(c[a] / c[a - 5] - 1)
        sur = np.full(len(syms), np.nan); ana = np.full(len(syms), np.nan)
        for j, s in enumerate(syms):
            if not eligible[j]:
                continue
            if s in surprises:
                t, val = surprises[s]; hi = np.searchsorted(t, close_t, side='right')
                if hi and t[hi - 1] > start:
                    sur[j] = val[hi - 1]
            if s in actions:
                t, val = actions[s]; lo, hi = np.searchsorted(t, start, side='right'), np.searchsorted(t, close_t, side='right')
                if hi > lo:
                    sur_a = val[lo:hi]; ana[j] = sur_a.sum() / len(sur_a)
        r = dict(momentum=rank(mom, eligible), pullback=rank(pull, eligible),
                 surprise=np.where(np.isnan(sur) & eligible, 0.5, rank(sur, eligible & ~np.isnan(sur))),
                 analyst=np.where(np.isnan(ana) & eligible, 0.5, rank(ana, eligible & ~np.isnan(ana))))
        r['composite3'] = (r['momentum'] + r['pullback'] + r['surprise']) / 3
        r['composite4'] = (r['composite3'] * 3 + r['analyst']) / 4
        months.append(dict(month=str(idx[k0].to_period('M')), entry=str(idx[k0].date()), eligible=eligible, ret=ret,
                           bench=float(np.nanmean(ret[eligible])), ranks=r, n_eligible=int(eligible.sum()),
                           has_surprise=int((~np.isnan(sur)).sum()), has_analyst=int((~np.isnan(ana)).sum())))

    def portfolio(score_name, n, universe_mask=None, require=None):
        rows, held = [], set()
        for m in months:
            ok = m['eligible'].copy()
            if universe_mask is not None:
                ok &= universe_mask
            if require is not None:
                ok &= ~np.isnan(require(m))
            score = np.where(ok, m['ranks'][score_name], -np.inf)
            order = sorted(np.flatnonzero(ok), key=lambda j: (-score[j], syms[j]))[:n]
            pick = set(order)
            if len(pick) < n:
                rows.append(dict(month=m['month'], excess=np.nan, gross=np.nan, bench=m['bench'], turnover=np.nan, picks=[])); continue
            turnover = 1.0 if not held else len(pick - held) / n
            cost = SIDE_COST * (1 if not held else 2 * turnover)
            gross = float(np.mean(m['ret'][list(pick)]))
            bench = m['bench'] if universe_mask is None else float(np.nanmean(m['ret'][m['eligible'] & universe_mask]))
            rows.append(dict(month=m['month'], excess=gross - bench - cost, gross=gross - cost, bench=bench, turnover=turnover,
                             picks=sorted(syms[j] for j in pick)))
            held = pick
        return pd.DataFrame(rows)

    def ci(x, n=4000, seed=11, block=6):
        x = np.asarray(x[~np.isnan(x)]); rng = np.random.default_rng(seed)
        iid = np.percentile(x[rng.integers(0, len(x), (n, len(x)))].mean(1), [2.5, 97.5])
        nb = int(np.ceil(len(x) / block)); starts = rng.integers(0, len(x) - block + 1, (n, nb))
        blk = np.percentile(np.stack([x[s:s + block] for s in starts.ravel()]).reshape(n, -1)[:, :len(x)].mean(1), [2.5, 97.5])
        return [float(min(iid[0], blk[0])), float(max(iid[1], blk[1]))]

    def stats(df, start='2006-01', end='2026-12'):
        g = df[(df.month >= start) & (df.month <= end)].dropna(subset=['excess'])
        if len(g) < 12:
            return dict(months=len(g))
        e = g.excess.values; curve = np.cumsum(e); dd = float(np.max(np.maximum.accumulate(np.maximum(curve, 0)) - curve))
        out = dict(months=len(g), first=g.month.iloc[0], last=g.month.iloc[-1], mean_excess_pct=100 * e.mean(),
                   ci_pct=[100 * x for x in ci(e)], annual_excess_pct=100 * ((1 + e).prod() ** (12 / len(e)) - 1),
                   months_beating_benchmark=float((e > 0).mean()), max_drawdown_excess_pct=100 * dd,
                   annual_return_pct=100 * ((1 + g.gross.values).prod() ** (12 / len(g)) - 1),
                   benchmark_annual_return_pct=100 * ((1 + g.bench.values).prod() ** (12 / len(g)) - 1),
                   mean_turnover=float(g.turnover.mean()))
        for name, lo, hi in PERIODS:
            p = g[(g.month >= lo[:7]) & (g.month <= hi[:7])]
            out[name] = dict(months=len(p), mean_excess_pct=100 * p.excess.mean(), ci_pct=[100 * x for x in ci(p.excess.values)] if len(p) >= 12 else None)
        return out

    def diff(a, b, start, end):
        m = a.merge(b, on='month', suffixes=('_a', '_b'))
        m = m[(m.month >= start) & (m.month <= end)].dropna(subset=['excess_a', 'excess_b'])
        x = (m.excess_a - m.excess_b).values
        return dict(months=len(x), difference_pct=100 * x.mean(), ci_pct=[100 * v for v in ci(x)])

    plat = np.array([s in platform for s in syms])
    P = {name: portfolio(name, N) for name in ('composite3', 'momentum', 'pullback', 'composite4')}
    P['surprise'] = portfolio('surprise', N, require=lambda m: np.where(m['ranks']['surprise'] == 0.5, np.nan, 1.0))
    P['analyst'] = portfolio('analyst', N, require=lambda m: np.where(m['ranks']['analyst'] == 0.5, np.nan, 1.0))
    P['composite3_top50'] = portfolio('composite3', N_WIDE)
    P['composite3_platform_symbols'] = portfolio('composite3', N, universe_mask=plat)
    res = dict(n_months=len(months), symbols=len(syms), coverage=[dict(month=m['month'], eligible=m['n_eligible'], with_surprise=m['has_surprise'],
                                                                         with_analyst=m['has_analyst']) for m in months])
    res['portfolios'] = {k: stats(v) for k, v in P.items()}
    res['portfolios_2013_on'] = {k: stats(v, '2013-01') for k, v in P.items()}
    best_single = max(('momentum', 'pullback', 'surprise'), key=lambda k: res['portfolios'][k].get('mean_excess_pct', -1e9))
    comp = res['portfolios']['composite3']
    res['primary'] = dict(
        best_single_input=best_single,
        composite_minus_best_single=diff(P['composite3'], P[best_single], '2006-01', '2026-12'),
        pass_1_interval_above_zero=comp['ci_pct'][0] > 0,
        pass_2_beats_every_single_input={k: comp['mean_excess_pct'] > res['portfolios'][k]['mean_excess_pct'] for k in ('momentum', 'pullback', 'surprise')},
        pass_3_positive_in_every_period={p: comp[p]['mean_excess_pct'] > 0 for p, _, _ in PERIODS})
    res['primary']['passed'] = bool(res['primary']['pass_1_interval_above_zero'] and all(res['primary']['pass_2_beats_every_single_input'].values())
                                    and all(res['primary']['pass_3_positive_in_every_period'].values()))
    res['secondary'] = dict(composite4_minus_composite3_2013_on=diff(P['composite4'], P['composite3'], '2013-01', '2026-12'),
                            analyst_only_2013_on=res['portfolios_2013_on']['analyst'])
    json.dump(res, open(os.path.join(out_dir, 'combined-results.json'), 'w'), indent=1, default=float)
    for k, v in P.items():
        v.assign(picks=v.picks.map(' '.join)).to_csv(os.path.join(out_dir, f'months-{k}.csv'), index=False)
    print(f"{'portfolio':30s} months  mean%/mo   95% interval      ann.excess  beat-mo  maxDD   ann.ret  bench  turnover | 2006-15  2016-20  2021-26")
    for label, table in (('all', res['portfolios']), ('2013 on', res['portfolios_2013_on'])):
        print(f'-- {label}')
        for k, s in table.items():
            if s.get('months', 0) < 12: print(f'{k:30s} {s.get("months")}'); continue
            print(f"{k:30s} {s['months']:5d} {s['mean_excess_pct']:+8.2f}  {s['ci_pct'][0]:+6.2f} to {s['ci_pct'][1]:+6.2f}  {s['annual_excess_pct']:+7.2f}%  {100 * s['months_beating_benchmark']:5.1f}% {s['max_drawdown_excess_pct']:6.1f} {s['annual_return_pct']:+7.2f}% {s['benchmark_annual_return_pct']:+6.2f}% {s['mean_turnover']:6.2f} | "
                  + '  '.join(f"{s[p]['mean_excess_pct']:+6.2f}" if s[p]['months'] else '   —  ' for p, _, _ in PERIODS))
    print('\nPRIMARY', json.dumps(res['primary'], default=float)); print('SECONDARY', json.dumps(res['secondary']['composite4_minus_composite3_2013_on'], default=float))


if __name__ == '__main__':
    main(*sys.argv[1:6])
