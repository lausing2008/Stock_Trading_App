"""Regression test for T247-SIGNALENGINE-INIT-GRADE.

_fetch_analyst_momentum()'s own docstring says "init counts as an upgrade if to_grade is
positive", but the original code unconditionally counted every "init"/"initiated" action as
an upgrade regardless of to_grade — a bearish coverage initiation (e.g. {action: "init",
to_grade: "Sell"}) was misclassified as bullish, inflating analyst_upgrades_7d and pushing a
borderline signal over the BUY threshold on the basis of what was actually bearish coverage.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

import src.generators.signals as signals_mod
from src.generators.signals import _fetch_analyst_momentum

# _fetch_analyst_momentum() only counts actions from the last 7 days
# (`a.get("date","") >= cutoff`, cutoff = today - 7d). These tests originally hardcoded
# "2026-07-10", which was recent when written but silently aged out of that window — every
# action was then filtered before classification, so the function returned (0, 0) for every
# case. That made the three "must NOT count" tests pass for entirely the wrong reason (date
# filtering, not the grade logic they exist to pin) and the rest fail outright. Always derive
# the date relative to today so the window stays valid.
_RECENT = (date.today() - timedelta(days=1)).isoformat()
_STALE = (date.today() - timedelta(days=30)).isoformat()


def _mock_response(actions):
    return MagicMock(status_code=200, json=lambda: {"analyst_actions": actions})


def _set_actions(monkeypatch, actions):
    client = MagicMock()
    client.__enter__.return_value.get.return_value = _mock_response(actions)
    monkeypatch.setattr(signals_mod.httpx, "Client", lambda **kw: client)


def test_bullish_init_counts_as_upgrade(monkeypatch):
    _set_actions(monkeypatch, [{"date": _RECENT, "action": "init", "to_grade": "Buy"}])
    ups, downs = _fetch_analyst_momentum("AAPL")
    assert ups == 1
    assert downs == 0


def test_bearish_init_does_not_count_as_upgrade(monkeypatch):
    """The exact bug scenario: {action: init, to_grade: Sell} must NOT be counted as an
    upgrade — the original code counted every init unconditionally."""
    _set_actions(monkeypatch, [{"date": _RECENT, "action": "init", "to_grade": "Sell"}])
    ups, downs = _fetch_analyst_momentum("AAPL")
    assert ups == 0
    assert downs == 0  # init is never counted as a downgrade either — just not an upgrade


def test_neutral_init_does_not_count_as_upgrade(monkeypatch):
    _set_actions(monkeypatch, [{"date": _RECENT, "action": "init", "to_grade": "Neutral"}])
    ups, _ = _fetch_analyst_momentum("AAPL")
    assert ups == 0


def test_explicit_up_action_still_counts_regardless_of_grade(monkeypatch):
    """A genuine "up" action must still count as an upgrade even without inspecting
    to_grade — this fix only changes the "init" path, not the pre-existing "up" behavior."""
    _set_actions(monkeypatch, [{"date": _RECENT, "action": "up", "to_grade": "Hold"}])
    ups, _ = _fetch_analyst_momentum("AAPL")
    assert ups == 1


def test_down_action_still_counts_as_downgrade(monkeypatch):
    _set_actions(monkeypatch, [{"date": _RECENT, "action": "down", "to_grade": "Hold"}])
    _, downs = _fetch_analyst_momentum("AAPL")
    assert downs == 1


def test_various_positive_grades_all_count_as_upgrade_on_init(monkeypatch):
    for grade in ("Buy", "Strong Buy", "Outperform", "Overweight", "BUY", "outperform"):
        _set_actions(monkeypatch, [{"date": _RECENT, "action": "init", "to_grade": grade}])
        ups, _ = _fetch_analyst_momentum("AAPL")
        assert ups == 1, f"expected grade={grade!r} to count as an upgrade on init"


def test_outside_7_day_window_excluded(monkeypatch):
    _set_actions(monkeypatch, [{"date": "2020-01-01", "action": "up", "to_grade": "Buy"}])
    ups, downs = _fetch_analyst_momentum("AAPL")
    assert ups == 0
    assert downs == 0


# ── the 7-day window itself ──────────────────────────────────────────────────
# Added after the whole file silently rotted: every test hardcoded a date that aged out of
# the lookback window, so the function returned (0,0) everywhere and the "must not count"
# assertions passed for the wrong reason. These pin the window behavior directly.

def test_action_older_than_7_days_is_excluded(monkeypatch):
    _set_actions(monkeypatch, [{"date": _STALE, "action": "up", "to_grade": "Buy"}])
    ups, downs = _fetch_analyst_momentum("AAPL")
    assert (ups, downs) == (0, 0)


def test_recent_and_stale_actions_are_separated(monkeypatch):
    """Only the in-window action counts — proves the filter discriminates rather than
    passing or dropping everything wholesale."""
    _set_actions(monkeypatch, [
        {"date": _RECENT, "action": "up", "to_grade": "Buy"},
        {"date": _STALE, "action": "up", "to_grade": "Buy"},
        {"date": _STALE, "action": "down", "to_grade": "Sell"},
    ])
    ups, downs = _fetch_analyst_momentum("AAPL")
    assert ups == 1
    assert downs == 0


def test_action_missing_a_date_is_excluded_not_crashed(monkeypatch):
    """`a.get("date", "")` yields "" which sorts below any real cutoff — must be dropped
    quietly, never raise."""
    _set_actions(monkeypatch, [{"action": "up", "to_grade": "Buy"}])
    assert _fetch_analyst_momentum("AAPL") == (0, 0)


def test_non_200_response_returns_zeros(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value.get.return_value = MagicMock(status_code=503)
    monkeypatch.setattr(signals_mod.httpx, "Client", lambda **kw: client)
    assert _fetch_analyst_momentum("AAPL") == (0, 0)


def test_transport_failure_fails_open_to_zeros(monkeypatch):
    """Documented contract: returns (0, 0) on any failure — analyst momentum is a bonus
    input, never a reason to abort signal generation."""
    def _boom(**kw):
        raise ConnectionError("market-data unreachable")
    monkeypatch.setattr(signals_mod.httpx, "Client", _boom)
    assert _fetch_analyst_momentum("AAPL") == (0, 0)
