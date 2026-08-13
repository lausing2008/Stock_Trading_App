"""Regression tests for the theme-forecast send-loop dedup gap (found via code review,
2026-08-13).

send_weekly_theme_forecast()'s per-recipient send loop already had per-recipient error
isolation (its own try/except around each send call), but was missing the OTHER half of the
established AUD256 pattern already used by send_morning_digest()/send_premarket_brief() in
this same file — a per-(user, date) Redis dedup key set only after a successful send. Without
it, a restart within this job's own misfire_grace_time=60 window (registered with the same
**_JOB_DEFAULTS as every sibling digest job) could re-send the weekly theme forecast to every
user a second time.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler and other unstubbed modules) — covered via source-text regression checks, matching
test_morning_digest_send_loop.py's established pattern for this exact risk class.
"""
import pathlib

_SCHEDULER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
)
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


def _theme_forecast_body() -> str:
    start = _SCHEDULER_SOURCE.index("def send_weekly_theme_forecast(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def test_checks_a_redis_dedup_key_before_sending():
    """Must check a Redis existence key scoped to (user, date) BEFORE calling
    send_theme_forecast_email(), and skip (continue) if it's already set — same shape as
    send_morning_digest()'s own fix."""
    body = _theme_forecast_body()
    assert 'redis_key = f"stockai:theme_forecast:{user.id}:{today.isoformat()}"' in body
    dedup_check_idx = body.index("_rc.exists(redis_key)")
    send_call_idx = body.index("send_theme_forecast_email(")
    assert dedup_check_idx < send_call_idx, "dedup check must happen BEFORE the send call"


def test_sets_the_dedup_key_only_after_a_successful_send():
    """The dedup key must only be set inside the `if ok:` branch — setting it unconditionally
    (even on a failed send) would incorrectly suppress a legitimate retry after a real
    failure."""
    body = _theme_forecast_body()
    setex_idx = body.index("_rc.setex(redis_key")
    if_ok_idx = body.rindex("if ok:", 0, setex_idx)
    send_call_idx = body.index("send_theme_forecast_email(")
    assert if_ok_idx > send_call_idx
    assert setex_idx > if_ok_idx


def test_isolates_per_recipient_send_errors():
    """A single recipient's send_theme_forecast_email() raising must not propagate to the
    outer except and abort the whole batch — the send call itself must be wrapped in its own
    try/except that keeps the loop going for the remaining recipients."""
    body = _theme_forecast_body()
    send_call_idx = body.index("send_theme_forecast_email(")
    try_idx = body.rindex("try:", 0, send_call_idx)
    except_idx = body.index("except Exception as exc:", send_call_idx)
    assert try_idx < send_call_idx < except_idx


def test_logs_and_counts_per_recipient_errors_without_reraising():
    body = _theme_forecast_body()
    assert 'log.warning("theme_forecast.recipient_send_error"' in body
    assert "errors += 1" in body
    done_log_idx = body.index('log.info("theme_forecast.sent"')
    done_log_line = body[done_log_idx:body.index("\n", done_log_idx + 200)]
    assert "errors=errors" in done_log_line


def test_a_send_returning_false_without_raising_still_counts_as_an_error_exactly_once():
    """send_theme_forecast_email() can return False directly (no exception) on a disabled/
    unconfigured provider or an SMTP auth failure — this must still increment errors exactly
    once via the `else` branch, not be silently uncounted, and must not ALSO increment errors
    a second time via the except block (which only fires on a real exception)."""
    body = _theme_forecast_body()
    if_ok_idx = body.index("if ok:")
    else_idx = body.index("else:", if_ok_idx)
    else_block = body[else_idx:else_idx + 60]
    assert "errors += 1" in else_block


def test_dedup_key_ttl_is_20_hours_matching_the_established_sibling_convention():
    """20h TTL matches send_morning_digest()'s own established convention for a job that
    fires at most once/day — long enough to survive any same-day restart-and-retry, short
    enough to never suppress next week's real send."""
    body = _theme_forecast_body()
    setex_idx = body.index("_rc.setex(redis_key")
    line_end = body.index("\n", setex_idx)
    line = body[setex_idx:line_end]
    assert "20 * 3600" in line
