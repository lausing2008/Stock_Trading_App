"""Design-set description of the baseline on the independent data. Reads DESIGN trades only."""
import sys
import pandas as pd
from rzy_stats import load, summary, table, by, thirds
from split import assign
df = load(sys.argv[1], 'baseline'); df['set'] = assign(df)
print(df.groupby(['timeframe', 'set']).size().unstack(fill_value=0))
d = df[df['set'] == 'design']
print('\n== DESIGN baseline by timeframe'); print(table(by(d, 'timeframe')))
print('\n== by timeframe x direction'); print(table(by(d, ['timeframe', 'direction'])))
print('\n== exits'); print(d.groupby(['timeframe', 'exit_reason'])['r'].agg(['size', 'mean']).round(3))
print('\n== 1d by year'); print(table(by(d[d.timeframe == '1d'], 'year')))
print('\n== 1h by month'); print(table(by(d[d.timeframe == '1h'], 'month')))
