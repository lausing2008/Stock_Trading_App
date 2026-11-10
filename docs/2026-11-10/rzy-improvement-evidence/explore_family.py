"""Design-set table of a variant family with both kinds of twin, and one variant by year."""
import sys
import numpy as np
from rzy_stats import load, summary, table, by, cluster_ci
df = load(sys.argv[1]); focus = sys.argv[2]
df['edge_ctx'] = df['r'] - df['control_ctx_mean_r']
rows = []
for name, g in df.groupby('variant', sort=False):
    s = summary(g, name)
    a = cluster_ci(g['edge_ctx'].values, g['symbol'].values); b = cluster_ci(g['edge_ctx'].values, g['month'].values)
    s.update(edge_ctx=float(np.nanmean(g['edge_ctx'])), ctx_lo=min(a[0], b[0]), ctx_hi=max(a[1], b[1]))
    rows.append(s)
print(table(rows, ('group', 'trades', 'win', 'mean_r', 'mean_r_lo', 'mean_r_hi', 'edge', 'edge_lo', 'edge_hi', 'edge_ctx', 'ctx_lo', 'ctx_hi', 'pf', 'hold', 'total_r', 'max_dd_r')))
print(f'\n{focus} by year')
print(table(by(df[df.variant == focus], 'year'), ('group', 'trades', 'win', 'mean_r', 'edge', 'pf', 'total_r')))
