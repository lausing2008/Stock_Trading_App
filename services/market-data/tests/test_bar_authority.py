"""OD-01 fix 1: one authoritative provider per completed bar, through the REAL ingest_symbol."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

URL = os.environ.get("RZY_PG_URL")


@pytest.mark.skipif(not URL, reason="RZY_PG_URL is absent; required in make test-integration-pg-schema")
def test_a_fallback_never_rewrites_a_completed_bar_and_an_outage_cannot_restart_the_cycle():
    run = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("_bar_authority_probe.py"))],
                         capture_output=True, text=True, timeout=150)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    r = json.loads(run.stdout.strip().splitlines()[-1])
    assert r["s1"] == [100.0, 101.0, 102.0]
    # Outage: the fallback fills the new bar and the forming bar only; the authority's bars and a
    # pre-trace bar with no recorded writer are protected.
    assert r["s2_yf"] == {"allowed": 2, "protected": 3, "replaced": 0}
    assert r["s2"] == [50.0, 100.0, 101.0, 103.0, 102.5]
    # Authority back: corrects its own bar (same provider) and replaces the gap fill once.
    assert r["s3_uw"]["replaced"] == 1 and r["s3"] == [50.2, 100.1, 103.3]
    # A second outage cannot rewrite anything: no cycle.
    assert r["s4_yf"] == {"allowed": 0, "protected": 4, "replaced": 0}
    assert r["s4"] == [50.2, 100.1, 101.0, 103.3]
    assert all(w == "unusual_whales" for _, w in r["writers"])
    assert ("unusual_whales", 7, 1) in [tuple(a) for a in r["authority"]]
    # Daily: a completed session's close is protected too.
    assert r["daily_uw"]["replaced"] == 1                     # yfinance backfill replaced once
    assert r["daily_yf"]["protected"] == 1 and r["daily_close"] == 200.0
