"""Real ORM, production engine/adapter/outbox and sender; no network or real mail."""
import importlib.util,json,pathlib,sys,tempfile,types,math,os,copy
from datetime import datetime,timedelta,timezone
from sqlalchemy import create_engine,select,func,event
from sqlalchemy.orm import sessionmaker
ROOT=pathlib.Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'shared'),str(ROOT/'services/market-data')]
# Replace only database configuration/session wiring. Models and all rules are shipped code.
db=types.ModuleType('db');db.__path__=[str(ROOT/'shared/db')];sys.modules['db']=db
spec=importlib.util.spec_from_file_location('db.models',ROOT/'shared/db/models.py')
models=importlib.util.module_from_spec(spec);sys.modules['db.models']=models;spec.loader.exec_module(models)
for key in vars(models):setattr(db,key,getattr(models,key))
from src.services import rzy_workflow as W
from src.services.rzy_models import RzyStream,RzyEvent,RzyPreference
from common import outbox
from common.price_basis import resolve_ohlc
from common.market_calendar import session_bounds
from src.services.rzy_data import prepare_bars
from src.services.little_rzy import Config
import pandas as pd
url=os.environ.get('RZY_PG_URL')
if url:
    from sqlalchemy.engine import make_url
    from sqlalchemy import text
    parsed=make_url(url)
    assert parsed.drivername.startswith('postgresql') and parsed.host in ('localhost','127.0.0.1') and parsed.database in ('schemaprobe','rzyprobe'), 'Disposable local database only'
    engine=create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('DROP SCHEMA public CASCADE'));conn.execute(text('CREATE SCHEMA public'))
else:engine=create_engine('sqlite:///'+str(pathlib.Path(tempfile.mkdtemp())/'rzy.db'))
models.Base.metadata.create_all(engine);Session=sessionmaker(bind=engine,autoflush=False)
base=datetime(2026,10,16,13,30,tzinfo=timezone.utc)
clock=[base]
outbox.utcnow=lambda:clock[0].replace(tzinfo=None)
with Session() as s:
    s.add_all([models.User(id=1,username='owner',password_hash='fixture',email='owner@example.invalid',is_active=True),models.User(id=2,username='other',password_hash='fixture',email='other@example.invalid',is_active=True),
        models.Stock(id=1,symbol='TEST',market=models.Market.US,exchange=models.Exchange.NASDAQ,name='Synthetic test',active=True,delisted=False),
        models.Stock(id=2,symbol='OTHER',market=models.Market.US,exchange=models.Exchange.NASDAQ,name='Other',active=True,delisted=False)])
    s.commit();s.add_all([models.WatchlistItem(user_id=1,stock_id=1),models.WatchlistItem(user_id=2,stock_id=2)]);s.commit()
result={}
with Session() as s:
    assert W.watchlist_symbols(s,1)=={'TEST'};assert W.watchlist_symbols(s,2)=={'OTHER'}
    user=s.get(models.User,1)
    assert not W.eligible(s,user,None,'little_rzy');assert not W.eligible(s,user,None,'short_term_digest')
    s.add_all([models.AlertPreference(user_id=1,alert_type='little_rzy',enabled=True),models.AlertPreference(user_id=1,alert_type='short_term_digest',enabled=True),RzyPreference(user_id=1,timeframes=['5m'],updated_at=base.replace(tzinfo=None))]);s.commit()
    assert W.eligible(s,user,None,'little_rzy')
    def add(i,o,h,l,c):
        s.add(models.Price(id=i+1,stock_id=1,ts=(base+timedelta(minutes=5*i)).replace(tzinfo=None),timeframe=models.TimeFrame.M5,open=o,high=h,low=l,close=c,volume=10000,session='REGULAR'));s.commit()
    for i in range(57):
        c=100+.12*i+5*math.sin(i*.22);add(i,c-.2,c+.6,c-.8,c)
    now=base+timedelta(minutes=5*57+2)
    a=W.evaluate(s,'TEST','5m',now,persist=True);s.commit()
    assert a['bootstrap'] and a['status']=='research_only' and a['structures'][-1]['state']=='ARMED'
    # Real ORM contains only M5 rows: larger frames must use the shipped adapter fallback.
    cache={}
    fifteen=W.evaluate(s,'TEST','15m',now,bars_cache=cache)
    hourly=W.evaluate(s,'TEST','1h',now,bars_cache=cache)
    assert fifteen['evidence']['derived_from_timeframe']=='5m' and len(fifteen['price_grid'])==19
    assert hourly['evidence']['derived_from_timeframe']=='5m' and len(hourly['price_grid'])==4
    assert ('TEST','5m') in cache
    count=s.scalar(select(func.count()).select_from(RzyEvent))
    writes=[]
    def capture_update(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('UPDATE LITTLE_RZY_STREAMS'): writes.append(statement)
    event.listen(engine,'before_cursor_execute',capture_update)
    try:
        repeated=W.evaluate(s,'TEST','5m',now,persist=True);s.commit()
        assert writes==[], 'A scan without a new candle rewrote the entire JSON checkpoint'
    finally: event.remove(engine,'before_cursor_execute',capture_update)
    assert not repeated['events'];assert s.scalar(select(func.count()).select_from(RzyEvent))==count
    # Real persistence/reload retains frozen geometry and charts retain historical events.
    preview=W.evaluate(s,'TEST','5m',now);assert preview['events'] and preview['structures']==a['structures']
    st=a['structures'][-1]
    def line(i):
        x,_,z=st['anchors'];return x['price']+(z['price']-x['price'])*(i-x['index'])/(z['index']-x['index'])
    p=line(57);add(57,p+.2,p+.4,p-.1,p+.1)
    p=line(58);add(58,p+.3,p+.8,p-.1,p+.7)
    now=base+timedelta(minutes=5*59+2)
    clock[0]=now
    scanned=W.scan(s,now);s.commit()
    assert scanned['queued']==1,scanned
    rows=s.scalars(select(models.NotificationOutbox)).all();assert len(rows)==1
    assert 'Bullish setup triggered' in rows[0].subject and 'Research only' in rows[0].body_text and 'No broker order' in rows[0].body_text
    assert '/alerts/unsubscribe' in rows[0].body_text or 'Turn off' in rows[0].body_text
    assert 'owner@example.invalid'==rows[0].recipient
    # De-duplicated on restart and same timestamp.
    W.scan(s,now);s.commit();assert s.scalar(select(func.count()).select_from(models.NotificationOutbox))==1
    # Sender's REAL central preference gate honors an opt-out after enqueue.
    pref=s.scalar(select(models.AlertPreference).where(models.AlertPreference.alert_type=='little_rzy'));pref.enabled=False;s.commit()
    sent=[]
    def send(*args):sent.append(args);return True
    W.drain(s,send,now);s.commit();assert not sent
    assert s.get(models.NotificationOutbox,rows[0].id).state=='suppressed'
    result['persistence_and_sender_optout']=True
    # New explicit opt-in plus a separate new event actually traverses the production sender.
    pref.enabled=True;s.commit()
    event=s.scalar(select(RzyEvent).where(RzyEvent.event_type=='TRIGGERED'))
    outbox.enqueue(s,event_id=f'rzy:1:{event.event_id}:accept',user_id=1,recipient=user.email,alert_type='little_rzy',subject='fixture',body_html='fixture',body_text='fixture',expires_at=(now+timedelta(minutes=5)).replace(tzinfo=None))
    # Source identity is the last segment, so put the test-only prefix before it.
    row=s.scalar(select(models.NotificationOutbox).where(models.NotificationOutbox.subject=='fixture'));row.event_id=f'rzy:accept:1:{event.event_id}';s.commit()
    delivered=W.drain(s,send,now);s.commit();assert len(sent)==1,delivered
    assert row.state=='accepted'
    W.drain(s,send,now);s.commit();assert len(sent)==1
    result['one_provider_acceptance']=True
    # Digest is 20 min before open, skips holidays/weekends, never uses another user's symbols.
    monday=datetime(2026,10,19,13,10,tzinfo=timezone.utc)
    clock[0]=monday
    assert W.morning_digest(s,monday-timedelta(minutes=1))['queued']==0
    assert W.morning_digest(s,monday)['queued']==1;s.commit()
    assert W.morning_digest(s,monday+timedelta(minutes=1))['queued']==0;s.commit()
    assert W.morning_digest(s,datetime(2026,10,18,13,10,tzinfo=timezone.utc))['queued']==0
    assert W.morning_digest(s,datetime(2026,12,25,14,10,tzinfo=timezone.utc))['queued']==0
    digest=s.scalar(select(models.NotificationOutbox).where(models.NotificationOutbox.alert_type=='short_term_digest'))
    assert 'No qualifying candidates' in digest.body_text and 'OTHER' not in digest.body_text
    assert digest.expires_at==datetime(2026,10,19,13,30)
    result['calendar_scope_digest_dedup']=True
    # Delivery checks the queued channel's consent independently of pattern alerts.
    p=s.scalar(select(models.AlertPreference).where(models.AlertPreference.alert_type=='short_term_digest'));p.enabled=False;s.commit()
    W.drain(s,send,monday);s.commit();assert len(sent)==1
    result['digest_optout']=True
    # Restatements: storage noise and volume restatements keep the point-in-time state; a
    # material OHLC change rebuilds a new generation, archives the old one, and blocks mail
    # queued from the superseded history. Nothing is silently patched in place.
    from src.services.rzy_models import RzyStreamGeneration
    key=f"TEST:5m:{Config().hash}"
    changed=s.get(models.Price,10)
    changed.open*=1+1e-9;changed.volume=12345;s.commit()
    noisy=W.evaluate(s,'TEST','5m',now,persist=True);s.commit()
    assert noisy['status']=='research_only' and not noisy['rebuilt'] and noisy['generation']==1
    outbox.enqueue(s,event_id=f'rzy:superseded:1:{event.event_id}',user_id=1,recipient=user.email,alert_type='little_rzy',subject='superseded',body_html='fixture',body_text='fixture',expires_at=(monday+timedelta(minutes=5)).replace(tzinfo=None));s.commit()
    clock[0]=now+timedelta(minutes=1)
    changed.open+=.05;s.commit()
    revised=W.evaluate(s,'TEST','5m',now+timedelta(minutes=1),persist=True);s.commit()
    assert revised['status']=='research_only' and revised['rebuilt'] and revised['rebuild_reason']=='stored_completed_bar_changed' and revised['generation']==2
    archived=s.scalars(select(RzyStreamGeneration).where(RzyStreamGeneration.stream_key==key)).all()
    assert len(archived)==1 and archived[0].generation==1 and archived[0].detail['detail']['before']['open']!=archived[0].detail['detail']['after']['open']
    assert archived[0].detail['checkpoint_sha256'] and archived[0].detail['structures']
    assert s.scalar(select(func.count()).select_from(RzyEvent))>=count, 'Superseded events must be retained'
    clock[0]=monday
    # The revision did not touch this setup: the same occurrence exists unchanged in the
    # current generation, so the queued message is still accurate and is delivered once.
    W.drain(s,send,monday);s.commit();assert len(sent)==2
    assert s.scalar(select(models.NotificationOutbox).where(models.NotificationOutbox.subject=='superseded')).state=='accepted'
    # RZA-01 (audit scenario): raise the extreme candle's high so D and the target change.
    trig=s.scalar(select(RzyEvent).where(RzyEvent.event_type=='TRIGGERED'))
    occ=trig.payload['occurrence_id'];old_target=trig.payload['primary_target']
    assert trig.event_id==occ and trig.payload['generation']==1
    outbox.enqueue(s,event_id=f'rzy:moved:1:{occ}',user_id=1,recipient=user.email,alert_type='little_rzy',subject='moved',body_html='fixture',body_text='fixture',expires_at=(monday+timedelta(minutes=5)).replace(tzinfo=None));s.commit()
    extreme=s.get(models.Price,trig.payload['extreme']['index']+1);extreme.high+=.20;s.commit()
    before=s.scalar(select(func.count()).select_from(RzyEvent))
    moved=W.evaluate(s,'TEST','5m',now+timedelta(minutes=1),persist=True);s.commit()
    assert moved['rebuilt'] and moved['generation']==3
    assert s.scalar(select(func.count()).select_from(RzyEvent))>before, 'A rebuild must store its own generation of events'
    versions=s.scalars(select(RzyEvent).where(RzyEvent.event_type=='TRIGGERED')).all()
    new=[e for e in versions if e.payload['generation']==3 and e.payload['occurrence_id']==occ]
    assert len(new)==1 and new[0].event_id!=occ and new[0].payload['primary_target']!=old_target
    # Current views carry only generation 3, with the new target; history is explicit.
    view=W.evaluate(s,'TEST','5m',now+timedelta(minutes=1))
    assert {e.get('generation',1) for e in view['events']}=={3} and view['superseded_event_count']>0
    assert [e['primary_target'] for e in view['events'] if e['event_type']=='TRIGGERED']==[new[0].payload['primary_target']]
    hist=W.superseded_events(s,'TEST','5m')
    assert hist['current_generation']==3 and old_target in [e['primary_target'] for e in hist['events'] if e['event_type']=='TRIGGERED']
    # The queued message showed the old target: suppressed, and the new version shares the
    # outbox key (occurrence), so it can never be mailed a second time.
    W.drain(s,send,monday);s.commit();assert len(sent)==2
    assert s.scalar(select(models.NotificationOutbox).where(models.NotificationOutbox.subject=='moved')).state=='suppressed'
    assert new[0].payload['occurrence_id']==occ
    result['generation_identity']=True
    # A rebuild replays history, so it must not mail anything already stale.
    assert W.scan(s,now+timedelta(minutes=1))['queued']==0
    # A legacy quarantine from the previous release rebuilds instead of staying locked forever.
    row=s.get(RzyStream,key);cp=copy.deepcopy(row.checkpoint);cp['requires_audited_replay']={'reason':'stored_completed_bar_changed','observed_at':'x'}
    row.checkpoint=cp;s.commit()
    legacy=W.evaluate(s,'TEST','5m',now+timedelta(minutes=1),persist=True);s.commit()
    assert legacy['rebuilt'] and legacy['rebuild_reason']=='legacy_quarantine' and legacy['generation']==4
    assert not s.get(RzyStream,key).checkpoint.get('requires_audited_replay')
    result['persistent_revision_quarantine']=True
    # A bar missing after the checkpoint waits for backfill, then rebuilds on the new segment.
    add(60,110,110.5,109.5,110.2)   # slot 59 never arrives
    soon=base+timedelta(minutes=5*61+2)
    waiting=W.evaluate(s,'TEST','5m',soon,persist=True);s.commit()
    assert waiting['status']=='awaiting_backfill' and s.get(RzyStream,key).checkpoint['bars'][-1]['time']<waiting['evidence']['latest_gap']['before']
    later=base+timedelta(minutes=5*61+40)
    gapped=W.evaluate(s,'TEST','5m',later,persist=True);s.commit()
    assert gapped['rebuilt'] and gapped['rebuild_reason']=='gap_after_checkpoint' and gapped['status'] in ('research_only','stale')
    assert gapped['evidence']['segment_start']==(base+timedelta(minutes=5*60)).isoformat() and gapped['evidence']['segment_bars']==1
    result['gap_segment_rebuild']=True
    # Cancellations caused only by standing data blockers are not re-mailed; real ones are.
    standing=dict(event_type='ENTRY_CANCELLED',failed_rules=['data_basis_or_freshness_unverified'])
    assert not W.emailable(standing) and W.emailable(dict(standing,failed_rules=['data_basis_or_freshness_unverified','liquidity_requirement']))
    body=W.render_event(dict(event.payload),('provider_delay_unverified',))
    assert 'Paper execution unavailable' in body and 'provider_delay_unverified' in body
    result['email_noise_gating']=True
    # RZA-02: slot 59 (18:25-18:30) is missing, so the 15m bin closing 18:30 is incomplete while
    # the 5m tail (slot 60, closed 18:35) is current. 15m freshness must come from 15m itself.
    fresh5=base+timedelta(minutes=5*61+3)
    five=W.evaluate(s,'TEST','5m',fresh5);fifteen=W.evaluate(s,'TEST','15m',fresh5)
    assert five['evidence'].get('stale') is False
    assert fifteen['status']=='stale' and fifteen['evidence']['stale_reason']=='stale_15m_aggregate'
    assert fifteen['evidence']['expected_latest_close']==(base+timedelta(minutes=5*60)).isoformat()
    assert fifteen['evidence']['data_as_of']<fifteen['evidence']['expected_latest_close']
    pref=s.get(RzyPreference,1);pref.timeframes=['15m'];s.commit()
    digest=W.digest_preview(s,1,fresh5)
    assert not digest['candidates']
    ex=[x for x in digest['exclusions'] if x['timeframe']=='15m']
    assert ex and ex[0]['reason']=='stale_15m_aggregate' and ex[0]['expected_latest_close'] and ex[0]['source']=='derived_from_5m'
    pref.timeframes=['5m'];s.commit()
    result['derived_freshness']=True
    # RZA-07: one stale native 15m row must not disable the fresh 5m-derived series.
    s.add(models.Price(id=9001,stock_id=1,ts=base.replace(tzinfo=None),timeframe=models.TimeFrame.M15,open=100,high=101,low=99,close=100,volume=1,session='REGULAR'));s.commit()
    chosen=W.evaluate(s,'TEST','15m',base+timedelta(minutes=5*58+3))
    assert chosen['evidence']['source']=='derived_from_5m' and chosen['evidence']['alternative']['source']=='native'
    assert chosen['evidence']['alternative']['status']=='stale' and len(chosen['price_grid'])>1
    s.delete(s.get(models.Price,9001));s.commit()
    result['native_derived_choice']=True
# Whole OHLC adjustment and missing/forming/stale data are tested at the shipped adapter.
rows=[types.SimpleNamespace(ts=base+timedelta(minutes=5*i),open=100,high=102,low=98,close=100,adj_close=50,volume=10,session='REGULAR') for i in range(3)]
bars,ev=prepare_bars(rows,'5m',base+timedelta(minutes=17),session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
assert bars[0]['open']==50 and bars[0]['high']==51 and bars[0]['low']==49
assert bars[0]['execution_eligible'] is False and bars[0]['provider']=='stored_prices_upstream_not_retained'
from src.services.rzy_data import break_indices
gapped,ev=prepare_bars([rows[0],rows[2]],'5m',base+timedelta(minutes=17),session_bounds=session_bounds,resolve_ohlc=resolve_ohlc)
assert ev['gaps'] and break_indices(gapped,'5m',session_bounds)==[1], 'A gap is reported and splits segments, never bridged'
bars,ev=prepare_bars(rows,'5m',base+timedelta(minutes=11),session_bounds=session_bounds,resolve_ohlc=resolve_ohlc);assert len(bars)==1 and any(x['reason']=='incomplete_or_settling' for x in ev['exclusions'])
_,ev=prepare_bars(rows,'5m',base+timedelta(minutes=45),session_bounds=session_bounds,resolve_ohlc=resolve_ohlc);assert ev['status']=='stale'
result['price_basis_completion_gaps']=True
print(json.dumps(result))
