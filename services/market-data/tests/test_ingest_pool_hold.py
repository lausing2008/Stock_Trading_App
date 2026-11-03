"""AUD-POOL-INGESTHOLD: ingest must not hold a pooled connection while it waits on a provider.

On 2026-11-02 the market-data pool (5 + 10 overflow) was exhausted 11 times. At every full-pool
moment 5-12 of the 15 connections were ingest workers whose session had queried the head bar and
was still open across `adapter.fetch_ohlcv`, for up to 18.5 s (docs/2026-11-02/nov2-checks.md §5).
The probe runs the real ingest_symbol against PostgreSQL with a real QueuePool and counts the
connections checked out while the fake provider is being asked.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

URL = os.environ.get("RZY_PG_URL")
REQUIRED = os.environ.get("RZY_PG_REQUIRED") == "1"


@pytest.mark.skipif(not URL and not REQUIRED, reason="RZY_PG_URL is absent; required in make test-integration-pg-schema")
def test_no_connection_is_held_during_the_provider_request_and_force_keeps_its_transaction():
    assert URL, "RZY_PG_REQUIRED=1 but RZY_PG_URL is not set"
    run = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("_ingest_pool_hold_probe.py"))],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    r = json.loads(run.stdout.strip().splitlines()[-1])
    # Incremental: the read transaction ended before the fetch, and the write still landed.
    assert r["incremental"]["held_during_fetch"] == 0
    assert r["incremental"]["advanced"] is True and r["incremental"]["stored"] == 4
    assert r["incremental"]["held_after"] == 0
    # force=True deletes before fetching; the DELETE must stay uncommitted until the INSERT, so
    # its connection is deliberately still held, and the end state is exactly the fetched bars.
    assert r["force"]["held_during_fetch"] == 1
    assert r["force"]["stored"] == 3 and r["force"]["held_after"] == 0
