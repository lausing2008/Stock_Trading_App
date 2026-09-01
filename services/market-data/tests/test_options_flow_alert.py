"""Tests for MPE-OPTIONS-FLOW-ALERT — real Unusual Whales unusual-options-activity alert
(check_options_flow_alerts()).

send_options_flow_alert_email() is pure string composition (no DB/network dependency), so it's
tested directly with real inputs. _options_flow_alert_direction() is a small, pure function —
extracted via source-text exec() and tested behaviorally with real values, matching
test_kscore_curve_params.py's/test_squeeze_family_recommendations_wiring.py's own established
technique for a pure helper embedded in a Docker-only-dependency module. check_options_flow_
alerts()/_record_options_flow_alert_outcome()/_build_options_flow_alert_calibration() themselves
can't be imported in this test environment — scheduler.py's import chain pulls in apscheduler
and other unstubbed modules — so their wiring is covered by source-text regression checks
instead, matching test_gamma_unwind_alert.py's/test_short_squeeze_alert.py's established pattern.
"""
import pathlib
from unittest.mock import patch

from src.services.email_service import send_options_flow_alert_email

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def _func_body(name: str) -> str:
    start = _scheduler_source.index(f"def {name}(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


def _extract_options_flow_alert_direction():
    """_options_flow_alert_direction() is pure and dependency-free — exec() it in isolation
    against the real source text, matching this repo's own established technique for a pure
    helper embedded in a module that can't be imported wholesale."""
    body = _func_body("_options_flow_alert_direction")
    namespace: dict = {}
    exec(body, namespace)
    return namespace["_options_flow_alert_direction"]


_options_flow_alert_direction = _extract_options_flow_alert_direction()


# ── _options_flow_alert_direction() — the real 4-way directional derivation ─────────────────

def test_call_ask_side_dominant_is_bullish():
    assert _options_flow_alert_direction("call", True) == "bullish"


def test_put_ask_side_dominant_is_bearish():
    assert _options_flow_alert_direction("put", True) == "bearish"


def test_put_bid_side_dominant_is_bullish():
    """Aggressive SELLING of puts — a bet the stock will NOT fall — the 'option sell' half of
    the user's own request, and genuinely bullish, not the naive 'put=bearish' shortcut."""
    assert _options_flow_alert_direction("put", False) == "bullish"


def test_call_bid_side_dominant_is_bearish():
    """Aggressive SELLING of calls — a bet the stock will NOT rise — covered-call/short-call
    positioning, genuinely bearish-leaning, not the naive 'call=bullish' shortcut."""
    assert _options_flow_alert_direction("call", False) == "bearish"


# ── send_options_flow_alert_email() — pure composition, tested directly ────────────────────

def test_single_bullish_candidate_renders_symbol_direction_and_strike():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "MSFT", "option_chain": "MSFT231222C00375000", "option_type": "call",
             "direction": "bullish", "strike": 375.0, "expiry": "2023-12-22",
             "price": 372.99, "total_premium": 186705.0, "ask_side_dominant": True,
             "volume_oi_ratio": 0.31, "has_sweep": True, "alert_rule": "RepeatedHits"},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "MSFT" in html and "BULLISH" in html and "CALL" in html
    assert "$375.00" in html and "2023-12-22" in html and "$186,705" in html
    assert "MSFT" in text


def test_subject_reports_the_real_bullish_bearish_split():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "call", "direction": "bullish",
             "strike": 100.0, "expiry": "2026-09-05", "price": 98.0, "total_premium": 60000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 1.5, "has_sweep": True, "alert_rule": "RepeatedHits"},
            {"symbol": "BBB", "option_chain": "BBB1", "option_type": "put", "direction": "bearish",
             "strike": 50.0, "expiry": "2026-09-05", "price": 52.0, "total_premium": 70000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 2.0, "has_sweep": True, "alert_rule": "RepeatedHits"},
        ])
    assert "1 bullish" in calls[0]["subject"]
    assert "1 bearish" in calls[0]["subject"]


def test_bullish_renders_green_bearish_renders_red():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "call", "direction": "bullish",
             "strike": 100.0, "expiry": "2026-09-05", "price": 98.0, "total_premium": 60000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 1.5, "has_sweep": True, "alert_rule": "RepeatedHits"},
            {"symbol": "BBB", "option_chain": "BBB1", "option_type": "put", "direction": "bearish",
             "strike": 50.0, "expiry": "2026-09-05", "price": 52.0, "total_premium": 70000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 2.0, "has_sweep": True, "alert_rule": "RepeatedHits"},
        ])
    html = calls[0]["html"]
    assert "#22c55e" in html  # bullish color
    assert "#ef4444" in html  # bearish color


def test_ask_side_dominant_renders_aggressive_buying_text():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "call", "direction": "bullish",
             "strike": 100.0, "expiry": "2026-09-05", "price": 98.0, "total_premium": 60000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 1.5, "has_sweep": True, "alert_rule": "RepeatedHits"},
        ])
    html = calls[0]["html"]
    assert "aggressive BUYING" in html
    assert "aggressive SELLING" not in html


def test_bid_side_dominant_renders_aggressive_selling_text():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "put", "direction": "bullish",
             "strike": 100.0, "expiry": "2026-09-05", "price": 98.0, "total_premium": 60000.0,
             "ask_side_dominant": False, "volume_oi_ratio": 1.5, "has_sweep": True, "alert_rule": "RepeatedHits"},
        ])
    html = calls[0]["html"]
    assert "aggressive SELLING" in html
    assert "aggressive BUYING" not in html


def test_sweep_flag_renders_when_true_and_omitted_when_false():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "call", "direction": "bullish",
             "strike": 100.0, "expiry": "2026-09-05", "price": 98.0, "total_premium": 60000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 1.5, "has_sweep": True, "alert_rule": "RepeatedHits"},
        ])
    assert "SWEEP" in calls[0]["html"]

    calls2, fake2 = _capture_send()
    with patch("src.services.email_service.send_email", fake2):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "call", "direction": "bullish",
             "strike": 100.0, "expiry": "2026-09-05", "price": 98.0, "total_premium": 60000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 1.5, "has_sweep": False, "alert_rule": "RepeatedHits"},
        ])
    assert "SWEEP" not in calls2[0]["html"]


def test_calibrated_win_rate_renders_when_present_and_honest_placeholder_when_absent():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "call", "direction": "bullish",
             "strike": 100.0, "expiry": "2026-09-05", "price": 98.0, "total_premium": 60000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 1.5, "has_sweep": True, "alert_rule": "RepeatedHits",
             "calibrated_win_rate": 0.62, "calibrated_win_rate_count": 45},
        ])
    html = calls[0]["html"]
    assert "62%" in html and "n=45" in html

    calls2, fake2 = _capture_send()
    with patch("src.services.email_service.send_email", fake2):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "call", "direction": "bullish",
             "strike": 100.0, "expiry": "2026-09-05", "price": 98.0, "total_premium": 60000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 1.5, "has_sweep": True, "alert_rule": "RepeatedHits"},
        ])
    assert "Not enough resolved history yet" in calls2[0]["html"]


def test_missing_strike_or_price_degrades_gracefully_not_crash():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        result = send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "call", "direction": "bullish",
             "strike": None, "expiry": "2026-09-05", "price": None, "total_premium": None,
             "ask_side_dominant": True, "volume_oi_ratio": None, "has_sweep": False, "alert_rule": None},
        ])
    assert result is True
    assert "—" in calls[0]["html"]


def test_body_frames_this_as_a_measured_fact_never_a_prediction():
    """Matches this app's own established alert-honesty discipline — never claims the stock
    will actually move."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_options_flow_alert_email("user@example.com", [
            {"symbol": "AAA", "option_chain": "AAA1", "option_type": "call", "direction": "bullish",
             "strike": 100.0, "expiry": "2026-09-05", "price": 98.0, "total_premium": 60000.0,
             "ask_side_dominant": True, "volume_oi_ratio": 1.5, "has_sweep": True, "alert_rule": "RepeatedHits"},
        ])
    html = calls[0]["html"].lower()
    assert "measured fact" in html
    # the real HTML template wraps this phrase across a line break ("not financial\n advice"),
    # so check for both words present rather than assuming exact substring adjacency.
    assert "not financial" in html and "advice" in html


# ── check_options_flow_alerts() — source-text regression checks ────────────────────────────

def test_gated_entirely_behind_unusual_whales_is_available():
    body = _func_body("check_options_flow_alerts")
    assert "_uw.is_available()" in body
    idx = body.index("_uw.is_available()")
    assert "return" in body[idx:idx + 60]


def test_uses_bounded_symbol_set_not_universe_wide():
    body = _func_body("check_options_flow_alerts")
    assert "_bounded_options_flow_symbols" in body


def test_calls_get_flow_alerts_with_the_real_named_threshold_constants():
    body = _func_body("check_options_flow_alerts")
    assert "_OPTIONS_FLOW_ALERT_MIN_PREMIUM" in body
    assert "_OPTIONS_FLOW_ALERT_MIN_VOLUME_OI_RATIO" in body
    assert "_OPTIONS_FLOW_ALERT_MAX_DTE" in body
    assert "is_sweep=True" in body


def test_keys_candidates_by_option_chain_not_symbol():
    """A single underlying can legitimately fire more than once the same day on two genuinely
    different contracts — this must never collapse to one candidate per symbol."""
    body = _func_body("check_options_flow_alerts")
    assert "candidates[row.option_chain] = " in body


def test_skips_a_row_with_no_real_ask_bid_premium_split():
    body = _func_body("check_options_flow_alerts")
    assert "ask == 0.0 and bid == 0.0" in body


def test_records_outcome_and_sends_email_only_for_real_candidates():
    body = _func_body("check_options_flow_alerts")
    assert "_record_options_flow_alert_outcome" in body
    assert "send_options_flow_alert_email" in body


def test_dedup_is_per_recipient_and_per_contract():
    body = _func_body("check_options_flow_alerts")
    assert "stockai:options_flow_alert_seen:" in body
    assert "current_chains - prev_seen" in body


def test_per_recipient_send_isolation():
    """One recipient's send exception must never abort the whole remaining recipient loop."""
    body = _func_body("check_options_flow_alerts")
    idx = body.index("for uid, user in recipients.items():")
    loop_body = body[idx:]
    assert "except Exception as _send_exc:" in loop_body


# ── _record_options_flow_alert_outcome() ────────────────────────────────────────────────────

def test_record_outcome_keys_on_option_chain_and_fired_date_not_symbol():
    body = _func_body("_record_options_flow_alert_outcome")
    assert "OptionsFlowAlertOutcome.option_chain == candidate[\"option_chain\"]" in body
    assert "OptionsFlowAlertOutcome.fired_date == today" in body


def test_record_outcome_fails_open_on_any_exception():
    body = _func_body("_record_options_flow_alert_outcome")
    assert "except Exception as exc:" in body
    assert "session.rollback()" in body


# ── _build_options_flow_alert_calibration() ─────────────────────────────────────────────────

def test_calibration_uses_the_shared_30_sample_floor():
    body = _func_body("_build_options_flow_alert_calibration")
    assert "_OPTIONS_FLOW_ALERT_CAL_MIN_COUNT" in body


def test_calibration_returns_none_not_a_fabricated_rate_below_the_floor():
    body = _func_body("_build_options_flow_alert_calibration")
    assert "return None" in body


# ── evaluate_options_flow_alert_outcomes() ──────────────────────────────────────────────────

def test_evaluator_reuses_the_shared_squeeze_outcome_lookup_price_helper():
    """Must reuse the SAME T+1-entry / bisect-nearest-bar helper every other outcome evaluator
    in this file uses — never a re-derived copy that could silently drift."""
    body = _func_body("evaluate_options_flow_alert_outcomes")
    assert "_squeeze_outcome_lookup_price(" in body
    assert "_SQUEEZE_OUTCOME_WINDOWS" in body
    assert "_SQUEEZE_OUTCOME_WIN_HURDLE_PCT" in body


def test_evaluator_scores_direction_from_the_rows_own_direction_column():
    """Unlike SqueezeAlertOutcome's fixed per-alert_type convention, this table holds BOTH
    directions — the evaluator must read direction PER ROW, not assume one fixed thesis."""
    body = _func_body("evaluate_options_flow_alert_outcomes")
    assert 'is_bearish_thesis = row.direction == "bearish"' in body


def test_evaluator_never_re_evaluates_an_already_closed_window():
    body = _func_body("evaluate_options_flow_alert_outcomes")
    assert "if getattr(row, price_field) is not None:" in body
    assert "continue" in body
