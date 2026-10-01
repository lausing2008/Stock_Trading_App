"""Child process for test_mu02_delivery_behaviour.py — the UTC-midnight case against a REAL DB.

market-data's conftest stubs `sqlalchemy`, so the candidate query cannot reach a database inside
the suite. The reviewer's point is that timestamp arithmetic cannot establish which event a run
selects; only executing the query can. So this runs the REAL `_pending_earnings_events()` against
real SQLAlchemy and a real SQLite database, on both sides of UTC midnight.

Usage: python3 _mu02_boundary_probe.py
"""
import json
import pathlib
import sys
import tempfile
import types
from datetime import date
from unittest.mock import MagicMock

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, create_engine, select
from sqlalchemy.orm import declarative_base, sessionmaker

_HERE = pathlib.Path(__file__).resolve()
SCHED_SRC = (_HERE.parents[1] / "src/services/scheduler.py").read_text()

Base = declarative_base()


class Stock(Base):
    __tablename__ = "stocks"
    id = Column(Integer, primary_key=True)
    symbol = Column(String)


class EarningsEvent(Base):
    __tablename__ = "earnings_events"
    id = Column(Integer, primary_key=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"))
    report_date = Column(Date)
    eps_actual = Column(Float)


def _load_query_fn():
    i = SCHED_SRC.index("def _pending_earnings_events")
    j = SCHED_SRC.find("\ndef ", i + 10)
    env = {"select": select, "Stock": Stock, "EarningsEvent": EarningsEvent,
           "date": date, "timedelta": __import__("datetime").timedelta}
    exec(compile(SCHED_SRC[i:j], "<query>", "exec"), env)
    return env["_pending_earnings_events"]


def main():
    path = pathlib.Path(tempfile.mkdtemp()) / "mu02.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    with S() as s:
        s.add(Stock(id=1, symbol="MU"))
        # MU's real event: reported 2026-09-30, structured EPS still NULL.
        s.add(EarningsEvent(id=1, stock_id=1, report_date=date(2026, 9, 30), eps_actual=None))
        # A FUTURE event for another symbol — the unbounded-window bug would admit this.
        s.add(Stock(id=2, symbol="FUT"))
        s.add(EarningsEvent(id=2, stock_id=2, report_date=date(2026, 10, 20), eps_actual=None))
        # An already-reported event — must never be selected.
        s.add(Stock(id=3, symbol="DONE"))
        s.add(EarningsEvent(id=3, stock_id=3, report_date=date(2026, 9, 30), eps_actual=4.2))
        s.commit()

    fn = _load_query_fn()
    out = {}
    syms = ["MU", "FUT", "DONE"]
    with S() as s:
        # The MU result published 2026-09-30 20:01 UTC. A scheduler run that evening and another
        # after UTC midnight must agree on WHICH event they are notifying about.
        before = fn(s, syms, date(2026, 9, 30))
        after = fn(s, syms, date(2026, 10, 1))
        out["before_midnight"] = {k: str(v) for k, v in before.items()}
        out["after_midnight"] = {k: str(v) for k, v in after.items()}
        out["same_event_date_for_MU"] = (
            before.get("MU") == after.get("MU") == date(2026, 9, 30))
        out["dedup_key_before"] = f"stockai:early_earnings_news:7:MU:{before.get('MU')}:results"
        out["dedup_key_after"] = f"stockai:early_earnings_news:7:MU:{after.get('MU')}:results"
        out["keys_identical"] = out["dedup_key_before"] == out["dedup_key_after"]
        out["future_event_excluded"] = "FUT" not in before and "FUT" not in after
        out["reported_event_excluded"] = "DONE" not in before and "DONE" not in after
        # Two days on, MU's event falls out of the {yesterday, today} window entirely.
        out["two_days_later"] = {k: str(v) for k, v in fn(s, syms, date(2026, 10, 2)).items()}
    print("---PROBE-JSON---")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
