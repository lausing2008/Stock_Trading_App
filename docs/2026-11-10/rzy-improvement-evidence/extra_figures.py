"""Extra comparison figures quoted in the results document (confirmation set, 1d)."""
import json, sys
import numpy as np
from rzy_stats import load

def diff_months(a, b, n=2000, seed=11):
    months = sorted(set(a['month']) | set(b['month'])); idx = {m: i for i, m in enumerate(months)}; k = len(months)
    def agg(d):
        codes = d['month'].map(idx).values
        return np.bincount(codes, weights=d['r'].values, minlength=k), np.bincount(codes, minlength=k)
    sa, ca = agg(a); sb, cb = agg(b)
    pick = np.random.default_rng(seed).integers(0, k, size=(n, k))
    with np.errstate(invalid='ignore', divide='ignore'):
        d = sa[pick].sum(1) / ca[pick].sum(1) - sb[pick].sum(1) / cb[pick].sum(1)
    d = d[~np.isnan(d)]
    return round(float(a['r'].mean() - b['r'].mean()), 3), [round(float(x), 3) for x in np.percentile(d, [2.5, 97.5])]

df = load(sys.argv[1]); lt = df[df.variant == 'lt_off']; b = df[df.variant == 'baseline']; lb = df[df.variant == 'lt_base']
print('lt_off - baseline, all', diff_months(lt, b))
for lo, hi in ((2016, 2020), (2021, 2026)):
    f = lambda d: d[(d.year >= lo) & (d.year <= hi)]
    print(lo, hi, 'lt_off - baseline', diff_months(f(lt), f(b)), '| lt_off - lt_base', diff_months(f(lt), f(lb)))
print('lt_off_be1 exits', df[df.variant == 'lt_off_be1'].groupby('exit_reason')['r'].agg(['size', 'mean']).round(3).to_dict('index'))
print('lt_off trades per year', round(len(lt) / 10.85, 1), 'mean hold bars', round(lt.holding_bars.mean(), 1))
r = json.load(open(sys.argv[2]))
for tf, key in (('1d', 'confirm_1d'), ('1h', 'confirm_1h'), ('15m', 'confirm_intraday'), ('5m', 'confirm_intraday')):
    s = r[key][tf]['baseline']
    print(tf, 'baseline', s['trades'], 'win', round(s['win_rate'], 3), 'mean', round(s['mean_r'], 3), [round(x, 3) for x in s['mean_r_ci']],
          'edge', round(s['edge_plain'], 3), [round(x, 3) for x in s['edge_plain_ci']], 'cost', round(s['mean_cost_r'], 3), 'ret%', round(s['mean_return_pct'], 3))
print('baseline 1d by year', {k: (v['trades'], round(v['mean_r'], 3)) for k, v in r['confirm_1d_detail']['baseline_by_year'].items()})
