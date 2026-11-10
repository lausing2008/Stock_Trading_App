"""The predeclared confirmation tests (rzy-improvement-preregistration.md). Reads the
confirmation runs and the platform-snapshot run; writes confirm-results.json and prints tables."""
import json, sys
import numpy as np
import pandas as pd
from rzy_stats import load, cluster_ci
from split import PLATFORM

runs = dict(a.split('=') for a in sys.argv[1:-1]); out_path = sys.argv[-1]
UNIVERSE = json.load(open(__file__.rsplit('/', 1)[0] + '/universe.json'))


def wide(values, df):
    """95% interval of the mean: the wider of resampling symbols and resampling months."""
    a = cluster_ci(values, df['symbol'].values); b = cluster_ci(values, df['month'].values)
    return [float(min(a[0], b[0])), float(max(a[1], b[1]))]


def stats(df):
    n = len(df)
    if n == 0:
        return dict(trades=0)
    r = df['r'].values; wins = r[r > 0]; losses = r[r <= 0]
    curve = np.cumsum(df.sort_values('exit_time')['r'].values)
    dd = float(np.max(np.maximum.accumulate(np.maximum(curve, 0)) - curve))
    e = df['edge'].values; c = (df['r'] - df['control_ctx_mean_r']).values
    years = df.groupby('year')['r'].mean()
    return dict(trades=n, symbols=int(df['symbol'].nunique()), win_rate=float((r > 0).mean()),
                mean_r=float(r.mean()), mean_r_ci=wide(r, df), median_r=float(np.median(r)),
                profit_factor=float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
                avg_win_r=float(wins.mean()) if len(wins) else None, avg_loss_r=float(losses.mean()) if len(losses) else None,
                edge_plain=float(np.nanmean(e)), edge_plain_ci=wide(e, df),
                edge_context=float(np.nanmean(c)), edge_context_ci=wide(c, df), context_twins=int(np.sum(~np.isnan(c))),
                total_r=float(r.sum()), max_drawdown_r=dd, median_hold_bars=float(df['holding_bars'].median()),
                mean_return_pct=float(100 * df['ret'].mean()), mean_cost_r=float(df['cost_r'].mean()),
                target_rate=float((df['exit_reason'] == 'target').mean()), stop_rate=float((df['exit_reason'] == 'stop').mean()),
                years=int(len(years)), positive_years=int((years > 0).sum()))


def diff_months(a, b, n=2000, seed=11):
    """Mean R of a minus mean R of b, resampling calendar months for both together."""
    months = sorted(set(a['month']) | set(b['month'])); idx = {m: i for i, m in enumerate(months)}; k = len(months)
    def agg(d):
        codes = d['month'].map(idx).values
        return np.bincount(codes, weights=d['r'].values, minlength=k), np.bincount(codes, minlength=k)
    sa, ca = agg(a); sb, cb = agg(b)
    pick = np.random.default_rng(seed).integers(0, k, size=(n, k))
    with np.errstate(invalid='ignore', divide='ignore'):
        d = sa[pick].sum(1) / ca[pick].sum(1) - sb[pick].sum(1) / cb[pick].sum(1)
    d = d[~np.isnan(d)]
    return dict(difference=float(a['r'].mean() - b['r'].mean()), ci=[float(x) for x in np.percentile(d, [2.5, 97.5])])


def show(title, rows):
    print(f'\n### {title}\n')
    print('| Variant | Trades | Stocks | Win rate | Mean R | Mean R 95% | Edge vs twins | Edge vs context twins (95%) | PF | Median hold | Total R | Max DD |')
    print('|---|---|---|---|---|---|---|---|---|---|---|---|')
    for name, s in rows.items():
        if not s.get('trades'):
            print(f'| {name} | 0 |'); continue
        pf = '—' if s['profit_factor'] is None else f"{s['profit_factor']:.2f}"
        print(f"| {name} | {s['trades']} | {s['symbols']} | {100 * s['win_rate']:.1f}% | {s['mean_r']:+.3f} | {s['mean_r_ci'][0]:+.3f} to {s['mean_r_ci'][1]:+.3f} | "
              f"{s['edge_plain']:+.3f} ({s['edge_plain_ci'][0]:+.3f} to {s['edge_plain_ci'][1]:+.3f}) | {s['edge_context']:+.3f} ({s['edge_context_ci'][0]:+.3f} to {s['edge_context_ci'][1]:+.3f}) | "
              f"{pf} | {s['median_hold_bars']:.0f} | {s['total_r']:+.1f} | {s['max_drawdown_r']:.1f} |")


res = {}
TWIN_LIMIT = 100.  # |mean twin R| beyond this is a bad-price artefact, not a trade result


def clean(df):
    """Deviation, found after the run: one QUBT short on 2022-12-01 has twins that cross a
    corrupt early Yahoo price (mean twin R of -17 million). Twin means beyond +/-100R are set to
    missing; the trade itself is kept. Counted and reported."""
    bad = 0
    for col in ('control_mean_r', 'control_ctx_mean_r'):
        mask = df[col].abs() > TWIN_LIMIT
        bad += int(mask.sum()); df.loc[mask, col] = np.nan
    df['edge'] = df['r'] - df['control_mean_r']
    return df, bad


res['twin_means_discarded'] = {}
for key, path in runs.items():
    df, bad = clean(load(path)); res['twin_means_discarded'][key] = bad
    res[key] = {}
    for tf, g in df.groupby('timeframe'):
        rows = {name: stats(v) for name, v in g.groupby('variant', sort=False)}
        res[key][tf] = rows
        show(f'{key} — {tf}', rows)
    if key == 'confirm_1d':
        g = df
        lt, base = g[g.variant == 'lt_off'], g[g.variant == 'lt_base']
        extra = dict(
            S3_lt_off_minus_lt_base=diff_months(lt, base),
            lt_off_by_year={str(y): stats(v) for y, v in lt.groupby('year')},
            baseline_by_year={str(y): stats(v) for y, v in g[g.variant == 'baseline'].groupby('year')},
            lt_off_by_group={
                'platform symbols': stats(lt[lt.symbol.isin(PLATFORM)]),
                'S&P 500 (not platform)': stats(lt[lt.symbol.isin(set(UNIVERSE['sp500']) - PLATFORM)]),
                'S&P 400 (not platform)': stats(lt[lt.symbol.isin(set(UNIVERSE['sp400']) - PLATFORM - set(UNIVERSE['sp500']))]),
                '2016-2020': stats(lt[lt.year <= 2020]), '2021-2026': stats(lt[lt.year >= 2021])},
            baseline_by_period={'2016-2020': stats(g[(g.variant == 'baseline') & (g.year <= 2020)]),
                                '2021-2026': stats(g[(g.variant == 'baseline') & (g.year >= 2021)])},
            lt_base_by_period={'2016-2020': stats(base[base.year <= 2020]), '2021-2026': stats(base[base.year >= 2021])},
            lt_off_context_twin_mean_r=float(lt['control_ctx_mean_r'].mean()),
            lt_off_plain_twin_mean_r=float(lt['control_mean_r'].mean()),
            t_off_by_direction={d: stats(v) for d, v in g[g.variant == 't_off'].groupby('direction')},
            lt_off_exits=lt.groupby('exit_reason')['r'].agg(['size', 'mean']).round(3).to_dict('index'),
            lt_off_without_top5_symbols=stats(lt[~lt.symbol.isin(lt.groupby('symbol')['r'].sum().nlargest(5).index)]),
            lt_off_concurrency=None)
        # How many lt_off positions are open together (no portfolio limit in the study).
        ent = pd.to_datetime(lt['entry_time'], utc=True, format='ISO8601'); ex = pd.to_datetime(lt['exit_time'], utc=True, format='ISO8601')
        days = pd.date_range(ent.min().normalize(), ex.max().normalize(), freq='B', tz='UTC')
        open_count = np.array([int(((ent <= d) & (ex >= d)).sum()) for d in days[::5]])
        extra['lt_off_concurrency'] = dict(median_open=float(np.median(open_count)), p90_open=float(np.percentile(open_count, 90)), max_open=int(open_count.max()))
        res['confirm_1d_detail'] = extra
        show('lt_off by year (1d confirmation)', extra['lt_off_by_year'])
        show('lt_off by group', extra['lt_off_by_group'])
        show('t_off by direction', extra['t_off_by_direction'])
        show('baseline by period', extra['baseline_by_period']); show('lt_base by period', extra['lt_base_by_period'])
        print('lt_off twins mean R: context', extra['lt_off_context_twin_mean_r'], 'plain', extra['lt_off_plain_twin_mean_r'])
        print('\nS3 lt_off − lt_base mean R:', extra['S3_lt_off_minus_lt_base'])
        print('exits:', extra['lt_off_exits']); print('without top 5 symbols:', {k: extra['lt_off_without_top5_symbols'][k] for k in ('trades', 'mean_r', 'mean_r_ci')})
        print('concurrency:', extra['lt_off_concurrency'])
        p1 = rows_p1 = res[key]['1d']['lt_off']
        b = res[key]['1d']['baseline']
        gates = dict(
            g1_edge_interval_above_zero=p1['edge_context_ci'][0] > 0,
            g2_mean_r_above_zero=p1['mean_r_ci'][0] > 0,
            g3_sample=p1['trades'] >= 150 and p1['symbols'] >= 40,
            g4_most_years_positive=p1['positive_years'] * 2 > p1['years'],
            g5_drawdown_not_worse_than_baseline=p1['max_drawdown_r'] <= b['max_drawdown_r'],
            g6_live_shadow='open: cannot be met by a backtest')
        res['P1'] = dict(passed=bool(gates['g1_edge_interval_above_zero'] and gates['g2_mean_r_above_zero']), gates=gates)
        print('\nP1:', res['P1'])
json.dump(res, open(out_path, 'w'), indent=1, default=str)
