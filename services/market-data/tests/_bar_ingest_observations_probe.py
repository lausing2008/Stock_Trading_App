"""Real models/storage; PostgreSQL additionally exercises actual ingestion and report paths."""
import runpy,pathlib,json,os,types,importlib.util,hashlib,tempfile
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from sqlalchemy import select,func,text
# Reuse the real database bootstrap, not a second implementation of model/session wiring.
g=runpy.run_path(str(pathlib.Path(__file__).with_name('_bar_provenance_probe.py')))
Session,models,engine,ROOT,db=(g[k] for k in ('Session','models','engine','ROOT','db'))
db.SessionLocal=Session;db.get_session=lambda:None
from src.services import bar_ingest_observations as O,rzy_models
models.Base.metadata.create_all(engine)
base=datetime(2026,10,23,13,30)
def row(i,seconds=0):
    return dict(stock_id=500,ts=base+timedelta(minutes=5*i,seconds=seconds),timeframe=models.TimeFrame.M5,open=100.,high=101.,low=99.,close=100.,volume=10.,adj_close=None,session='REGULAR')
out={}
with Session() as s:
    stock=models.Stock(id=500,symbol='OBS',market=models.Market.US,exchange=models.Exchange.NASDAQ,name='Synthetic',active=True,delisted=False)
    hk=models.Stock(id=501,symbol='0700.HK',market=models.Market.HK,exchange=models.Exchange.HKEX,name='Synthetic HK',active=True,delisted=False)
    s.add_all([stock,hk]);s.commit()
    O.record_ingest_observation(s,stock,'5m',[row(0),row(0),row(0,1),row(1)],[row(0)],[row(0,1)],'test',now=base+timedelta(minutes=7));s.commit()
    t=next(r for r in O.ingest_totals(s,session_day=base.date()) if r['market']=='US')
    assert {k:t[k] for k in ('received','duplicate_dropped','off_grid_rejected','authority_rejected','written')}==dict(received=4,duplicate_dropped=1,off_grid_rejected=1,authority_rejected=1,written=1)
    assert s.get(O.BarPostCloseWrite,(500,'5m',base)).observed_at==base+timedelta(minutes=7)
    for minute in (8,6):
        O.record_ingest_observation(s,stock,'5m',[row(0)],[row(0)],[],'test',now=base+timedelta(minutes=minute));s.commit()
    s.expire_all();assert s.get(O.BarPostCloseWrite,(500,'5m',base)).observed_at==base+timedelta(minutes=6)
    O.record_ingest_observation(s,stock,'5m',[row(2)],[row(2)],[],'test',now=base+timedelta(minutes=12));s.commit()
    assert s.get(O.BarPostCloseWrite,(500,'5m',row(2)['ts'])) is None
    O.record_ingest_observation(s,stock,'5m',[row(2)],[row(2)],[],'test',now=base+timedelta(minutes=16));s.commit()
    assert s.get(O.BarPostCloseWrite,(500,'5m',row(2)['ts'])).observed_at==base+timedelta(minutes=16)
    n=s.scalar(select(func.count()).select_from(O.BarIngestObservation))
    O.record_ingest_observation(s,stock,'5m',[row(3)],[row(3)],[],'test',now=base+timedelta(minutes=22));s.rollback()
    assert s.scalar(select(func.count()).select_from(O.BarIngestObservation))==n and s.get(O.BarPostCloseWrite,(500,'5m',row(3)['ts'])) is None
    hkrow=dict(row(0),stock_id=501,ts=datetime(2026,10,22,23))
    O.record_ingest_observation(s,hk,'5m',[hkrow],[hkrow],[],'test',now=base+timedelta(minutes=7));s.commit()
    assert any(t['market']=='HK' and t['session_day']=='2026-10-23' for t in O.ingest_totals(s))
    out.update(conserved_session_counts=True,first_observed_post_close=True,rollback_atomic=True,venue_dates=True)
from src.services.rzy_monitoring import monitoring_report
r=types.SimpleNamespace(symbol='OBS',timeframe='5m',event_type='TRIGGERED',observed_in='incremental',signal_time=base+timedelta(minutes=5),first_seen_at=base+timedelta(minutes=8),queued_at=None,occurrence_id='test')
for stamp,expected,missing,excluded in [(base+timedelta(minutes=7),120,0,0),(None,None,1,0),(base+timedelta(minutes=9),None,0,1)]:
    tf=monitoring_report([r],[],[],base,base,{('OBS',r.signal_time):stamp} if stamp else {})['by_timeframe']['5m']
    assert tf['seconds_from_close']['observed_post_close_write']['median']==expected
    assert tf['post_close_write_missing']==missing and tf['post_close_write_timestamp_excluded']==excluded
    if expected:assert tf['segments_seconds']['observed_write_to_detection']['median']==60
out['latency_anchors']=True
assert O.regular_us_close(datetime(2026,11,27,17,30),'1h')==datetime(2026,11,27,18)
assert O.regular_us_close(datetime(2026,11,27,18),'5m') is None
out['calendar_boundaries']=True
if os.environ.get('RZY_PG_URL'):
    import pandas as pd
    from src.services import ingestion as ING
    from src.adapters.base import OHLCV
    with engine.begin() as c:c.execute(text("SELECT setval(pg_get_serial_sequence('prices','id'),COALESCE(MAX(id),0)+1,false) FROM prices"))
    class Redis:
        def __init__(self):self.values={}
        def incr(self,k):self.values[k]=self.values.get(k,0)+1
        def incrby(self,k,v):self.values[k]=self.values.get(k,0)+v
    redis=Redis();ING.get_redis=lambda:redis;ING._write_parquet=lambda *a,**k:None;ING._polygon_budget_available=lambda:True;ING.SessionLocal=Session
    state={'close':100.}
    class Provider:
        name='fixture'
        def fetch_ohlcv(self,symbol,start,end,timeframe):
            data=[dict(row(i),close=state['close']) for i in (0,1,2)]+[dict(row(0,2),volume=0.,high=100.,low=100.)]
            return OHLCV(symbol,timeframe,pd.DataFrame(data)[['ts','open','high','low','close','volume','adj_close']])
    ING.get_adapter=lambda *a,**k:Provider();ING.get_adapters=lambda *a,**k:[Provider()];ING.primary_provider=lambda *a:'fixture'
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return (base+timedelta(minutes=17)).replace(tzinfo=timezone.utc) if tz else base+timedelta(minutes=17)
    with patch.object(O,'datetime',Clock):ING.ingest_symbol('OBS',timeframe='5m',provider='fixture')
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(models.Price).where(models.Price.stock_id==500))==3
        batches=s.scalars(select(O.BarIngestObservation).where(O.BarIngestObservation.provider=='fixture')).all()
        assert len(batches)==1 and batches[0].received==4 and batches[0].off_grid_rejected==1 and batches[0].written==3
    # The all-protected early return also records its actual zero writes.
    Provider.name='fallback';state['close']=100.25
    with patch.object(O,'datetime',Clock):ING.ingest_symbol('OBS',timeframe='5m',provider='fallback')
    with Session() as s:
        protected=s.scalar(select(O.BarIngestObservation).where(O.BarIngestObservation.provider=='fallback'))
        assert protected and protected.authority_rejected==3 and protected.written==0 and protected.off_grid_rejected==1
        assert s.scalar(select(models.Price.close).where(models.Price.stock_id==500,models.Price.ts==base))==100.
    Provider.name='fixture'
    def broken(session,*a,**k):session.execute(text('SELECT * FROM deliberately_missing_observation_table'))
    state['close']=100.5
    with patch.object(ING,'record_ingest_observation',broken):ING.ingest_symbol('OBS',timeframe='5m',provider='fixture')
    with Session() as s:
        assert s.scalar(select(func.count()).select_from(models.Price).where(models.Price.stock_id==500))==3
        assert s.scalar(select(models.Price.close).where(models.Price.stock_id==500,models.Price.ts==base))==100.5
        assert s.scalar(select(func.count()).select_from(O.BarIngestObservation).where(O.BarIngestObservation.provider=='fixture'))==1
    assert redis.values.get('stockai:metric:bar_ingest_observation_failures')==1
    out.update(real_ingest_hook=True,telemetry_failure_preserves_prices=True)
    # The existing scan cleans up even a symbol which is no longer ingested/subscribed.
    with Session() as s:
        stale=types.SimpleNamespace(id=999,symbol='INACTIVE',market='US')
        old=base-timedelta(days=42);oldrow=dict(row(0),stock_id=999,ts=old)
        O.record_ingest_observation(s,stale,'5m',[oldrow],[oldrow],[],'test',now=old+timedelta(minutes=7));s.commit()
        assert s.get(O.BarPostCloseWrite,(999,'5m',old)) is not None
        from src.services.rzy_workflow import scan
        scan(s,now=base+timedelta(minutes=20))
        assert s.get(O.BarPostCloseWrite,(999,'5m',old)) is None
        assert not s.scalars(select(O.BarIngestObservation).where(O.BarIngestObservation.stock_id==999)).all()
    out['inactive_retention']=True
    os.environ['RZY_EVAL_ROOT']=str(ROOT/'services/market-data')
    spec=importlib.util.spec_from_file_location('report',ROOT/'scripts/rzy_session_report.py');report=importlib.util.module_from_spec(spec);spec.loader.exec_module(report)
    assert report.observation_window('2026-10-23')==(datetime(2026,10,23,8),datetime(2026,10,24,0))
    assert report.observation_window('2026-11-02')==(datetime(2026,11,2,9),datetime(2026,11,3,1))
    folder=pathlib.Path(tempfile.mkdtemp())/'report'
    from sqlalchemy import event
    readonly=[]
    def observe_mode(conn,cursor,statement,parameters,context,executemany):
        if 'FROM bar_revisions' in statement:readonly.append(conn.exec_driver_sql('SHOW transaction_read_only').scalar_one())
    event.listen(engine,'before_cursor_execute',observe_mode)
    try:report.main('2026-10-23',folder)
    finally:event.remove(engine,'before_cursor_execute',observe_mode)
    assert readonly and set(readonly)=={'on'}
    result=json.loads((folder/'report.json').read_text());manifest=json.loads((folder/'manifest.json').read_text())
    assert result['ingest_observations']['available'] and manifest['rows']['ingest_observations']>0
    assert (folder/'post_close_writes.csv').exists()
    for name,h in manifest['files'].items():assert hashlib.sha256((folder/name).read_bytes()).hexdigest()==h
    try:report.main('2026-10-23',folder);raise AssertionError('Overwrote completed evidence')
    except ValueError:pass
    out['read_only_report_and_dst']=True
print(json.dumps(out))
