"""Tests for T257-VOLUME-ANOMALY-ALERT.

send_volume_anomaly_email() is pure string composition (no DB/network dependency), so it's
tested directly with real inputs. check_volume_anomalies() itself can't be imported in this
test environment — scheduler.py's import chain pulls in apscheduler and other unstubbed
modules (see test_price_alert_price_check.py's docstring for the same constraint) — so the
scan logic/job registration is covered by source-text regression checks instead, matching
test_scheduler_static_names.py's established pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_volume_anomaly_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


# ── send_volume_anomaly_email() — pure composition, tested directly ─────────────────────────

def test_single_alert_renders_symbol_rvol_and_direction():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_volume_anomaly_email("user@example.com", [
            {"symbol": "AAPL", "rvol": 3.2, "price": 150.25, "change_pct": 2.1, "level_note": None},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "AAPL" in html and "3.2x" in html and "$150.25" in html
    assert "+2.10%" in html
    assert "AAPL" in text and "3.2x" in text


def test_negative_change_pct_renders_red_and_no_plus_sign():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_volume_anomaly_email("user@example.com", [
            {"symbol": "XYZ", "rvol": 4.0, "price": 50.0, "change_pct": -3.5, "level_note": None},
        ])
    html = calls[0]["html"]
    assert "-3.50%" in html
    assert "+-3.50%" not in html  # must not double up the sign


def test_level_note_is_included_when_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_volume_anomaly_email("user@example.com", [
            {"symbol": "MSFT", "rvol": 2.8, "price": 400.0, "change_pct": 1.2,
             "level_note": "testing resistance at $405.00"},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "testing resistance at $405.00" in html
    assert "testing resistance at $405.00" in text


def test_missing_level_note_omits_the_line_without_crashing():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        ok = send_volume_anomaly_email("user@example.com", [
            {"symbol": "GLD", "rvol": 2.6, "price": 190.0, "change_pct": 0.5, "level_note": None},
        ])
    assert ok is True
    assert "None" not in calls[0]["html"]


def test_subject_reflects_count():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_volume_anomaly_email("user@example.com", [
            {"symbol": "A", "rvol": 3, "price": 1, "change_pct": 1, "level_note": None},
            {"symbol": "B", "rvol": 3, "price": 1, "change_pct": 1, "level_note": None},
        ])
    assert calls[0]["subject"] == "📊 Abnormal Volume — 2 stocks trading unusually heavy"


def test_measured_not_predicted_disclaimer_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_volume_anomaly_email("user@example.com", [
            {"symbol": "A", "rvol": 3, "price": 1, "change_pct": 1, "level_note": None},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "not a prediction" in html.lower()
    assert "not a prediction" in text.lower()


# ── check_volume_anomalies() — source-text regression checks ────────────────────────────────

def test_scan_reads_only_the_existing_redis_caches_never_yfinance_directly():
    """The entire design premise is that a universe-wide 1-min scan must read only Redis,
    never call yfinance/DB per-symbol — a regression here would silently reintroduce the
    exact rate-limiting risk this feature was designed to avoid."""
    start = _scheduler_source.index("def check_volume_anomalies(")
    end = _scheduler_source.index("\ndef ", start + 1)
    body = _scheduler_source[start:end]
    assert '"stockai:live_prices"' in body
    assert '"stockai:avg_volume"' in body
    assert "yf.download" not in body
    assert "import yfinance" not in body


def test_threshold_is_session_elapsed_scaled_not_a_flat_ratio():
    """Must reuse T241's session-elapsed scaling (a flat volume/avg_volume ratio over-
    triggers early in the trading session against a full-day average)."""
    start = _scheduler_source.index("def check_volume_anomalies(")
    end = _scheduler_source.index("\ndef ", start + 1)
    body = _scheduler_source[start:end]
    assert "_us_frac" in body and "_hk_frac" in body
    assert "_ABNORMAL_BASE" in body


def test_breakout_context_is_only_fetched_for_triggered_symbols_not_the_whole_universe():
    """The technical-analysis /levels HTTP call must happen AFTER the triggered list is
    built (few symbols/day), never inside the main universe-scanning loop (150 symbols)."""
    start = _scheduler_source.index("def check_volume_anomalies(")
    end = _scheduler_source.index("\ndef ", start + 1)
    body = _scheduler_source[start:end]
    triggered_build_idx = body.index("triggered.sort(")
    levels_call_idx = body.index("/ta/{quote(t['symbol'])}/levels")
    assert triggered_build_idx < levels_call_idx


def test_volume_anomaly_job_is_registered_every_minute():
    assert 'id="volume_anomaly_check"' in _scheduler_source
    start = _scheduler_source.index('id="volume_anomaly_check"')
    block = _scheduler_source[max(0, start - 200):start]
    assert "check_volume_anomalies" in block
    assert "minutes=1" in block


def test_daily_cap_and_dedup_keys_use_the_established_naming_convention():
    start = _scheduler_source.index("def check_volume_anomalies(")
    end = _scheduler_source.index("\ndef ", start + 1)
    body = _scheduler_source[start:end]
    assert "stockai:vol_anomaly_cap:" in body
    assert "stockai:vol_anomaly:" in body


# ── BUG-VOLANOM-STALEMARKET: market-hours gating ────────────────────────────────────────────
# Confirmed live in production 2026-07-20/21: GET /stocks/latest_prices' own cache-miss
# fallback (routes.py) re-fetches and re-writes stockai:live_prices on every cache-expiry
# request regardless of market hours, so a frontend page merely being open (no scheduled
# refresh job involved at all) kept the cache populated with yfinance's last COMPLETED daily
# bar for HK — closed at the time — stamped with a fresh 90s TTL every ~2 minutes. This job
# read that stale daily-bar volume as if it were live "this cycle" data and fired a false
# "abnormal volume" alert for an HK stock 80+ minutes before HK's actual 09:30 HKT open.

def _volume_anomaly_body() -> str:
    start = _scheduler_source.index("def check_volume_anomalies(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


def test_scan_checks_is_market_hours_for_both_markets_before_scanning():
    """Must reuse the already-established _is_market_hours() helper (same one
    _should_enter()'s fallback gate already uses, including HK's lunch-break/holiday
    handling) rather than trusting the cache blindly or hand-rolling a second, less complete
    market-hours check."""
    body = _volume_anomaly_body()
    assert "from .paper_trading_engine import _is_market_hours" in body
    assert '_is_market_hours("US")' in body
    assert '_is_market_hours("HK")' in body


def test_scan_short_circuits_entirely_when_both_markets_are_closed():
    """The whole scan must bail out early (before even computing thresholds) when neither
    market is open — not just filter individual rows, so a fully-closed dead window costs
    nothing beyond the two _is_market_hours() calls."""
    body = _volume_anomaly_body()
    gate_idx = body.index("if not _us_market_open and not _hk_market_open:")
    threshold_idx = body.index("_ABNORMAL_BASE = 2.5")
    assert gate_idx < threshold_idx, "the both-closed short-circuit must happen before threshold computation"


def test_scan_skips_hk_symbols_when_hk_market_is_closed_even_if_us_is_open():
    """HK can be closed while US is open (the exact confirmed production scenario) — the
    shared live_prices cache holds both markets' rows at once, so each row's OWN market must
    be checked individually, not just a single both-closed short-circuit."""
    body = _volume_anomaly_body()
    assert "if _is_hk_sym and not _hk_market_open:" in body
    assert "continue" in body[body.index("if _is_hk_sym and not _hk_market_open:"):][:80]


def test_scan_skips_us_symbols_when_us_market_is_closed_even_if_hk_is_open():
    """The mirror case — US symbols must be skipped when only HK is open."""
    body = _volume_anomaly_body()
    assert "if not _is_hk_sym and not _us_market_open:" in body
    assert "continue" in body[body.index("if not _is_hk_sym and not _us_market_open:"):][:80]
