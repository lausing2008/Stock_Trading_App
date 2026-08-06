"""Tests for AUD266-FIVE-ALERT-JOBS-RECORD-NO-STATUS.

check_price_alerts(), check_signal_alerts(), check_earnings_reactions(),
check_earnings_impact_alerts(), and check_macro_reaction_alerts() previously made ZERO
_record_job_status() calls — invisible to GET /admin/scheduler-status and the admin health
page's errorCount/staleCount/"All healthy" computation. A total outage in any of these 5 jobs
would render "All healthy" indefinitely, since that computation only ever inspects the `jobs`
array `_record_job_status` populates.

None of the 5 functions can be imported directly in this test environment — scheduler.py's
import chain pulls in apscheduler and other unstubbed modules (see
test_price_alert_price_check.py's docstring for the same constraint) — so this is covered by
source-text regression checks, matching test_top3_conviction_alert.py's/
test_scheduler_static_names.py's established pattern for exactly this class of function.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _function_body(name: str) -> str:
    start = _scheduler_source.index(f"\ndef {name}(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── check_price_alerts() ─────────────────────────────────────────────────────────────────

def test_check_price_alerts_records_ok_on_early_return_and_success():
    body = _function_body("check_price_alerts")
    assert '_record_job_status("check_price_alerts", "ok", time.monotonic() - _t0)' in body


def test_check_price_alerts_records_error_in_outer_except():
    body = _function_body("check_price_alerts")
    except_idx = body.rindex('except Exception as exc:\n        log.error("alert.check_error"')
    tail = body[except_idx:except_idx + 300]
    assert '_record_job_status("check_price_alerts", "error", time.monotonic() - _t0, str(exc))' in tail


def test_check_price_alerts_has_a_start_timer():
    body = _function_body("check_price_alerts")
    def_idx = body.index("def check_price_alerts(")
    assert "_t0 = time.monotonic()" in body[def_idx:def_idx + 400]


# ── check_signal_alerts() ────────────────────────────────────────────────────────────────

def test_check_signal_alerts_records_ok_on_early_return_and_success():
    body = _function_body("check_signal_alerts")
    assert '_record_job_status("check_signal_alerts", "ok", time.monotonic() - _t0)' in body


def test_check_signal_alerts_records_error_in_outer_except():
    body = _function_body("check_signal_alerts")
    except_idx = body.rindex('except Exception as exc:\n        log.error("signal_alert.check_error"')
    tail = body[except_idx:except_idx + 300]
    assert '_record_job_status("check_signal_alerts", "error", time.monotonic() - _t0, str(exc))' in tail


def test_check_signal_alerts_has_a_start_timer():
    body = _function_body("check_signal_alerts")
    docstring_end = body.index('"""', body.index('"""') + 3) + 3
    assert "_t0 = time.monotonic()" in body[docstring_end:docstring_end + 200]


def test_check_signal_alerts_success_status_comes_after_the_earnings_reminder_block():
    """The success record must sit AFTER the earnings-reminder digest inner try/except (its
    own separate exception boundary), not before it — otherwise a genuinely successful signal
    scan followed by a failed earnings-reminder send would still record "ok" for the whole
    cycle, but from the WRONG point (before real end-of-function work has actually happened)."""
    body = _function_body("check_signal_alerts")
    reminder_except_idx = body.index('log.warning("signal_alert.earnings_reminder_error"')
    ok_idx = body.rindex('_record_job_status("check_signal_alerts", "ok"')
    assert ok_idx > reminder_except_idx


# ── check_earnings_reactions() (already fixed in a prior segment — regression guard only) ──

def test_check_earnings_reactions_records_ok_and_error():
    body = _function_body("check_earnings_reactions")
    assert '_record_job_status("check_earnings_reactions", "ok", time.monotonic() - _t0)' in body
    assert '_record_job_status("check_earnings_reactions", "error", time.monotonic() - _t0, str(exc))' in body


# ── check_earnings_impact_alerts() ───────────────────────────────────────────────────────

def test_check_earnings_impact_alerts_records_ok_on_every_early_return():
    body = _function_body("check_earnings_impact_alerts")
    # 3 real early-return points: feature-flag-off, no-subscribed-symbols, no-pending-rows —
    # plus the final success path = 4 occurrences of the "ok" call.
    assert body.count('_record_job_status("check_earnings_impact_alerts", "ok", time.monotonic() - _t0)') == 4


def test_check_earnings_impact_alerts_records_error_in_outer_except():
    body = _function_body("check_earnings_impact_alerts")
    except_idx = body.rindex('except Exception as exc:\n        log.error("signal_alert.earnings_impact_error"')
    tail = body[except_idx:except_idx + 300]
    assert '_record_job_status("check_earnings_impact_alerts", "error", time.monotonic() - _t0, str(exc))' in tail


def test_check_earnings_impact_alerts_has_a_start_timer():
    body = _function_body("check_earnings_impact_alerts")
    docstring_end = body.index('"""', body.index('"""') + 3) + 3
    assert "_t0 = time.monotonic()" in body[docstring_end:docstring_end + 200]


# ── check_macro_reaction_alerts() ────────────────────────────────────────────────────────

def test_check_macro_reaction_alerts_records_ok_on_every_early_return():
    body = _function_body("check_macro_reaction_alerts")
    # 3 real early-return points: feature-flag-off, no-pending-rows, no-recipients —
    # plus the final success path = 4 occurrences of the "ok" call.
    assert body.count('_record_job_status("check_macro_reaction_alerts", "ok", time.monotonic() - _t0)') == 4


def test_check_macro_reaction_alerts_records_error_in_outer_except():
    body = _function_body("check_macro_reaction_alerts")
    except_idx = body.rindex('except Exception as exc:\n        log.error("signal_alert.macro_reaction_error"')
    tail = body[except_idx:except_idx + 300]
    assert '_record_job_status("check_macro_reaction_alerts", "error", time.monotonic() - _t0, str(exc))' in tail


def test_check_macro_reaction_alerts_has_a_start_timer():
    body = _function_body("check_macro_reaction_alerts")
    docstring_end = body.index('"""', body.index('"""') + 3) + 3
    assert "_t0 = time.monotonic()" in body[docstring_end:docstring_end + 200]


def test_check_macro_reaction_alerts_flag_disabled_return_still_records_status():
    """The feature-flag-disabled early return (macro_llm_reaction_enabled == "0") must NOT be
    a silent no-op — a job correctly skipping its own work because a flag is off is still a
    genuine "ok" cycle, distinct from the job never running at all."""
    body = _function_body("check_macro_reaction_alerts")
    flag_check_idx = body.index('if _get_redis().get(_REDIS_MACRO_LLM_ENABLED) == "0":')
    tail = body[flag_check_idx:flag_check_idx + 200]
    assert '_record_job_status("check_macro_reaction_alerts", "ok"' in tail
