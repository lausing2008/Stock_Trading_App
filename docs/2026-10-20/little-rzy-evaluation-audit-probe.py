"""Local-only reproductions for evaluation-control defects; no DB, provider or network."""
import json
import sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'services/market-data'))
from src.services.little_rzy import Config
from src.services.rzy_evaluation import simulate_control,matched_controls,ASSUMPTIONS

cfg=Config(commission_per_share=0,spread_bps=0,slippage_bps=0)
start=datetime(2026,1,5,14,30,tzinfo=timezone.utc)
bars=[dict(time=(start+timedelta(minutes=5*i)).isoformat(),closed_at=(start+timedelta(minutes=5*(i+1))).isoformat(),open=100.,high=100.1,low=99.9,close=100.) for i in range(6)]
inds=[{'atr':1.} for _ in bars]
bars[1]['high']=102.
actual=simulate_control(bars,inds,0,'short',1.,2.,5,cfg)
rate=ASSUMPTIONS['borrow_rate_annual'];year=365*86400
expected=-1.-100*rate*300/year
out={'short_borrow_exit_time':{'holding_cap_bars':5,'actual_stop_exit_bar':1,'actual_seconds_held':300,'charged_seconds':1500,'reported_r':actual,'r_using_actual_exit_time':expected,'difference_r':actual-expected}}
# Last entry has one bar available, despite the requested ten-bar holding window.
bars[5].update(close=100.2,high=100.3)
class LastSample:
 def sample(self,items,k): return items[-k:]
controls=matched_controls({'holding_bars':10,'trigger_index':0,'direction':'long','stop_atr':1.,'target_atr':2.},bars,inds,cfg,LastSample(),k=1)
out['truncated_control_window']={'requested_holding_bars':10,'available_holding_bars':1,'returned_controls':controls,'last_control_trigger_index':4}
assert actual<expected and len(controls)==1
print(json.dumps(out,indent=2))
