"""AUD-DP-TAB-BASELINE (2026-11-10): the baseline each dark-pool alert was judged against."""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from src.services import dark_pool_alert_baseline as bl


def test_real_database_insert_or_nothing_and_lookup():
    probe = Path(__file__).with_name("_dark_pool_alert_baseline_probe.py")
    r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout.split("DP_BASELINE_PROBE_JSON=")[-1]) == {"rows": 2, "database": "sqlite"}


def test_a_print_with_no_median_is_labelled_floor_only():
    row = bl.row_for(1, date(2026, 11, 10), {"baseline_status": "insufficient_history", "baseline_median": None})
    assert row["qualified_by"] == "floor_only" and row["baseline_status"] == "insufficient_history"


def test_a_print_judged_against_a_median_is_labelled_relative():
    row = bl.row_for(1, date(2026, 11, 10), {"baseline_status": "ok", "baseline_median": 2.0e5, "vs_baseline": 7.5})
    assert row["qualified_by"] == "floor_and_relative" and row["vs_baseline"] == 7.5


def test_the_job_writes_it_beside_the_outcome_and_the_route_returns_it():
    src = (Path(__file__).resolve().parents[1] / "src").joinpath
    sched = src("services/scheduler.py").read_text()
    body = sched[sched.index("def check_dark_pool_alerts("):]
    body = body[:body.index("\ndef ")]
    assert "_record_dp_baseline(session, id_by_symbol[symbol], _today_et(), cand)" in body
    routes = src("api/routes.py").read_text()
    route = routes[routes.index('@router.get("/dark-pool-alerts-recent")'):]
    route = route[:route.index("\n@router")]
    assert '"baseline": (lambda b:' in route and "_dp_baselines(session," in route
