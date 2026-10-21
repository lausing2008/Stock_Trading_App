"""Daily ('1d') bar trace on real ORM models (SQLite; disposable PostgreSQL via RZY_PG_URL); no network."""
import copy, importlib.util, json, os, pathlib, sys, tempfile, types
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.orm import sessionmaker
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'shared'), str(ROOT / 'services/market-data')]
db = types.ModuleType('db'); db.__path__ = [str(ROOT / 'shared/db')]; sys.modules['db'] = db
spec = importlib.util.spec_from_file_location('db.models', ROOT / 'shared/db/models.py')
models = importlib.util.module_from_spec(spec); sys.modules['db.models'] = models; spec.loader.exec_module(models)
for k in vars(models): setattr(db, k, getattr(models, k))
from src.services.bar_provenance import observe_bar_writes, revision_magnitude, BarProvenance, BarRevision
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
D1 = models.TimeFrame.D1
NY, HKT = ZoneInfo('America/New_York'), ZoneInfo('Asia/Hong_Kong')
out = {}

# --- revision_magnitude: pure unit checks --------------------------------------------------
def bar(o, h, l, c, v=1.):
    return dict(open=o, high=h, low=l, close=c, volume=v)
b = bar(984.0, 990.0, 980.0, 985.0)
assert revision_magnitude(b, dict(b)) == 'rounding'
assert revision_magnitude(b, dict(b, volume=9e9)) == 'rounding'                 # volume never decides
assert revision_magnitude(b, dict(b, close=985.0 + 0.0197)) == 'rounding'       # 2e-5 rel at ~$985 = $0.0197
assert revision_magnitude(b, dict(b, close=985.0 + 0.0200)) == 'material'
assert revision_magnitude(b, dict(b, close=985.0044)) == 'rounding'             # MU-scale (4.4e-3 on ~$985)
cheap = bar(10.0, 10.2, 9.9, 10.1)
assert revision_magnitude(cheap, dict(cheap, close=10.105)) == 'rounding'       # half a cent: abs bound dominates
assert revision_magnitude(cheap, dict(cheap, close=10.11)) == 'material'        # a full cent on $10 is material
assert revision_magnitude(cheap, dict(cheap, open=10.004, close=10.11)) == 'material'   # ANY field material -> material
assert revision_magnitude(cheap, dict(cheap, open=10.004, low=9.896)) == 'rounding'
assert revision_magnitude({k: str(v) for k, v in cheap.items()}, dict(cheap, close=10.2)) == 'material'   # JSON-ish values
out['magnitude_unit'] = True

def drow(stock_id, d, c, tz, v=1e6):
    # Ingestion passes the session date at 00:00 local wall time WITH an offset; the stored key drops it.
    return dict(stock_id=stock_id, ts=datetime(d.year, d.month, d.day, tzinfo=tz), timeframe=D1, open=c - 1, high=c + 2,
                low=c - 2, close=c, volume=v, adj_close=None, session='REGULAR')
_ids = [0]
def upsert(s, rows):
    for r in rows:
        k = r['ts'].replace(tzinfo=None)
        p = s.scalar(select(models.Price).where(models.Price.stock_id == r['stock_id'], models.Price.ts == k, models.Price.timeframe == D1))
        if p is None:
            _ids[0] += 1; s.add(models.Price(id=_ids[0], **dict(r, ts=k)))
        else:
            for f in ('open', 'high', 'low', 'close', 'volume'): setattr(p, f, r[f])
    s.commit()
def revs_at(s, t):
    return {r.ts: r for r in s.scalars(select(BarRevision).where(BarRevision.fetched_at == t)).all()}

with Session() as s:
    s.add(models.Stock(id=1, symbol='MU', market=models.Market.US, exchange=models.Exchange.NASDAQ, name='m', active=True, delisted=False))
    s.add(models.Stock(id=2, symbol='0700.HK', market=models.Market.HK, exchange=models.Exchange.HKEX, name='t', active=True, delisted=False))
    s.commit()
    us, hk = s.get(models.Stock, 1), s.get(models.Stock, 2)
    days = [datetime(2026, 10, d).date() for d in (14, 15, 16, 19, 20, 21)]    # Wed..Wed (no weekend dates)
    t0 = datetime(2026, 10, 21, 13, 30)                                         # 09:30 ET: Oct 21 US session forming
    base = [drow(1, d, 980 + i, NY) for i, d in enumerate(days)]
    sent = copy.deepcopy(base)
    c1 = observe_bar_writes(s, us, '1d', base, 'unusual_whales', t0); upsert(s, base)
    assert base == sent, 'observation must not modify the rows being written'
    assert c1['new'] == 6 and c1['ohlc_revisions'] == 0 and c1['off_grid'] == 0, c1
    p = s.get(BarProvenance, (1, '1d', datetime(2026, 10, 20)))
    assert p is not None and p.provider == 'unusual_whales' and p.off_grid is False, 'key is the wall-clock date, offset dropped'
    assert s.scalar(select(func.count()).select_from(BarRevision)) == 0
    out['daily_new_bar'] = True
    c2 = observe_bar_writes(s, us, '1d', base, 'unusual_whales', t0 + timedelta(minutes=1)); upsert(s, base)
    assert c2['unchanged'] == 6 and c2['ohlc_revisions'] == 0, c2
    out['daily_unchanged'] = True

    # yfinance at 13:35 UTC: rounding-scale restatement of Oct 20, material of Oct 19, and the forming Oct 21 moves.
    t1 = t0 + timedelta(minutes=5)
    yf = copy.deepcopy(base)
    yf[4]['close'] += 0.0044          # ~4.5e-6 relative, the MU 2026-10-21 scale
    yf[3]['close'] += 0.35            # material
    yf[5]['close'] += 1.5             # current session, still forming
    c3 = observe_bar_writes(s, us, '1d', yf, 'yfinance', t1); upsert(s, yf)
    assert c3['ohlc_revisions'] == 3, c3
    r = revs_at(s, t1)
    k20, k19, k21 = datetime(2026, 10, 20), datetime(2026, 10, 19), datetime(2026, 10, 21)
    assert r[k20].classification == 'cross_provider' and r[k20].previous_provider == 'unusual_whales' and r[k20].provider == 'yfinance'
    assert revision_magnitude(r[k20].before, r[k20].after) == 'rounding' and r[k20].changed_fields == ['close']
    out['daily_rounding_restatement'] = True
    assert r[k19].classification == 'cross_provider' and revision_magnitude(r[k19].before, r[k19].after) == 'material'
    out['daily_material_restatement'] = True
    # Oct 20 closed 16:00 EDT = 20:00 UTC; fetched 13:35 UTC next day -> 17h35m.
    assert abs(r[k20].bar_age_minutes - 1055) < 1e-9, r[k20].bar_age_minutes
    assert r[k21].classification == 'cross_provider_forming_bar' and r[k21].bar_age_minutes < 0
    out['daily_us_forming_vs_completed'] = True
    out['daily_cross_provider'] = True
    t2 = t1 + timedelta(hours=1)
    yf2 = copy.deepcopy(yf); yf2[3]['close'] -= 0.10
    observe_bar_writes(s, us, '1d', yf2, 'yfinance', t2); upsert(s, yf2)
    r2 = revs_at(s, t2)
    assert set(r2) == {k19} and r2[k19].classification == 'same_provider' and r2[k19].previous_provider == 'yfinance'
    out['daily_same_provider'] = True
    # After the 20:00 UTC close (here: 21:15 UTC) Oct 21 is a completed bar.
    t3 = datetime(2026, 10, 21, 21, 15)
    yf3 = copy.deepcopy(yf2); yf3[5]['close'] += 0.5
    observe_bar_writes(s, us, '1d', yf3, 'yfinance', t3); upsert(s, yf3)
    r3 = revs_at(s, t3)
    assert r3[k21].classification == 'same_provider' and abs(r3[k21].bar_age_minutes - 75) < 1e-9

    # HK: Oct 21 closes 16:00 HKT = 08:00 UTC.
    hk_rows = [drow(2, d, 400 + i, HKT) for i, d in enumerate(days)]
    h0 = datetime(2026, 10, 21, 5, 0)                                           # 13:00 HKT: forming
    observe_bar_writes(s, hk, '1d', hk_rows, 'yfinance', h0); upsert(s, hk_rows)
    h1 = copy.deepcopy(hk_rows); h1[5]['close'] += 0.4; h1[4]['close'] += 0.4
    observe_bar_writes(s, hk, '1d', h1, 'yfinance', h0 + timedelta(minutes=30)); upsert(s, h1)
    rh = revs_at(s, h0 + timedelta(minutes=30))
    assert rh[k21].classification == 'same_provider_forming_bar' and rh[k20].classification == 'same_provider'
    assert abs(rh[k20].bar_age_minutes - (21 * 60 + 30)) < 1e-9            # Oct 20 08:00 UTC -> Oct 21 05:30 UTC
    h2 = copy.deepcopy(h1); h2[5]['close'] += 0.4
    t_hk_closed = datetime(2026, 10, 21, 8, 30)                               # 16:30 HKT: completed (US still pre-open)
    observe_bar_writes(s, hk, '1d', h2, 'polygon', t_hk_closed); upsert(s, h2)
    rh2 = revs_at(s, t_hk_closed)
    assert rh2[k21].classification == 'cross_provider' and abs(rh2[k21].bar_age_minutes - 30) < 1e-9
    out['daily_hk_forming_vs_completed'] = True

    # A non-session date (Sunday) counts as completed; age from the date's end.
    sun = drow(1, datetime(2026, 10, 18).date(), 970, NY)
    observe_bar_writes(s, us, '1d', [sun], 'yfinance', t0); upsert(s, [sun])
    sun2 = dict(sun, close=975.0)
    observe_bar_writes(s, us, '1d', [sun2], 'yfinance', t0 + timedelta(seconds=7))
    rs = s.scalar(select(BarRevision).where(BarRevision.ts == datetime(2026, 10, 18)))
    assert rs.classification == 'same_provider' and rs.bar_age_minutes > 0
    out['daily_non_session_completed'] = True

    # Bounded: rows older than RETENTION (35 d) are not traced at all.
    old = [drow(1, datetime(2026, 8, 3).date() + timedelta(days=i), 900, NY) for i in range(5)]
    c_old = observe_bar_writes(s, us, '1d', old, 'yfinance', t0)
    assert c_old == {} and s.scalar(select(func.count()).select_from(BarProvenance).where(BarProvenance.ts < datetime(2026, 9, 1))) == 0
    out['daily_bounded'] = True
    s.commit()
    # Intraday behaviour unchanged by the daily branch (still UTC-converted, off-grid checked).
    m5 = models.TimeFrame.M5
    i_row = dict(stock_id=1, ts=datetime(2026, 10, 21, 9, 30, tzinfo=NY), timeframe=m5, open=1, high=2, low=0.5, close=1.5, volume=10,
                 adj_close=None, session='REGULAR')
    observe_bar_writes(s, us, '5m', [i_row], 'yfinance', datetime(2026, 10, 21, 14, 0))
    assert s.get(BarProvenance, (1, '5m', datetime(2026, 10, 21, 13, 30))) is not None
    out['intraday_key_unchanged'] = True
print(json.dumps(out))
