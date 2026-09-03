"""Tests for T325-FIXEFFECTIVENESS's recheck_fix_effectiveness() scheduled job — direct user
request (2026-09-02) for a way to compare "before vs. after a fix" over time. Same source-text-
extraction technique as test_options_flow_tab_routes.py (scheduler.py can't be imported
directly in this test environment — its import chain pulls in apscheduler, not installed
locally): regression checks on the WIRING itself, not the full scheduled-job execution.
"""
import pathlib

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


def _extract(start_marker: str, end_marker: str) -> str:
    start = _SCHEDULER_SOURCE.index(start_marker)
    end = _SCHEDULER_SOURCE.index(end_marker, start)
    return _SCHEDULER_SOURCE[start:end]


_JOB_SOURCE = _extract(
    "def recheck_fix_effectiveness(",
    "\n_VALUE_AREA_COMPUTE_LOCK_KEY",
)


def test_job_is_registered_with_the_scheduler():
    assert 'id="fix_effectiveness_recheck_daily"' in _SCHEDULER_SOURCE


def test_job_queries_all_fix_records():
    assert "select(FixRecord)" in _JOB_SOURCE


def test_job_uses_the_most_recent_snapshot_or_fixed_at_as_the_last_check_time():
    """The core scheduling logic: a FixRecord with no snapshots yet uses its own fixed_at as
    the baseline clock; one with snapshots uses the MOST RECENT snapshot, not the original
    fixed_at — otherwise every daily run after the first overdue check would re-fire forever."""
    assert "func.max(FixSnapshot.taken_at)" in _JOB_SOURCE
    assert "last_check = latest_snapshot_at or record.fixed_at" in _JOB_SOURCE


def test_job_respects_each_records_own_recheck_after_days_not_a_global_constant():
    assert "record.recheck_after_days" in _JOB_SOURCE


def test_job_skips_records_not_yet_due():
    assert "continue" in _JOB_SOURCE
    assert "(now - last_check_utc).days < record.recheck_after_days" in _JOB_SOURCE


def test_job_calls_the_real_snapshot_endpoint_with_the_records_own_fix_id():
    assert '/fix-effectiveness/{record.fix_id}/snapshot' in _JOB_SOURCE


def test_job_uses_the_lock_pattern_to_prevent_overlap():
    assert "_FIX_EFFECTIVENESS_RECHECK_LOCK_KEY" in _JOB_SOURCE
    assert 'nx=True' in _JOB_SOURCE


def test_job_is_in_the_non_alert_job_id_allowlist():
    """BUG-LOCALDEV-ALERTS-UNGATED's own safety mechanism: every scheduled job must be
    classified as alert-emitting or not. This job sends no email — must be in the non-alert
    list, never accidentally left unclassified or misclassified as an alert."""
    gate_test_path = pathlib.Path(__file__).resolve().parent / "test_alerts_env_gate.py"
    gate_test_source = gate_test_path.read_text()
    non_alert_start = gate_test_source.index("_NON_ALERT_JOB_IDS = {")
    non_alert_end = gate_test_source.index("\n}", non_alert_start)
    non_alert_block = gate_test_source[non_alert_start:non_alert_end]
    assert '"fix_effectiveness_recheck_daily"' in non_alert_block


def test_job_does_not_send_any_email():
    """Structural confirmation this is genuinely a non-alert job — no email-sending call
    anywhere in its body."""
    assert "send_" not in _JOB_SOURCE
