"""Tests for AUD266-ALERT-JOBS-LACK-STATUS-CONSEQUENCE-DQ.

There was no DQ check asserting "the alert jobs have actually run recently" — combined with
the (separately fixed, AUD266-FIVE-ALERT-JOBS-RECORD-NO-STATUS) missing job-status calls, a
total alert-system outage had NO detection path at all. That prior fix added the underlying
Redis liveness records (scheduler:job:{name}); this fix gives _DQ_CHECKS a way to read them —
a `source: "job_status"` marker that routes the per-check loop to a Redis read instead of a
SQL query — and adds 5 new entries, one per alert job, so alert-liveness gets its own,
independent detection/alerting surface (this DQ framework's own dedicated email-alert path)
distinct from admin-health.tsx's JS rendering of the same underlying facts.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler/db, both stubbed by conftest.py) — the new branching logic inside the per-check
loop is small and self-contained enough to extract via exec() and run against a real,
synthetic Redis-shaped payload, matching the technique already established for
_lookup_outcome_price()/score_size_mult earlier in this codebase's history. The 5 new
_DQ_CHECKS entries themselves are checked via source-text regression, matching every other
scheduler.py test file's established pattern for this exact import constraint.
"""
import json
import pathlib
from datetime import datetime, timedelta, timezone

_SCHEDULER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
)
_SOURCE = _SCHEDULER_PATH.read_text()

_ALERT_JOB_NAMES = [
    "check_price_alerts",
    "check_signal_alerts",
    "check_earnings_reactions",
    "check_earnings_impact_alerts",
    "check_macro_reaction_alerts",
]


def _resolve_job_status_check(raw_status: str | None):
    """Pulls the real `if check.get("source") == "job_status": ...` branch out of
    run_data_quality_checks() and exec()s it against a synthetic Redis .get() return value —
    the exact statements that decide `result` for a job_status-sourced check, isolated from
    the surrounding SQL-query branch and the SessionLocal()/session.execute() machinery this
    test doesn't need."""
    start = _SOURCE.index('if check.get("source") == "job_status":')
    end = _SOURCE.index("else:\n                        result = session.execute", start)
    body = _SOURCE[start:end]
    dedented = [ln[20:] if ln.startswith(" " * 20) else ln for ln in body.splitlines()]
    func_source = (
        "def _resolve(check, redis_client):\n"
        + "\n".join("    " + ln for ln in dedented)
        + "\n    return result\n"
    )
    namespace = {"json": json, "datetime": datetime}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source

    class _FakeRedis:
        def get(self, key):
            return raw_status

    return namespace["_resolve"]({"job_name": "check_price_alerts", "source": "job_status"}, _FakeRedis())


def test_a_healthy_recent_status_resolves_to_a_real_tz_aware_datetime():
    now = datetime.now(timezone.utc)
    raw = json.dumps({"job": "check_price_alerts", "status": "ok", "last_run": now.isoformat(),
                       "duration_s": 0.2, "error": None})
    result = _resolve_job_status_check(raw)
    assert result is not None
    assert result.tzinfo is not None
    age_seconds = (datetime.now(timezone.utc) - result).total_seconds()
    assert 0 <= age_seconds < 5


def test_a_missing_status_key_resolves_to_none_not_a_crash():
    """No scheduler:job:{name} key at all (job never ran even once, or the key expired) must
    resolve to None — the surrounding is-None branch then correctly reports ok=False,
    age_hours=None, matching every other check's own missing-data handling."""
    assert _resolve_job_status_check(None) is None


def test_a_stale_last_run_still_parses_correctly_far_in_the_past():
    stale = datetime.now(timezone.utc) - timedelta(hours=6)
    raw = json.dumps({"job": "check_signal_alerts", "status": "ok", "last_run": stale.isoformat(),
                       "duration_s": 1.0, "error": None})
    result = _resolve_job_status_check(raw)
    age_hours = (datetime.now(timezone.utc) - result).total_seconds() / 3600
    assert 5.9 < age_hours < 6.1


def test_all_5_alert_jobs_have_a_job_status_dq_check_entry():
    for job_name in _ALERT_JOB_NAMES:
        entry_idx = _SOURCE.index(f'"job_name": "{job_name}", "source": "job_status"')
        assert entry_idx > 0, f"missing job_status DQ check entry for {job_name}"


def test_job_status_entries_have_no_sql_query_field():
    """A job_status-sourced check must never carry a "query" key — that would make it
    ambiguous which code path the loop should take, and the real loop only checks `source`,
    never falls back to running a query for one of these 5 entries."""
    checks_start = _SOURCE.index("_DQ_CHECKS: list[dict] = [")
    checks_end = _SOURCE.index("\n]\n\n\ndef run_data_quality_checks", checks_start)
    checks_block = _SOURCE[checks_start:checks_end]
    for job_name in _ALERT_JOB_NAMES:
        entry_start = checks_block.index(f'"job_name": "{job_name}"')
        dict_start = checks_block.rindex("{", 0, entry_start)
        dict_end = checks_block.index("}", entry_start) + 1
        entry_dict_text = checks_block[dict_start:dict_end]
        assert '"query"' not in entry_dict_text, f"{job_name}'s entry unexpectedly has a query key"


def test_job_status_checks_are_gated_before_the_sql_query_branch():
    """Regression guard on ordering: the `if check.get("source") == "job_status":` branch
    must be checked BEFORE the SQL query branch, not after — otherwise every check
    (including the 5 new ones, which have no "query" key at all) would hit
    session.execute(text(check["query"])) first and raise a KeyError."""
    source_check_idx = _SOURCE.index('if check.get("source") == "job_status":')
    sql_query_idx = _SOURCE.index('result = session.execute(text(check["query"]))')
    assert source_check_idx < sql_query_idx
