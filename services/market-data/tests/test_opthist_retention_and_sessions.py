"""Option-chain archive (2026-10-28): tiered retention and session-only capture.

The PostgreSQL test SKIPS unless `OPTHIST_PG_URL` points at a disposable PostgreSQL, e.g.
    OPTHIST_PG_URL=postgresql+psycopg2://postgres:<password>@localhost:55432/rzyprobe
It works in a throwaway schema and drops it afterwards.
"""
import os
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest

import src.services.scheduler as sched


class _Result:
    def first(self):
        return None


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return _Result()

    def commit(self):
        pass


def _capture(start, end):
    fetched = []

    def fetch(sym, as_of):
        fetched.append(as_of)
        return {"rows": [], "status": "complete", "pages": 1, "page_size": 500, "reason": "terminal_page"}

    import src.services.unusual_whales as UW
    with patch("db.SessionLocal", _Session), \
         patch.object(UW, "get_historical_option_chain_result", fetch), \
         patch.object(sched, "_OPTHIST_THROTTLE_S", 0), \
         patch.object(sched, "_record_job_status", lambda *a, **k: None):
        res = sched.capture_option_chain_history(["NET"], start, end)
    return fetched, res


def test_weekends_are_never_fetched_or_recorded():
    fetched, res = _capture(date(2026, 10, 23), date(2026, 10, 26))   # Fri .. Mon
    assert fetched == ["2026-10-23", "2026-10-26"]
    assert res["days_non_session_skipped"] == 2


def test_nyse_holidays_are_skipped_and_the_early_close_is_a_session():
    fetched, res = _capture(date(2026, 11, 25), date(2026, 11, 27))   # Wed, Thanksgiving, early-close Fri
    assert fetched == ["2026-11-25", "2026-11-27"]
    assert res["days_non_session_skipped"] == 1


def test_retention_tiers_are_declared():
    assert sched._OPTHIST_RETENTION_DAYS == 800
    assert sched._OPTHIST_NON_LEAPS_RETENTION_DAYS == 90
    assert {"QQQ", "SPY", "MU"} <= set(sched._OPTHIST_SYMBOLS)


def test_the_purge_runs_the_declared_statements_with_tiered_cutoffs():
    ran = []

    class S(_Session):
        def execute(self, stmt, params=None):
            ran.append((str(stmt), params))
            return type("R", (), {"rowcount": 0, "one": lambda self: (0, 0)})()

    with patch("db.SessionLocal", S), patch("sqlalchemy.text", lambda s: s), \
         patch.object(sched, "_record_job_status", lambda *a, **k: None):
        sched._purge_option_chain_history()
    assert [sql for sql, _ in ran] == list(sched._OPTHIST_PURGE_SQL)
    p = ran[0][1]
    assert (date.today() - p["c"]).days == 800 and (date.today() - p["cn"]).days == 90
    assert "QQQ" in p["leaps"] and "IONQ" not in p["leaps"]
    assert all("fetched_at" not in sql for sql in sched._OPTHIST_PURGE_SQL)


_URL = os.environ.get("OPTHIST_PG_URL")

# The unit suite replaces SQLAlchemy with mocks (conftest), so the statements run in a clean
# interpreter against real PostgreSQL.
_PROBE = r"""
import json, os, sys, uuid
from datetime import date, timedelta
from sqlalchemy import create_engine, text
cfg = json.loads(sys.argv[1])
schema = "opthist_" + uuid.uuid4().hex[:8]
eng = create_engine(os.environ["OPTHIST_PG_URL"], connect_args={"options": f"-csearch_path={schema}"})
today = date.fromisoformat(cfg["today"])
ages = {"QQQ": [10, 89, 91, 799, 801], "IONQ": [10, 89, 91, 799, 801]}
with eng.begin() as c:
    c.execute(text(f"CREATE SCHEMA {schema}"))
    c.execute(text("CREATE TABLE option_chain_history (symbol varchar(20), as_of date, option_symbol varchar(40))"))
    c.execute(text("CREATE TABLE option_chain_coverage (symbol varchar(16), as_of date, status varchar(24))"))
    for sym, days in ages.items():
        for d in days:
            a = today - timedelta(days=d)
            c.execute(text("INSERT INTO option_chain_history VALUES (:s, :d, :o)"), {"s": sym, "d": a, "o": f"{sym}{d}"})
            c.execute(text("INSERT INTO option_chain_coverage VALUES (:s, :d, 'complete')"), {"s": sym, "d": a})
try:
    p = {"c": date.fromisoformat(cfg["c"]), "cn": date.fromisoformat(cfg["cn"]), "leaps": cfg["leaps"]}
    with eng.begin() as c:
        counts = list(c.execute(text(cfg["sql"][0]), p).one())
        c.execute(text(cfg["sql"][1]), p)
    out = {"counts": counts}
    with eng.connect() as c:
        for t in ("option_chain_history", "option_chain_coverage"):
            rows = c.execute(text(f"SELECT symbol, as_of FROM {t}")).all()
            out[t] = {s: sorted((today - a).days for sym, a in rows if sym == s) for s in ages}
    print("===PROBE_JSON===" + json.dumps(out))
finally:
    with eng.begin() as c:
        c.execute(text(f"DROP SCHEMA {schema} CASCADE"))
"""


@pytest.mark.skipif(not _URL, reason="OPTHIST_PG_URL not set; see this module's docstring")
def test_tiered_purge_on_postgresql():
    import json
    import subprocess
    import sys
    today = date.today()
    p = sched._opthist_purge_params(today)
    cfg = {"today": today.isoformat(), "c": p["c"].isoformat(), "cn": p["cn"].isoformat(),
           "leaps": p["leaps"], "sql": list(sched._OPTHIST_PURGE_SQL)}
    proc = subprocess.run([sys.executable, "-c", _PROBE, json.dumps(cfg)], capture_output=True,
                          text=True, timeout=120, env={**os.environ, "OPTHIST_PG_URL": _URL})
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.split("===PROBE_JSON===", 1)[1])
    expected = {"QQQ": [10, 89, 91, 799], "IONQ": [10, 89]}          # LEAPS 800 d, others 90 d
    assert out["option_chain_history"] == expected
    assert out["option_chain_coverage"] == expected                 # coverage follows the rows
    assert out["counts"] == [2, 2]          # past 800 d (QQQ 801, IONQ 801); non-LEAPS 90-800 d (IONQ 91, 799)
