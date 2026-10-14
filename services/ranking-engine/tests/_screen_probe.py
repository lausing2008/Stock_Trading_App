"""/rankings/screen against a real database (SQLite), for the copilot's scope and coverage.

Calls the route FUNCTION with a real session, so the SQL actually runs. Prints JSON.
"""
import json
import pathlib
import sys
import tempfile
import types
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "ranking-engine"))
for m in ["redis", "structlog", "yfinance"]:
    sys.modules.setdefault(m, MagicMock())
import common                                                        # noqa: E402
_cfg = types.ModuleType("common.config")
_DB = "sqlite:///" + str(pathlib.Path(tempfile.mkdtemp()) / "screen.db")
class _Settings(types.SimpleNamespace):
    def __getattr__(self, _name):          # any service URL / flag the module reads at import
        return ""


_SETTINGS = _Settings(database_url=_DB, admin_password=None, jwt_secret="x")
_cfg.get_settings = lambda: _SETTINGS
sys.modules["common.config"] = _cfg
common.config = _cfg

from sqlalchemy import create_engine                                 # noqa: E402
from sqlalchemy.orm import sessionmaker                              # noqa: E402
from db.models import (Base, Exchange, Market, Ranking, Signal,      # noqa: E402
                       SignalHorizon, SignalType, Stock)
from src.api import routes                                           # noqa: E402

eng = create_engine(_DB)
Base.metadata.create_all(eng)
S = sessionmaker(bind=eng)
today = date.today()
with S() as s:
    rows = [("AAA", Market.US, 80, SignalType.BUY), ("BBB", Market.US, 60, SignalType.HOLD),
            ("CCC", Market.HK, 75, SignalType.BUY), ("DDD", Market.US, 90, SignalType.SELL)]
    for i, (sym, mk, score, sig) in enumerate(rows, 1):
        st = Stock(symbol=sym, name=sym, market=mk, exchange=Exchange.NASDAQ, currency="USD",
                   sector="Technology" if sym != "DDD" else "Energy")
        s.add(st); s.flush()
        s.add(Ranking(id=i, stock_id=st.id, as_of=today, score=score, technical=50,
                      momentum=score - 10, volatility=30))
        s.add(Signal(id=i, stock_id=st.id, ts=datetime.utcnow(), signal=sig,
                     horizon=SignalHorizon.SWING, confidence=55.0, source="t"))
    # Stale ranking: outside the 60-day window, so never screened.
    st = Stock(symbol="OLD", name="Old", market=Market.US, exchange=Exchange.NYSE, currency="USD")
    s.add(st); s.flush()
    s.add(Ranking(id=9, stock_id=st.id, as_of=today - timedelta(days=90), score=99, technical=50,
                  momentum=50, volatility=30))
    s.commit()

D = dict(market=None, sector=None, signal=None, min_confidence=None, min_score=None,
         max_score=None, min_momentum=None, min_technical=None, min_rs=None, min_growth=None,
         sort_by="score", limit=50)
R = {}
with S() as s:
    full = routes.screen(**D, symbols=None, session=s)
    R["unscoped"] = {"total": full["total"], "universe": full["universe"],
                     "symbols": [x["symbol"] for x in full["items"]],
                     "has_scope_keys": "requested" in full}
    scoped = routes.screen(**{**D, "min_score": 70}, symbols="aaa, BBB,OLD,ZZZ", session=s)
    R["scoped"] = {"total": scoped["total"], "universe": scoped["universe"],
                   "requested": scoped["requested"], "unranked": scoped["unranked"],
                   "symbols": [x["symbol"] for x in scoped["items"]]}
    empty = routes.screen(**D, symbols=",", session=s)
    R["empty_scope"] = {"total": empty["total"], "universe": empty["universe"]}
print(json.dumps(R))
