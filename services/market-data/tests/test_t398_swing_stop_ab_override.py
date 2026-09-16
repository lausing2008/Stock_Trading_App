"""T398-SWING-STOPWIDTH-AB: _build_game_plan_for_style() accepts an optional per-call
stop_pct_override/atr_stop_mult_override so ONE portfolio can run a wider stop than the rest of
its style's fleet, without mutating the shared _STYLE_PARAMS dict every other portfolio of that
style also reads. Real closed SWING trades average a 5.31% stop against only a 4.26% favorable
excursion before reversal (52.5% stop-out rate) — this override is what lets a wider-stop
variant be tested as a genuinely separate, comparable portfolio rather than a fleet-wide change
with no control group left.

_scan_for_entries() threads these from `cfg.get("stop_pct_override"/"atr_stop_mult_override")`,
which resolve_entry_config() only ever populates from a real portfolio.config key — so a
portfolio that never sets these keys gets None, and behavior must be byte-identical to before
this change existed.
"""
from src.services.paper_trading_engine import _build_game_plan_for_style, _STYLE_PARAMS, _round_step


def test_no_override_matches_pre_existing_style_default():
    baseline = _build_game_plan_for_style(
        symbol="TEST", style="SWING", current_price=100.0, signal_reasons={}, atr=2.0,
    )
    explicit_none = _build_game_plan_for_style(
        symbol="TEST", style="SWING", current_price=100.0, signal_reasons={}, atr=2.0,
        stop_pct_override=None, atr_stop_mult_override=None,
    )
    assert baseline == explicit_none


def test_stop_pct_override_widens_the_floor_stop():
    step = _round_step(100.0)
    plan = _build_game_plan_for_style(
        symbol="TEST", style="SWING", current_price=100.0, signal_reasons={}, atr=None,
        stop_pct_override=0.925,
    )
    # No ATR/expected-move -> pure fixed-percentage floor, using the OVERRIDE not _STYLE_PARAMS.
    expected = round(100.0 * 0.925 / step) * step
    assert plan["stop"] == expected
    assert plan["stop"] != round(100.0 * _STYLE_PARAMS["SWING"]["stop_pct"] / step) * step


def test_atr_stop_mult_override_widens_the_atr_based_stop():
    step = _round_step(100.0)
    plan = _build_game_plan_for_style(
        symbol="TEST", style="SWING", current_price=100.0, signal_reasons={}, atr=2.0,
        atr_stop_mult_override=3.0,
    )
    expected_atr_stop = round((100.0 - 3.0 * 2.0) / step) * step
    expected_fixed_stop = round(100.0 * _STYLE_PARAMS["SWING"]["stop_pct"] / step) * step
    assert plan["stop"] == max(expected_atr_stop, expected_fixed_stop)


def test_override_never_mutates_the_shared_style_params_dict():
    before = dict(_STYLE_PARAMS["SWING"])
    _build_game_plan_for_style(
        symbol="TEST", style="SWING", current_price=100.0, signal_reasons={}, atr=2.0,
        stop_pct_override=0.80, atr_stop_mult_override=9.0,
    )
    assert _STYLE_PARAMS["SWING"] == before


def test_take_profit_is_unaffected_by_a_stop_override():
    # The A/B test isolates stop-width only — take-profit must stay on the style default.
    plan_default = _build_game_plan_for_style(
        symbol="TEST", style="SWING", current_price=100.0, signal_reasons={}, atr=None,
    )
    plan_override = _build_game_plan_for_style(
        symbol="TEST", style="SWING", current_price=100.0, signal_reasons={}, atr=None,
        stop_pct_override=0.925,
    )
    assert plan_default["take_profit"] == plan_override["take_profit"]
