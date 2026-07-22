"""Regression test for AUD-DUPLOGIC: paper_trading_engine.py's _build_game_plan_for_style()
used to hardcode its own ATR-stop-multiplier literal (3.0 for GROWTH, 2.0 otherwise) instead of
reading it from _STYLE_PARAMS — the single canonical source of truth for every other per-style
game-plan value (entry/breakout/stop/target percentages). decision-engine's own independent
copy of this exact multiplier said 2.5 for GROWTH, an undocumented drift between the two
systems now closed by making atr_stop_mult a real _STYLE_PARAMS field both sides read.

This confirms the WRITE side (market-data, the real/authoritative game-plan builder used for
actual paper trades) reads the field correctly rather than reverting to its own hardcoded
literal.
"""
from src.services.paper_trading_engine import _build_game_plan_for_style, _STYLE_PARAMS


def test_style_params_growth_atr_mult_is_3x():
    assert _STYLE_PARAMS["GROWTH"]["atr_stop_mult"] == 3.0


def test_style_params_non_growth_styles_are_2x():
    for style in ("SHORT", "SWING", "LONG"):
        assert _STYLE_PARAMS[style]["atr_stop_mult"] == 2.0


def test_build_game_plan_growth_uses_3x_atr_mult():
    plan = _build_game_plan_for_style(
        symbol="TEST", style="GROWTH", current_price=100.0,
        signal_reasons={}, atr=2.0,
    )
    # atr_stop = round((100 - 3.0*2.0) / step) * step = round(94.0 / step) * step
    # fixed_stop = round((100 * 0.880) / step) * step = round(88.0 / step) * step
    # stop = max(atr_stop, fixed_stop) -> atr_stop wins here (94 > 88)
    from src.services.paper_trading_engine import _round_step
    step = _round_step(100.0)
    expected_atr_stop = round((100.0 - 3.0 * 2.0) / step) * step
    expected_fixed_stop = round((100.0 * _STYLE_PARAMS["GROWTH"]["stop_pct"]) / step) * step
    expected_stop = max(expected_atr_stop, expected_fixed_stop)
    assert plan["stop"] == expected_stop


def test_build_game_plan_swing_uses_2x_atr_mult():
    plan = _build_game_plan_for_style(
        symbol="TEST", style="SWING", current_price=100.0,
        signal_reasons={}, atr=2.0,
    )
    from src.services.paper_trading_engine import _round_step
    step = _round_step(100.0)
    expected_atr_stop = round((100.0 - 2.0 * 2.0) / step) * step
    expected_fixed_stop = round((100.0 * _STYLE_PARAMS["SWING"]["stop_pct"]) / step) * step
    expected_stop = max(expected_atr_stop, expected_fixed_stop)
    assert plan["stop"] == expected_stop


def test_build_game_plan_falls_back_to_2x_when_atr_stop_mult_missing():
    """If a style entry somehow lacked the field, the function must degrade to the safe 2.0x
    default rather than crashing with a KeyError."""
    import src.services.paper_trading_engine as pte
    original = dict(pte._STYLE_PARAMS["GROWTH"])
    try:
        del pte._STYLE_PARAMS["GROWTH"]["atr_stop_mult"]
        plan = _build_game_plan_for_style(
            symbol="TEST", style="GROWTH", current_price=100.0,
            signal_reasons={}, atr=2.0,
        )
        from src.services.paper_trading_engine import _round_step
        step = _round_step(100.0)
        expected_atr_stop = round((100.0 - 2.0 * 2.0) / step) * step  # 2.0x fallback, not 3.0x
        expected_fixed_stop = round((100.0 * original["stop_pct"]) / step) * step
        expected_stop = max(expected_atr_stop, expected_fixed_stop)
        assert plan["stop"] == expected_stop
    finally:
        pte._STYLE_PARAMS["GROWTH"] = original
