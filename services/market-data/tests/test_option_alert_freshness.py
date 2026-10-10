import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.option_alert_freshness import (
    FLOW_EVENT_FUTURE_TOLERANCE,
    FLOW_EVENT_MAX_AGE,
    classify_flow_event_freshness,
    partition_fresh_flow_candidates,
)


NOW = datetime(2026, 10, 9, 18, tzinfo=timezone.utc)


def test_flow_event_age_boundaries_are_explicit():
    assert classify_flow_event_freshness(NOW - FLOW_EVENT_MAX_AGE, NOW)["state"] == "fresh"
    stale = classify_flow_event_freshness(NOW - FLOW_EVENT_MAX_AGE - timedelta(microseconds=1), NOW)
    assert stale["state"] == "stale" and stale["eligible"] is False
    tolerated = classify_flow_event_freshness(NOW + FLOW_EVENT_FUTURE_TOLERANCE, NOW)
    assert tolerated["state"] == "fresh" and tolerated["age_seconds"] == 0
    future = classify_flow_event_freshness(
        NOW + FLOW_EVENT_FUTURE_TOLERANCE + timedelta(microseconds=1), NOW)
    assert future["state"] == "future" and future["eligible"] is False


def test_unknown_timezone_abstains():
    result = classify_flow_event_freshness(NOW.replace(tzinfo=None), NOW)
    assert result == {"eligible": False, "state": "timezone_missing", "age_seconds": None}


def test_final_selection_rechecks_every_candidate_against_one_decision_clock():
    rows = {
        "fresh": {"event_at": NOW - timedelta(minutes=3)},
        "stale": {"event_at": NOW - timedelta(minutes=16)},
        "future": {"event_at": NOW + timedelta(minutes=1)},
    }
    eligible, excluded = partition_fresh_flow_candidates(rows, NOW)
    assert list(eligible) == ["fresh"]
    assert excluded == {"stale": 1, "future": 1, "timezone_missing": 0}


def test_real_flow_selector_calls_freshness_policy_before_candidate_assignment():
    source = Path(__file__).parents[1] / "src/services/scheduler.py"
    tree = ast.parse(source.read_text())
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "check_options_flow_alerts")
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)]
    called = {call.func.id for call in calls}
    assert {"classify_flow_event_freshness", "partition_fresh_flow_candidates"} <= called
