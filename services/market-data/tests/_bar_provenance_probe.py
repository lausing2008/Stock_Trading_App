"""Real ORM models + bar_provenance on SQLite (or disposable PostgreSQL); no network."""
import copy, importlib.util, json, os, pathlib, sys, tempfile, types
from datetime import datetime, timedelta
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import sessionmaker
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'shared'), str(ROOT / 'services/market-data')]
db = types.ModuleType('db'); db.__path__ = [str(ROOT / 'shared/db')]; sys.modules['db'] = db
spec = importlib.util.spec_from_file_location('db.models', ROOT / 'shared/db/models.py')
models = importlib.util.module_from_spec(spec); sys.modules['db.models'] = models; spec.loader.exec_module(models)
for k in vars(models): setattr(db, k, getattr(models, k))
from src.services.bar_provenance import observe_bar_writes, BarProvenance, BarRevision
url = os.environ.get('RZY_PG_URL')
if url:
    from sqlalchemy.engine import make_url
    u = make_url(url)
    assert u.drivername.startswith('postgresql') and u.host in ('localhost', '127.0.0.1') and u.database in ('schemaprobe', 'rzyprobe')
    engine = create_engine(url)
    with engine.begin() as c: c.execute(text('DROP SCHEMA public CASCADE')); c.execute(text('CREATE SCHEMA public'))
else:
    engine = create_engine('sqlite:///' + str(pathlib.Path(tempfile.mkdtemp()) / 'p.db'))
models.Base.metadata.create_all(engine); Session = sessionmaker(bind=engine, autoflush=True)
base = datetime(2026, 10, 21, 13, 30)
M5 = models.TimeFrame.M5
def row(i, c, v=1000., ts=None):
    return dict(stock_id=1, ts=ts or base + timedelta(minutes=5 * i), timeframe=M5, open=c - .1, high=c + .3, low=c - .3, close=c, volume=v, adj_close=None, session='REGULAR')
_ids = [0]
def upsert(s, rows):
    for r in rows:
        p = s.scalar(select(models.Price).where(models.Price.stock_id == 1, models.Price.ts == r['ts'], models.Price.timeframe == M5))
        if p is None:
            _ids[0] += 1; s.add(models.Price(id=_ids[0], **r))
        else:
            for k in ('open', 'high', 'low', 'close', 'volume'): setattr(p, k, r[k])
    s.commit()
out = {}
with Session() as s:
    s.add(models.Stock(id=1, symbol='TEST', market=models.Market.US, exchange=models.Exchange.NASDAQ, name='t', active=True, delisted=False)); s.commit()
    stock = s.get(models.Stock, 1)
    # A bar that existed before tracing began (no provenance).
    upsert(s, [row(0, 99.5)])
    day = [row(i, 100 + i * .1) for i in range(6)]
    now = base + timedelta(minutes=31)          # bars 0..4 complete, bar 5 (13:55) forming
    sent = copy.deepcopy(day)
    c1 = observe_bar_writes(s, stock, '5m', day, 'unusual_whales', now); upsert(s, day)
    assert day == sent, 'observation must not modify the rows being written'
    assert c1['new'] == 5 and c1['ohlc_revisions'] == 1, c1          # bar 0 pre-existed with other values
    r0 = s.scalar(select(BarRevision))
    assert r0.classification == 'previous_provider_unknown' and r0.previous_provider is None and r0.provider == 'unusual_whales'
    out['first_write_and_unknown_previous'] = True
    c2 = observe_bar_writes(s, stock, '5m', day, 'unusual_whales', now + timedelta(minutes=1)); upsert(s, day)
    assert c2['unchanged'] == 6 and c2['ohlc_revisions'] == 0, c2
    out['unchanged_rewrite_records_nothing'] = True
    # yfinance answers with slightly different values for completed bars 1-2 and the forming bar.
    yf = copy.deepcopy(day); yf[1]['close'] += .08; yf[2]['high'] += .05; yf[5]['close'] += .2
    t3 = now + timedelta(minutes=5)
    c3 = observe_bar_writes(s, stock, '5m', yf, 'yfinance', t3); upsert(s, yf)
    revs = s.scalars(select(BarRevision).where(BarRevision.fetched_at == t3)).all()
    cls = {r.ts: r.classification for r in revs}
    assert cls[day[1]['ts']] == 'cross_provider' and cls[day[2]['ts']] == 'cross_provider' and cls[day[5]['ts']] == 'cross_provider'
    r1 = next(r for r in revs if r.ts == day[1]['ts'])
    assert r1.previous_provider == 'unusual_whales' and r1.provider == 'yfinance' and r1.changed_fields == ['close']
    assert r1.before['close'] == day[1]['close'] and r1.after['close'] == yf[1]['close'] and r1.max_rel_change > 1e-4
    assert abs(r1.bar_age_minutes - (t3 - (day[1]['ts'] + timedelta(minutes=5))).total_seconds() / 60) < 1e-9
    assert s.get(BarProvenance, (1, '5m', day[1]['ts'])).provider == 'yfinance'
    # A bar still forming at fetch time (14:05-14:10 seen at 14:06) is labelled as such.
    forming = [row(7, 100.7)]
    observe_bar_writes(s, stock, '5m', forming, 'unusual_whales', t3); upsert(s, forming)
    f2 = copy.deepcopy(forming); f2[0]['close'] += .05
    observe_bar_writes(s, stock, '5m', f2, 'unusual_whales', t3 + timedelta(seconds=30)); upsert(s, f2)
    assert s.scalar(select(BarRevision.classification).where(BarRevision.ts == forming[0]['ts'])) == 'same_provider_forming_bar'
    out['cross_provider_revision'] = True
    yf2 = copy.deepcopy(yf); yf2[1]['close'] -= .01
    observe_bar_writes(s, stock, '5m', yf2, 'yfinance', t3 + timedelta(minutes=5)); upsert(s, yf2)
    assert s.scalar(select(BarRevision.classification).where(BarRevision.fetched_at == t3 + timedelta(minutes=5))) == 'same_provider'
    out['same_provider_correction'] = True
    vol = copy.deepcopy(yf2); vol[3]['volume'] += 7
    c5 = observe_bar_writes(s, stock, '5m', vol, 'yfinance', t3 + timedelta(minutes=6)); upsert(s, vol)
    assert c5['volume_only'] == 1 and c5['ohlc_revisions'] == 0 and s.get(BarProvenance, (1, '5m', day[3]['ts'])).volume_only_revisions == 1
    c6 = observe_bar_writes(s, stock, '5m', vol, 'unusual_whales', t3 + timedelta(minutes=7)); upsert(s, vol)
    assert c6['provider_switch_same_values'] == 6 and c6['ohlc_revisions'] == 0
    out['volume_only_and_same_value_switch_counted'] = True
    snap = [row(0, 100, ts=base + timedelta(seconds=1))]
    c7 = observe_bar_writes(s, stock, '5m', snap, 'unusual_whales', t3 + timedelta(minutes=8))
    assert c7['off_grid'] == 1 and s.get(BarProvenance, (1, '5m', base + timedelta(seconds=1))).off_grid is True
    out['off_grid_flagged'] = True
    s.commit()
    # Failure isolation: an exception inside the SAVEPOINT leaves the outer write intact.
    try:
        with s.begin_nested():
            observe_bar_writes(s, stock, '5m', [dict(row(9, 1), ts='not-a-datetime')], 'x', t3)
    except Exception:
        pass
    upsert(s, [row(10, 101)])
    assert s.scalar(select(func.count()).select_from(models.Price).where(models.Price.ts == base + timedelta(minutes=50))) == 1
    out['failure_isolated'] = True
print(json.dumps(out))
