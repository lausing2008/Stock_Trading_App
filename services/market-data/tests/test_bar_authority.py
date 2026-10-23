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


@pytest.mark.skipif(not URL, reason="RZY_PG_URL is absent; required in make test-integration-pg-schema")
def test_authority_rules_at_their_boundaries_with_an_injected_clock():
    run = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("_bar_authority_rules_probe.py"))],
                         capture_output=True, text=True, timeout=150)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    r = json.loads(run.stdout.strip().splitlines()[-1])
    # B1: a forming snapshot is not owned — the fallback completes it; then the authority owns it.
    assert r["b1"] == [True, True, 104.0, True, False, 104.2]
    # Ownership starts at the first write at/after completion (exact ts+5m boundary).
    assert r["boundary"] == [True, False, True, True]
    assert r["hour_old"] == [True, False, 70.0]                 # the Oct 21 pattern is refused
    assert r["self_correction"] == [True, True, 80.3]           # a fallback corrects its own gap fill
    assert r["unchanged_resend"] == [True, True, False, 90.0]   # an unchanged re-send stamps ownership
    # 457: post-close reconciliation completes the final candle without any fallback overwrite.
    assert r["final_forming"] == [True, True, False, 121.0]
    assert r["final_missing"] == [True, False, 130.0]
    assert r["final_fallback_forming"] == [True, True, 141.0]
    # Daily: the 16:00:30 close-burst write is replaceable by the settled close; after 16:45 owned.
    assert r["daily_settle"] == [True, True, 301.0, True, False, 301.2]
    assert r["dates"] == ["2026-10-21", "2026-11-04", "2026-10-22"]
    assert r["hour_bar_done"] == "2026-10-20T20:00:00"
    # B2: cooldown routing serves yfinance, but the authority stays the primary.
    assert r["primary_in_cooldown"] == ["unusual_whales", "unusual_whales", "yfinance", "yfinance"]
    assert r["trace_failed"] == [False, True] and r["fail_closed"] == [1, 2]
    assert r["record"][0] == "unusual_whales" and r["record"][3] > 0 and r["record"][4] is True
