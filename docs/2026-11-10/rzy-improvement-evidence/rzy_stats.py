"""Shared statistics for the RZY improvement study (pandas/numpy; research only).

edge = trade R minus the mean R of its 20 random-timing twins. Intervals resample whole
clusters: symbols (a symbol's trades are dependent) and, separately, calendar months (trades in
one month share the market). The wider of the two is the one to trust.
"""
import numpy as np
import pandas as pd


def load(path, variant=None):
    df = pd.read_csv(path, low_memory=False)
    if variant:
        df = df[df['variant'] == variant]
    df = df.copy()
    df['signal_time'] = pd.to_datetime(df['signal_time'], utc=True, format='ISO8601')
    df['month'] = df['signal_time'].dt.strftime('%Y-%m')
    df['year'] = df['signal_time'].dt.year
    df['edge'] = df['r'] - df['control_mean_r']
    return df


def cluster_ci(values, clusters, n=2000, seed=11):
    """95% interval of the mean, resampling whole clusters."""
    values = np.asarray(values, float); ok = ~np.isnan(values)
    values = values[ok]; codes, _ = pd.factorize(np.asarray(clusters)[ok])
    k = codes.max() + 1 if len(codes) else 0
    if k < 2:
        return (np.nan, np.nan)
    sums = np.bincount(codes, weights=values, minlength=k); counts = np.bincount(codes, minlength=k)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, k, size=(n, k))
    means = sums[pick].sum(1) / counts[pick].sum(1)
    return tuple(np.percentile(means, [2.5, 97.5]))


def summary(df, label=''):
    n = len(df)
    if not n:
        return dict(group=label, trades=0)
    r = df['r'].values; wins = r[r > 0]; losses = r[r <= 0]
    curve = np.cumsum(df.sort_values('exit_time')['r'].values)
    dd = float(np.max(np.maximum.accumulate(np.maximum(curve, 0)) - curve)) if n else 0.
    e = df['edge'].values
    lo_s, hi_s = cluster_ci(e, df['symbol'].values); lo_m, hi_m = cluster_ci(e, df['month'].values)
    rlo, rhi = cluster_ci(r, df['month'].values)
    return dict(group=label, trades=n, symbols=df['symbol'].nunique(), win=float((r > 0).mean()), mean_r=float(r.mean()),
                mean_r_lo=rlo, mean_r_hi=rhi, median_r=float(np.median(r)),
                pf=float(wins.sum() / -losses.sum()) if losses.sum() < 0 else np.nan,
                avg_win=float(wins.mean()) if len(wins) else np.nan, avg_loss=float(losses.mean()) if len(losses) else np.nan,
                edge=float(np.nanmean(e)), edge_lo=min(lo_s, lo_m), edge_hi=max(hi_s, hi_m),
                total_r=float(r.sum()), max_dd_r=dd, hold=float(df['holding_bars'].median()),
                target_rate=float((df['exit_reason'] == 'target').mean()), cost_r=float(df['cost_r'].mean()))


def table(rows, cols=('group', 'trades', 'win', 'mean_r', 'edge', 'edge_lo', 'edge_hi', 'pf', 'total_r', 'max_dd_r')):
    out = pd.DataFrame(rows)
    cols = [c for c in cols if c in out.columns]
    return out[cols].to_string(index=False, float_format=lambda x: f'{x:+.3f}')


def by(df, key, min_n=1):
    return [summary(g, str(k)) for k, g in df.groupby(key, dropna=False) if len(g) >= min_n]


def thirds(df, col):
    """Split by the column's own terciles (descriptive)."""
    d = df[df[col].notna()]
    if len(d) < 30:
        return []
    q = d[col].quantile([1 / 3, 2 / 3]).values
    lab = np.where(d[col] < q[0], f'low(<{q[0]:.3g})', np.where(d[col] < q[1], 'mid', f'high(>={q[1]:.3g})'))
    return [summary(g, f'{col}:{k}') for k, g in d.groupby(lab)]


def diff_ci(a, b, n=2000, seed=11):
    """Mean edge of a minus mean edge of b, resampling calendar months (both groups together)."""
    months = sorted(set(a['month']) | set(b['month']))
    idx = {m: i for i, m in enumerate(months)}; k = len(months)
    def agg(d):
        codes = d['month'].map(idx).values; e = d['edge'].fillna(0).values; ok = d['edge'].notna().values
        return np.bincount(codes[ok], weights=e[ok], minlength=k), np.bincount(codes[ok], minlength=k)
    sa, ca = agg(a); sb, cb = agg(b)
    rng = np.random.default_rng(seed); pick = rng.integers(0, k, size=(n, k))
    with np.errstate(invalid='ignore', divide='ignore'):
        d = sa[pick].sum(1) / ca[pick].sum(1) - sb[pick].sum(1) / cb[pick].sum(1)
    d = d[~np.isnan(d)]
    return float(a['edge'].mean() - b['edge'].mean()), tuple(np.percentile(d, [2.5, 97.5]))
