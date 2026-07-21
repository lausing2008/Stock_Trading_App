"""Regression tests for BUG-MORNINGDIGEST-SENDLOOP.

send_morning_digest()'s per-recipient send loop had the identical unguarded pattern already
found and fixed in send_premarket_brief() (AUD256, 2026-07-20c): no dedup (a restart within
this job's own misfire-grace window could re-email every recipient a second time) and no
per-recipient error isolation (a single bad send would propagate to the outer except, aborting
the whole batch and silently skipping every recipient still left in the loop).

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler and other unstubbed modules — see test_price_alert_price_check.py's docstring for
the same constraint) — the fix is covered by source-text regression checks, matching
test_premarket_brief.py's established pattern for this exact risk class.
"""
import pathlib

_SCHEDULER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
)
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


def _morning_digest_body() -> str:
    start = _SCHEDULER_SOURCE.index("def send_morning_digest(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def test_checks_a_redis_dedup_key_before_sending():
    """Must check a Redis existence key scoped to (user, market, date) BEFORE calling
    send_morning_digest_email(), and skip (continue) if it's already set — same shape as
    send_premarket_brief()'s own fix."""
    body = _morning_digest_body()
    assert 'redis_key = f"stockai:morning_digest:{user.id}:{market_key}:{today_str}"' in body
    dedup_check_idx = body.index("_rc.exists(redis_key)")
    send_call_idx = body.index("send_morning_digest_email(")
    assert dedup_check_idx < send_call_idx, "dedup check must happen BEFORE the send call"


def test_sets_the_dedup_key_only_after_a_successful_send():
    """The dedup key must only be set inside the `if ok:` branch — setting it unconditionally
    (even on a failed send) would incorrectly suppress a legitimate retry after a real failure."""
    body = _morning_digest_body()
    setex_idx = body.index("_rc.setex(redis_key")
    if_ok_idx = body.rindex("if ok:", 0, setex_idx)
    send_call_idx = body.index("send_morning_digest_email(")
    assert if_ok_idx > send_call_idx
    assert setex_idx > if_ok_idx


def test_isolates_per_recipient_send_errors():
    """A single recipient's send_morning_digest_email() raising must not propagate to the
    outer except and abort the whole batch — the send call itself must be wrapped in its own
    try/except that keeps the loop going for the remaining recipients."""
    body = _morning_digest_body()
    send_call_idx = body.index("send_morning_digest_email(")
    try_idx = body.rindex("try:", 0, send_call_idx)
    except_idx = body.index("except Exception as _send_exc:", send_call_idx)
    assert try_idx < send_call_idx < except_idx


def test_logs_and_counts_per_recipient_errors_without_reraising():
    body = _morning_digest_body()
    assert 'log.warning("morning_digest.recipient_send_error"' in body
    assert "errors += 1" in body
    done_log_idx = body.index('log.info("morning_digest.done"')
    done_log_line = body[done_log_idx:body.index("\n", done_log_idx + 200)]
    assert "errors=errors" in done_log_line


def test_dedup_key_is_scoped_per_market_not_shared_across_a_us_and_hk_run_on_the_same_day():
    """send_morning_digest() is called once per market (US and HK are separate invocations,
    per its own docstring) — the dedup key must include market_key so a US-market digest and
    an HK-market digest on the same day don't collide and suppress each other."""
    body = _morning_digest_body()
    assert 'market_key = "_".join(m.lower() for m in markets)' in body
    assert "{market_key}" in body
