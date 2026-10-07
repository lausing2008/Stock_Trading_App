"""Is the capture job actually scheduled, and registered so a silent death is visible?

A capture job that stops running loses days permanently — the upstream consensus is overwritten
in place, so there is no later query that recovers what it would have recorded. That makes
liveness registration load-bearing here in a way it is not for a job that can simply catch up.
"""
import ast
import pathlib

_SCHED = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services"
          / "scheduler.py").read_text()
_TREE = ast.parse(_SCHED)


def _fn(name):
    return next(n for n in ast.walk(_TREE)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def _job(job_id):
    """The add_job call for one id, found through the AST rather than by text search."""
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "add_job"):
            continue
        for kw in node.keywords:
            if kw.arg == "id" and isinstance(kw.value, ast.Constant) \
                    and kw.value.value == job_id:
                return node
    raise AssertionError(f"no add_job with id={job_id!r}")


def test_the_capture_function_exists_and_calls_the_research_engine():
    body = ast.unparse(_fn("capture_prospective_estimates"))
    assert "quality-value/capture/estimates" in body
    assert "_service_token()" in body, \
        "an auth-protected endpoint called without a header 401s — a known recurring bug class"


def test_it_is_scheduled_once_daily_rather_than_on_an_interval():
    """Twice a day would write the same values under two timestamps and inflate the series."""
    job = _job("prospective_estimate_capture")
    assert job.args and isinstance(job.args[1], ast.Constant)
    assert job.args[1].value == "cron", "an interval trigger would capture repeatedly"
    kw = {k.arg: k.value for k in job.keywords}
    assert isinstance(kw["hour"], ast.Constant) and isinstance(kw["minute"], ast.Constant)
    assert kw["timezone"].value == "UTC", "a local timezone drifts across DST"


def test_a_brief_outage_does_not_make_the_day_uncapturable():
    """AUD-T398-MISFIREGAP: a 60s grace made APScheduler DISCARD a delayed job, and the day
    became permanently unrecoverable. This job has exactly that property."""
    kw = {k.arg: k.value for k in _job("prospective_estimate_capture").keywords}
    assert kw["misfire_grace_time"].value >= 3600, \
        "a short grace discards a delayed run, and this run cannot be repeated later"
    assert kw["coalesce"].value is True
    assert kw["max_instances"].value == 1


def test_it_is_registered_for_liveness_with_its_unrecoverability_stated():
    seg = _SCHED[_SCHED.index('"name": "capture_prospective_estimates"'):][:400]
    assert "UNRECOVERABLE" in seg, \
        "a reader must know a missed run is not merely late"


def test_a_failure_is_logged_rather_than_raising_into_the_scheduler():
    body = ast.unparse(_fn("capture_prospective_estimates"))
    assert "except Exception" in body and "prospective_capture.failed" in body


def test_it_is_not_gated_behind_the_alerting_switch():
    """FOUND BY AN EXISTING GUARD, not by me. The job was first placed beside the Claude budget
    alert, inside the `_is_alerting_enabled()` block — so a stack with alerting off would have
    silently stopped capturing, and every day it missed is permanently unrecoverable because
    the upstream consensus is overwritten in place. It sends nothing and spends no provider
    budget; it has no business behind an alerting switch."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[0]))
    from test_alerts_env_gate import _job_registration_gating, _NON_ALERT_JOB_IDS
    assert "prospective_estimate_capture" in _NON_ALERT_JOB_IDS
    assert _job_registration_gating().get("prospective_estimate_capture") is not True
