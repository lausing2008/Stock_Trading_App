"""Compare variants on one set of trades. Usage: explore_variants.py <trades.csv> <tfs> [query]
`query` is a pandas query applied to every variant (for example "direction == 'long'")."""
import sys
import pandas as pd
from rzy_stats import load, summary, table
df = load(sys.argv[1])
if len(sys.argv) > 3:
    df = df.query(sys.argv[3])
COLS = ('group', 'trades', 'win', 'mean_r', 'mean_r_lo', 'mean_r_hi', 'edge', 'edge_lo', 'edge_hi', 'pf', 'avg_win', 'avg_loss', 'hold', 'total_r', 'max_dd_r')
for tf in sys.argv[2].split(','):
    g = df[df.timeframe == tf]
    print(f'\n######## {tf}')
    print(table([summary(v, name) for name, v in g.groupby('variant', sort=False)], COLS))
