"""AUD-E04-FLOWHOURSGATE / AUD-E04-RIGHTNOWCLAIM — check_options_flow_alerts() had two related
defects, both about the email describing events that had already happened as if they were
current:

1. The market-hours gate was `if not _is_market_hours("US") and not _is_market_hours("HK"):
   return` — i.e. skip only when BOTH markets are closed. This alert's own candidate universe
   is US-only (_bounded_options_flow_symbols()), so the job kept running through the entire US
   overnight session whenever HK happened to be open (which is most weeknight hours, since HK's
   session sits almost exactly inside US's closed hours). Measured live: 97 of 247 candidate
   rows dated 2026-09-05 onward were recorded outside 09:30-16:15 New York time.

2. The email's header claimed "detected right now" for every candidate regardless of age —
   get_flow_alerts()'s own 48h lookback window keeps the same UW row eligible for up to two
   days, and the adapter's FlowAlert.created_at was never even copied into the candidate dict,
   so there was no way to tell an old event from a fresh one.

scheduler.py can't be imported directly in this test environment — source-scanned, matching
this repo's established convention for this file. The per-row age rendering in
send_options_flow_alert_email() IS directly importable and is tested behaviorally.
"""
import pathlib
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.services.email_service import send_options_flow_alert_email

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
).read_text()


def _check_options_flow_alerts_body() -> str:
    start = _SOURCE.index("def check_options_flow_alerts() -> None:")
    end = _SOURCE.index("\n\ndef ", start + 10)
    return _SOURCE[start:end]


_BODY = _check_options_flow_alerts_body()


# ── AUD-E04-FLOWHOURSGATE ────────────────────────────────────────────────────────────────────

def test_market_hours_gate_checks_us_only_not_both_markets():
    """The exact bug: gating on 'both closed' let a US-only alert keep firing all night
    whenever HK happened to be open."""
    assert 'if not _is_market_hours("US"):' in _BODY


def test_market_hours_gate_no_longer_requires_hk_to_also_be_closed():
    assert 'not _is_market_hours("US") and not _is_market_hours("HK")' not in _BODY


def test_market_hours_check_still_fails_open_on_a_lookup_error():
    """A market-calendar lookup failure must not silently disable a real alert — matches this
    module's own established fail-open convention, unchanged by this fix."""
    gate_idx = _BODY.index('if not _is_market_hours("US"):')
    surrounding = _BODY[max(0, gate_idx - 400):gate_idx + 600]
    assert "except Exception as _mh_exc:" in surrounding
    assert "market_hours_check_failed" in surrounding


# ── AUD-E04-RIGHTNOWCLAIM: created_at is now captured ───────────────────────────────────────

def test_created_at_is_captured_in_the_candidate_dict():
    assert '"created_at": row.created_at,' in _BODY


# ── AUD-E04-RIGHTNOWCLAIM: the email renders real per-row age ───────────────────────────────

def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _candidate(**overrides):
    base = dict(
        symbol="AAPL", option_chain="AAPL1", option_type="call", direction="bullish",
        strike=100.0, expiry="2099-09-05", price=98.0, total_premium=60000.0,
        ask_side_dominant=True, volume_oi_ratio=1.5, has_sweep=False, alert_rule="RepeatedHits",
    )
    base.update(overrides)
    return base


def test_header_no_longer_claims_every_candidate_was_detected_right_now():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [_candidate()])
    assert "detected right now" not in calls[0]["html"]


def test_a_fresh_event_shows_a_recent_age():
    fresh = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [_candidate(created_at=fresh)])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "detected 2m ago" in html
    assert "detected 2m ago" in text


def test_an_old_event_from_the_48h_lookback_window_shows_its_real_age_not_right_now():
    """The exact reported defect: an event sitting in the 48h lookback window must not be
    described the same way as one detected seconds ago."""
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [_candidate(created_at=old)])
    html = calls[0]["html"]
    assert "detected 1.3d ago" in html


def test_missing_created_at_renders_no_fabricated_age():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [_candidate(created_at=None)])
    assert "ago" not in calls[0]["html"]


def test_unparseable_created_at_fails_safe_not_a_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        ok = send_options_flow_alert_email("user@example.com", [_candidate(created_at="not-a-timestamp")])
    assert ok is True
    assert "ago" not in calls[0]["html"]
