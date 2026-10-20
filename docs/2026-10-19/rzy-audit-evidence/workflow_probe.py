from pathlib import Path
import json,importlib.util
import os
if os.environ.get('RZY_PG_URL'): raise SystemExit('Unset RZY_PG_URL: this audit probe permits disposable SQLite only')
probe=Path(__file__).resolve().parents[3]/'services/market-data/tests/_little_rzy_probe.py'
ns={'__file__':str(probe),'__name__':'audit_fixture'}
exec(compile(probe.read_text().split('    # Real ORM contains only')[0],str(probe),'exec'),ns)
W,Session,models,RzyEvent,RzyStream,select,now,base=(ns[k] for k in ('W','Session','models','RzyEvent','RzyStream','select','now','base'))
from datetime import timedelta
out={}
with Session() as s:
 old=ns['a']['structures'][-1]
 original=s.scalar(select(RzyEvent).where(RzyEvent.event_type=='ARMED',RzyEvent.symbol=='TEST'))
 changed=s.get(models.Price,old['extreme']['index']+1);changed.high+=.2;s.commit()
 rebuilt=W.evaluate(s,'TEST','5m',now+timedelta(minutes=1),persist=True);s.commit()
 current=next(x for x in rebuilt['structures'] if x['id']==old['id'])
 read=W.evaluate(s,'TEST','5m',now+timedelta(minutes=1))
 returned=next(e for e in read['events'] if e['event_id']==original.event_id)
 out['generation_provenance']={'rebuilt':rebuilt['rebuilt'],'generation':rebuilt['generation'],'old_target':old['target'],'current_target':current['target'],'returned_armed_event_target':returned['primary_target'],'returned_event_generation':returned.get('generation'),'rebuild_new_events':len(rebuilt['new_events'])}
 before,_=W.load_bars(s,'TEST','15m',now)
 s.add(models.Price(id=900,stock_id=1,ts=base.replace(tzinfo=None),timeframe=models.TimeFrame.M15,open=100,high=102,low=99,close=101,volume=1000,session='REGULAR'));s.commit()
 after,evidence=W.load_bars(s,'TEST','15m',now)
 out['stale_native_disables_fallback']={'derived_before':len(before),'native_after':len(after),'status_after':evidence['status'],'data_as_of_after':evidence['data_as_of']}
# Short entries with actual standing blockers still generate emailable cancellation.
root=probe.parents[3]
spec=importlib.util.spec_from_file_location('fixtures',root/'services/market-data/tests/test_little_rzy.py');f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
_,bars,_=f.triggered();mirror=[dict(b,open=300-b['open'],close=300-b['close'],high=300-b['low'],low=300-b['high']) for b in bars]
mirror[-1].update(execution_eligible=False,shortable=None,borrow_rate_annual=None)
_,events=f.replay('TEST','5m',mirror)
cancel=next(e for e in events if e['event_type']=='ENTRY_CANCELLED')
out['standing_short_cancel']={'failed_rules':cancel['failed_rules'],'emailable':W.emailable(cancel,['provider_delay_unverified','borrow_availability_not_stored','execution_basis_unverified'])}
print(json.dumps(out,indent=2))
