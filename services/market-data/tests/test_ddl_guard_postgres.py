"""Startup DDL against a real lock on PostgreSQL.

The fake-engine probe records the statements `_ddl_begin()` issues, which establishes that the
timeout is SET and says nothing about whether the statement that actually blocks runs on that
connection. `create_all()` ran on its own connection with `lock_timeout = 0`; only a real server
holding a real conflicting lock tells those apart. To run it:

    docker run -d --name stockai-ddl-pgtest -e POSTGRES_PASSWORD=probe \
        -e POSTGRES_DB=ddlprobe -p 55439:5432 postgres:16-alpine
    DDL_GUARD_PG_URL=postgresql+psycopg2://postgres:probe@localhost:55439/ddlprobe \
        python -m pytest services/market-data/tests/test_ddl_guard_postgres.py

The probe DROPS AND RECREATES the public schema. Throwaway databases only.
"""
import os
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_ddl_guard_pg_probe.py"
_URL = os.environ.get("DDL_GUARD_PG_URL")
_REQUIRED = os.environ.get("DDL_GUARD_PG_REQUIRED", "").strip().lower() in ("1", "true", "yes")

if _REQUIRED and not _URL:
    raise RuntimeError(
        "DDL_GUARD_PG_REQUIRED is set but DDL_GUARD_PG_URL is not. This test covers a real "
        "lock wait: refusing to pass without having exercised PostgreSQL.")

pytestmark = pytest.mark.skipif(
    not _URL, reason="DDL_GUARD_PG_URL not set; see this module's docstring to run it")


def test_contended_startup_ddl_is_bounded_by_lock_timeout():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=400, env={**os.environ, "DDL_GUARD_PG_URL": _URL})
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-3000:]
