"""One authoritative provider per completed bar (OD-01 fix 1) through the REAL ingest_symbol.

Fake unusual_whales (primary) and yfinance (fallback) adapters take turns, reproducing the
Oct 21 flip-flop. PostgreSQL only (pg_insert); RZY_PG_URL must name a disposable local DB.
"""
import importlib.util
import json
import time as _time
import os as _os
_os.environ["TZ"] = "UTC"; _time.tzset()   # production containers run in UTC; date.today() must agree
import os
import pathlib
import sys
import types
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "services/market-data")]
db = types.ModuleType("db"); db.__path__ = [str(ROOT / "shared/db")]; sys.modules["db"] = db
spec = importlib.util.spec_from_file_location("db.models", ROOT / "shared/db/models.py")
models = importlib.util.module_from_spec(spec); sys.modules["db.models"] = models; spec.loader.exec_module(models)
for k in vars(models):
    setattr(db, k, getattr(models, k))
url = os.environ["RZY_PG_URL"]
u = make_url(url)
assert u.host in ("localhost", "127.0.0.1") and u.database in ("schemaprobe", "rzyprobe"), "disposable local database only"
engine = create_engine(url)
with engine.begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE")); c.execute(text("CREATE SCHEMA public"))
Session = sessionmaker(bind=engine)
db.SessionLocal = Session

from src.services import ingestion as ING  # noqa: E402
from src.services.bar_provenance import BarSessionAuthority, BarProvenance  # noqa: E402
from src.adapters.base import OHLCV  # noqa: E402
models.Base.metadata.create_all(engine)
ING.SessionLocal = Session
ING._write_parquet = lambda *a, **k: None
ING._polygon_budget_available = lambda: True


from datetime import timedelta  # noqa: E402

now = datetime.now(timezone.utc).replace(tzinfo=None)
day = (now - timedelta(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)    # a past session, 10:00 ET
b1, b2, b3 = day, day + timedelta(minutes=5), day + timedelta(minutes=10)
forming = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)     # not yet completed
answer = {"unusual_whales": {}, "yfinance": {}}
down = set()


class Fake:
    def __init__(self, name): self.name = name

    def fetch_ohlcv(self, symbol, start, end, timeframe):
        if self.name in down:
            raise RuntimeError(f"{self.name} down")
        bars = answer[self.name]
        ts = pd.to_datetime([t.replace(tzinfo=timezone.utc) for t in bars])
        c = list(bars.values())
        return OHLCV(symbol, timeframe, pd.DataFrame({"ts": ts, "open": c, "high": [x + 1 for x in c],
            "low": [x - 1 for x in c], "close": c, "volume": 1000.0, "adj_close": c}))


UW, YF = Fake("unusual_whales"), Fake("yfinance")
ING.get_adapters = lambda *a, **k: [UW, YF]
ING.get_adapter = lambda name, *a, **k: {"unusual_whales": UW, "yfinance": YF}[name]
with Session() as s:
    s.add(models.Stock(id=1, symbol="TEST", market=models.Market.US, exchange=models.Exchange.NASDAQ,
                       name="t", active=True, delisted=False)); s.commit()
    # A completed bar stored BEFORE the trace existed: writer unknown.
    b0 = day - timedelta(minutes=5)
    s.add(models.Price(stock_id=1, ts=b0, timeframe=models.TimeFrame.M5, open=50, high=51, low=49, close=50, volume=10)); s.commit()


def closes():
    with Session() as s:
        return {p.ts: p.close for p in s.query(models.Price).filter_by(stock_id=1, timeframe=models.TimeFrame.M5)}


def ingest():
    r = ING.ingest_symbol("TEST", timeframe="5m"); assert "authority" in r, r; return r["authority"]


out = {}
answer["unusual_whales"] = {b1: 100.0, b2: 101.0, forming: 102.0}
out["s1_uw"] = ingest(); c = closes(); out["s1"] = [c[b1], c[b2], c[forming]]
down.add("unusual_whales")                                   # outage: fallback answers
answer["yfinance"] = {b0: 55.0, b1: 100.4, b2: 101.4, b3: 103.0, forming: 102.5}
out["s2_yf"] = ingest(); c = closes(); out["s2"] = [c[b0], c[b1], c[b2], c[b3], c[forming]]
down.clear()                                                 # authority back: corrects its own bar, replaces the gap fill once
answer["unusual_whales"] = {b0: 50.2, b1: 100.1, b2: 101.0, b3: 103.3}
out["s3_uw"] = ingest(); c = closes(); out["s3"] = [c[b0], c[b1], c[b3]]
down.add("unusual_whales")                                   # another outage: no cycle
answer["yfinance"] = {b0: 56.0, b1: 99.0, b2: 99.0, b3: 104.0}
out["s4_yf"] = ingest(); c = closes(); out["s4"] = [c[b0], c[b1], c[b2], c[b3]]
# Daily bars: a completed session's close is owned by the authority too.
# A symbol's FIRST daily fetch uses yfinance (existing initial-history rule): the authority then
# replaces that completed bar once, after which a yfinance rewrite is refused.
down.clear(); d1 = datetime(day.year, day.month, day.day)
answer["yfinance"] = {d1: 199.0}
ING.ingest_symbol("TEST", timeframe="1d")
answer["unusual_whales"] = {d1: 200.0}
out["daily_uw"] = ING.ingest_symbol("TEST", timeframe="1d")["authority"]
down.add("unusual_whales"); answer["yfinance"] = {d1: 200.9}
out["daily_yf"] = ING.ingest_symbol("TEST", timeframe="1d")["authority"]
with Session() as s:
    out["daily_close"] = s.query(models.Price).filter_by(stock_id=1, timeframe=models.TimeFrame.D1, ts=d1).one().close
with Session() as s:
    recs = s.query(BarSessionAuthority).filter_by(timeframe="5m").all()
    out["authority"] = [(r.authority, r.protected, r.replaced) for r in recs]
    out["writers"] = sorted({(p.ts.isoformat(), p.provider) for p in s.query(BarProvenance).filter(BarProvenance.timeframe == "5m", BarProvenance.ts.in_([b0, b1, b2, b3]))})
print(json.dumps(out, default=str))
