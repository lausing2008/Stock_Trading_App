"""Per-symbol daily fetch evidence through the REAL ingest_symbol on PostgreSQL (fake provider)."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

URL = os.environ.get("RZY_PG_URL")


@pytest.mark.skipif(not URL, reason="RZY_PG_URL is absent; required in make test-integration-pg-schema")
def test_each_symbol_records_its_own_successful_fetch_and_observed_session():
    run = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("_daily_ingest_record_probe.py"))],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    r = json.loads(run.stdout.strip().splitlines()[-1])
    assert r["first"] == {"ok_at": True, "observed": "2026-10-20T00:00:00", "provider": "fakeprov", "failure": None}
    # A failed fetch is recorded but never erases or advances the last verified session.
    assert r["raised"] and r["after_failure"] == {"observed": "2026-10-20T00:00:00", "failure": True,
                                                  "error": "provider down", "success_kept": True}
    assert r["advanced"] == "2026-10-21T00:00:00"
