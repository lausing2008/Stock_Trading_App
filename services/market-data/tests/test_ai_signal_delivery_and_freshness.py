"""AQ06/AQ07: generated signals, delivered alerts and freshness are separate facts."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ast
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services.ai_signal_alert_contract import (  # noqa: E402
    CANDIDATE, SUPPRESSED_BY_COOLDOWN, QUEUED_NOT_SENT, SENDER_ACCEPTED,
    CONTRACT_VERSION, MAX_SIGNAL_AGE, classify_selected_signal_freshness, merge,
)


NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)


def test_fresh_price_cannot_make_a_stale_selected_signal_current():
    result = classify_selected_signal_freshness(
        NOW - MAX_SIGNAL_AGE - timedelta(seconds=1), decided_at=NOW)
    assert result["eligible"] is False and result["state"] == "stale"


def test_unknown_invalid_and_future_signal_times_abstain():
    assert classify_selected_signal_freshness(None, decided_at=NOW)["state"] == "absent"
    assert classify_selected_signal_freshness("broken", decided_at=NOW)["state"] == "invalid"
    assert classify_selected_signal_freshness(
        NOW + timedelta(minutes=10), decided_at=NOW)["state"] == "future"


def test_naive_database_timestamp_is_explicitly_read_as_utc_and_can_be_fresh():
    result = classify_selected_signal_freshness(
        (NOW - timedelta(hours=2)).replace(tzinfo=None), decided_at=NOW)
    assert result["eligible"] is True and result["state"] == "fresh"
    assert result["age_seconds"] == 7200


def test_delivery_state_only_advances_and_provider_acceptance_is_the_strongest():
    state = merge(None, CANDIDATE)
    state = merge(state, SUPPRESSED_BY_COOLDOWN)
    state = merge(state, QUEUED_NOT_SENT)
    state = merge(state, SENDER_ACCEPTED)
    assert state == SENDER_ACCEPTED
    assert merge(state, CANDIDATE) == SENDER_ACCEPTED
    assert CONTRACT_VERSION


def _function(name: str) -> ast.FunctionDef:
    path = Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
    return next(n for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_real_selector_gates_on_the_selected_signal_clock_before_transition_logic():
    body = ast.unparse(_function("check_signal_alerts"))
    fresh = body.index("_classify_selected_signal_freshness")
    transition = body.index("if prev == current")
    assert fresh < transition
    assert "selected_signal_not_current" in body


def test_real_selector_persists_exact_signal_id_and_distinguishes_send_failure():
    body = ast.unparse(_function("check_signal_alerts"))
    # The per-row UPDATE itself lives in ai_signal_delivery_write (lock-ordered).
    assert "_persist_ai_delivery(session, Signal, _ai_delivery" in body
    assert "_AI_SENDER_ACCEPTED" in body
    assert "_AI_QUEUED_NOT_SENT" in body
    assert "version=_AI_ALERT_CONTRACT_VERSION" in body

