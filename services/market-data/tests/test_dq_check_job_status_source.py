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

# AUD-DQCHECKS-VISIBILITY: 12 more minute-cadence jobs given job_status DQ checks in the same
# pass that fixed AUD-MISFIREGRACE-OPTIONSFLOW (2 of these — check_options_flow_alerts and
# check_dark_pool_alerts — were the exact jobs confirmed silently dead in production; a 3rd,
# check_sr_watch_reverts, was found with the identical live symptom during that investigation).
# Each maps its DQ-check "name" to the REAL Redis key it reads — check_portfolio_drawdown_alerts
# is the one exception: that job already called _record_job_status() correctly, just under its
# scheduler `id=` string ("portfolio_drawdown_alert_check") rather than its function name, so
# its DQ check's job_name intentionally does NOT match its own check "name" key.
_NEW_MINUTE_JOB_CHECKS = {
    "check_volume_anomalies": "check_volume_anomalies",
    "check_conditional_orders": "check_conditional_orders",
    "check_short_squeeze_alerts": "check_short_squeeze_alerts",
    "check_squeeze_ignition_alerts": "check_squeeze_ignition_alerts",
    "check_squeeze_watch_reverts": "check_squeeze_watch_reverts",
    "check_options_flow_alerts": "check_options_flow_alerts",
    "check_dark_pool_alerts": "check_dark_pool_alerts",
    "check_sr_watch_reverts": "check_sr_watch_reverts",
    "check_value_area_breakdown": "check_value_area_breakdown",
    "check_portfolio_drawdown_alerts": "portfolio_drawdown_alert_check",
    "check_early_earnings_news_alerts": "check_early_earnings_news_alerts",
    "check_top3_conviction": "check_top3_conviction",
}


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


# ── AUD-DQCHECKS-VISIBILITY: 12 more minute jobs given job_status DQ checks ──────────────────

def test_all_12_new_minute_jobs_have_a_job_status_dq_check_entry_with_the_right_key():
    for check_name, job_name in _NEW_MINUTE_JOB_CHECKS.items():
        entry_idx = _SOURCE.index(f'"job_name": "{job_name}", "source": "job_status"')
        assert entry_idx > 0, f"missing job_status DQ check entry for {check_name} (job_name={job_name})"


def test_new_job_status_entries_have_no_sql_query_field():
    checks_start = _SOURCE.index("_DQ_CHECKS: list[dict] = [")
    checks_end = _SOURCE.index("\n]\n\n\ndef run_data_quality_checks", checks_start)
    checks_block = _SOURCE[checks_start:checks_end]
    for check_name, job_name in _NEW_MINUTE_JOB_CHECKS.items():
        entry_start = checks_block.index(f'"job_name": "{job_name}"')
        dict_start = checks_block.rindex("{", 0, entry_start)
        dict_end = checks_block.index("}", entry_start) + 1
        entry_dict_text = checks_block[dict_start:dict_end]
        assert '"query"' not in entry_dict_text, f"{check_name}'s entry unexpectedly has a query key"


def test_the_two_confirmed_silently_dead_jobs_have_dq_checks():
    """The exact 2 jobs this whole investigation started from — confirm they're not just
    listed in _NEW_MINUTE_JOB_CHECKS (the test fixture) but genuinely present in the real
    _DQ_CHECKS source."""
    for job_name in ("check_options_flow_alerts", "check_dark_pool_alerts"):
        assert f'"job_name": "{job_name}", "source": "job_status"' in _SOURCE


def test_portfolio_drawdown_check_uses_its_real_existing_redis_key_not_its_function_name():
    """check_portfolio_drawdown_alerts() already calls _record_job_status() under the id=
    string "portfolio_drawdown_alert_check", not its own function name — the DQ check's
    job_name must match the key that's ACTUALLY written, or this check would silently never
    find real data and always report ok=False/stale."""
    assert '"job_name": "portfolio_drawdown_alert_check", "source": "job_status"' in _SOURCE
    # And must NOT have accidentally used the (wrong, never-written) function-name key instead.
    assert '"job_name": "check_portfolio_drawdown_alerts"' not in _SOURCE


def test_conditional_orders_now_records_job_status_in_its_own_module():
    """check_conditional_orders() lives in conditional_orders.py, not scheduler.py — confirm
    the _record_job_status() calls this pass added actually exist there, not just that
    scheduler.py's _DQ_CHECKS entry expects them to."""
    cond_orders_path = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "conditional_orders.py"
    )
    body = cond_orders_path.read_text()
    assert '_record_job_status("check_conditional_orders", "ok"' in body
    assert '_record_job_status("check_conditional_orders", "error"' in body


# ── AUD-DQCHECKS-VISIBILITY: Unusual Whales rate-limit gauge ─────────────────────────────────

def test_uw_rate_limit_gauge_entry_exists_and_has_no_pass_fail_concept():
    """Must be a "gauge" source (like the 3 fundamentals-cache-miss counters), not a "query" or
    "job_status" check — a nonzero rate-limit count is expected background noise sometimes, not
    inherently a failure, matching every other gauge's own established framing."""
    checks_start = _SOURCE.index("_DQ_CHECKS: list[dict] = [")
    checks_end = _SOURCE.index("\n]\n\n\ndef run_data_quality_checks", checks_start)
    checks_block = _SOURCE[checks_start:checks_end]
    entry_start = checks_block.index('"name": "uw_rate_limit_events_48h"')
    dict_start = checks_block.rindex("{", 0, entry_start)
    dict_end = checks_block.index("}", entry_start) + 1
    entry_dict_text = checks_block[dict_start:dict_end]
    assert '"source": "gauge"' in entry_dict_text
    assert '"query"' not in entry_dict_text
    assert '"job_name"' not in entry_dict_text


def test_uw_rate_limit_gauge_reads_the_real_counter_key_from_unusual_whales_module():
    """The gauge's counter_key must reference the SAME constant unusual_whales.py's
    _incr_rate_limit_counter() actually increments — imported at module level (a plain
    constant, not a function, so no circular-import risk), not a separately-defined literal
    string that could silently drift from the real key."""
    assert "from .unusual_whales import _RATE_LIMIT_COUNTER_KEY as _UW_RATE_LIMIT_COUNTER_KEY" in _SOURCE
    assert '"counter_key": _UW_RATE_LIMIT_COUNTER_KEY' in _SOURCE


# ── AUD-DQCHECK-WRONGCADENCE: check_signal_alerts' threshold matched to its real cadence ─────

def test_check_signal_alerts_uses_a_market_hours_threshold_not_a_1_minute_one():
    """check_signal_alerts() is NOT a 1-minute cron — it runs 5x/day via _run_market_refresh()
    (US market-hours cadence) plus once at container startup. Its DQ entry originally copied
    max_age_hours=1 from its 4 genuinely-1-minute siblings, causing a guaranteed false "stale"
    every single evening/overnight/weekend with zero real liveness problem — confirmed live in
    production. Must match signals_us/signals_hk's own convention for this identical cadence:
    max_age_hours=30 plus a "market" tag so the weekend/holiday skip logic applies."""
    checks_start = _SOURCE.index("_DQ_CHECKS: list[dict] = [")
    checks_end = _SOURCE.index("\n]\n\n\ndef run_data_quality_checks", checks_start)
    checks_block = _SOURCE[checks_start:checks_end]
    entry_start = checks_block.index('"job_name": "check_signal_alerts"')
    dict_start = checks_block.rindex("{", 0, entry_start)
    dict_end = checks_block.index("}", entry_start) + 1
    entry_dict_text = checks_block[dict_start:dict_end]
    assert '"max_age_hours": 30' in entry_dict_text
    assert '"market": "US"' in entry_dict_text
    assert '"max_age_hours": 1,' not in entry_dict_text


def test_check_signal_alerts_market_tag_is_actually_honored_by_the_job_status_dispatch():
    """The job_status dispatch branch resolves `result` differently from the query branch, but
    must still fall through into the SAME shared market-tag skip logic (T242-DQ1) — otherwise
    adding "market": "US" to a job_status entry would be a silent no-op that never actually
    prevents a weekend/holiday false positive."""
    job_status_idx = _SOURCE.index('if check.get("source") == "job_status":')
    # AUD-CONVRATIO-WEEKEND: anchor on the newline+indent prefix, not a bare substring.
    # The ratio branch's own `_ratio_market = check.get("market")` (added earlier in the
    # function) CONTAINS the bare string `market = check.get("market")`, so a plain .index()
    # matched that line instead and made this assertion compare unrelated offsets.
    market_check_idx = _SOURCE.index('\n                    market = check.get("market")')
    # The market-tag skip logic must appear AFTER the job_status branch resolves `result`,
    # in the same shared code path (not a separate, job_status-exclusive branch that could
    # diverge from the query branch's behavior).
    assert job_status_idx < market_check_idx
    us_skip_idx = _SOURCE.index('if market == "US" and not _is_us_trading_day():', market_check_idx)
    assert market_check_idx < us_skip_idx
