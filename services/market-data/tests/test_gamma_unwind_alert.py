"""Tests for the options-expiry gamma-unwind alert (check_gamma_unwind_alerts()).

send_gamma_unwind_email() is pure string composition (no DB/network dependency), so it's
tested directly with real inputs. check_gamma_unwind_alerts() itself can't be imported in this
test environment — scheduler.py's import chain pulls in apscheduler and other unstubbed
modules — so the scan logic/job registration is covered by source-text regression checks
instead, matching test_volume_anomaly_alert.py's / test_short_squeeze_alert.py's established
pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_gamma_unwind_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _check_gamma_unwind_alerts_body() -> str:
    start = _scheduler_source.index("def check_gamma_unwind_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── send_gamma_unwind_email() — pure composition, tested directly ───────────────────────────

def test_single_candidate_renders_symbol_side_and_concentration():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 68.5,
             "total_oi_near_money": 45000, "price": 250.0},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "TSLA" in html and "68% calls" in html and "45,000" in html and "$250.00" in html
    assert "TSLA" in text


def test_expires_today_renders_distinct_urgency_text():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-05", "days_to_expiry": 0,
             "dominant_side": "puts", "concentration_pct": 60.0,
             "total_oi_near_money": 10000, "price": 250.0},
        ])
    html = calls[0]["html"]
    assert "expires TODAY" in html
    assert "expires in 0d" not in html  # must use the distinct today-specific phrasing


def test_subject_and_body_never_claim_a_firm_direction():
    """This is the one property that MUST hold — unlike the short-squeeze alert's explicit
    BUY-signal framing, this alert must never assert a specific direction, since the app has
    no real gamma-exposure calc to back that claim up."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "TSLA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 68.5,
             "total_oi_near_money": 45000, "price": 250.0},
        ])
    subject, html = calls[0]["subject"], calls[0]["html"]
    assert "BUY" not in subject.upper() and "SELL" not in subject.upper()
    assert "not a directional" in html.lower() or "genuinely uncertain" in html.lower()


def test_puts_dominant_renders_red_calls_dominant_renders_green():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_gamma_unwind_email("user@example.com", [
            {"symbol": "AAA", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 60.0,
             "total_oi_near_money": 1000, "price": 100.0},
            {"symbol": "BBB", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "puts", "concentration_pct": 60.0,
             "total_oi_near_money": 1000, "price": 100.0},
        ])
    html = calls[0]["html"]
    assert "#22c55e" in html  # calls-dominant color
    assert "#ef4444" in html  # puts-dominant color


def test_missing_price_degrades_gracefully_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_gamma_unwind_email("user@example.com", [
            {"symbol": "XYZ", "expiry": "2026-08-07", "days_to_expiry": 2,
             "dominant_side": "calls", "concentration_pct": 60.0,
             "total_oi_near_money": 1000, "price": None},
        ])
    assert result is True
    assert "—" in calls[0]["html"]


# ── check_gamma_unwind_alerts() — source-text regression checks ─────────────────────────────

def test_uses_bounded_symbol_set_not_universe_wide():
    """yfinance options-chain is this app's most rate-limit-fragile call — must never scan the
    whole universe, matching the pre-existing EOD options-flow snapshot job's own discipline."""
    body = _check_gamma_unwind_alerts_body()
    assert "_bounded_options_flow_symbols" in body


def test_filters_to_expiries_within_the_max_days_window():
    body = _check_gamma_unwind_alerts_body()
    assert "_GAMMA_UNWIND_MAX_DAYS_TO_EXPIRY" in body


def test_requires_minimum_total_oi_floor_to_avoid_thin_chain_false_signal():
    body = _check_gamma_unwind_alerts_body()
    assert "_GAMMA_UNWIND_MIN_TOTAL_OI" in body


def test_requires_concentration_threshold_on_either_side():
    body = _check_gamma_unwind_alerts_body()
    assert "_GAMMA_UNWIND_MIN_OI_CONCENTRATION" in body
    assert 'dominant_side = "calls"' in body
    assert 'dominant_side = "puts"' in body


def test_has_a_per_symbol_rate_limit_sleep():
    body = _check_gamma_unwind_alerts_body()
    assert "time.sleep(" in body


def test_uses_a_redis_lock():
    body = _check_gamma_unwind_alerts_body()
    assert "_GAMMA_UNWIND_LOCK_KEY" in body
    assert "nx=True" in body


def test_dedup_is_per_symbol_and_expiry_not_just_symbol():
    """A stock legitimately has a NEW gamma-unwind setup for a different expiry later — dedup
    keyed on symbol alone would silently suppress that genuine second setup."""
    body = _check_gamma_unwind_alerts_body()
    assert 'f"{sym}:{c[\'expiry\']}"' in body or "dedup_field" in body


def test_job_is_registered_at_a_multi_hour_interval_not_every_minute():
    assert 'id="gamma_unwind_alert_check"' in _scheduler_source
    idx = _scheduler_source.index('id="gamma_unwind_alert_check"')
    preceding = _scheduler_source[max(0, idx - 300):idx]
    assert "hours=" in preceding
    assert "minutes=1" not in preceding
