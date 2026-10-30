"""Real ingest_symbol on PostgreSQL: a UW cooldown skips a fresh symbol, fetches a stale one.

Run by test_uw_cooldown_skip.py when RZY_PG_URL names a disposable local database.
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
engine = create_engine(url)
with engine.begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE")); c.execute(text("CREATE SCHEMA public"))
Session = sessionmaker(bind=engine)
db.SessionLocal = Session

import common.market_calendar as CAL  # noqa: E402
from src.services import ingestion as ING  # noqa: E402
from src.services.bar_provenance import DailyIngestRecord  # noqa: E402
from src.adapters.base import OHLCV  # noqa: E402
models.Base.metadata.create_all(engine)
ING.SessionLocal = Session
ING._write_parquet = lambda *a, **k: None
ING._polygon_budget_available = lambda: True
ING._count_cooldown_skip = lambda tf: None
ING._bust_live_price_cache = lambda: None
CAL.is_regular_session = lambda market, now=None: True     # the run is "inside the session"
ING.primary_provider = lambda market, tf: "unusual_whales"

fetches = []


class Fake:
    name = "yfinance"

    def fetch_ohlcv(self, symbol, start, end, timeframe):
        fetches.append(timeframe)
        today = datetime.now(timezone.utc).date()
        ts = pd.to_datetime([today - timedelta(days=1), today])
        return OHLCV(symbol, timeframe, pd.DataFrame({"ts": ts, "open": 10.0, "high": 11.0, "low": 9.0,
                                                      "close": 10.5, "volume": 1000.0, "adj_close": 10.5}))


adapters = {"list": [Fake()]}                                 # UW absent = it is cooling down
ING.get_adapter = lambda *a, **k: Fake()
ING.get_adapters = lambda *a, **k: adapters["list"]
with Session() as s:
    s.add(models.Stock(id=1, symbol="TEST", market=models.Market.US, exchange=models.Exchange.NASDAQ,
                       name="t", active=True, delisted=False)); s.commit()

out = {}
# No stored bars yet: the batch branch fetches regardless.
out["first"] = {"skipped": ING.ingest_symbol("TEST", timeframe="1d").get("skipped")}
out["fetches_after_first"] = len(fetches)
# Stored bars + a successful fetch moments ago: skip.
out["fresh"] = {"skipped": ING.ingest_symbol("TEST", timeframe="1d").get("skipped"), "fetches": len(fetches)}
# Last success 20 minutes ago: fall back and fetch.
with Session() as s:
    rec = s.get(DailyIngestRecord, 1)
    rec.last_success_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=20)
    s.commit()
out["stale"] = {"skipped": ING.ingest_symbol("TEST", timeframe="1d").get("skipped"), "fetches": len(fetches)}
# UW back in the list: never skipped.
class _UW(Fake):
    name = "unusual_whales"
adapters["list"] = [_UW()]
out["uw_available"] = {"skipped": ING.ingest_symbol("TEST", timeframe="1d").get("skipped"), "fetches": len(fetches)}
print(json.dumps(out, default=str))
