import sys,importlib.util,json
from pathlib import Path
from dataclasses import replace
root=Path(__file__).resolve().parents[3];sys.path.insert(0,str(root/'services/market-data'))
spec=importlib.util.spec_from_file_location('fixtures',root/'services/market-data/tests/test_little_rzy.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
from src.services import rzy_backtest as B
from src.services.little_rzy import Config,Engine
cfg=Config(bb_length=5,atr_length=3,pivot_left=1,pivot_right=1,bollinger_context=False,no_chase_filter=False,sequence_filter=False,min_impulse_atr=0,min_rr=.01,max_armed_bars=100)
bars=f._random_stream(0,26000)
r=B.run('AUDIT','5m',bars,cfg)
closed=[e for e in r['events'] if e['event_type']=='PAPER_EXIT' and e['paper_state']=='CLOSED']
class Unbounded(Engine): KEEP_CLOSED_POSITIONS=10**9;KEEP_STRUCTURES=10**9
original=B.Engine;B.Engine=Unbounded
full=B.run('AUDIT','5m',bars,cfg);B.Engine=original
out={'backtest_retention':{'bars':len(bars),'closed_trade_events':len(closed),'reported_trades':r['metrics']['trade_count'],'unbounded_reported_trades':full['metrics']['trade_count'],'bounded_drawdown':r['metrics']['maximum_mark_to_market_drawdown_per_unit'],'unbounded_drawdown':full['metrics']['maximum_mark_to_market_drawdown_per_unit'],'identical_events':r['events']==full['events']}}
# True completed source is fresh, but all later aggregate bins miss constituents.
spec=importlib.util.spec_from_file_location('agg',root/'services/market-data/tests/test_rzy_aggregation.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
from src.services.rzy_data import aggregate_completed_bars
from datetime import datetime,timezone
src,_,_=g.source(); src=src[:6]+src[6::3]+[src[-1]]
result,ev=aggregate_completed_bars(src,{'status':'research_only','stale':False,'data_as_of':'2026-10-16T20:00:00+00:00'},'15m',datetime(2026,10,16,20,5,tzinfo=timezone.utc),session_bounds=g.calendar.session_bounds)
out['aggregate_staleness']={'latest_source':src[-1]['closed_at'],'latest_aggregate':result[-1]['closed_at'],'status':ev['status'],'stale':ev['stale'],'missing_bins':len(ev['gaps'])}
print(json.dumps(out,indent=2))
