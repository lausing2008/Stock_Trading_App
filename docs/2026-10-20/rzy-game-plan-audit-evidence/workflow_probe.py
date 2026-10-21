"""Real sender and persistence against disposable SQLite; fake mail sink only."""
import os,pathlib,json
if os.environ.get('RZY_PG_URL'): raise SystemExit('Unset RZY_PG_URL; disposable SQLite only')
root=pathlib.Path(__file__).resolve().parents[3]
p=root/'services/market-data/tests/_little_rzy_probe.py'
ns={'__file__':str(p),'__name__':'audit_fixture'}
exec(compile(p.read_text().split('result={}')[0],str(p),'exec'),ns)
W,Session,models,RzyEvent,RzyStream,RzyPreference,base=(ns[k] for k in ('W','Session','models','RzyEvent','RzyStream','RzyPreference','base'))
from datetime import timedelta
from src.services.little_rzy import generation_event_id
from src.services.rzy_models import RzyStreamSnapshot
from common.outbox import enqueue
from sqlalchemy import select,delete
out={}
with Session() as s:
    s.add_all([models.AlertPreference(user_id=1,alert_type='little_rzy',enabled=True),RzyPreference(user_id=1,timeframes=['5m'],updated_at=base.replace(tzinfo=None))]);s.commit()
    cfg=W.current_config(); key=f'TEST:5m:{cfg.hash}'
    for gen in (128,129):
        occurrence=f'audit-occurrence-{gen}'
        eid=generation_event_id(occurrence,gen)
        payload=dict(config_hash=cfg.hash,generation=gen,occurrence_id=occurrence,planned_entry=100,stop=99,primary_target=103,D=2,reward_risk=3,event_type='TRIGGERED',direction='long')
        s.merge(RzyStream(stream_key=key,symbol='TEST',timeframe='5m',config_hash=cfg.hash,checkpoint={'generation':gen},updated_at=base.replace(tzinfo=None)))
        s.add(RzyEvent(event_id=eid,symbol='TEST',timeframe='5m',event_type='TRIGGERED',payload=payload,created_at=base.replace(tzinfo=None)));s.commit()
        enqueue(s,event_id=f'rzy:1:{occurrence}',user_id=1,recipient='owner@example.invalid',alert_type='little_rzy',subject='audit',body_text='audit',body_html='audit',expires_at=(base+timedelta(minutes=5)).replace(tzinfo=None));s.commit()
        sent=[]
        W.drain(s,lambda *a: sent.append(a) or True,base);s.commit()
        row=s.scalar(select(models.NotificationOutbox).where(models.NotificationOutbox.event_id==f'rzy:1:{occurrence}'))
        out[f'generation_{gen}']={'sender_calls':len(sent),'outbox_state':row.state,'terminal_reason':row.terminal_reason}
    candidate=dict(symbol='TEST',timeframe='5m',direction='long',structure_id='old',sequence=1,sequence_confidence='known',data_as_of=base.isoformat())
    s.add(RzyStreamSnapshot(stream_key=key,symbol='TEST',timeframe='5m',config_hash=cfg.hash,checked_at=base.replace(tzinfo=None),status='research_only',summary={'candidates':[candidate],'exclusion':None}));s.commit()
    snap=W.digest_snapshot(s,1,base+timedelta(days=7))
    out['week_old_snapshot']={k:snap[k] for k in ('available','snapshot_age_minutes','total_candidates','candidates','exclusions')}
    from src.services.rzy_models import RzyStreamGeneration
    for g in range(1,9):
        s.add(RzyStreamGeneration(stream_key=key,generation=g,reason='stored_completed_bar_changed',detail={},archived_at=(base+timedelta(minutes=30*g)).replace(tzinfo=None)))
    for g in range(1,10):
        s.add(RzyEvent(event_id=f'prune-probe-{g}',symbol='TEST',timeframe='5m',event_type='TRIGGERED',payload={'generation':g,'config_hash':cfg.hash},created_at=(base+timedelta(minutes=30*g)).replace(tzinfo=None)))
    s.commit()
    W._prune_generations(s,key,'TEST','5m',cfg.hash,9);s.commit()
    out['within_session_retention']={'rebuilds_created':8,'archives_remaining':len(s.scalars(select(RzyStreamGeneration)).all()),
      'event_versions_created':9,'event_versions_remaining':len(s.scalars(select(RzyEvent).where(RzyEvent.event_id.like('prune-probe-%'))).all())}
print(json.dumps(out,indent=2))
