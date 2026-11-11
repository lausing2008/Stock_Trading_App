"""Real ingest_symbol on PostgreSQL: is a pooled connection held while the provider is asked?

Run by test_ingest_pool_hold.py when RZY_PG_URL names a disposable local database.
"""
import importlib.util
import json
import os
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

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
engine = create_engine(url, pool_size=2, max_overflow=0)       # a real QueuePool, as in production
with engine.begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE")); c.execute(text("CREATE SCHEMA public"))
Session = sessionmaker(bind=engine, autoflush=False, future=True)
db.SessionLocal = Session

from src.services import ingestion as ING  # noqa: E402
from src.adapters.base import OHLCV  # noqa: E402
models.Base.metadata.create_all(engine)
ING.SessionLocal = Session
ING._write_parquet = lambda *a, **k: None
ING._polygon_budget_available = lambda: True
ING._skip_during_primary_cooldown = lambda *a, **k: False
ING.primary_provider = lambda market, tf: "yfinance"

held_during_fetch = []


class Fake:
    name = "yfinance"

    def fetch_ohlcv(self, symbol, start, end, timeframe):
        held_during_fetch.append(engine.pool.checkedout())     # connections out while "on the network"
        today = datetime.now(timezone.utc).date()
        ts = pd.to_datetime([today - timedelta(days=2), today - timedelta(days=1), today])
        return OHLCV(symbol, timeframe, pd.DataFrame({"ts": ts, "open": 10.0, "high": 11.0, "low": 9.0,
                                                      "close": 10.5, "volume": 1000.0, "adj_close": 10.5}))


ING.get_adapter = lambda *a, **k: Fake()
ING.get_adapters = lambda *a, **k: [Fake()]
with Session() as s:
    s.add(models.Stock(id=1, symbol="TEST", market=models.Market.US, exchange=models.Exchange.NASDAQ,
                       name="t", active=True, delisted=False))
    s.add(models.Price(stock_id=1, ts=datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0,
                                                                         microsecond=0) - timedelta(days=5),
                       timeframe=models.TimeFrame("1d"), open=9, high=10, low=8, close=9.5, volume=900))
    s.commit()


def stored():
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM prices WHERE stock_id = 1")).scalar()


out = {}
r = ING.ingest_symbol("TEST", timeframe="1d")
out["incremental"] = {"held_during_fetch": held_during_fetch[-1], "advanced": r.get("advanced"),
                      "stored": stored(), "held_after": engine.pool.checkedout()}
r = ING.ingest_symbol("TEST", timeframe="1d", force=True)
out["force"] = {"held_during_fetch": held_during_fetch[-1], "inserted": r.get("inserted"),
                "stored": stored(), "held_after": engine.pool.checkedout()}
print(json.dumps(out, default=str))
