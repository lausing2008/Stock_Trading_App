"""Runs the Intraday suites outside this directory's stubbing conftest (they need real
SQLAlchemy/FastAPI), each in its own process because they install different `db` modules:
always on SQLite; the storage and input suites also on PostgreSQL 16 when INTRADAY_PG_URL names a
disposable database (REQUIRED by `make test-integration-pg-schema`, which provides one)."""
import os
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).parent
DB_SUITES = ("intraday_store_suite.py", "intraday_inputs_suite.py")
PG_URL = os.environ.get("INTRADAY_PG_URL")
if os.environ.get("INTRADAY_PG_REQUIRED") == "1" and not PG_URL:
    raise RuntimeError("INTRADAY_PG_REQUIRED needs a disposable INTRADAY_PG_URL")


def _run(suite, url="sqlite://"):
    env = dict(os.environ, INTRADAY_SUITE_URL=url)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest",
                           "-W", "ignore", str(HERE / suite)], capture_output=True, text=True, timeout=300,
                          env=env, cwd=str(HERE))


@pytest.mark.parametrize("suite", DB_SUITES + ("intraday_api_suite.py",))
def test_intraday_suite_on_sqlite(suite):
    r = _run(suite)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert " passed" in r.stdout


@pytest.mark.skipif(not PG_URL, reason="INTRADAY_PG_URL is absent; required in make test-integration-pg-schema")
@pytest.mark.parametrize("suite", DB_SUITES)
def test_intraday_suite_on_postgresql(suite):
    r = _run(suite, PG_URL)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "skipped" not in r.stdout, "the PostgreSQL run must exercise every trigger test"
