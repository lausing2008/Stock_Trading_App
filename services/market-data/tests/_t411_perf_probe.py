"""Child process for test_t411_options_performance.py — runs the REAL queries against a REAL
database.

WHY A SUBPROCESS. market-data's conftest stubs `sqlalchemy` itself, so inside the suite
`text()` is a MagicMock and no query can reach a database however the test is written. The heart
of this feature is SQL — which contract gets picked as "the one that was at the money" — and a
wrong pick produces a table that looks entirely plausible and is about a different instrument.
A fake session cannot catch that, because the fake would be asserting on the rows the test
itself handed back.

So this script runs in a clean interpreter with the REAL sqlalchemy and an on-disk SQLite
database, builds an option chain with known-correct answers, and prints the resulting payload as
JSON for the parent to assert on.

SQLite stands in for Postgres here. The queries use only portable constructs (DISTINCT, BETWEEN,
abs(), ORDER BY, LIMIT) — no Postgres-specific syntax — and dates are declared DATE with
explicit adapters so the driver returns real `datetime.date` objects the way psycopg2 does,
rather than strings, which would otherwise make this exercise a different code path than
production.

Usage: python3 _t411_perf_probe.py <scenario>
"""
import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import types
from datetime import date, timedelta
from unittest.mock import MagicMock

# Real datetime.date round-tripping, matching psycopg2's behaviour rather than SQLite's default
# string returns. Explicit rather than relying on the deprecated default adapters.
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_converter("DATE", lambda b: date.fromisoformat(b.decode()))

from sqlalchemy import create_engine, text          # the REAL sqlalchemy
from sqlalchemy.orm import sessionmaker

_HERE = pathlib.Path(__file__).resolve()
_SVC = _HERE.parents[1]

# _t411_ivhv imports fastapi (for Depends) and structlog at module level; neither is needed by
# the functions under test here, and fastapi in particular may not be installed locally.
for _name in ("fastapi", "structlog"):
    if _name not in sys.modules:
        try:
            __import__(_name)
        except ImportError:
            sys.modules[_name] = MagicMock()

_spec = importlib.util.spec_from_file_location(
    "t411_ivhv_real", _SVC / "src" / "api" / "_t411_ivhv.py"
)
ivhv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ivhv)


_SCHEMA = """
CREATE TABLE stocks (id INTEGER PRIMARY KEY, symbol TEXT);
CREATE TABLE prices (
    id INTEGER PRIMARY KEY, stock_id INTEGER, ts DATE, timeframe TEXT, close REAL
);
CREATE TABLE option_chain_history (
    id INTEGER PRIMARY KEY, symbol TEXT, as_of DATE, option_symbol TEXT,
    expiry DATE, strike REAL, option_type TEXT,
    nbbo_bid REAL, nbbo_ask REAL, open_interest INTEGER, volume INTEGER
);
"""

SYM = "TEST"
# 15 consecutive weekday sessions.
SESSIONS = []
_d = date(2026, 9, 8)
while len(SESSIONS) < 15:
    if _d.weekday() < 5:
        SESSIONS.append(_d)
    _d += timedelta(days=1)

# The underlying rises 100 -> 114 across the window: +14%.
CLOSES = {d: 100.0 + i for i, d in enumerate(SESSIONS)}

# Expiries: one that dies INSIDE the window, one just past it, one far out.
EXPIRY_INSIDE = SESSIONS[7]
EXPIRY_NEAR = SESSIONS[-1] + timedelta(days=10)
EXPIRY_FAR = SESSIONS[-1] + timedelta(days=60)


def _mk_session(scenario):
    path = pathlib.Path(tempfile.mkdtemp()) / "t411.db"
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"detect_types": sqlite3.PARSE_DECLTYPES},
    )
    with engine.begin() as conn:
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text("INSERT INTO stocks (id, symbol) VALUES (1, :s)"), {"s": SYM})
        for d, c in CLOSES.items():
            conn.execute(
                text("INSERT INTO prices (stock_id, ts, timeframe, close) "
                     "VALUES (1, :ts, 'D1', :c)"),
                {"ts": d, "c": c},
            )
        _seed_chain(conn, scenario)
    return sessionmaker(bind=engine)()


def _ins(conn, **kw):
    conn.execute(text("""
        INSERT INTO option_chain_history
          (symbol, as_of, option_symbol, expiry, strike, option_type,
           nbbo_bid, nbbo_ask, open_interest, volume)
        VALUES (:symbol, :as_of, :option_symbol, :expiry, :strike, :option_type,
                :nbbo_bid, :nbbo_ask, :oi, :vol)
    """), kw)


def _seed_chain(conn, scenario):
    if scenario == "no_option_history":
        return
    if scenario == "one_session_only":
        _ins(conn, symbol=SYM, as_of=SESSIONS[0], option_symbol="T_C100", expiry=EXPIRY_NEAR,
             strike=100.0, option_type="call", nbbo_bid=5.0, nbbo_ask=5.2, oi=10, vol=10)
        return

    for i, d in enumerate(SESSIONS):
        spot = CLOSES[d]
        for side in ("call", "put"):
            for strike in (90.0, 100.0, 110.0, 130.0):
                # Intrinsic + a decaying time value, so the numbers behave like real options.
                intrinsic = max(spot - strike, 0.0) if side == "call" else max(strike - spot, 0.0)
                tv = max(6.0 - 0.4 * i, 0.5)
                mid = intrinsic + tv
                for expiry, tag in ((EXPIRY_INSIDE, "I"), (EXPIRY_NEAR, "N"), (EXPIRY_FAR, "F")):
                    if scenario == "expiry_inside_only" and tag != "I":
                        continue
                    if expiry < d:
                        continue  # an expired contract is simply not quoted any more
                    bid, ask = mid - 0.1, mid + 0.1
                    if scenario == "entry_unquoted" and d == SESSIONS[0] and strike == 100.0:
                        bid, ask = 0.0, 0.0  # listed but not tradeable at entry
                    if scenario == "mid_window_gap" and d == SESSIONS[5] and tag == "N":
                        continue  # a missing capture day
                    _ins(conn, symbol=SYM, as_of=d,
                         option_symbol=f"T_{tag}_{side[0].upper()}{int(strike)}",
                         expiry=expiry, strike=strike, option_type=side,
                         nbbo_bid=round(bid, 2), nbbo_ask=round(ask, 2), oi=100, vol=50)


def main():
    scenario = sys.argv[1]
    session = _mk_session(scenario)
    try:
        payload = ivhv._performance_payload(session, SYM, 15)
    finally:
        session.close()
    print(json.dumps(payload, default=str))


if __name__ == "__main__":
    main()
