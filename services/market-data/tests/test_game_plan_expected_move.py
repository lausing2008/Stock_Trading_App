"""Regression tests for AUD-DECIDE4-EXPECTEDMOVE: paper_trading_engine.py's
_build_game_plan_for_style() gained optional session/stock_id params so it can read a real,
UW-derived expected_move_pct from yesterday's OptionsGamePlanSnapshot and use it in place of
the fixed-percentage take-profit / no-ATR-stop fallback — the fabricated "2.00:1 R:R" the
Domain 2 platform audit (2026-09-03) found was the dominant real decision-engine reject reason.

Per the user's explicit direction, a real expected move REPLACES the fixed take-profit target
outright (no max() against the fixed default) — unlike the stop side, where the fixed percentage
remains a safety floor via max(atr_or_expected_move_stop, fixed_pct_stop).
"""
from unittest.mock import patch

from src.services.paper_trading_engine import _build_game_plan_for_style, _STYLE_PARAMS, _round_step


class _FakeSnapshot:
    def __init__(self, expected_move_pct):
        self.expected_move_pct = expected_move_pct


def _patch_snapshot(monkeypatch_target, snapshot):
    return patch(
        "src.services.options_game_plan_snapshot.get_latest_options_game_plan",
        return_value=snapshot,
    )


def test_no_session_or_stock_id_is_backward_compatible_unchanged_behavior():
    """Every pre-existing call site (scheduler.py's _squeeze_game_plan, conditional_orders.py,
    options_game_plan_snapshot.py's own internal call) omits session/stock_id entirely — this
    must produce byte-identical output to before the change."""
    plan = _build_game_plan_for_style("TEST", "SWING", 100.0, {}, atr=None)
    step = _round_step(100.0)
    expected_stop = round(100.0 * _STYLE_PARAMS["SWING"]["stop_pct"] / step) * step
    expected_tp = round(100.0 * _STYLE_PARAMS["SWING"]["default_tp_pct"] / step) * step
    assert plan["stop"] == expected_stop
    assert plan["take_profit"] == expected_tp


def test_session_without_stock_id_does_not_attempt_a_snapshot_lookup():
    with _patch_snapshot(None, _FakeSnapshot(50.0)) as mock_lookup:
        plan = _build_game_plan_for_style("TEST", "SWING", 100.0, {}, atr=None, session=object())
    mock_lookup.assert_not_called()
    step = _round_step(100.0)
    assert plan["take_profit"] == round(100.0 * _STYLE_PARAMS["SWING"]["default_tp_pct"] / step) * step


def test_real_expected_move_replaces_the_fixed_take_profit_outright():
    with _patch_snapshot(None, _FakeSnapshot(8.0)):
        plan = _build_game_plan_for_style(
            "TEST", "SWING", 100.0, {}, atr=None, session=object(), stock_id=1,
        )
    step = _round_step(100.0)
    expected_tp = round(100.0 * (1 + 8.0 / 100) / step) * step
    fixed_tp = round(100.0 * _STYLE_PARAMS["SWING"]["default_tp_pct"] / step) * step
    assert plan["take_profit"] == expected_tp
    assert plan["take_profit"] != fixed_tp  # replaced, not maxed against the fixed default


def test_expected_move_used_for_stop_only_when_atr_is_unavailable():
    """ATR, when present, still wins for the stop side — expected_move_pct is only a fallback
    for the stop, never overriding a real ATR-based stop."""
    with _patch_snapshot(None, _FakeSnapshot(5.0)):
        plan_with_atr = _build_game_plan_for_style(
            "TEST", "SWING", 100.0, {}, atr=2.0, session=object(), stock_id=1,
        )
        plan_without_atr = _build_game_plan_for_style(
            "TEST", "SWING", 100.0, {}, atr=None, session=object(), stock_id=1,
        )
    step = _round_step(100.0)
    atr_stop = round((100.0 - 2.0 * _STYLE_PARAMS["SWING"]["atr_stop_mult"]) / step) * step
    fixed_stop = round(100.0 * _STYLE_PARAMS["SWING"]["stop_pct"] / step) * step
    assert plan_with_atr["stop"] == max(atr_stop, fixed_stop)

    expected_move_stop = round((100.0 * (1 - 5.0 / 100)) / step) * step
    assert plan_without_atr["stop"] == max(expected_move_stop, fixed_stop)


def test_fixed_percentage_floor_still_applies_to_expected_move_based_stop():
    """The fixed stop_pct remains a safety floor even when a real expected_move_pct is used in
    place of ATR — an unusually tight expected move must never produce a looser-than-normal
    stop relative to the fixed percentage default."""
    with _patch_snapshot(None, _FakeSnapshot(0.1)):
        plan = _build_game_plan_for_style(
            "TEST", "SWING", 100.0, {}, atr=None, session=object(), stock_id=1,
        )
    step = _round_step(100.0)
    fixed_stop = round(100.0 * _STYLE_PARAMS["SWING"]["stop_pct"] / step) * step
    expected_move_stop = round((100.0 * (1 - 0.1 / 100)) / step) * step
    assert plan["stop"] == max(expected_move_stop, fixed_stop)
    assert plan["stop"] >= fixed_stop  # the floor is never breached regardless of rounding


def test_no_snapshot_falls_back_to_fixed_percentage_take_profit_and_stop():
    with _patch_snapshot(None, None):
        plan = _build_game_plan_for_style(
            "TEST", "SWING", 100.0, {}, atr=None, session=object(), stock_id=1,
        )
    step = _round_step(100.0)
    assert plan["take_profit"] == round(100.0 * _STYLE_PARAMS["SWING"]["default_tp_pct"] / step) * step
    assert plan["stop"] == round(100.0 * _STYLE_PARAMS["SWING"]["stop_pct"] / step) * step


def test_snapshot_with_none_expected_move_pct_falls_back_to_fixed():
    with _patch_snapshot(None, _FakeSnapshot(None)):
        plan = _build_game_plan_for_style(
            "TEST", "SWING", 100.0, {}, atr=None, session=object(), stock_id=1,
        )
    step = _round_step(100.0)
    assert plan["take_profit"] == round(100.0 * _STYLE_PARAMS["SWING"]["default_tp_pct"] / step) * step


def test_snapshot_lookup_failure_fails_open_to_fixed_percentage():
    with patch(
        "src.services.options_game_plan_snapshot.get_latest_options_game_plan",
        side_effect=RuntimeError("boom"),
    ):
        plan = _build_game_plan_for_style(
            "TEST", "SWING", 100.0, {}, atr=None, session=object(), stock_id=1,
        )
    step = _round_step(100.0)
    assert plan["take_profit"] == round(100.0 * _STYLE_PARAMS["SWING"]["default_tp_pct"] / step) * step
