"""Exploratory: structure features of every baseline trade (same snapshot as run 2026-11-08-16fe5a81)."""
import sys, json, csv, random, importlib.util
from statistics import mean
REPO='/home/lausing/Documents/MyProjects/Stock_Trading_App'
sys.path[:0]=[REPO+'/services/market-data', REPO+'/shared']
spec=importlib.util.spec_from_file_location('ev', REPO+'/scripts/evaluate_little_rzy.py'); ev=importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
from src.services.rzy_evaluation import research_bars, run_segment, matched_controls
from src.services.little_rzy import Config
meta, streams = ev.load_snapshot(sys.argv[1])
cfg=Config(); rng=random.Random('7:baseline'); out=[]
for sym, tf, segs in streams:
    for seg in segs:
        seg=research_bars(seg)
        engine, trades, _, _ = run_segment(sym, tf, seg, cfg)
        inds=engine.s['indicators']
        for t in trades:
            ctrl=matched_controls(t, seg, inds, cfg, rng, 20)
            st=next(s for s in engine.s['structures'].values() if s.get('state') in ('COMPLETED','INVALIDATED') and engine.s['positions'].get(s['id'],{}).get('entry_index')==t['entry_index'])
            a,b,c=st['anchors']; sign=1 if st['direction']=='long' else -1
            atr_b=inds[b['index']]['atr']; atr_t=inds[t['trigger_index']]['atr']
            imp=sign*(b['price']-a['price']); ret=sign*(b['price']-c['price'])/imp
            vol=lambda i,j: mean(seg[k]['volume'] for k in range(i,j+1)) if j>=i else None
            vi,vp=vol(a['index'],b['index']),vol(b['index']+1,c['index'])
            slope=sign*(c['price']-a['price'])/(c['index']-a['index'])/atr_t
            ctx=st['context_at_C']; pb=ctx.get('percent_b')
            tr=t['trigger_index']; ind_t=inds[tr]
            out.append(dict(symbol=sym,timeframe=tf,direction=st['direction'],signal_time=t['signal_time'],r=t['r'],
                edge=(t['r']-mean(ctrl)) if ctrl else None, mfe=t['mfe_r'], cost_r=t['cost_r'], exit=t['exit_reason'], hold=t['holding_bars'],
                stop_atr=t['stop_atr'], target_atr=t['target_atr'], impulse_atr=imp/atr_b if atr_b else None, retrace=ret,
                vol_ratio=(vp/vi) if vi and vp is not None else None, slope_atr_per_bar=slope,
                pctb_c=(pb if sign==1 else (1-pb if pb is not None else None)),
                bars_c_to_trigger=tr-c['index'], bars_a_to_c=c['index']-a['index'],
                pctb_trigger=(ind_t['percent_b'] if sign==1 else (1-ind_t['percent_b'] if ind_t['percent_b'] is not None else None)),
                bandwidth_t=ind_t['bandwidth'], price=t['fill'], seq=st['sequence_number'], seqconf=st['sequence_confidence']))
w=csv.DictWriter(open(sys.argv[2],'w',newline=''),fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
print(len(out))
