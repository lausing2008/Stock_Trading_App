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
from src.services import bar_provenance as _bar_provenance  # register its tables before create_all
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
    pre=W.digest_snapshot(s,1,now)
    assert pre['available'] is False and 'not available yet' in pre['message']
    scanned=W.scan(s,now);s.commit()
    assert scanned['queued']==1,scanned
    # The interactive preview reads saved snapshots only; evaluating during the request fails.
    real_evaluate=W.evaluate
    def forbidden(*a,**k): raise AssertionError('preview must not evaluate streams')
    W.evaluate=forbidden
    try: snap=W.digest_snapshot(s,1,now+timedelta(minutes=4))
    finally: W.evaluate=real_evaluate
    assert snap['available'] and snap['snapshot_from']==now.replace(tzinfo=None).isoformat() and snap['snapshot_age_minutes']==4.0
    assert snap['streams_with_snapshot']==1 and snap['streams_expected']==1
    assert snap['candidates'] or snap['exclusions'], snap
    result['snapshot_preview']=True
    from src.services.rzy_models import RzyScanRun
    run=s.scalars(select(RzyScanRun).order_by(RzyScanRun.id.desc())).first()
    assert run and run.mode=='session' and run.streams==1 and run.duration_seconds>=0 and run.queued==1 and run.new_emailable_events>=1
    assert sum(run.statuses.values())==1
    result['scan_run_recorded']=True
    # The route module only needs db.get_session at import; the probe passes its own session.
    for name in ('get_session','SessionLocal'):
        if not hasattr(db,name): setattr(db,name,lambda: None)
    from src.api.little_rzy import admin_overview
    ov=admin_overview(_admin=None,session=s)
    # Status counts cover CURRENT snapshots only (F07); this fixture's are dated 2026-10-16.
    assert ov['streams']['total']==1 and ov['streams']['current']+ov['streams']['expired']==1
    assert sum(ov['streams']['by_status'].values())==ov['streams']['current']
    assert ov['scans']['latest'] and ov['scans']['latest'][0]['queued']==1
    assert ov['alerts']['recent'] and all('recipient' not in m for m in ov['alerts']['recent'])
    assert 'not a validated trade recommendation' in ov['positioning'].lower()
    result['admin_overview']=True
    rows=s.scalars(select(models.NotificationOutbox)).all();assert len(rows)==1
    assert rows[0].subject.startswith('RZY research | ') and 'bullish trigger' in rows[0].subject and 'Bullish setup triggered' in rows[0].body_text and 'Research only' in rows[0].body_text and 'No broker order' in rows[0].body_text
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
    # The restatement scenarios below exercise rebuild semantics back to back; the rebuild cap
    # has its own section further down.
    W.REBUILD_MIN_INTERVAL=timedelta(0)
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
    template=copy.deepcopy(new[0].payload)     # its row is pruned later; F02 below reuses the payload
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
    # Rebuild cap: a second restatement within the interval pauses the stream instead of
    # rebuilding; the sender blocks its queued mail; after the interval it rebuilds once.
    W.REBUILD_MIN_INTERVAL=timedelta(minutes=30)
    t0=base+timedelta(minutes=5*61+80)   # after the gap rebuild at +5*61+40
    s.get(models.Price,61).close+=.07;s.commit()
    first=W.evaluate(s,'TEST','5m',t0,persist=True);s.commit()
    assert first['rebuilt'];g=first['generation']
    s.get(models.Price,61).close-=.03;s.commit()
    paused=W.evaluate(s,'TEST','5m',t0+timedelta(minutes=5),persist=True);s.commit()
    assert paused['status']=='restatement_pending' and paused['reason']=='stored_completed_bar_changed'
    assert s.get(RzyStream,key).checkpoint['restatement_pending'] and s.get(RzyStream,key).checkpoint['generation']==g
    any_ev=s.scalar(select(RzyEvent).where(RzyEvent.event_type=='TRIGGERED'))
    clock[0]=t0
    outbox.enqueue(s,event_id=f"rzy:pending:1:{any_ev.payload['occurrence_id']}",user_id=1,recipient=user.email,alert_type='little_rzy',subject='pending',body_html='x',body_text='x',expires_at=(t0+timedelta(days=1)).replace(tzinfo=None));s.commit()
    W.drain(s,send,t0+timedelta(minutes=6));s.commit()
    assert s.scalar(select(models.NotificationOutbox).where(models.NotificationOutbox.subject=='pending')).state=='suppressed'
    # Bulk digest preview never rebuilds in memory.
    pref=s.get(RzyPreference,1)
    assert any(x['reason'] in ('restatement_pending','rebuild_pending') for x in W.digest_preview(s,1,t0+timedelta(minutes=6))['exclusions'] if x['timeframe']=='5m')
    later_t=t0+timedelta(minutes=31)
    resumed=W.evaluate(s,'TEST','5m',later_t,persist=True);s.commit()
    assert resumed['rebuilt'] and resumed['generation']==g+1 and not s.get(RzyStream,key).checkpoint.get('restatement_pending')
    result['rebuild_cap']=True
    # Retention: only the last 5 generations of archives and stored events survive.
    W.REBUILD_MIN_INTERVAL=timedelta(0)
    before_gens={e.payload.get('generation',1) for e in s.scalars(select(RzyEvent).where(RzyEvent.symbol=='TEST',RzyEvent.timeframe=='5m')).all()}
    assert before_gens and min(before_gens)<s.get(RzyStream,key).checkpoint['generation']+6-W.KEEP_GENERATIONS+1
    for k in range(6):
        s.get(models.Price,61).close+=.05*(-1)**k;s.commit()
        assert W.evaluate(s,'TEST','5m',later_t+timedelta(minutes=k+1),persist=True)['rebuilt'];s.commit()
    current=s.get(RzyStream,key).checkpoint['generation']
    assert s.scalar(select(func.count()).select_from(RzyStreamGeneration).where(RzyStreamGeneration.stream_key==key))<=W.KEEP_GENERATIONS
    gens={e.payload.get('generation',1) for e in s.scalars(select(RzyEvent).where(RzyEvent.symbol=='TEST',RzyEvent.timeframe=='5m')).all()}
    assert all(g>=current-W.KEEP_GENERATIONS+1 for g in gens), (sorted(gens),current)
    result['generation_retention']=True
    # ── F02: delivery finds the current event through its occurrence record at ANY generation ──
    from src.services.rzy_models import RzyOccurrence,RzyRebuild
    from src.services.little_rzy import generation_event_id
    row=s.get(RzyStream,key); cur=row.checkpoint['generation']
    occ_id=template['occurrence_id']; occ_row=s.get(RzyOccurrence,occ_id)
    assert occ_row is not None and occ_row.latest_generation>=3
    occ_row.queued_figures=W.figures(template); s.commit()      # what scan records at enqueue
    t_send=later_t+timedelta(minutes=30); clock[0]=t_send
    def queue(tag): 
        outbox.enqueue(s,event_id=f'rzy:{tag}:1:{occ_id}',user_id=1,recipient=user.email,alert_type='little_rzy',subject=tag,body_html='x',body_text='x',expires_at=(t_send+timedelta(minutes=5)).replace(tzinfo=None)); s.commit()
        return s.scalar(select(models.NotificationOutbox).where(models.NotificationOutbox.subject==tag))
    def at_generation(G):
        cp=copy.deepcopy(row.checkpoint); cp['generation']=G; cp.pop('restatement_pending',None); row.checkpoint=cp
        payload=dict(template,generation=G,event_id=generation_event_id(occ_id,G))
        if s.get(RzyEvent,payload['event_id']) is None:
            s.add(RzyEvent(event_id=payload['event_id'],symbol='TEST',timeframe='5m',event_type=template['event_type'],payload=payload,created_at=t_send.replace(tzinfo=None)))
        s.commit()
    outcomes={}
    for G in (128,129,1000):
        at_generation(G); r=queue(f'gen{G}'); n=len(sent)
        W.drain(s,send,t_send);s.commit(); s.refresh(r)
        outcomes[G]=(len(sent)-n,r.state)
    assert outcomes=={128:(1,'accepted'),129:(1,'accepted'),1000:(1,'accepted')}, outcomes
    n=len(sent); W.drain(s,send,t_send);s.commit(); assert len(sent)==n, 'no duplicate send'
    # Each refusal records its real cause instead of "recipient opted out".
    occ_row.queued_figures=dict(W.figures(template),stop=-1.0); s.commit()
    r=queue('changed'); W.drain(s,send,t_send);s.commit(); s.refresh(r)
    assert r.state=='suppressed' and r.terminal_reason.startswith('superseded: a rebuild changed the trade figures'), r.terminal_reason
    occ_row.queued_figures=W.figures(template)
    cp=copy.deepcopy(row.checkpoint); cp['generation']=1001; row.checkpoint=cp; s.commit()
    r=queue('absent'); W.drain(s,send,t_send);s.commit(); s.refresh(r)
    assert r.state=='suppressed' and r.terminal_reason=='superseded: occurrence absent from the current generation', r.terminal_reason
    at_generation(1000)
    pref_mail=s.scalar(select(models.AlertPreference).where(models.AlertPreference.alert_type=='little_rzy')); pref_mail.enabled=False; s.commit()
    r=queue('optout'); W.drain(s,send,t_send);s.commit(); s.refresh(r)
    assert r.state=='suppressed' and r.terminal_reason=='recipient opted out'
    pref_mail.enabled=True; s.commit()
    result['delivery_any_generation']=True
    # ── F08: rebuild counts and first observation survive generation pruning ──
    logged=s.scalars(select(RzyRebuild).where(RzyRebuild.stream_key==key)).all()
    archives=s.scalar(select(func.count()).select_from(RzyStreamGeneration).where(RzyStreamGeneration.stream_key==key))
    assert len(logged)==cur-1 and archives<=W.KEEP_GENERATIONS<len(logged), (len(logged),archives,cur)
    first_trig=s.get(RzyOccurrence,occ)
    assert first_trig.first_generation==1 and first_trig.observed_in=='incremental'
    assert first_trig.first_seen_at>=now.replace(tzinfo=None) and first_trig.first_seen_at<(now+timedelta(minutes=1)).replace(tzinfo=None)
    assert s.get(RzyEvent,occ) is None, 'its generation-1 event row was pruned, yet first observation is intact'
    replayed=s.scalars(select(RzyOccurrence).where(RzyOccurrence.observed_in.in_(('rebuild','bootstrap')))).all()
    assert replayed, 'occurrences found while replaying history are labelled, not mixed with new ones'
    result['records_survive_pruning']=True
    # ── F09: a slow earlier stream makes the next one late; times are actual, cutoff kept ──
    for sid,sym in ((3,'EARLY'),(4,'LATE')):
        s.add(models.Stock(id=sid,symbol=sym,market=models.Market.US,exchange=models.Exchange.NASDAQ,name=sym,active=True,delisted=False)); s.commit()
        s.add(models.WatchlistItem(user_id=1,stock_id=sid)); s.commit()
        for i in range(57):
            c=100+.12*i+5*math.sin(i*.22)
            s.add(models.Price(id=sid*10000+i,stock_id=sid,ts=(base+timedelta(minutes=5*i)).replace(tzinfo=None),timeframe=models.TimeFrame.M5,open=c-.2,high=c+.6,low=c-.8,close=c,volume=10000,session='REGULAR'))
        s.commit()
        assert W.evaluate(s,sym,'5m',base+timedelta(minutes=5*57+2),persist=True)['bootstrap']; s.commit()
        for i,(do,dh,dl,dc) in ((57,(.2,.4,-.1,.1)),(58,(.3,.8,-.1,.7))):
            p=line(i); s.add(models.Price(id=sid*10000+i,stock_id=sid,ts=(base+timedelta(minutes=5*i)).replace(tzinfo=None),timeframe=models.TimeFrame.M5,open=p+do,high=p+dh,low=p+dl,close=p+dc,volume=10000,session='REGULAR'))
        s.commit()
    fake=[now]; clock[0]=now; real_eval=W.evaluate
    def slow(session,symbol,*a,**k):
        if symbol=='LATE': fake[0]=now+timedelta(minutes=9)    # EARLY's processing took 9 minutes
        return real_eval(session,symbol,*a,**k)
    W.evaluate=slow
    try: late_scan=W.scan(s,now,clock=lambda: fake[0]); s.commit()
    finally: W.evaluate=real_eval
    trig={o.symbol:o for o in s.scalars(select(RzyOccurrence).where(RzyOccurrence.symbol.in_(('EARLY','LATE')),RzyOccurrence.event_type=='TRIGGERED')).all()}
    assert set(trig)=={'EARLY','LATE'}
    assert trig['EARLY'].first_seen_at==now.replace(tzinfo=None) and trig['EARLY'].queued_at is not None
    assert trig['LATE'].first_seen_at==(now+timedelta(minutes=9)).replace(tzinfo=None) and trig['LATE'].first_cutoff==now.replace(tzinfo=None)
    assert trig['LATE'].queued_at is None and late_scan['too_late_to_mail']>=1
    queued_ids={x.event_id.split(':')[-1] for x in s.scalars(select(models.NotificationOutbox)).all()}
    assert trig['EARLY'].occurrence_id in queued_ids and trig['LATE'].occurrence_id not in queued_ids
    result['actual_time_freshness']=True
    # ── F07: an old snapshot is history, never a current candidate ──
    fresh=W.digest_snapshot(s,1,now+timedelta(minutes=4))
    assert fresh['streams_current']>=2 and fresh['streams_expired']<=1
    week=W.digest_snapshot(s,1,now+timedelta(days=7))
    assert week['available'] and week['candidates']==[] and week['streams_current']==0
    assert week['streams_expired']==week['streams_with_snapshot'] and all(x['reason']=='snapshot_expired' for x in week['exclusions'])
    utc_=lambda *a: datetime(*a,tzinfo=timezone.utc)
    friday_close_cut=datetime(2026,10,16,19,45)                     # 16:00 EDT close − 15 min
    assert W.snapshot_current_since(utc_(2026,10,17,16,0))==friday_close_cut       # Saturday
    assert W.snapshot_current_since(utc_(2026,10,19,12,30))==friday_close_cut      # Monday 08:30 ET, pre-open
    assert W.snapshot_current_since(utc_(2026,10,19,15,0))==datetime(2026,10,19,14,45)   # in session
    assert W.snapshot_current_since(utc_(2026,11,27,20,0))==datetime(2026,11,27,17,45)   # early close 13:00 EST
    result['snapshot_expiry']=True
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
