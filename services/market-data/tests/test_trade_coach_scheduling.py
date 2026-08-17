"""Tests for T286-TRADE-PATTERN-COACH's send_weekly_trade_coach() scheduler wiring and
send_trade_coach_email() (email_service.py) composition.

send_trade_coach_email() is pure string composition (no DB/network dependency), tested
directly with real inputs. send_weekly_trade_coach() itself can't be imported in this test
environment (scheduler.py's import chain pulls in apscheduler and other unstubbed modules) —
covered by source-text regression checks, matching test_earnings_beat_screener.py's own
established pattern for this exact constraint.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_trade_coach_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _result(
    n_trades=25, window_days=90, win_rate=0.44, avg_return_pct=1.2,
    by_exit_reason=None, avg_giveback_pct_on_winners=8.5, avg_hold_days_vs_expected=-2.0,
    summary_text="Winning trades gave back a meaningful chunk from their own peak this window.",
):
    if by_exit_reason is None:
        by_exit_reason = [
            {"exit_reason": "stop_hit", "count": 10, "win_rate": 0.2, "avg_return_pct": -3.0, "total_pnl": -500.0},
            {"exit_reason": "target_reached", "count": 8, "win_rate": 1.0, "avg_return_pct": 12.0, "total_pnl": 900.0},
        ]
    return {
        "n_trades": n_trades, "window_days": window_days, "win_rate": win_rate,
        "avg_return_pct": avg_return_pct, "by_exit_reason": by_exit_reason,
        "avg_giveback_pct_on_winners": avg_giveback_pct_on_winners,
        "avg_hold_days_vs_expected": avg_hold_days_vs_expected, "summary_text": summary_text,
    }


# ── send_trade_coach_email() — pure composition, tested directly ────────────────────────────

def test_renders_trade_count_win_rate_and_avg_return():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_trade_coach_email("user@example.com", "Sun, Aug 17", _result())
    html, text = calls[0]["html"], calls[0]["text"]
    assert "25 closed trades" in html
    assert "44%" in html
    assert "+1.20%" in html
    assert "25 closed trades" in text


def test_renders_giveback_and_hold_delta():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_trade_coach_email("user@example.com", "Sun, Aug 17", _result())
    html = calls[0]["html"]
    assert "8.5%" in html
    assert "-2.0 days vs. expected" in html


def test_renders_all_exit_reason_rows():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_trade_coach_email("user@example.com", "Sun, Aug 17", _result())
    html = calls[0]["html"]
    assert "stop_hit" in html and "target_reached" in html


def test_renders_ai_summary_when_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_trade_coach_email("user@example.com", "Sun, Aug 17", _result())
    assert "gave back a meaningful chunk" in calls[0]["html"]


def test_missing_summary_shows_no_ai_summary_note_not_a_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_trade_coach_email("user@example.com", "Sun, Aug 17", _result(summary_text=None))
    assert result is True
    assert "No AI summary available" in calls[0]["html"]


def test_empty_exit_reason_list_shows_no_data_note_not_a_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_trade_coach_email("user@example.com", "Sun, Aug 17", _result(by_exit_reason=[]))
    assert result is True
    assert "No exit-reason data available" in calls[0]["html"]


def test_missing_giveback_and_hold_delta_render_em_dash_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_trade_coach_email(
            "user@example.com", "Sun, Aug 17",
            _result(avg_giveback_pct_on_winners=None, avg_hold_days_vs_expected=None),
        )
    assert result is True


def test_body_never_asserts_a_prescriptive_advice_claim():
    """The one honest-framing property this alert must have — this is a MEASURED-patterns
    review, never prescriptive trading advice about what to change."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_trade_coach_email("user@example.com", "Sun, Aug 17", _result())
    html = " ".join(calls[0]["html"].lower().split())  # collapse whitespace/line-wraps
    assert "not prescriptive advice" in html
    assert "not a prediction of future performance" in html


# ── send_weekly_trade_coach() — source-text regression checks ───────────────────────────────

def _send_weekly_trade_coach_body() -> str:
    start = _scheduler_source.index("def send_weekly_trade_coach(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


def test_gated_behind_the_feature_flag_checked_first():
    body = _send_weekly_trade_coach_body()
    assert '_get_redis().get(_REDIS_TRADE_COACH_ENABLED) != "1"' in body
    # the gate check must appear before the DB session is ever opened
    assert body.index('_REDIS_TRADE_COACH_ENABLED') < body.index("with SessionLocal()")


def test_recipients_are_all_users_with_an_email_not_price_alert_scoped():
    """This is a single account-wide aggregate across every portfolio, not tied to any one
    symbol subscription — matching send_weekly_theme_forecast()'s own all-User audience,
    NOT the PriceAlert-subscriber query every per-symbol alert in this file uses."""
    body = _send_weekly_trade_coach_body()
    assert "select(User).where(User.email.isnot(None)" in body
    assert "select(PriceAlert)" not in body


def test_skipped_entirely_when_compute_returns_none():
    body = _send_weekly_trade_coach_body()
    assert "if result is None:" in body


def test_reuses_compute_trade_patterns_not_a_second_derivation():
    body = _send_weekly_trade_coach_body()
    assert "from .trade_coach import compute_trade_patterns, generate_trade_coach_summary" in body
    assert "compute_trade_patterns(session)" in body


def test_dedup_is_per_user_and_date():
    body = _send_weekly_trade_coach_body()
    assert 'f"stockai:trade_coach:{user.id}:{today.isoformat()}"' in body


def test_per_recipient_send_is_isolated_from_the_rest_of_the_loop():
    body = _send_weekly_trade_coach_body()
    assert "for user in users:" in body
    assert "except Exception as exc:" in body


def test_dedup_key_is_set_only_after_a_successful_send():
    body = _send_weekly_trade_coach_body()
    # the setex call must appear inside the `if ok:` branch, not unconditionally before it
    if_ok_idx = body.index("if ok:")
    setex_idx = body.index("_rc.setex(redis_key")
    assert setex_idx > if_ok_idx


def test_job_is_registered_with_the_correct_cron_schedule():
    assert (
        'send_weekly_trade_coach,\n'
        '            CronTrigger(day_of_week="sun", hour=17, minute=45, timezone="America/New_York"),\n'
        '            id="trade_coach_weekly"'
    ) in _scheduler_source


def test_job_registration_is_gated_behind_is_alerting_enabled():
    idx = _scheduler_source.index('id="trade_coach_weekly"')
    preceding = _scheduler_source[max(0, idx - 400):idx]
    assert "if _is_alerting_enabled():" in preceding
