"""Tests for T260-BEARISH-PUTS-WATCHLIST's check_squeeze_watch_reverts() (scheduler.py) and
send_squeeze_watch_revert_email() (email_service.py).

send_squeeze_watch_revert_email() is pure string composition (no DB/network dependency), so
it's tested directly with real inputs. check_squeeze_watch_reverts() itself can't be imported
in this test environment — scheduler.py's import chain pulls in apscheduler and other
unstubbed modules — so the scan logic/job registration is covered by source-text regression
checks instead, matching test_short_squeeze_alert.py's / test_gamma_unwind_alert.py's
established pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_squeeze_watch_revert_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _check_squeeze_watch_reverts_body() -> str:
    start = _scheduler_source.index("def check_squeeze_watch_reverts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── send_squeeze_watch_revert_email() — pure composition, tested directly ───────────────────

def test_short_squeeze_watch_type_uses_short_squeeze_label():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_watch_revert_email(
            "user@example.com", "GME", "short_squeeze",
            "price recovered to $210.00 (was $180.00 when added)", 210.0, 8.0,
        )
    assert "Short Squeeze Watch" in calls[0]["subject"]
    assert "GME" in calls[0]["html"]
    assert "$210.00" in calls[0]["html"]
    assert "Short % of float" in calls[0]["html"]


def test_bearish_puts_watch_type_uses_bearish_puts_label():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_watch_revert_email(
            "user@example.com", "XYZ", "bearish_puts",
            "short-side options/interest pressure has faded", 42.5, 48.0,
        )
    assert "Bearish Puts Watch" in calls[0]["subject"]
    assert "Puts concentration" in calls[0]["html"]


def test_revert_reason_rendered_in_body():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_watch_revert_email(
            "user@example.com", "ABC", "bearish_puts",
            "price recovered to $50.00 (was $40.00 when added); short-side options/interest pressure has faded",
            50.0, 40.0,
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "price recovered to $50.00" in html
    assert "price recovered to $50.00" in text


def test_missing_price_or_metric_degrades_gracefully_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_squeeze_watch_revert_email(
            "user@example.com", "XYZ", "bearish_puts", "setup rolled off the scan", None, None,
        )
    assert result is True
    assert "—" in calls[0]["html"]


def test_body_states_this_watch_will_not_alert_again():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_watch_revert_email(
            "user@example.com", "GME", "short_squeeze", "price recovered", 210.0, 8.0,
        )
    html, text = calls[0]["html"], calls[0]["text"]
    assert "will not alert again" in html.lower()
    assert "will not alert again" in text.lower()


# ── check_squeeze_watch_reverts() — source-text regression checks ───────────────────────────

def test_only_un_reverted_watches_are_checked():
    body = _check_squeeze_watch_reverts_body()
    assert "SqueezeWatch.reverted.is_(False)" in body


def test_uses_a_redis_lock():
    body = _check_squeeze_watch_reverts_body()
    assert "_SQUEEZE_WATCH_LOCK_KEY" in body
    assert "nx=True" in body


def test_uses_live_prices_and_bearish_watch_caches_not_a_fresh_fetch():
    """MUST read the same caches other fast alerts read (stockai:live_prices,
    stockai:bearish_puts_watch, stockai:fundamentals:v2:*) — never a per-symbol yfinance call
    inside this 1-minute loop."""
    body = _check_squeeze_watch_reverts_body()
    assert '"stockai:live_prices"' in body
    assert '"stockai:bearish_puts_watch"' in body
    assert "import yfinance" not in body


def test_revert_condition_is_an_or_not_an_and():
    """Per the user's own explicit choice: EITHER price recovery OR the metric fading alone is
    enough to mark reverted — not both required together."""
    body = _check_squeeze_watch_reverts_body()
    assert "if not (price_recovered or metric_faded):" in body


def test_short_squeeze_metric_faded_uses_the_same_threshold_that_qualified_it():
    body = _check_squeeze_watch_reverts_body()
    assert "metric_faded = current_metric < _SQUEEZE_MIN_SHORT_FLOAT" in body


def test_bearish_puts_metric_faded_uses_the_gamma_unwind_concentration_threshold():
    body = _check_squeeze_watch_reverts_body()
    assert "_GAMMA_UNWIND_MIN_OI_CONCENTRATION" in body


def test_bearish_puts_watch_rolling_off_the_scan_entirely_counts_as_faded():
    """If a symbol no longer appears in the current bearish-puts-watch cache at all (or is no
    longer puts-dominant), that must ALSO count as the setup having faded — not just a
    concentration % drop while still present."""
    body = _check_squeeze_watch_reverts_body()
    assert 'bp is None or bp.get("dominant_side") != "puts"' in body


def test_marks_reverted_only_after_a_successful_send_not_before():
    """A failed email send must not silently mark the watch reverted — the user would then
    never learn about a real revert that happened to hit a delivery failure."""
    body = _check_squeeze_watch_reverts_body()
    sent_idx = body.index("sent_ok = send_squeeze_watch_revert_email(")
    reverted_idx = body.index("w.reverted = True")
    assert sent_idx < reverted_idx
    assert "if sent_ok:" in body


def test_job_is_registered_at_one_minute_interval():
    assert 'id="squeeze_watch_revert_check"' in _scheduler_source
    idx = _scheduler_source.index('id="squeeze_watch_revert_check"')
    preceding = _scheduler_source[max(0, idx - 300):idx]
    assert "minutes=1" in preceding
