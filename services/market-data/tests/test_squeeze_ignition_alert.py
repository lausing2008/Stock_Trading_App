"""Tests for T260-SQUEEZE-IGNITION — the early-warning tier BELOW check_short_squeeze_alerts()'s
own 3% intraday-move floor. Direct user report: TMDX fired the classic squeeze alert ~45
minutes into a real move that started quietly on modest volume; check_prebreakout_alerts()
never flagged it (TMDX was never actually compressing beforehand) and check_volume_anomalies()
never flagged it either (its general RVOL scan has no concept of short interest, so it
under-triggers on a "quietly building" move). This alert closes exactly that gap.

send_squeeze_ignition_email() is pure string composition (no DB/network dependency), so it's
tested directly with real inputs. check_squeeze_ignition_alerts() itself can't be imported in
this test environment — matches test_short_squeeze_alert.py's own documented constraint — so
the scan logic/job registration is covered by source-text regression checks instead.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_squeeze_ignition_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _check_squeeze_ignition_alerts_body() -> str:
    start = _scheduler_source.index("def check_squeeze_ignition_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


# ── send_squeeze_ignition_email() — pure composition, tested directly ──────────────────────

def test_single_candidate_renders_symbol_short_pct_change_and_rvol():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_ignition_email("user@example.com", [
            {"symbol": "TMDX", "short_percent_of_float": 34.5, "change_pct": 1.8, "rvol": 2.1, "price": 89.76},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "TMDX" in html and "34.5%" in html and "+1.80%" in html and "2.1x avg volume" in html and "$89.76" in html
    assert "TMDX" in text and "34.5%" in text and "2.1x avg volume" in text


def test_subject_is_a_watch_not_a_buy_signal():
    """Deliberately softer framing than the classic alert — this is a lower-confidence,
    earlier-stage read, not a firm BUY signal."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_ignition_email("user@example.com", [
            {"symbol": "TMDX", "short_percent_of_float": 34.5, "change_pct": 1.8, "rvol": 2.1, "price": 89.76},
        ])
    assert "BUY signal" not in calls[0]["subject"]
    assert "Watch" in calls[0]["subject"]


def test_body_states_this_is_an_early_stage_lower_confidence_read():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_ignition_email("user@example.com", [
            {"symbol": "TMDX", "short_percent_of_float": 34.5, "change_pct": 1.8, "rvol": 2.1, "price": 89.76},
        ])
    html = calls[0]["html"].lower()
    assert "earlier" in html or "early" in html
    assert "fade back" in html or "lower-confidence" in html.replace(" ", "-") or "lower confidence" in html


def test_multiple_candidates_all_rendered():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_ignition_email("user@example.com", [
            {"symbol": "TMDX", "short_percent_of_float": 34.5, "change_pct": 1.8, "rvol": 2.1, "price": 89.76},
            {"symbol": "SOUN", "short_percent_of_float": 20.0, "change_pct": 1.2, "rvol": 1.9, "price": 10.50},
        ])
    html = calls[0]["html"]
    assert "TMDX" in html and "SOUN" in html


def test_missing_change_pct_rvol_or_price_degrades_gracefully_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_squeeze_ignition_email("user@example.com", [
            {"symbol": "XYZ", "short_percent_of_float": 20.0, "change_pct": None, "rvol": None, "price": None},
        ])
    assert result is True
    assert "—" in calls[0]["html"]


def test_short_interest_date_rendered_when_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_ignition_email("user@example.com", [
            {"symbol": "TMDX", "short_percent_of_float": 34.5, "change_pct": 1.8, "rvol": 2.1, "price": 89.76,
             "short_interest_date": "2026-07-31"},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "2026-07-31" in html
    assert "2026-07-31" in text


def test_game_plan_rendered_when_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_ignition_email("user@example.com", [
            {"symbol": "TMDX", "short_percent_of_float": 34.5, "change_pct": 1.8, "rvol": 2.1, "price": 89.76,
             "game_plan": {"entry1": 89.76, "stop": 85.0, "take_profit": 98.0}},
        ])
    html = calls[0]["html"]
    assert "$89.76" in html and "$85.00" in html and "$98.00" in html


def test_calibrated_win_rate_rendered_when_present_and_placeholder_when_absent():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_ignition_email("user@example.com", [
            {"symbol": "A", "short_percent_of_float": 34.5, "change_pct": 1.8, "rvol": 2.1, "price": 10.0,
             "calibrated_win_rate": 0.62, "calibrated_win_rate_count": 41},
            {"symbol": "B", "short_percent_of_float": 20.0, "change_pct": 1.2, "rvol": 1.9, "price": 20.0},
        ])
    html = calls[0]["html"]
    assert "62%" in html and "n=41" in html
    assert "Not enough resolved history" in html


def test_no_critical_days_to_cover_escalation_this_alert_has_no_critical_tier():
    """Unlike the classic alert, this early-stage tier has no CRITICAL days-to-cover
    escalation — confirms the subject/body never claims one."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_ignition_email("user@example.com", [
            {"symbol": "TMDX", "short_percent_of_float": 34.5, "change_pct": 1.8, "rvol": 2.1, "price": 89.76,
             "short_ratio": 1.2},
        ])
    assert "CRITICAL" not in calls[0]["subject"]


def test_short_ratio_rendered_as_days_to_cover_when_present():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_squeeze_ignition_email("user@example.com", [
            {"symbol": "TMDX", "short_percent_of_float": 34.5, "change_pct": 1.8, "rvol": 2.1, "price": 89.76,
             "short_ratio": 3.4},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "3.4d to cover" in html
    assert "3.4d to cover" in text


# ── check_squeeze_ignition_alerts() — source-text regression checks ────────────────────────

def test_uses_stockai_live_prices_and_avg_volume_not_yfinance_in_the_scan_loop():
    body = _check_squeeze_ignition_alerts_body()
    assert '"stockai:live_prices"' in body
    assert '"stockai:avg_volume"' in body
    assert "import yfinance" not in body


def test_uses_is_market_hours_helper():
    body = _check_squeeze_ignition_alerts_body()
    assert "_is_market_hours" in body


def test_uses_its_own_dedicated_redis_lock_distinct_from_the_classic_alert():
    body = _check_squeeze_ignition_alerts_body()
    assert "_SQUEEZE_IGNITION_LOCK_KEY" in body
    assert "check_squeeze_ignition_alerts" in _scheduler_source.split("_SQUEEZE_IGNITION_LOCK_KEY = ")[1][:80]
    assert 'nx=True' in body


def test_move_band_is_strictly_below_the_classic_alerts_own_floor():
    """The whole point of this tier is to catch candidates BELOW the classic alert's 3.0%
    floor — confirm the upper bound is that exact constant (not a second, independently
    duplicated 3.0 literal that could silently drift from it), and the lower bound is a real,
    positive floor (not 0%, which would just be "any green stock")."""
    body = _check_squeeze_ignition_alerts_body()
    assert "_SQUEEZE_IGNITION_MIN_MOVE_PCT" in body
    assert "_SQUEEZE_IGNITION_MAX_MOVE_PCT" in body
    assert "_SQUEEZE_IGNITION_MIN_MOVE_PCT <= change_pct < _SQUEEZE_IGNITION_MAX_MOVE_PCT" in body
    # The max-move constant must be defined as a reference to the classic alert's own floor,
    # not a second hardcoded 3.0 literal.
    max_const_idx = _scheduler_source.index("_SQUEEZE_IGNITION_MAX_MOVE_PCT = ")
    line_end = _scheduler_source.index("\n", max_const_idx)
    assert "_SQUEEZE_MIN_INTRADAY_MOVE_PCT" in _scheduler_source[max_const_idx:line_end]


def test_requires_short_float_threshold_same_as_classic_alert():
    """Reuses the SAME short-float floor as check_short_squeeze_alerts() — not a second,
    independently-tuned bar for this tier."""
    body = _check_squeeze_ignition_alerts_body()
    assert "_SQUEEZE_MIN_SHORT_FLOAT" in body


def test_requires_an_rvol_floor_using_session_elapsed_scaling():
    """Must reuse the SAME shared _session_elapsed_rvol_thresholds() helper check_volume_
    anomalies()/check_short_squeeze_alerts() also call (T241-AUDIT-RVOL-INTRADAY-BIAS,
    extracted AUD288-SQUEEZE-NO-VOLUME-CONFIRM) — not a naive flat threshold that would
    over-trigger early in the session relative to a full-day average, and not a 4th
    independently-duplicated copy of the same session-elapsed math."""
    body = _check_squeeze_ignition_alerts_body()
    assert "_SQUEEZE_IGNITION_RVOL_BASE" in body
    assert "_session_elapsed_rvol_thresholds(" in body
    assert "rvol < rvol_threshold" in body


def test_stale_short_interest_is_rejected_same_discipline_as_classic_alert():
    body = _check_squeeze_ignition_alerts_body()
    assert "_sqi_stale_cutoff_str" in body
    assert '_si_date is None or _si_date < _sqi_stale_cutoff_str' in body


def test_fires_only_on_state_transition_via_its_own_dedicated_redis_set():
    """Must use its OWN dedup set (stockai:squeeze_ignition_active:{uid}), SEPARATE from the
    classic alert's stockai:squeeze_active:{uid} — a symbol progressing from ignition to
    classic must fire BOTH alerts once each, not have the classic alert silently suppressed
    because the symbol already looks "active" under the ignition alert's own bookkeeping.

    The function's own docstring legitimately mentions the classic alert's key name in prose
    (explaining why the two are kept separate) — the real check is on the EXECUTABLE code
    only, sliced past the closing docstring delimiter, matching this repo's own established
    fix for this exact false-positive class (see e.g. test_premarket_gappers.py's docstring
    on the same trap)."""
    body = _check_squeeze_ignition_alerts_body()
    assert "prev_active" in body
    assert "newly_qualifying" in body
    assert "current_active - prev_active" in body
    code_only = body.split('"""', 2)[2]
    assert "stockai:squeeze_ignition_active:" in code_only
    assert "stockai:squeeze_active:" not in code_only


def test_uses_the_squeeze_ignition_alert_type_for_outcome_tracking():
    """Must record outcomes under its OWN alert_type ("squeeze_ignition"), not silently reuse
    "short_squeeze" — the two are genuinely different moments (see SqueezeAlertOutcome's own
    docstring for why mixing them would conflate different questions)."""
    body = _check_squeeze_ignition_alerts_body()
    assert '"squeeze_ignition"' in body
    assert '_record_squeeze_alert_outcome(\n                    session, "squeeze_ignition"' in body


def test_reuses_the_game_plan_and_calibration_helpers_not_a_reimplementation():
    body = _check_squeeze_ignition_alerts_body()
    assert "_squeeze_game_plan(" in body
    assert "_squeeze_family_calibration_for_alert_type(" in body


def test_job_is_registered_at_one_minute_interval():
    assert 'id="squeeze_ignition_alert_check"' in _scheduler_source
    idx = _scheduler_source.index('id="squeeze_ignition_alert_check"')
    preceding = _scheduler_source[max(0, idx - 300):idx]
    assert "minutes=1" in preceding


def test_fundamentals_cache_misses_are_counted_with_their_own_dedicated_counter():
    body = _check_squeeze_ignition_alerts_body()
    assert "_fundamentals_cache_misses += 1" in body
    assert "_incr_rolling_counter(_SQUEEZE_IGNITION_FUND_CACHE_MISS_COUNTER_KEY)" in body
