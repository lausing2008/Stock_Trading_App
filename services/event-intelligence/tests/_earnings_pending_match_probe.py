"""Real _fetch_earnings_for_symbol on PostgreSQL: is each reported quarter written to its own row?

Run by test_earnings_pending_match.py when EARNINGS_PG_URL names a disposable local database.
EARNINGS_SRC may point at another copy of earnings.py (the sabotage check runs the old one).
Each scenario rebuilds a shape found in production on 2026-11-03
(docs/audits/2026-11-03-earnings-history-write-and-idle-transaction.md).
"""
import importlib.util
import json
import os
import pathlib
import sys
import types
from datetime import date, datetime

import pandas as pd
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared")]
db = types.ModuleType("db"); db.__path__ = [str(ROOT / "shared/db")]; sys.modules["db"] = db
spec = importlib.util.spec_from_file_location("db.models", ROOT / "shared/db/models.py")
models = importlib.util.module_from_spec(spec); sys.modules["db.models"] = models; spec.loader.exec_module(models)
for k in vars(models):
    setattr(db, k, getattr(models, k))
url = os.environ["EARNINGS_PG_URL"]
u = make_url(url)
assert u.host in ("localhost", "127.0.0.1") and u.database in ("schemaprobe", "earningsprobe"), "disposable local database only"
engine = create_engine(url)
with engine.begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE")); c.execute(text("CREATE SCHEMA public"))
models.Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, autoflush=False, future=True)
db.SessionLocal = Session
db.get_session = lambda: None

cfg = types.ModuleType("common.config"); cfg.get_settings = lambda: types.SimpleNamespace()
common = types.ModuleType("common"); common.__path__ = []; common.config = cfg
sys.modules.update({"common": common, "common.config": cfg})
warnings = []


class _Log:
    def info(self, *a, **k): pass
    def debug(self, *a, **k): pass
    def warning(self, event, **k): warnings.append((event, k))
    error = warning


structlog = types.ModuleType("structlog"); structlog.get_logger = lambda *a, **k: _Log()
sys.modules["structlog"] = structlog

PROVIDER: dict = {}


class _Ticker:
    def __init__(self, symbol):
        p = PROVIDER[symbol]
        self.earnings_history = pd.DataFrame(
            [{"epsEstimate": e, "epsActual": a} for _, e, a in p["history"]],
            index=pd.to_datetime([pe for pe, _, _ in p["history"]]))
        self.earnings_dates = pd.DataFrame(
            [{"Reported EPS": a} for _, a in p.get("announced", [])],
            index=pd.to_datetime([d for d, _ in p.get("announced", [])]))
        self.quarterly_financials = pd.DataFrame()
        self.calendar = ({"Earnings Date": [pd.Timestamp(p["calendar"])], "EPS Estimate": 1.0,
                          "Revenue Estimate": 1e9} if p.get("calendar") else {})


yf = types.ModuleType("yfinance"); yf.Ticker = _Ticker; sys.modules["yfinance"] = yf

src = pathlib.Path(os.environ.get("EARNINGS_SRC") or ROOT / "services/event-intelligence/src/services/earnings.py")
spec = importlib.util.spec_from_file_location("earnings_under_test", src)
E = importlib.util.module_from_spec(spec); spec.loader.exec_module(E)
E.SessionLocal = Session

D = date.fromisoformat
_ids = iter(range(1, 1000))


def stock(symbol, rows):
    with Session() as s:
        st = models.Stock(symbol=symbol, market=list(models.Market)[0], exchange=list(models.Exchange)[0], name=symbol)
        s.add(st); s.flush()
        for rd, act in rows:
            s.add(models.EarningsEvent(stock_id=st.id, report_date=D(rd), period="x", eps_actual=act))
        s.commit()
        return st.id


def rows(stock_id):
    with Session() as s:
        return [(r.report_date.isoformat(), r.eps_actual) for r in s.execute(
            select(models.EarningsEvent).where(models.EarningsEvent.stock_id == stock_id)
            .order_by(models.EarningsEvent.report_date)).scalars()]


def run(symbol, stock_id):
    stats: dict = {}
    n = E._fetch_earnings_for_symbol(symbol, stock_id, stats)
    return n, stats


out: dict = {}

# 1. UBER/VRTX: three quarters stored at their real dates, the newest quarter's pending row two
#    days off its real date, and the provider now holding the newest actual. The calendar has
#    moved on to the next quarter.
sid = stock("VRTX", [("2026-02-12", 5.03), ("2026-05-04", 4.47), ("2026-08-03", 4.73), ("2026-11-02", None)])
PROVIDER["VRTX"] = {
    "history": [("2025-12-31", 5.1, 5.03), ("2026-03-31", 4.3, 4.47), ("2026-06-30", 4.7, 4.73), ("2026-09-30", 4.6, 4.76)],
    "announced": [("2026-02-12", 5.03), ("2026-05-04", 4.47), ("2026-08-03", 4.73), ("2026-11-04", 4.76)],
    "calendar": "2027-02-10"}
n, st = run("VRTX", sid)
out["reported"] = dict(rows=rows(sid), dropped=st.get("dropped"), written=st.get("history_rows_written"),
                       newest=str(st.get("history_newest_written")))

# 2. Duplicate projections for one upcoming report (UBER 11-03/11-04), one with an issuer document.
sid = stock("UBER", [("2026-05-06", 0.13), ("2026-11-03", None), ("2026-11-04", None)])
with Session() as s:
    dup = s.execute(select(models.EarningsEvent).where(models.EarningsEvent.stock_id == sid,
                                                       models.EarningsEvent.report_date == D("2026-11-03"))).scalar_one()
    s.add(models.IssuerDocument(stock_id=sid, document_type="press_release", source_url="u", retrieved_at=datetime(2026, 11, 3),
                                event_id=dup.id))
    s.commit()
PROVIDER["UBER"] = {"history": [("2026-03-31", 0.7, 0.13)], "announced": [("2026-05-06", 0.13)], "calendar": "2026-11-04"}
run("UBER", sid)
with Session() as s:
    doc = s.execute(select(models.IssuerDocument)).scalar_one()
    linked = s.get(models.EarningsEvent, doc.event_id) if doc.event_id else None
out["duplicates"] = dict(rows=rows(sid), doc_on=None if linked is None else linked.report_date.isoformat())

# 3. HK: history rows without EPS sit at their period ends; the calendar projects the next
#    report. None of the old rows may move.
sid = stock("3323.HK", [("2025-12-31", None), ("2026-03-31", None), ("2026-06-30", None)])
PROVIDER["3323.HK"] = {"history": [("2025-12-31", None, None), ("2026-03-31", None, None), ("2026-06-30", None, None)],
                       "calendar": "2026-08-27"}
run("3323.HK", sid)
out["hk_no_eps"] = dict(rows=rows(sid))

# 4. A stand-in written at the period end before the real date was known, plus a pair already
#    duplicated (stand-in + real date, same actual).
sid = stock("DT", [("2026-06-30", 0.48), ("2026-08-05", 0.48), ("2026-09-30", 0.54)])
PROVIDER["DT"] = {"history": [("2026-06-30", 0.44, 0.48), ("2026-09-30", 0.49, 0.54)],
                  "announced": [("2026-08-05", 0.48), ("2026-11-03", 0.54)], "calendar": "2027-02-04"}
run("DT", sid)
out["stand_in"] = dict(rows=rows(sid))

# 5. The calendar still shows a report that already happened: nothing is restated.
sid = stock("AMZN", [("2026-10-29", 1.89)])
PROVIDER["AMZN"] = {"history": [("2026-09-30", 1.6, 1.89)], "announced": [("2026-10-29", 1.89)], "calendar": "2026-10-30"}
run("AMZN", sid)
out["stale_calendar"] = dict(rows=rows(sid))

# 6. One row failing must not drop the others.
sid = stock("COIN", [])
PROVIDER["COIN"] = {"history": [("2026-03-31", 1.0, 1.1), ("2026-06-30", 1.0, 1.2), ("2026-09-30", 1.0, 1.3)],
                    "announced": [("2026-05-01", 1.1), ("2026-07-31", 1.2), ("2026-10-30", 1.3)]}
real = getattr(E, "_history_target", None)
if real is not None:
    def flaky(s, stock_id, period_end, *a, **k):
        if period_end == D("2026-06-30"):
            s.execute(text("SELECT 1/0"))
        return real(s, stock_id, period_end, *a, **k)
    E._history_target = flaky
n, st = run("COIN", sid)
E._history_target = real
out["isolated"] = dict(rows=rows(sid), dropped=st.get("dropped"))
out["warnings"] = sorted({e for e, _ in warnings})
print(json.dumps(out, default=str))
