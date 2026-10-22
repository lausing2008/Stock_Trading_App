"""Bar-authority rules at their boundaries, with an injected clock (review of OD-01 fix 1).

Calls the real authorize_bar_writes / observe_bar_writes / primary_provider on PostgreSQL.
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
from datetime import timedelta  # noqa: E402
from src.services import bar_provenance as BP  # noqa: E402
from src.adapters import registry as REG  # noqa: E402
models.Base.metadata.create_all(engine)

with Session() as s:
    s.add(models.Stock(id=1, symbol="TEST", market=models.Market.US, exchange=models.Exchange.NASDAQ,
                       name="t", active=True, delisted=False)); s.commit()
T = lambda h, m, sec=0: datetime(2026, 10, 20, h, m, sec)        # Tuesday, naive UTC (EDT: ET = UTC-4)
out = {}


def write(provider, ts, close, now, tf="5m"):
    """One fetch of one bar: authorise, trace, then upsert what was allowed."""
    with Session() as s:
        stock = s.get(models.Stock, 1)
        tfv = {"5m": models.TimeFrame.M5, "1h": models.TimeFrame.H1, "1d": models.TimeFrame.D1}[tf]
        row = {"stock_id": 1, "ts": ts, "timeframe": tfv, "open": close, "high": close + 1, "low": close - 1,
               "close": close, "volume": 1000.0}
        allowed, counts = BP.authorize_bar_writes(s, stock, tf, [row], provider, "unusual_whales", now=now)
        BP.observe_bar_writes(s, stock, tf, allowed, provider, now=now)
        for r in allowed:
            p = s.query(models.Price).filter_by(stock_id=1, ts=ts, timeframe=tfv).one_or_none()
            if p is None:
                s.add(models.Price(**r))
            else:
                p.close = r["close"]; p.open = r["open"]; p.high = r["high"]; p.low = r["low"]
        s.commit()
        return counts.get("protected", 0) == 0


def close_of(ts, tf=models.TimeFrame.M5):
    with Session() as s:
        return s.query(models.Price).filter_by(stock_id=1, ts=ts, timeframe=tf).one().close


# B1: UW's 30-second forming snapshot is completed by the fallback; then the authority takes over.
b = T(14, 0)
out["b1"] = [write("unusual_whales", b, 100.0, T(14, 0, 30)), write("yfinance", b, 104.0, T(14, 5, 30)),
             close_of(b), write("unusual_whales", b, 104.2, T(14, 6)), write("yfinance", b, 103.0, T(14, 7)), close_of(b)]
# Exact completion boundary: written at ts+5m is owned; at ts+5m-1s is still a forming snapshot.
b2, b3 = T(14, 10), T(14, 20)
out["boundary"] = [write("unusual_whales", b2, 50.0, T(14, 15, 0)), write("yfinance", b2, 51.0, T(14, 16)),
                   write("unusual_whales", b3, 60.0, T(14, 24, 59)), write("yfinance", b3, 61.0, T(14, 26))]
# Oct 21 pattern: a same-day bar an hour old, owned by the authority, is not rewritten.
b4 = T(13, 0)
out["hour_old"] = [write("unusual_whales", b4, 70.0, T(13, 6)), write("yfinance", b4, 70.5, T(14, 5)), close_of(b4)]
# A fallback may correct its own gap fill.
b5 = T(14, 30)
out["self_correction"] = [write("yfinance", b5, 80.0, T(14, 36)), write("yfinance", b5, 80.3, T(14, 41)), close_of(b5)]
# Unchanged re-send after completion establishes ownership (re-review residual).
b6 = T(15, 0)
out["unchanged_resend"] = [write("unusual_whales", b6, 90.0, T(15, 0, 30)), write("unusual_whales", b6, 90.0, T(15, 5, 30)),
                           write("yfinance", b6, 94.0, T(15, 6)), close_of(b6)]
# Daily: a close-burst write (16:00:30 ET) does not lock out the settled close; after 16:45 ET it does.
d = datetime(2026, 10, 20)
out["daily_settle"] = [write("unusual_whales", d, 300.0, T(20, 0, 30), "1d"), write("yfinance", d, 301.0, T(20, 30), "1d"),
                       close_of(d, models.TimeFrame.D1), write("unusual_whales", d, 301.2, T(20, 50), "1d"),
                       write("yfinance", d, 299.0, T(21, 0), "1d"), close_of(d, models.TimeFrame.D1)]
# Session dates across UTC midnight (EDT and EST) and HK.
out["dates"] = [BP._session_date("5m", datetime(2026, 10, 21, 23, 30), "US").date().isoformat(),
                BP._session_date("5m", datetime(2026, 11, 5, 0, 30), "US").date().isoformat(),
                BP._session_date("5m", datetime(2026, 10, 21, 17, 0), "HK").date().isoformat()]
# 1h bar 15:30 ET completes at the 16:00 close, not 16:30.
out["hour_bar_done"] = BP._completed_at("1h", T(19, 30), "US").isoformat()
# B2: a cooldown (supports() False) does not change the authority.
uw = REG._registry.get("unusual_whales")
real = uw.supports
uw.supports = lambda *a: False
out["primary_in_cooldown"] = [REG.primary_provider("US", "5m"), REG.primary_provider("US", "1d"),
                              REG.primary_provider("HK", "5m"), REG.get_adapters("US", "5m")[0].name]
uw.supports = real
# S1: after a trace failure, a fallback cannot even correct its own bar; the authority can.
with Session() as s:
    BP.mark_trace_failed(s, s.get(models.Stock, 1), "5m", [{"ts": b5}]); s.commit()
out["trace_failed"] = [write("yfinance", b5, 80.9, T(14, 50)), write("unusual_whales", b5, 81.0, T(14, 51))]
# S2: no decision -> a fallback only adds bars newer than the head.
rows = [{"ts": T(14, 30)}, {"ts": T(15, 0)}]
out["fail_closed"] = [len(BP.fail_closed_rows(rows, "5m", "yfinance", "unusual_whales", T(14, 45))),
                      len(BP.fail_closed_rows(rows, "5m", "unusual_whales", "unusual_whales", T(14, 45)))]
with Session() as s:
    rec = s.get(BP.BarSessionAuthority, (1, "5m", datetime(2026, 10, 20)))
    out["record"] = [rec.authority, rec.protected, rec.replaced, round(rec.max_protected_divergence or 0, 4), rec.trace_failed]
print(json.dumps(out, default=str))
