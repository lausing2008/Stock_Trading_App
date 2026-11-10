"""POST-HOC, exploratory (run after the confirmation results were read; not a predeclared test).
Does a market filter change the daily long-trend variant? SPY's last close at the signal vs its
200-day and 50-day averages, on the design and confirmation sets separately."""
import json, sys
import numpy as np, pandas as pd
from rzy_stats import load, summary, table
market = json.load(open(sys.argv[1]))['closes']
spy = pd.DataFrame(market, columns=['t', 'close']); spy['t'] = pd.to_datetime(spy['t'], utc=True, format='ISO8601')
spy['above200'] = spy['close'] > spy['close'].rolling(200).mean(); spy['above50'] = spy['close'] > spy['close'].rolling(50).mean()
spy.loc[spy.index < 199, 'above200'] = np.nan
COLS = ('group', 'trades', 'win', 'mean_r', 'mean_r_lo', 'mean_r_hi', 'edge', 'edge_lo', 'edge_hi', 'pf', 'total_r', 'max_dd_r')
for label, path in (('design 2005-2015', sys.argv[2]), ('confirmation 2016-2026', sys.argv[3])):
    df = load(path, 'lt_off'); df = df[df.timeframe == '1d'].sort_values('signal_time')
    df = pd.merge_asof(df, spy[['t', 'above200', 'above50']], left_on='signal_time', right_on='t')
    df['edge'] = df['r'] - df['control_ctx_mean_r']   # context-matched twins
    print(f'\n== lt_off, {label} (edge = vs context-matched twins)')
    print(table([summary(df, 'all'), summary(df[df.above200 == True], 'SPY above 200-day'), summary(df[df.above200 == False], 'SPY below 200-day'),
                 summary(df[df.above50 == True], 'SPY above 50-day'), summary(df[df.above50 == False], 'SPY below 50-day')], COLS))
