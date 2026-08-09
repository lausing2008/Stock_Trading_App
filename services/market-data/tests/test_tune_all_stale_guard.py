"""Tests for AUD263-TUNEALL-STALE-GUARD-NOT-WEEKLY's market-data/scheduler.py half — the
21-day stale-guard freshness check used to read "scheduler:job:tune_all_sent", a key that only
ever records that the POST to ml-prediction's /ml/tune_all was DISPATCHED, never that the
~2-4h background run actually FINISHED. Fixed to read ml-prediction's own real completion
marker ("stockai:tune_all_completed") instead, and both dispatch call sites (the weekly kick +
the stale-guard rescue itself) now pass a real triggered_by param instead of nothing at all.

scheduler.py can't be imported directly in this test environment — its import chain pulls in
apscheduler (documented extensively elsewhere in this codebase's own test suite for this exact
constraint) — source-text regression checks, matching test_scheduler_static_names.py's
established convention.
"""
import pathlib

_SCHED_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHED_SOURCE = _SCHED_PATH.read_text()


def _stale_guard_block() -> str:
    start = _SCHED_SOURCE.index("# Stale model guard: if tune_all hasn't ACTUALLY FINISHED")
    end = _SCHED_SOURCE.index("except Exception as _ta_e:", start)
    return _SCHED_SOURCE[start:end]


def test_stale_guard_reads_the_real_completion_marker_not_the_dispatch_only_key():
    body = _stale_guard_block()
    assert '"stockai:tune_all_completed"' in body
    # The old, dispatch-only key must no longer be what THIS check reads (it may still appear
    # elsewhere in the file as a write target for _record_job_status, which is a separate,
    # legitimate "was the POST dispatched" signal — just not what freshness should be judged on).
    assert 'get("scheduler:job:tune_all_sent")' not in body


def test_stale_guard_reads_completed_at_not_last_run():
    """The completion marker's own field is "completed_at" (ml-prediction/routes.py's
    tune_all()) — reading the old marker's "last_run" field here would silently always be
    None against the new key's real shape."""
    body = _stale_guard_block()
    assert '.get("completed_at")' in body


def test_a_missing_marker_counts_as_stale_not_fresh():
    """A completion marker that has NEVER been written at all (e.g. right after this fix first
    deploys, or ml-prediction has never once finished a run) must be treated the same as "long
    overdue" — not silently treated as fresh just because there's nothing to compare against."""
    body = _stale_guard_block()
    assert "_days_stale is None or _days_stale > 21" in body


def test_stale_guard_retrigger_passes_triggered_by_stale_guard():
    body = _stale_guard_block()
    assert '"triggered_by": "stale_guard"' in body


def test_weekly_kick_passes_triggered_by_weekly():
    start = _SCHED_SOURCE.index("def _weekly_full_refresh(")
    end = _SCHED_SOURCE.index("\n\n\n", start)
    body = _SCHED_SOURCE[start:end]
    assert '"triggered_by": "weekly"' in body


def test_both_tune_all_dispatch_call_sites_pass_a_distinct_triggered_by():
    """Regression guard against the two call sites accidentally converging on the same
    literal (which would silently defeat the whole point of this fix — being able to tell a
    real weekly run apart from a stale-guard rescue in the audit trail)."""
    assert _SCHED_SOURCE.count('params={"triggered_by": "weekly"}') == 1
    assert _SCHED_SOURCE.count('params={"triggered_by": "stale_guard"}') == 1
