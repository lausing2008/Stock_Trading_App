"""Does the lead's edge over the preset equal its smaller round-trip spread? Paired, holding exit,
entries where lead != preset. Prints per strategy x horizon x period: pairs, mean ROC diff,
mean entry spread_pct diff, and the ROC diff after adding back half the entry spread difference
(the entry-side cost) -- a rough split, not a model."""
import json, sys, statistics
from collections import defaultdict
P=defaultdict(dict)
for l in open(sys.argv[1]):
    t=json.loads(l)
    if t['exit_type']!='holding' or t['arm'] not in ('lead','preset') or t['horizon']=='GROWTH': continue
    P[(t['symbol'],t['entry'],t['strategy'],t['horizon'])][t['arm']]=t
agg=defaultdict(list)
for k,a in P.items():
    if len(a)<2 or a['lead']['option_symbol']==a['preset']['option_symbol']: continue
    L,R=a['lead'],a['preset']
    agg[(k[2],k[3],L['period'])].append((L['roc']-R['roc'],(L['spread_pct']-R['spread_pct'])/100,
        (L['strike']-R['strike'])/L['stock_ref'] if L.get('stock_ref') else None, L['expiry']!=R['expiry']))
out=[]
for k,v in sorted(agg.items()):
    d=[x[0] for x in v]; s=[x[1] for x in v]; m=[x[2] for x in v if x[2] is not None]
    out.append(dict(strategy=k[0],horizon=k[1],period=k[2],differing_pairs=len(v),mean_roc_diff=statistics.fmean(d),
        median_roc_diff=statistics.median(d),mean_spread_diff=statistics.fmean(s),
        mean_strike_gap_pct_of_spot=statistics.fmean(m) if m else None,share_different_expiry=sum(x[3] for x in v)/len(v)))
    o=out[-1]
    print(f"{o['strategy'][:12]:12} {o['horizon']:5} {o['period']:11} pairs={o['differing_pairs']:5} roc_diff={o['mean_roc_diff']*100:+6.2f} med={o['median_roc_diff']*100:+6.2f} spread_diff={o['mean_spread_diff']*100:+6.2f}pp strike_gap={o['mean_strike_gap_pct_of_spot']*100:+5.2f}% other_expiry={o['share_different_expiry']*100:4.0f}%")
json.dump(out,open(sys.argv[2],'w'),indent=1)
