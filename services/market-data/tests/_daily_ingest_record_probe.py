"""Real ingest_symbol on PostgreSQL with a fake provider: per-symbol daily fetch evidence.

Run by test_daily_ingest_record.py when RZY_PG_URL names a disposable local database.
"""
import importlib.util
import json
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
from src.services.bar_provenance import DailyIngestRecord  # noqa: E402
from src.adapters.base import OHLCV  # noqa: E402
models.Base.metadata.create_all(engine)
ING.SessionLocal = Session
ING._write_parquet = lambda *a, **k: None
ING._polygon_budget_available = lambda: True

state = {"fail": False, "days": ["2026-10-16", "2026-10-19", "2026-10-20"]}


class Fake:
    name = "fakeprov"

    def fetch_ohlcv(self, symbol, start, end, timeframe):
        if state["fail"]:
            raise RuntimeError("provider down")
        ts = pd.to_datetime(state["days"])
        return OHLCV(symbol, timeframe, pd.DataFrame({"ts": ts, "open": 10.0, "high": 11.0, "low": 9.0,
                                                      "close": 10.5, "volume": 1000.0, "adj_close": 10.5}))


ING.get_adapter = lambda *a, **k: Fake()
ING.get_adapters = lambda *a, **k: [Fake()]
with Session() as s:
    s.add(models.Stock(id=1, symbol="TEST", market=models.Market.US, exchange=models.Exchange.NASDAQ,
                       name="t", active=True, delisted=False)); s.commit()
out = {}
t0 = datetime.now(timezone.utc).replace(tzinfo=None)
ING.ingest_symbol("TEST", timeframe="1d")
with Session() as s:
    r = s.get(DailyIngestRecord, 1)
    out["first"] = dict(ok_at=bool(r.last_success_at and r.last_success_at >= t0.replace(microsecond=0)),
                        observed=r.last_observed_session.isoformat(), provider=r.last_provider, failure=r.last_failure_at)
state["fail"] = True
try:
    ING.ingest_symbol("TEST", timeframe="1d")
    out["raised"] = False
except Exception:
    out["raised"] = True
with Session() as s:
    r = s.get(DailyIngestRecord, 1)
    out["after_failure"] = dict(observed=r.last_observed_session.isoformat(), failure=bool(r.last_failure_at),
                                error=r.last_error, success_kept=r.last_success_at is not None)
state.update(fail=False, days=["2026-10-20", "2026-10-21"])
ING.ingest_symbol("TEST", timeframe="1d")
with Session() as s:
    out["advanced"] = s.get(DailyIngestRecord, 1).last_observed_session.isoformat()
print(json.dumps(out, default=str))
