"""Child process for test_r06_income_concurrency.py — runs open_income_positions() for REAL.

WHY A SUBPROCESS. market-data's conftest stubs `sqlalchemy` itself (along with `db` and
`psycopg2`), because almost nothing in this service can be imported locally otherwise. That
makes a real database impossible INSIDE the suite: `select()` returns a MagicMock, so the
function under test cannot touch a real session however the test is written.

R06's acceptance is explicit — "Test database results, not just whether another worker's Redis
key survives" — and a source-text assertion does not meet it. So this script runs in a clean
interpreter with the REAL sqlalchemy, the REAL shared/db/models.py, and an in-memory SQLite
database, and prints the resulting rows and cash as JSON for the parent to assert on.

SQLite has no row-level locking, so `_lock_portfolio_row` degrades to a warning here and the
UNIQUE INTENT KEY is the guard actually being exercised. That is the intended reading: they are
independent guards, and the intent key is the one that still holds when a worker is killed
rather than merely slow.

Usage: python3 _r06_db_probe.py <scenario>
"""
import atexit
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

_HERE = pathlib.Path(__file__).resolve()
_SVC = _HERE.parents[1]
_ROOT = _HERE.parents[3]

from sqlalchemy import Integer, create_engine, event, select   # the REAL sqlalchemy
from sqlalchemy.orm import sessionmaker

# ── Real models, real tables ─────────────────────────────────────────────────
_spec = importlib.util.spec_from_file_location("r06_models", _ROOT / "shared" / "db" / "models.py")
models = importlib.util.module_from_spec(_spec)
sys.modules["r06_models"] = models
_spec.loader.exec_module(models)

for _cls in (models.OptionsIncomePortfolio, models.OptionsIncomePosition,
             models.OptionsIncomeEquityCurve, models.Stock):
    _cls.__table__.c.id.type = Integer()

# A FILE, not ":memory:". An in-memory SQLite database is one connection shared by every
# session, so the two "workers" below would sit on the same transaction and the second BEGIN
# fails outright — which models nothing. A file gives each session its own real connection,
# which is the whole point of the scenario.
_DB_FILE = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_DB_FILE.close()
atexit.register(lambda: os.unlink(_DB_FILE.name))
ENGINE = create_engine(f"sqlite:///{_DB_FILE.name}")


# pysqlite does not emit BEGIN on its own, so a SAVEPOINT commits implicitly and a later
# session.rollback() cannot undo it — which made the lease-lost scenario report a row that
# Postgres would have discarded. This is SQLAlchemy's own documented workaround for pysqlite's
# transaction handling, and it is required for a SAVEPOINT test on SQLite to mean anything.
@event.listens_for(ENGINE, "connect")
def _sqlite_no_implicit_begin(dbapi_conn, _rec):
    dbapi_conn.isolation_level = None
    dbapi_conn.execute("PRAGMA busy_timeout=5000")


@event.listens_for(ENGINE, "begin")
def _sqlite_explicit_begin(conn):
    conn.exec_driver_sql("BEGIN")
models.Base.metadata.create_all(ENGINE, tables=[
    models.OptionsIncomePortfolio.__table__,
    models.OptionsIncomePosition.__table__,
    models.OptionsIncomeEquityCurve.__table__,
    models.Stock.__table__,
])
Session = sessionmaker(bind=ENGINE)

# ── Minimal real-enough package shims so the engine module imports ───────────
_db = types.ModuleType("db")
_db_models = types.ModuleType("db.models")
for _n in dir(models):
    if not _n.startswith("_"):
        setattr(_db_models, _n, getattr(models, _n))
_db_session = types.ModuleType("db.session")
_db_session.SessionLocal = Session
sys.modules["db"] = _db
sys.modules["db.models"] = _db_models
sys.modules["db.session"] = _db_session

for _n in ("common", "common.config", "common.logging", "common.redis_client",
           "common.market_calendar"):
    sys.modules.setdefault(_n, MagicMock())
sys.modules["common.market_calendar"].NYSE_HOLIDAYS = set()

sys.path.insert(0, str(_SVC))
from src.services import options_income_engine as E        # noqa: E402


class _Log:
    """Structured log lines to STDERR, so the parent can show them on a failure without them
    contaminating the JSON on stdout. Not silenced: `_lock_portfolio_row` legitimately warns
    here (SQLite has no row-level locking) and a REAL failure inside the engine must stay
    visible rather than being swallowed by a MagicMock."""

    def _emit(self, level, event, **kw):
        print(f"[{level}] {event} {kw}", file=sys.stderr)

    def info(self, event, **kw):
        self._emit("info", event, **kw)

    def warning(self, event, **kw):
        self._emit("warning", event, **kw)

    def error(self, event, **kw):
        self._emit("error", event, **kw)


E.log = _Log()

TODAY = date(2026, 9, 24)
E._today_et = lambda: TODAY


def _portfolio(session, cash=50_000.0):
    p = models.OptionsIncomePortfolio(
        name="R06", initial_capital=50_000.0, current_cash=cash, is_active=True,
        config={"symbols": ["AAA"], "max_positions": 5, "max_entries_per_day": 5,
                "contracts_per_position": 1, "min_annualized_yield_pct": 1.0,
                "max_positions_per_symbol": 3, "max_collateral_pct_per_position": 0.9},
    )
    session.add(p)
    session.commit()
    return p


def _cand(option_symbol="OPT-A", collateral=9_000.0, premium=150.0):
    return {"symbol": "AAA", "strategy": "CASH_SECURED_PUT", "option_symbol": option_symbol,
            "strike": 90.0, "expiry": TODAY + timedelta(days=30), "current_price": 100.0,
            "delta": -0.25, "iv": 0.3, "premium_per_contract": premium,
            "collateral_required": collateral, "annualized_yield_pct": 20.0}


class Lease:
    def __init__(self, *held):
        self._seq, self._i = list(held), 0

    def is_held(self):
        v = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return v


def _state(pid):
    with Session() as s:
        rows = s.execute(select(models.OptionsIncomePosition).where(
            models.OptionsIncomePosition.portfolio_id == pid)).scalars().all()
        p = s.get(models.OptionsIncomePortfolio, pid)
        return {"rows": sorted(r.option_symbol for r in rows),
                "cash": round(float(p.current_cash), 2)}


def main(scenario: str) -> dict:
    if scenario == "two_workers_same_intent":
        # Both hold a snapshot taken before either committed: same cash, no open positions,
        # a full daily budget. Every Python check passes for both.
        # SEQUENTIAL, and deliberately so. SQLite serializes writers at the FILE level, so two
        # genuinely overlapping write transactions cannot be staged here at all — the second
        # fails with "database is locked", which proves nothing about this code.
        #
        # What is reproducible, and what the audit's acceptance actually asks for, is the
        # OUTCOME: worker B decided on this candidate against a snapshot taken before A
        # committed, and arrives with it afterwards. Every Python check in
        # open_income_positions() passes for B — the cash is still ample, the position count is
        # under the cap, the daily budget has room — so nothing but the schema stops it. The
        # unique intent key is order-independent by construction, which is exactly why it is
        # the guard that survives a worker being paused or killed rather than merely slow.
        with Session() as a:
            pa = _portfolio(a)
            pa_id = pa.id
            first = E.open_income_positions(a, pa, candidates=[_cand()])
        with Session() as b:
            pb = b.get(models.OptionsIncomePortfolio, pa_id)
            second = E.open_income_positions(b, pb, candidates=[_cand()])
        return {"first": first, "second": second, **_state(pa_id)}

    if scenario == "savepoint_keeps_the_batch":
        with Session() as a:
            p = _portfolio(a)
            E.open_income_positions(a, p, candidates=[_cand("DUP")])
        with Session() as b:
            pb = b.get(models.OptionsIncomePortfolio, p.id)
            opened = E.open_income_positions(b, pb, candidates=[
                _cand("DUP"), _cand("FRESH-1"), _cand("FRESH-2")])
        return {"opened": opened, **_state(p.id)}

    if scenario == "same_contract_next_day":
        with Session() as a:
            p = _portfolio(a)
            E.open_income_positions(a, p, candidates=[_cand("SAME")])
        E._today_et = lambda: TODAY + timedelta(days=1)
        with Session() as b:
            pb = b.get(models.OptionsIncomePortfolio, p.id)
            opened = E.open_income_positions(b, pb, candidates=[_cand("SAME")])
        E._today_et = lambda: TODAY
        return {"opened": opened, **_state(p.id)}

    if scenario == "two_portfolios_same_contract":
        with Session() as s:
            p1, p2 = _portfolio(s), _portfolio(s)
            p1_id, p2_id = p1.id, p2.id
            one = E.open_income_positions(s, p1, candidates=[_cand("SHARED")])
        with Session() as s2:
            p2b = s2.get(models.OptionsIncomePortfolio, p2_id)
            two = E.open_income_positions(s2, p2b, candidates=[_cand("SHARED")])
        return {"one": one, "two": two, "p1": _state(p1_id), "p2": _state(p2_id)}

    if scenario == "lease_lost_before_commit":
        # Held at the entry check, lost by the time the work would commit.
        with Session() as s:
            p = _portfolio(s)
            opened = E.open_income_positions(
                s, p, candidates=[_cand()], lease=Lease(True, False))
        return {"opened": opened, **_state(p.id)}

    if scenario == "lease_never_held":
        with Session() as s:
            p = _portfolio(s)
            opened = E.open_income_positions(s, p, candidates=[_cand()], lease=Lease(False))
        return {"opened": opened, **_state(p.id)}

    if scenario == "lease_held_throughout":
        with Session() as s:
            p = _portfolio(s)
            opened = E.open_income_positions(s, p, candidates=[_cand()], lease=Lease(True))
        return {"opened": opened, **_state(p.id)}

    # ── R08: the mark evidence must actually land in the row ────────────────
    if scenario.startswith("snapshot_"):
        quote_age = {"snapshot_fresh_quote": 0, "snapshot_stale_quote": 4,
                     "snapshot_no_quote": None}[scenario]
        with Session() as s:
            p = _portfolio(s)
            p_id = p.id
            s.add(models.OptionsIncomePosition(
                portfolio_id=p_id, symbol="AAA", strategy="CASH_SECURED_PUT",
                option_symbol="OPT-MARK", strike=90.0, expiry=TODAY + timedelta(days=30),
                contracts=1, entry_date=TODAY,
                entry_time=datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc),
                underlying_entry_price=100.0,
                premium_per_contract=150.0, total_premium_collected=150.0,
                collateral_reserved=9_000.0, stage="open"))
            s.commit()

            # The archived ask and its date, as _latest_option_ask would have found them.
            if quote_age is None:
                E._latest_option_ask = lambda *_a, **_kw: (None, None)
            else:
                qd = TODAY - timedelta(days=quote_age)
                E._latest_option_ask = lambda *_a, **_kw: (0.50, qd)
            # A live underlying, so the underlying source is not what is under test here.
            # Registered as a module rather than imported: the real paper_trading_engine pulls
            # in common.indicators, and `common` is a MagicMock here, not a package.
            _pte = types.ModuleType("src.services.paper_trading_engine")
            _pte._fetch_live_prices = lambda syms: {sym: 100.0 for sym in syms}
            sys.modules["src.services.paper_trading_engine"] = _pte

            E._snapshot_income_equity_curve(s, [p], TODAY)

        with Session() as s:
            row = s.execute(select(models.OptionsIncomeEquityCurve).where(
                models.OptionsIncomeEquityCurve.portfolio_id == p_id)).scalars().one()
            return {"mark_evidence": row.mark_evidence, "equity": round(float(row.equity), 2)}

    raise SystemExit(f"unknown scenario: {scenario}")


if __name__ == "__main__":
    print("R06_JSON " + json.dumps(main(sys.argv[1])))
