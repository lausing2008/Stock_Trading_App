"""Tests for T252-VALUE-AREA-BREAKDOWN-ALERT.

send_value_area_breakdown_email() is pure string composition (no DB/network dependency), so
it's tested directly with real inputs. compute_value_area_levels_daily()/
check_value_area_breakdown() themselves can't be imported in this test environment —
scheduler.py's import chain pulls in apscheduler and other unstubbed modules (see
test_price_alert_price_check.py's docstring for the same constraint) — so the job wiring/scan
logic is covered by source-text regression checks instead, matching
test_scheduler_static_names.py's and test_volume_anomaly_alert.py's established pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_value_area_breakdown_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _breakdown_alert(symbol="AAPL", price=100.0, poc=105.0, vah=108.0, val=102.0):
    return {
        "symbol": symbol, "price": price, "poc": poc, "vah": vah, "val": val,
        "note": f"closed below Value Area Low (${val:.2f})",
        "kind": "breakdown", "as_of": "2026-07-21",
    }


def _breakout_alert(symbol="MSFT", price=112.0, poc=105.0, vah=108.0, val=102.0):
    return {
        "symbol": symbol, "price": price, "poc": poc, "vah": vah, "val": val,
        "note": f"closed above Value Area High (${vah:.2f})",
        "kind": "breakout", "as_of": "2026-07-21",
    }


# ── send_value_area_breakdown_email() — pure composition, tested directly ───────────────────

def test_single_breakdown_renders_symbol_price_and_levels():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        ok = send_value_area_breakdown_email("user@example.com", [_breakdown_alert()])
    assert ok is True
    assert len(calls) == 1
    assert "AAPL" in calls[0]["html"]
    assert "AAPL" in calls[0]["text"]
    assert "Breakdown" in calls[0]["html"]
    assert "102.00" in calls[0]["html"]  # VAL
    assert "108.00" in calls[0]["html"]  # VAH
    assert "105.00" in calls[0]["html"]  # POC


def test_breakout_renders_with_distinct_label_and_color():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_value_area_breakdown_email("user@example.com", [_breakout_alert()])
    assert "Breakout" in calls[0]["html"]
    assert "MSFT" in calls[0]["html"]


def test_multiple_alerts_all_appear_in_one_email():
    calls, fake = _capture_send()
    alerts = [_breakdown_alert("AAPL"), _breakout_alert("MSFT"), _breakdown_alert("NVDA", price=200.0, poc=210, vah=215, val=205)]
    with patch("src.services.email_service.send_email", fake):
        send_value_area_breakdown_email("user@example.com", alerts)
    assert len(calls) == 1
    for sym in ("AAPL", "MSFT", "NVDA"):
        assert sym in calls[0]["html"]
        assert sym in calls[0]["text"]


def test_subject_line_reflects_alert_count():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_value_area_breakdown_email("user@example.com", [_breakdown_alert(), _breakout_alert()])
    assert "2 stocks" in calls[0]["subject"]


def test_singular_subject_for_one_alert():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_value_area_breakdown_email("user@example.com", [_breakdown_alert()])
    assert "1 stock " in calls[0]["subject"] or "1 stock" in calls[0]["subject"]
    assert "1 stocks" not in calls[0]["subject"]


def test_disclaimer_present_and_not_predictive():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_value_area_breakdown_email("user@example.com", [_breakdown_alert()])
    body = calls[0]["html"].lower()
    assert "not a" in body and "prediction" in body
    assert "not financial advice" in calls[0]["html"].lower()


def test_as_of_date_included_in_body():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_value_area_breakdown_email("user@example.com", [_breakdown_alert()])
    assert "2026-07-21" in calls[0]["html"]
    assert "2026-07-21" in calls[0]["text"]


def test_returns_false_when_send_email_fails():
    with patch("src.services.email_service.send_email", return_value=False):
        ok = send_value_area_breakdown_email("user@example.com", [_breakdown_alert()])
    assert ok is False


# ── Scheduler wiring — source-text checks (scheduler.py can't be imported here) ─────────────

def test_compute_job_and_alert_checker_both_exist_in_source():
    assert "def compute_value_area_levels_daily" in _scheduler_source
    assert "def check_value_area_breakdown" in _scheduler_source


def test_compute_job_is_registered_as_a_daily_cron_job():
    assert 'id="value_area_levels_daily"' in _scheduler_source
    assert "CronTrigger(hour=18, minute=0" in _scheduler_source


def test_alert_checker_is_registered_as_a_1_minute_interval_job():
    assert 'id="value_area_breakdown_check"' in _scheduler_source
    # Confirm the interval registration for this specific job id, not just that "minutes=1"
    # appears somewhere else in the file (many other jobs share that literal).
    idx = _scheduler_source.index('id="value_area_breakdown_check"')
    surrounding = _scheduler_source[max(0, idx - 200):idx + 50]
    assert "minutes=1" in surrounding


def test_alert_checker_reads_live_prices_cache_not_yfinance():
    """MUST read only the existing stockai:live_prices Redis cache — never yfinance/a
    per-symbol DB call in the loop, matching check_volume_anomalies()'s own established
    discipline for exactly this class of per-minute universe-touching job."""
    start = _scheduler_source.index("def check_value_area_breakdown")
    end = _scheduler_source.index("\ndef ", start + 10)
    body = _scheduler_source[start:end]
    assert "stockai:live_prices" in body
    assert "import yfinance" not in body


def test_alert_checker_has_a_lock_and_a_dedup_key():
    start = _scheduler_source.index("def check_value_area_breakdown")
    end = _scheduler_source.index("\ndef ", start + 10)
    body = _scheduler_source[start:end]
    assert "_VALUE_AREA_ALERT_LOCK_KEY" in body
    assert "stockai:value_area_alert:" in body


def test_compute_job_delegates_math_to_volume_area_module_not_reimplementing_it():
    """The daily job must call into volume_area.py's compute_value_area_levels_for_stocks()
    rather than re-deriving the bucket/value-area math a second time in scheduler.py."""
    start = _scheduler_source.index("def compute_value_area_levels_daily")
    end = _scheduler_source.index("\ndef ", start + 10)
    body = _scheduler_source[start:end]
    assert "from .volume_area import compute_value_area_levels_for_stocks" in body
