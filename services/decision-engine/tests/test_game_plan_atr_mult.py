"""Regression test for AUD-DUPLOGIC: _default_game_plan()'s ATR-stop-multiplier used to be an
independently-hardcoded literal (2.5 for GROWTH, 2.0 otherwise) that silently disagreed with
market-data's real, authoritative paper_trading_engine.py::_build_game_plan_for_style() (which
uses 3.0 for GROWTH) — an undocumented drift between decision-engine's shadow-scoring game plan
approximation and the actual paper-trading game plan it's meant to approximate.

Fix: atr_stop_mult is now a real field in _STYLE_PARAMS (market-data's canonical source, fetched
live by decision-engine via GET /stocks/style-params) and in decision-engine's own
_STYLE_PARAMS_FALLBACK, read via p_raw.get("atr_stop_mult", 2.0) instead of a hardcoded
style-name check.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())

import src.api.core.aggregator as aggregator  # noqa: E402


def _reset_style_params_cache():
    aggregator._STYLE_PARAMS_CACHE = None
    aggregator._STYLE_PARAMS_TS = 0.0


# ── The fallback dict now carries the real, correct atr_stop_mult per style ──────────────────

def test_fallback_growth_atr_mult_matches_paper_trading_engines_real_value():
    """market-data's paper_trading_engine.py::_STYLE_PARAMS["GROWTH"]["atr_stop_mult"] is 3.0
    — the real, authoritative value used for actual paper trades. decision-engine's own
    fallback dict must match it, not the old, silently-wrong 2.5 literal."""
    assert aggregator._STYLE_PARAMS_FALLBACK["GROWTH"]["atr_stop_mult"] == 3.0


def test_fallback_non_growth_styles_use_2x_atr_mult():
    for style in ("SHORT", "SWING", "LONG"):
        assert aggregator._STYLE_PARAMS_FALLBACK[style]["atr_stop_mult"] == 2.0


# ── _default_game_plan() actually reads and applies the field ────────────────────────────────

def test_default_game_plan_uses_growth_atr_mult_of_3x_not_2_5x(monkeypatch):
    """End-to-end: a GROWTH game plan with a real ATR value must reflect the 3.0x multiplier,
    not the old 2.5x — computed directly from the known formula so this test fails loudly if
    the read-from-style-params wiring silently regresses back to a hardcoded literal."""
    _reset_style_params_cache()
    monkeypatch.setattr(aggregator, "_get_style_params", lambda: aggregator._STYLE_PARAMS_FALLBACK)

    live_price = 100.0
    atr_14 = 2.0
    plan = aggregator._default_game_plan(live_price, "GROWTH", atr_14)

    expected_atr_stop = live_price - 3.0 * atr_14  # 100 - 3.0*2.0 = 94.0
    fixed_stop = live_price * aggregator._STYLE_PARAMS_FALLBACK["GROWTH"]["stop_pct"]
    expected_stop = max(expected_atr_stop, fixed_stop)

    assert plan["stop"] == round(expected_stop, 4)


def test_default_game_plan_swing_still_uses_2x_atr_mult(monkeypatch):
    """Non-GROWTH styles were already correct at 2.0x before this fix — confirms the field-based
    read didn't change their behavior."""
    _reset_style_params_cache()
    monkeypatch.setattr(aggregator, "_get_style_params", lambda: aggregator._STYLE_PARAMS_FALLBACK)

    live_price = 100.0
    atr_14 = 2.0
    plan = aggregator._default_game_plan(live_price, "SWING", atr_14)

    expected_atr_stop = live_price - 2.0 * atr_14  # 100 - 2.0*2.0 = 96.0
    fixed_stop = live_price * aggregator._STYLE_PARAMS_FALLBACK["SWING"]["stop_pct"]
    expected_stop = max(expected_atr_stop, fixed_stop)

    assert plan["stop"] == round(expected_stop, 4)


def test_default_game_plan_falls_back_to_2x_when_style_params_missing_the_field(monkeypatch):
    """A live market-data response that (for whatever reason) omits atr_stop_mult entirely
    must not crash or silently apply a wrong multiplier — degrades to the safe 2.0x default."""
    _reset_style_params_cache()
    style_params_without_field = {
        "GROWTH": {"entry2_pct": 0.940, "breakout_pct": 1.035, "stop_pct": 0.880, "default_tp_pct": 1.35},
    }
    monkeypatch.setattr(aggregator, "_get_style_params", lambda: style_params_without_field)

    live_price = 100.0
    atr_14 = 2.0
    plan = aggregator._default_game_plan(live_price, "GROWTH", atr_14)

    expected_atr_stop = live_price - 2.0 * atr_14  # falls back to 2.0x, not 3.0x or 2.5x
    fixed_stop = live_price * style_params_without_field["GROWTH"]["stop_pct"]
    expected_stop = max(expected_atr_stop, fixed_stop)

    assert plan["stop"] == round(expected_stop, 4)
