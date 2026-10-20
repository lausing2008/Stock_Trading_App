from pathlib import Path
exec(Path(__file__).with_name('pure_probe.py').read_text().split('cfg=Config')[0])
from datetime import datetime,timedelta
out={}
e,bars,st=f.triggered(fill=False)
b=f.candle(len(bars));line=e.line(st,len(bars));b.update(open=line+.8,close=line+1,low=line+.3,high=line+1.2)
b['execution_eligible']='false'
ev=e.step(b)
out['string_false_execution_gate']={'input':b['execution_eligible'],'paper_fill':any(x['event_type']=='PAPER_FILL' for x in ev)}
e,bars,st=f.triggered(fill=False)
b=f.candle(len(bars));line=e.line(st,len(bars));b.update(open=line+.8,close=line+1,low=line+.3,high=line+1.2)
for k in ('time','closed_at'):b[k]=(datetime.fromisoformat(b[k])+timedelta(minutes=45)).isoformat()
ev=e.step(b)
out['missing_entry_bars']={'minutes_between_trigger_close_and_entry_open':45,'paper_fill':any(x['event_type']=='PAPER_FILL' for x in ev),'entry_cancelled':any(x['event_type']=='ENTRY_CANCELLED' for x in ev)}
print(json.dumps(out,indent=2))
