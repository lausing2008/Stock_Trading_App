"""Design-set gate combinations as SLICES of one variant's trades (exploratory)."""
import sys
import pandas as pd
from rzy_stats import load, summary, table, thirds, by
df = load(sys.argv[1]); variants = sys.argv[3].split(',')
COLS = ('group', 'trades', 'win', 'mean_r', 'mean_r_lo', 'mean_r_hi', 'edge', 'edge_lo', 'edge_hi', 'pf', 'total_r', 'max_dd_r')
Q = {
 'all': 'r == r',
 'long': "direction == 'long'",
 'short': "direction == 'short'",
 'with d_sma200': 'd_sma200 == True',
 'against d_sma200': 'd_sma200 == False',
 'long & d_sma200': "direction == 'long' and d_sma200 == True",
 'short & d_sma200': "direction == 'short' and d_sma200 == True",
 'd_sma200 & d_mom126>0': 'd_sma200 == True and d_mom126 > 0',
 'd_sma200 & d_mom126>0.10': 'd_sma200 == True and d_mom126 > 0.10',
 'd_sma200 & rs63>0': 'd_sma200 == True and rs63 > 0',
 'd_sma200 & regime': 'd_sma200 == True and regime == True',
 'd_sma200 & d_mom126>0 & regime': 'd_sma200 == True and d_mom126 > 0 and regime == True',
 'd_sma200 & d_mom126>0 & rs63>0': 'd_sma200 == True and d_mom126 > 0 and rs63 > 0',
 'd_sma200 & vol_ratio<1': 'd_sma200 == True and vol_ratio < 1',
 'd_sma200 & d_mom126>0 & vol_ratio<1': 'd_sma200 == True and d_mom126 > 0 and vol_ratio < 1',
 'long & d_sma200 & d_mom126>0': "direction == 'long' and d_sma200 == True and d_mom126 > 0",
 'long & d_sma200 & d_mom126>0 & regime': "direction == 'long' and d_sma200 == True and d_mom126 > 0 and regime == True",
 'htf aligned & d_sma200': "htf == 'aligned' and d_sma200 == True",
 'regime': 'regime == True', 'not regime': 'regime == False',
 'vol_ratio<1': 'vol_ratio < 1', 'vol_ratio<0.8': 'vol_ratio < 0.8',
}
for tf in sys.argv[2].split(','):
    for v in variants:
        g = df[(df.timeframe == tf) & (df.variant == v)]
        print(f'\n######## {tf} {v}')
        print(table([summary(g.query(q), name) for name, q in Q.items()], COLS))
        if len(sys.argv) > 4:
            for num in ('d_mom126', 'rs63'):
                print(table(thirds(g, num), COLS))
            print(table(by(g, 'd_sma200'), COLS)); print(table(by(g, 'd_sma50'), COLS))
