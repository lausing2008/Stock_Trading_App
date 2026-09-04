"""Regression test for AUD-GAMEPLAN-NONERECOMMENDATION.

_build_game_plan() (scheduler.py) crashed with "'NoneType' object has no attribute 'lower'"
whenever fundamentals["recommendation"] was a real, PRESENT key whose value is None — which is
the normal case for any ETF (GDX, SPY, etc. have no individual analyst BUY/SELL rating at all).
`.get("recommendation", "")`'s "" default only substitutes when the key is MISSING, not when
it's present-but-None, so the .lower() call crashed, the whole function's outer except caught
it, and the signal alert email fired with NO game plan section at all — confirmed live in
production for a real GDX BUY alert (game_plan.build_failed, 2026-09-04).

scheduler.py can't be imported directly in this test environment (apscheduler/db import chain)
— source-text extraction + exec(), matching this file's own established pattern for pure
functions with no DB/scheduler dependency in their own body (_build_game_plan only touches
yfinance.Ticker, mocked out here entirely).
"""
import pathlib
from unittest.mock import MagicMock, patch

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _extract_build_game_plan():
    start = _scheduler_source.index("def _build_game_plan(")
    end = _scheduler_source.index("\ndef ", start + 1)
    func_source = _scheduler_source[start:end]
    namespace = {}
    # _STYLE_PARAMS and _round_step are module-level dependencies _build_game_plan reads —
    # extract them too rather than hand-copy a value that could silently drift from the real one.
    style_params_start = _scheduler_source.index("_STYLE_PARAMS: dict[str, dict] = {")
    style_params_end = _scheduler_source.index("\n}\n", style_params_start) + 3
    exec(_scheduler_source[style_params_start:style_params_end], namespace)  # noqa: S102

    round_step_start = _scheduler_source.index("def _round_step(")
    round_step_end = _scheduler_source.index("\ndef ", round_step_start + 1)
    exec(_scheduler_source[round_step_start:round_step_end], namespace)  # noqa: S102

    exec(func_source, namespace)  # noqa: S102 — isolated eval of the real function under test
    return namespace["_build_game_plan"]


_build_game_plan = _extract_build_game_plan()


def _fake_yf_ticker(prices):
    import pandas as pd
    _mod = MagicMock()
    hist = pd.DataFrame({"Close": prices})
    _mod.Ticker.return_value.history.return_value = hist
    return _mod


def test_none_recommendation_value_does_not_crash():
    """The exact bug scenario: fundamentals has a real 'recommendation' key whose value is
    None (an ETF's real shape) — must not raise, must return a real game plan dict."""
    with patch.dict("sys.modules", {"yfinance": _fake_yf_ticker([100.0] * 60)}):
        result = _build_game_plan(
            "GDX", {"reasons": {}}, {"recommendation": None}, style="GROWTH",
        )
    assert result is not None
    assert "catalysts" in result


def test_missing_recommendation_key_entirely_still_works():
    """The pre-existing, already-working case (no 'recommendation' key at all) must be
    unaffected by this fix."""
    with patch.dict("sys.modules", {"yfinance": _fake_yf_ticker([100.0] * 60)}):
        result = _build_game_plan("SPY", {"reasons": {}}, {}, style="SWING")
    assert result is not None


def test_none_fundamentals_entirely_still_works():
    with patch.dict("sys.modules", {"yfinance": _fake_yf_ticker([100.0] * 60)}):
        result = _build_game_plan("AAPL", {"reasons": {}}, None, style="SWING")
    assert result is not None


def test_bullish_recommendation_still_adds_the_catalyst_line():
    """The fix must not accidentally suppress the real, working bullish-recommendation
    catalyst for a normal single-stock case with a real rating."""
    with patch.dict("sys.modules", {"yfinance": _fake_yf_ticker([100.0] * 60)}):
        result = _build_game_plan(
            "AAPL", {"reasons": {}}, {"recommendation": "buy"}, style="SWING",
        )
    assert any("Analyst consensus bullish" in c for c in result["catalysts"])


def test_bearish_recommendation_does_not_add_the_bullish_catalyst():
    with patch.dict("sys.modules", {"yfinance": _fake_yf_ticker([100.0] * 60)}):
        result = _build_game_plan(
            "AAPL", {"reasons": {}}, {"recommendation": "sell"}, style="SWING",
        )
    assert not any("Analyst consensus bullish" in c for c in result["catalysts"])
