"""Tests for AUD-EARNINGS-DIGEST: send_earnings_reminder_digest_email() — consolidates every
upcoming earnings print for a user into ONE table email, instead of check_signal_alerts()'s
previous one-send_email()-call-per-(user, symbol) pattern (the inbox flood a user reported
seeing: 8 separate "Earnings in Xd: SYMBOL" emails in a row).

Pure string composition (no DB/network dependency), tested directly with real inputs, matching
send_volume_anomaly_email()/send_top3_conviction_email()'s established convention.
"""
from unittest.mock import patch

from src.services.email_service import send_earnings_reminder_digest_email


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _row(symbol="AAPL", days_to_earnings=1, price=190.5, change_pct=1.2,
         forward_eps=1.5, eps_beat_rate=0.875, eps_avg_surprise_pct=9.2, kscore=72.0):
    return {
        "symbol": symbol, "days_to_earnings": days_to_earnings, "price": price,
        "change_pct": change_pct, "forward_eps": forward_eps, "eps_beat_rate": eps_beat_rate,
        "eps_avg_surprise_pct": eps_avg_surprise_pct, "kscore": kscore,
    }


# ── Consolidation — the core behavior this fix delivers ─────────────────────────────────────

def test_multiple_symbols_land_in_one_email_not_separate_sends():
    calls, fake = _capture_send()
    rows = [_row("AAPL", 1), _row("GOOG", 1), _row("VZ", 2), _row("INTC", 3)]
    with patch("src.services.email_service.send_email", fake):
        ok = send_earnings_reminder_digest_email("user@example.com", rows)
    assert ok is True
    assert len(calls) == 1  # ONE email, not 4
    for sym in ("AAPL", "GOOG", "VZ", "INTC"):
        assert sym in calls[0]["html"]
        assert sym in calls[0]["text"]


def test_subject_reflects_stock_count():
    calls, fake = _capture_send()
    rows = [_row("AAPL", 1), _row("GOOG", 2), _row("VZ", 3)]
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", rows)
    assert "3 stocks" in calls[0]["subject"]


def test_singular_subject_for_one_stock():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", [_row("AAPL", 1)])
    assert "1 stock " in calls[0]["subject"] or "1 stock" in calls[0]["subject"]
    assert "1 stocks" not in calls[0]["subject"]


def test_rows_sorted_by_soonest_earnings_first():
    calls, fake = _capture_send()
    # Deliberately out of order — furthest-out first.
    rows = [_row("SLOW", 5), _row("FAST", 1), _row("MID", 3)]
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", rows)
    html = calls[0]["html"]
    assert html.index("FAST") < html.index("MID") < html.index("SLOW")


# ── Table content — price, EPS estimate, beat rate, K-Score ──────────────────────────────────

def test_price_and_change_pct_rendered():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", [_row(price=190.50, change_pct=1.2)])
    assert "190.50" in calls[0]["html"]
    assert "+1.2%" in calls[0]["html"]


def test_negative_change_pct_rendered_with_minus_sign():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", [_row(change_pct=-2.5)])
    assert "-2.5%" in calls[0]["html"]
    assert "--2.5%" not in calls[0]["html"]


def test_missing_price_shows_placeholder_not_crash():
    calls, fake = _capture_send()
    row = _row()
    row["price"] = None
    row["change_pct"] = None
    with patch("src.services.email_service.send_email", fake):
        ok = send_earnings_reminder_digest_email("user@example.com", [row])
    assert ok is True
    assert "—" in calls[0]["html"]


def test_beat_rate_formatted_as_fraction_with_avg_surprise():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", [_row(eps_beat_rate=0.875, eps_avg_surprise_pct=9.2)])
    assert "7/8" in calls[0]["html"]
    assert "+9.2%" in calls[0]["html"]


def test_missing_beat_rate_shows_placeholder():
    calls, fake = _capture_send()
    row = _row()
    row["eps_beat_rate"] = None
    row["eps_avg_surprise_pct"] = None
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", [row])
    assert "—" in calls[0]["html"]


def test_kscore_rendered_when_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", [_row(kscore=72.4)])
    assert "72" in calls[0]["html"]


def test_missing_kscore_shows_placeholder_not_crash():
    calls, fake = _capture_send()
    row = _row()
    row["kscore"] = None
    with patch("src.services.email_service.send_email", fake):
        ok = send_earnings_reminder_digest_email("user@example.com", [row])
    assert ok is True


def test_days_to_earnings_rendered_per_row():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", [_row("AAPL", 3)])
    assert "3d" in calls[0]["html"]


def test_forward_eps_rendered_with_dollar_sign():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_earnings_reminder_digest_email("user@example.com", [_row(forward_eps=1.50)])
    assert "$1.50" in calls[0]["html"]


def test_returns_false_when_send_email_fails():
    with patch("src.services.email_service.send_email", return_value=False):
        ok = send_earnings_reminder_digest_email("user@example.com", [_row()])
    assert ok is False
