"""AUD-RZY-PARTIALCANDLE B: real models and the real workflow; a partial candle is held, not consumed.

Run by test_rzy_partial_candle.py (SQLite, or a disposable local PostgreSQL via RZY_PG_URL).
"""
import importlib.util,json,math,os,pathlib,sys,tempfile,types
from datetime import datetime,timedelta,timezone
from sqlalchemy import create_engine,select,func,update
from sqlalchemy.orm import sessionmaker
ROOT=pathlib.Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'shared'),str(ROOT/'services/market-data')]
db=types.ModuleType('db');db.__path__=[str(ROOT/'shared/db')];sys.modules['db']=db
spec=importlib.util.spec_from_file_location('db.models',ROOT/'shared/db/models.py')
models=importlib.util.module_from_spec(spec);sys.modules['db.models']=models;spec.loader.exec_module(models)
for key in vars(models):setattr(db,key,getattr(models,key))
from src.services import rzy_workflow as W
from src.services.bar_provenance import BarProvenance
from src.services.rzy_models import RzyStream,RzyRebuild
url=os.environ.get('RZY_PG_URL')
if url:
    from sqlalchemy.engine import make_url
    from sqlalchemy import text
    u=make_url(url)
    assert u.host in ('localhost','127.0.0.1') and u.database in ('schemaprobe','rzyprobe'),'Disposable local database only'
    engine=create_engine(url)
    with engine.begin() as c: c.execute(text('DROP SCHEMA public CASCADE'));c.execute(text('CREATE SCHEMA public'))
else: engine=create_engine('sqlite:///'+str(pathlib.Path(tempfile.mkdtemp())/'partial.db'))
models.Base.metadata.create_all(engine);Session=sessionmaker(bind=engine,autoflush=False)
from src.services.receipts import schema as _receipts_schema
with engine.begin() as _c: _receipts_schema.create(_c)
os.environ['STOCKAI_RELEASE_SHA']='partialprobe'

OPEN=datetime(2026,10,16,13,30,tzinfo=timezone.utc)          # Fri 09:30 ET
naive=lambda t:t.astimezone(timezone.utc).replace(tzinfo=None)
out={}


def seed(s,sid,symbol,n):
    s.add(models.Stock(id=sid,symbol=symbol,market=models.Market.US,exchange=models.Exchange.NASDAQ,name=symbol,active=True,delisted=False));s.commit()
    for i in range(n):
        c=100+.12*i+5*math.sin(i*.22); ts=naive(OPEN+timedelta(minutes=5*i))
        s.add(models.Price(id=sid*1000+i,stock_id=sid,ts=ts,timeframe=models.TimeFrame.M5,open=c-.2,high=c+.6,low=c-.8,close=c,volume=10000,session='REGULAR'))
        s.add(BarProvenance(stock_id=sid,timeframe='5m',ts=ts,provider='unusual_whales',first_written_at=ts+timedelta(minutes=5),
            last_written_at=ts+timedelta(minutes=5,seconds=20),open=c-.2,high=c+.6,low=c-.8,close=c,volume=10000,
            ohlc_revisions=0,volume_only_revisions=0,provider_switches_same_values=0,off_grid=False))
    s.commit()


def write(s,sid,i,written_at,**ohlc):
    """What ingest leaves behind: the stored price and its provenance, in one transaction."""
    ts=naive(OPEN+timedelta(minutes=5*i)); p=s.scalar(select(models.Price).where(models.Price.stock_id==sid,models.Price.ts==ts))
    for k,v in ohlc.items(): setattr(p,k,v)
    s.execute(update(BarProvenance).where(BarProvenance.stock_id==sid,BarProvenance.ts==ts).values(
        last_written_at=naive(written_at),open=p.open,high=p.high,low=p.low,close=p.close));s.commit()
    return p


with Session() as s:
    # ── 5m and 15m: bar 59 (14:25-14:30 ET) written 27 s into the candle, then completed ──
    seed(s,1,'PART',60)
    head_ts=OPEN+timedelta(minutes=5*59); close59=head_ts+timedelta(minutes=5)
    partial=write(s,1,59,head_ts+timedelta(seconds=27),high=110.0,close=109.5)
    now1=close59+timedelta(minutes=3)                         # past the 2-min settle, not rewritten
    a=W.evaluate(s,'PART','5m',now1,persist=True);s.commit()
    cp=s.get(RzyStream,next(k for k in s.scalars(select(RzyStream.stream_key)) if k.startswith('PART:5m'))).checkpoint
    out['held_5m']=dict(held=a['evidence'].get('held_until_post_close_write'),status=a['status'],
        last=cp['bars'][-1]['time'],expected_last=(head_ts-timedelta(minutes=5)).isoformat())
    b=W.evaluate(s,'PART','15m',now1,persist=True);s.commit()
    ex=b['evidence'].get('exclusions',[]); gaps=b['evidence'].get('gaps',[])
    out['held_15m']=dict(awaiting=any(x.get('reason')=='awaiting_post_close_write' for x in ex),
        gap=any(g.get('reason')=='missing_aggregate_constituents' for g in gaps))
    # The next run writes the final candle (high/close move 2-3%, as ANF's did).
    write(s,1,59,close59+timedelta(seconds=25),high=113.0,close=112.4)
    now2=close59+timedelta(minutes=8)
    c5=W.evaluate(s,'PART','5m',now2,persist=True);s.commit()
    c15=W.evaluate(s,'PART','15m',now2,persist=True);s.commit()
    cp=s.get(RzyStream,next(k for k in s.scalars(select(RzyStream.stream_key)) if k.startswith('PART:5m'))).checkpoint
    out['completed']=dict(rebuilt_5m=bool(c5.get('rebuilt')),rebuilt_15m=bool(c15.get('rebuilt')),
        last=cp['bars'][-1]['time'],high=cp['bars'][-1]['high'],
        rebuild_rows=s.scalar(select(func.count()).select_from(RzyRebuild).where(RzyRebuild.symbol=='PART')))

    # ── 1h at the session close: the 15:30 ET hour ends at 16:00 and needs the 15:55 bar's post-close write ──
    seed(s,2,'CLOSE',78)
    last_ts=OPEN+timedelta(minutes=5*77); session_close=last_ts+timedelta(minutes=5)      # 16:00 ET
    write(s,2,77,session_close-timedelta(seconds=30))       # written while forming, not rewritten after the bell
    h1=W.load_bars(s,'CLOSE','1h',session_close+timedelta(minutes=3))
    out['close_1h_held']=dict(last_closed=h1[0][-1]['closed_at'] if h1[0] else None,
        awaiting=any(x.get('reason')=='awaiting_post_close_write' for x in h1[1].get('exclusions',[])))
    write(s,2,77,session_close+timedelta(minutes=2))         # the post-close final-candle pass
    h2=W.load_bars(s,'CLOSE','1h',session_close+timedelta(minutes=5))
    out['close_1h_done']=dict(last_open=h2[0][-1]['time'],last_closed=h2[0][-1]['closed_at'])
    # ── the reader's session is the newest REGULAR bar's, never a premarket head's ──
    R=lambda t,sess: type('R',(),dict(ts=naive(t),session=sess))()
    fri=OPEN+timedelta(minutes=5*59)
    out['reader']=dict(premarket_head=len(W._latest_session_write_times(s,1,'5m',[R(OPEN+timedelta(days=3,hours=-1),'PRE'),R(fri,'REGULAR')])),
        regular_only=len(W._latest_session_write_times(s,1,'5m',[R(fri,'REGULAR')])))
print(json.dumps(out,default=str))
