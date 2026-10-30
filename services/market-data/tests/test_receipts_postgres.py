"""Measurement-record tables on a real PostgreSQL: the no-update trigger, the CHECK constraints,
idempotent creation, and that a failed entry-receipt write leaves the caller's transaction usable.

SQLite cannot show any of these: it has no such trigger, and a failed statement inside a savepoint
does not poison a PostgreSQL-style transaction there. Run with a DISPOSABLE database:

    RECEIPTS_PG_URL=postgresql+psycopg2://postgres:probe@localhost:55439/schemaprobe \
        python -m pytest services/market-data/tests/test_receipts_postgres.py
"""
import os
import pathlib
import sys
import subprocess

import pytest

PG_URL = os.environ.get("RECEIPTS_PG_URL")
if os.environ.get("RECEIPTS_PG_REQUIRED", "").strip().lower() in ("1", "true", "yes") and not PG_URL:
    raise RuntimeError("RECEIPTS_PG_REQUIRED needs a disposable RECEIPTS_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="RECEIPTS_PG_URL is absent; required in make test-integration-pg-schema")

_PROBE = pathlib.Path(__file__).resolve().parent / "_receipts_pg_probe.py"


def test_receipt_tables_on_postgresql():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True,
                          timeout=300, env={**os.environ, "RECEIPTS_PG_URL": PG_URL})
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-3000:]
