"""Design-set feature slices of baseline trades (exploratory; chooses what to predeclare)."""
import sys
import pandas as pd
from rzy_stats import load, summary, table, by, thirds
from split import assign
df = load(sys.argv[1], 'baseline'); df['set'] = assign(df)
d = df[df['set'] == 'design']
COLS = ('group', 'trades', 'win', 'mean_r', 'edge', 'edge_lo', 'edge_hi')
for tf in sys.argv[2].split(','):
    g = d[d.timeframe == tf]
    print(f'\n######## {tf}  ({len(g)} design trades)')
    for cat in ('htf', 'regime', 'sma50', 'sma200', 'direction', 'tod', 'sequence'):
        print(f'-- {cat}'); print(table(by(g, cat, 20), COLS))
    for num in ('vol_ratio', 'trigger_vol_ratio', 'slope_atr', 'impulse_atr', 'retrace', 'stop_atr_plan', 'rr_plan', 'd_atr',
                'cost_r_plan', 'price', 'bars_ac', 'bars_ct', 'ext_mid_atr', 'outer_band_c', 'mom20', 'mom60', 'mom120', 'rs63', 'atr_pct'):
        rows = thirds(g, num)
        if rows: print(table(rows, COLS))
