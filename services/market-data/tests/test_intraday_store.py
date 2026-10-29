"""Runs intraday_store_suite.py outside this directory's stubbing conftest (it needs real
SQLAlchemy): always on SQLite; on PostgreSQL 16 when INTRADAY_PG_URL names a disposable database
(REQUIRED by `make test-integration-pg-schema`, which provides one)."""
import os
import pathlib
import subprocess
import sys

import pytest

SUITE = pathlib.Path(__file__).with_name("intraday_store_suite.py")
PG_URL = os.environ.get("INTRADAY_PG_URL")
if os.environ.get("INTRADAY_PG_REQUIRED") == "1" and not PG_URL:
    raise RuntimeError("INTRADAY_PG_REQUIRED needs a disposable INTRADAY_PG_URL")


def _run(url):
    env = dict(os.environ, INTRADAY_SUITE_URL=url)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest",
                           "-W", "ignore", str(SUITE)], capture_output=True, text=True, timeout=300, env=env,
                          cwd=str(SUITE.parent))


def test_intraday_storage_contract_on_sqlite():
    r = _run("sqlite://")
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert " passed" in r.stdout


@pytest.mark.skipif(not PG_URL, reason="INTRADAY_PG_URL is absent; required in make test-integration-pg-schema")
def test_intraday_storage_contract_on_postgresql():
    r = _run(PG_URL)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "skipped" not in r.stdout, "the PostgreSQL run must exercise every trigger test"
