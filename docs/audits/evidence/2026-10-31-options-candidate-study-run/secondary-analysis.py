"""Secondary analysis of trades.jsonl (options candidate study, 2026-10-31 run). Read-only.
Usage: python analyse.py TRADES SUMMARY OUT_JSON"""
import json, random, statistics, sys
from collections import defaultdict
trades=[json.loads(l) for l in open(sys.argv[1])]
summary=json.load(open(sys.argv[2]))
def ci(by_date, seed=1, B=2000):
    dates=list(by_date)
    if len(dates)<2: return None
    rng=random.Random(seed); out=[]
    for _ in range(B):
        vals=[v for d in (rng.choice(dates) for _ in dates) for v in by_date[d]]
        out.append(statistics.fmean(vals))
    out.sort(); return [out[int(.025*B)], out[int(.975*B)-1]]
res={"n_trades":len(trades)}
# 1. reproduce one summary group exactly (n, mean)
g0=next(g for g in summary['groups'] if g['earnings']=='all' and g['period']=='held_out' and g['strategy']=='long_call' and g['horizon']=='SHORT' and g['exit']=='holding' and g['arm']=='lead')
mine=[t['roc'] for t in trades if t['period']=='held_out' and t['strategy']=='long_call' and t['horizon']=='SHORT' and t['exit_type']=='holding' and t['arm']=='lead']
res['reproduce']={"summary_n":g0['n'],"summary_mean":g0['mean_roc'],"mine_n":len(mine),"mine_mean":statistics.fmean(mine)}
# 2. arms vs stock alone, per strategy x horizon(SHORT,SWING,LONG) x exit x period, all earnings
cells=defaultdict(list)
for t in trades:
    if t['horizon']=='GROWTH': continue
    cells[(t['strategy'],t['horizon'],t['exit_type'],t['period'],t['arm'])].append(t)
table=[]
for k,ts in sorted(cells.items()):
    by=defaultdict(list); sb=defaultdict(list)
    for t in ts:
        by[t['entry']].append(t['roc'])
        if t.get('stock_return') is not None: sb[t['entry']].append(t['stock_return'])
    rocs=[t['roc'] for t in ts]; sr=[t['stock_return'] for t in ts if t.get('stock_return') is not None]
    table.append({"strategy":k[0],"horizon":k[1],"exit":k[2],"period":k[3],"arm":k[4],"n":len(ts),"dates":len(by),
        "mean_roc":statistics.fmean(rocs),"ci95":ci(by),"median_roc":statistics.median(rocs),
        "win_rate":sum(r>0 for r in rocs)/len(rocs),"worst":min(rocs),
        "stock_mean":statistics.fmean(sr) if sr else None,"stock_ci95":ci(sb) if sr else None})
res['arms']=table
# 3. how often lead == preset contract (same symbol, entry, strategy, horizon, exit)
idx=defaultdict(dict)
for t in trades:
    idx[(t['symbol'],t['entry'],t['strategy'],t['horizon'],t['exit_type'])][t['arm']]=t['option_symbol']
same=defaultdict(lambda:[0,0])
for k,a in idx.items():
    if 'lead' in a and 'preset' in a:
        same[k[2]][1]+=1; same[k[2]][0]+= a['lead']==a['preset']
res['lead_is_preset']={s:{"same":v[0],"pairs":v[1],"share":v[0]/v[1]} for s,v in same.items()}
# 4. monthly, holding exit: arm mean roc + stock mean, per strategy x horizon
mon=defaultdict(list)
for t in trades:
    if t['exit_type']=='holding' and t['horizon']!='GROWTH':
        mon[(t['strategy'],t['horizon'],t['entry'][:7],t['arm'])].append(t)
res['monthly_holding']=[{"strategy":k[0],"horizon":k[1],"month":k[2],"arm":k[3],"n":len(v),
    "mean_roc":statistics.fmean(x['roc'] for x in v),
    "stock_mean":statistics.fmean([x['stock_return'] for x in v if x.get('stock_return') is not None] or [float('nan')])}
    for k,v in sorted(mon.items())]
# 5. monthly paired lead-minus-preset (holding), count of months positive
pair=defaultdict(dict)
for t in trades:
    if t['exit_type']=='holding' and t['arm'] in ('lead','preset') and t['horizon']!='GROWTH':
        pair[(t['symbol'],t['entry'],t['strategy'],t['horizon'])][t['arm']]=t['roc']
pm=defaultdict(list)
for k,a in pair.items():
    if len(a)==2: pm[(k[2],k[3],k[1][:7])].append(a['lead']-a['preset'])
res['monthly_lead_minus_preset_holding']=[{"strategy":k[0],"horizon":k[1],"month":k[2],"pairs":len(v),"mean_diff":statistics.fmean(v)} for k,v in sorted(pm.items())]
json.dump(res,open(sys.argv[3],'w'),indent=1,default=str)
print(json.dumps(res['reproduce']), json.dumps(res['lead_is_preset']))
