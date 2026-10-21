"""Audit only: real packet builder and ORM, disposable SQLite, no model/network calls."""
import os, pathlib, json
if os.environ.get('STOCKAI_INTEL_DB'): raise SystemExit('Unset STOCKAI_INTEL_DB; disposable SQLite only')
root=pathlib.Path(__file__).resolve().parents[3]
p=root/'services/research-engine/tests/_copilot_plan_probe.py'
ns={'__file__':str(p),'__name__':'audit_fixture'}
exec(compile(p.read_text().split('class ScriptedModel:')[0],str(p),'exec'),ns)
reset,pkt,Session,Price,datetime=(ns[k] for k in ('reset','pkt','Session','Price','datetime'))
out={}
reset(cohort=(),own=())
with Session() as s:
    sid=s.get(Price,1).stock_id
    s.add(Price(id=9999,stock_id=sid,ts=datetime(2026,10,9,0),timeframe=ns['TimeFrame'].D1,
        open=100,high=103,low=99,close=102,volume=1500000));s.commit()
    packet=ns['build_packet'](s,ns['SYM'],now=datetime(2026,10,9,15),plan_request=ns['I'].PositionIntent(kind='entry'))
    out['unfinished_daily_bar']={'evaluation_utc':'2026-10-09T15:00:00','session_close_utc':'2026-10-09T20:00:00',
      'price':packet.fields['price'].value,'price_state':packet.fields['price'].state.value,
      'plan_price_as_of':packet.plan.to_json()['price_as_of'],'setup_states':[x['state'] for x in packet.plan.to_json()['setups']]}
reset(bar_age_days=4,cohort=(),own=())
p= pkt(kind='entry')
out['four_day_old_close']={'price_state':p.fields['price'].state.value,'price_as_of':p.plan.as_of,'states':[x['state'] for x in p.plan.to_json()['setups']]}
reset(cohort=(),own=())
p=pkt(kind='entry',risk_amount=500,account=1000)
out['conflicting_sizing']={'rule':p.plan.items['rule.e6'].display,'setups':[{k:s[k] for k in ('id','shares','capital','state','risk_note')} for s in p.plan.setups]}
s=p.plan.setups[1]
out['breakout_reference']={'entry':s['entry'],'stop':s['stop'],'first_target':s['targets'][0], 'playbook':s['playbook'][0]['text']}
reset(last_close=90,cohort=(),own=())
p=pkt(kind='entry')
out['blocked_playbook']=[{k:s[k] for k in ('id','state','reasons','playbook')} for s in p.plan.to_json()['setups']]
reset(bar_age_days=12,cohort=(),own=())
p=pkt(kind='entry')
out['stale_playbook']=[{'id':s['id'],'state':s['state'],'playbook_count':len(s['playbook'])} for s in p.plan.to_json()['setups']]
print(json.dumps(out,indent=2))
