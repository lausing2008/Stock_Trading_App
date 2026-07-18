"""Tests for T257-TOP3-CONVICTION-ALERT.

send_top3_conviction_email() is pure string composition, tested directly with real inputs.
check_top3_conviction() itself can't be imported in this test environment (scheduler.py's
import chain pulls in apscheduler and other unstubbed modules — see
test_price_alert_price_check.py's docstring for the same constraint), so the scan/gating
logic is covered by source-text regression checks, matching test_scheduler_static_names.py's
established pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_top3_conviction_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


# ── send_top3_conviction_email() — pure composition, tested directly ────────────────────────

def test_single_pick_renders_symbol_direction_and_measured_win_rate():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_top3_conviction_email("user@example.com", [
            {"symbol": "AAPL", "horizon": "SWING", "direction": "BUY", "confidence": 78.0, "win_rate": 0.72, "count": 41},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "AAPL" in html and "BUY" in html and "SWING" in html
    assert "72% measured win rate" in html
    assert "n=41" in html
    assert "AAPL" in text and "72%" in text and "41" in text


def test_sell_pick_uses_red_and_correct_direction_label():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_top3_conviction_email("user@example.com", [
            {"symbol": "XYZ", "horizon": "SHORT", "direction": "SELL", "confidence": 82.0, "win_rate": 0.75, "count": 35},
        ])
    html = calls[0]["html"]
    assert "SELL" in html
    assert "#ef4444" in html  # red for SELL


def test_subject_reflects_count_and_win_rate_bar():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_top3_conviction_email("user@example.com", [
            {"symbol": "A", "horizon": "SWING", "direction": "BUY", "confidence": 80, "win_rate": 0.71, "count": 30},
            {"symbol": "B", "horizon": "SWING", "direction": "BUY", "confidence": 80, "win_rate": 0.71, "count": 30},
        ])
    assert calls[0]["subject"] == "🎯 Top 2 High-Conviction Picks — measured win rate ≥70%"


def test_measured_win_rate_framing_not_raw_confidence_disclaimer_present():
    """The whole point of this feature is gating on MEASURED win rate, not raw model
    confidence — the email must say so explicitly, not just print a number."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_top3_conviction_email("user@example.com", [
            {"symbol": "A", "horizon": "SWING", "direction": "BUY", "confidence": 80, "win_rate": 0.71, "count": 30},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "not raw model confidence" in html.lower() or "not raw confidence" in html.lower()
    assert "not a prediction" in html.lower()
    assert "not a prediction" in text.lower()


def test_empty_qualifying_set_is_never_passed_to_this_builder():
    """This builder is only ever called with a non-empty top3 list (the caller returns early
    on zero qualifiers) — documenting that contract here rather than defensively handling an
    empty list, since a zero-pick day should produce NO email, not an empty-picks email."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        ok = send_top3_conviction_email("user@example.com", [])
    assert ok is True  # doesn't crash, but callers must not invoke this with []
    assert calls[0]["subject"] == "🎯 Top 0 High-Conviction Picks — measured win rate ≥70%"


# ── check_top3_conviction() — source-text regression checks ─────────────────────────────────

def _job_body() -> str:
    start = _scheduler_source.index("def check_top3_conviction(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


def test_gate_gates_on_measured_calibration_bucket_not_raw_confidence():
    """The core design constraint: qualification must check the calibration bucket's
    win_rate/count, not just the signal's own confidence field."""
    body = _job_body()
    assert "cal_buckets.get(" in body
    assert 'entry.get("count", 0) < _TOP3_MIN_COUNT' in body
    assert 'win_rate < min_win_rate' in body


def test_zero_calibration_data_produces_zero_picks_not_a_fallback_to_raw_confidence():
    """If signal-engine's calibration cache is empty, the scan must return early with NO
    picks — it must NEVER fall back to raw confidence, which would silently defeat the
    entire honest-accuracy-gate design."""
    body = _job_body()
    assert "if not cal_buckets:" in body
    idx = body.index("if not cal_buckets:")
    following = body[idx:idx + 400]
    assert "return" in following


def test_min_win_rate_threshold_is_redis_tunable():
    body = _job_body()
    assert "stockai:top3_min_win_rate" in body


def test_only_emails_when_qualifying_composition_changes():
    """Must not re-email an unchanged qualifying set every minute — signals only regenerate
    on the 5-10 min refresh cycle, so re-scanning identical data every 60s would otherwise
    spam the same picks repeatedly."""
    body = _job_body()
    assert "stockai:top3_last_composition" in body
    assert "composition_key == prev_key" in body


def test_bear_or_risk_off_regime_blocks_buy_qualification():
    """A BUY pick in a bear/risk_off regime must never qualify, regardless of its measured
    win rate — matching this repo's existing conviction-gate regime discipline."""
    body = _job_body()
    assert 'regime in ("bear", "risk_off")' in body


def test_kscore_floor_applies_to_buy_candidates():
    body = _job_body()
    assert "_TOP3_MIN_KSCORE" in body
    assert "kscore < _TOP3_MIN_KSCORE" in body


def test_ranked_by_win_rate_not_confidence():
    body = _job_body()
    sort_idx = body.index("qualifying.sort(")
    sort_line = body[sort_idx:body.index("\n", sort_idx)]
    assert '"win_rate"' in sort_line
    win_rate_pos = sort_line.index('"win_rate"')
    confidence_pos = sort_line.index('"confidence"')
    assert win_rate_pos < confidence_pos, "win_rate must be the primary sort key, confidence only a tiebreak"


def test_capped_at_exactly_three():
    body = _job_body()
    assert "qualifying[:3]" in body


def test_job_is_registered_every_minute():
    assert 'id="top3_conviction_check"' in _scheduler_source
    start = _scheduler_source.index('id="top3_conviction_check"')
    block = _scheduler_source[max(0, start - 200):start]
    assert "check_top3_conviction" in block
    assert "minutes=1" in block


def test_regime_lookup_uses_same_process_function_not_an_http_call():
    """scheduler.py already imports get_last_regime() at module level and runs inside the
    same process as market-data — must call it directly (Redis-cached, no round-trip), not
    make a fragile HTTP call back to its own service."""
    body = _job_body()
    assert "get_last_regime()" in body
    assert "get_last_hk_regime()" in body
    assert "/stocks/regime" not in body
