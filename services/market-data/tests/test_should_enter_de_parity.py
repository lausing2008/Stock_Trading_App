"""Regression tests for T232-DL-DUALSCORER-DEBT's fallback-hardening fix.

_should_enter() (the DE-outage fallback gate) was missing three scoring layers that
decision-engine's scorer.py already has: regime-as-a-direct-score, the pre-regime
early-warning penalty, and K-Score as a direct +/-1 layer. Under normal operation
decision-engine is authoritative (decision_engine_mode="primary") and _should_enter()'s
verdict is only used when DE is unreachable — but during exactly that outage window, the
fallback was measurably weaker than DE for the same inputs. These tests isolate just the
three new layers with otherwise-neutral inputs so a regression in any one of them is caught
without needing to reconstruct decision-engine's full request/response cycle.
"""
from datetime import datetime, timezone

import pytest

from src.services.paper_trading_engine import _should_enter


@pytest.fixture(autouse=True)
def _always_market_hours(monkeypatch):
    """_should_enter()'s first hard-reject depends on wall-clock time via _is_market_hours()
    — pin it to always pass so these tests aren't flaky depending on when they run."""
    import src.services.paper_trading_engine as pte
    monkeypatch.setattr(pte, "_is_market_hours", lambda market="US": True)


def _neutral_inputs():
    """A candidate that clears every hard reject and scores neutral (0) on every
    pre-existing layer, so only the layer under test moves the score/notes."""
    live_price = 100.0
    game_plan = {
        "entry2": 100.0,       # live_price sits exactly at entry2 -> "in optimal zone" +2
        "breakout": 103.0,
        "stop": 95.0,          # stop_dist=5, well above the min_stop_dist floor
        "take_profit": 110.0,  # rr = 10/5 = 2.0 -> "Acceptable R:R" +0
    }
    signal_data = {
        "confidence": 80.0,     # comfortably above any min_confidence*0.90 floor
        "bullish_probability": 0.60,  # between 0.58 and 0.70 -> neutral, +0
        # macro_blackout=False (not just absent) short-circuits the DB fallback query below
        # it — conftest.py stubs SessionLocal as a MagicMock whose chained .fetchone() is
        # truthy, which would otherwise trip the macro-blackout hard reject unconditionally.
        "reasons": {"macro_blackout": False},
        "ts": datetime.now(timezone.utc),  # fresh -> +1 (accepted; not under test here)
    }
    cfg = {"min_entry_score": -99}  # low floor so `should_enter` alone doesn't obscure `score`
    return live_price, game_plan, signal_data, cfg


def _score_only(live_regime=None, kscore=None, cfg_overrides=None, game_plan_overrides=None,
                 signal_data_overrides=None):
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    if cfg_overrides:
        cfg.update(cfg_overrides)
    if game_plan_overrides:
        game_plan.update(game_plan_overrides)
    if signal_data_overrides:
        signal_data.update(signal_data_overrides)
    should_enter, score, notes = _should_enter(
        "TEST", signal_data, live_price, game_plan, cfg, live_regime, kscore=kscore,
    )
    return should_enter, score, notes


# ── Regime as a direct score layer ────────────────────────────────────────────────────

def test_bull_regime_adds_one_point():
    _, score_bull, notes = _score_only(live_regime={"state": "bull"})
    _, score_none, _ = _score_only(live_regime=None)
    assert score_bull == score_none + 1
    assert any("Regime: bull" in n for n in notes)


def test_choppy_regime_subtracts_one_point():
    # choppy/risk_off raise the R:R hard-reject floor (regime_min_rr_ratio) — bump take_profit
    # so the baseline rr=2.0 setup doesn't get hard-rejected before scoring even runs. Note:
    # choppy/bear also trigger the PRE-EXISTING cross-horizon-consensus penalty (-1, when
    # cross_style_buys==0) independent of the new regime layer under test here — supply
    # cross_style_buys=2 so that pre-existing layer is neutralized and doesn't confound the
    # delta being asserted.
    override = {"take_profit": 130.0}
    signal_overrides = {"reasons": {"macro_blackout": False, "cross_style_buys": 2}}
    _, score_choppy, notes = _score_only(
        live_regime={"state": "choppy"}, game_plan_overrides=override, signal_data_overrides=signal_overrides,
    )
    _, score_neutral, _ = _score_only(
        live_regime={"state": "neutral"}, game_plan_overrides=override, signal_data_overrides=signal_overrides,
    )
    assert score_choppy == score_neutral - 1
    assert any("Regime: choppy" in n for n in notes)


def test_risk_off_regime_subtracts_two_points():
    override = {"take_profit": 130.0}
    signal_overrides = {"reasons": {"macro_blackout": False, "cross_style_buys": 2}}
    _, score_risk_off, _ = _score_only(
        live_regime={"state": "risk_off"}, game_plan_overrides=override, signal_data_overrides=signal_overrides,
    )
    _, score_neutral, _ = _score_only(
        live_regime={"state": "neutral"}, game_plan_overrides=override, signal_data_overrides=signal_overrides,
    )
    assert score_risk_off == score_neutral - 2


def test_neutral_regime_adds_no_note_and_no_score_change():
    _, score_neutral, notes = _score_only(live_regime={"state": "neutral"})
    _, score_missing, _ = _score_only(live_regime=None)
    assert score_neutral == score_missing
    assert not any(n.startswith("Regime:") for n in notes)


def test_no_regime_dict_defaults_to_neutral_no_score_change():
    _, score_none, notes = _score_only(live_regime=None)
    _, score_explicit_neutral, _ = _score_only(live_regime={"state": "neutral"})
    assert score_none == score_explicit_neutral
    assert not any(n.startswith("Regime:") for n in notes)


# ── Pre-regime early-warning layer ────────────────────────────────────────────────────

def test_pre_risk_off_subtracts_one_point_and_takes_priority_over_pre_choppy():
    _, score, notes = _score_only(
        live_regime={"state": "neutral", "is_pre_risk_off": True, "is_pre_choppy": True}
    )
    _, score_base, _ = _score_only(live_regime={"state": "neutral"})
    assert score == score_base - 1
    assert any("Pre-risk-off" in n for n in notes)
    assert not any("Pre-choppy" in n for n in notes)


def test_pre_choppy_subtracts_one_point():
    _, score, notes = _score_only(live_regime={"state": "neutral", "is_pre_choppy": True})
    _, score_base, _ = _score_only(live_regime={"state": "neutral"})
    assert score == score_base - 1
    assert any("Pre-choppy" in n for n in notes)


def test_no_pre_regime_flags_leaves_score_untouched():
    _, score, notes = _score_only(live_regime={"state": "neutral"})
    assert not any("Pre-choppy" in n or "Pre-risk-off" in n for n in notes)


# ── K-Score as a direct +/-1 layer ────────────────────────────────────────────────────

def test_kscore_at_or_above_55_adds_one_point():
    _, score_high, notes = _score_only(kscore=55.0)
    _, score_none, _ = _score_only(kscore=None)
    assert score_high == score_none + 1
    assert any("K-Score 55" in n and "conviction positive" in n for n in notes)


def test_kscore_below_55_subtracts_one_point():
    _, score_low, notes = _score_only(kscore=40.0)
    _, score_none, _ = _score_only(kscore=None)
    assert score_low == score_none - 1
    assert any("K-Score 40" in n and "below 55" in n for n in notes)


def test_kscore_of_exactly_zero_is_scored_not_treated_as_missing():
    """A genuine K-Score of 0.0 is falsy in Python — must still be scored (as a weak case),
    not silently skipped the way `if kscore:` would (matching the sibling K-Score-falsy bug
    already fixed elsewhere in this file for _composite_priority())."""
    _, score, notes = _score_only(kscore=0.0)
    _, score_none, _ = _score_only(kscore=None)
    assert score == score_none - 1
    assert any("K-Score 0" in n for n in notes)


def test_no_kscore_leaves_score_untouched():
    _, score, notes = _score_only(kscore=None)
    assert not any(n.startswith("K-Score") for n in notes)


# ── Combined — matches decision-engine's scorer.py ordering/independence ─────────────

def test_all_three_new_layers_stack_independently():
    _, score, notes = _score_only(
        live_regime={"state": "bull"}, kscore=60.0,
    )
    _, score_base, _ = _score_only(live_regime=None, kscore=None)
    # bull regime (+1) + high kscore (+1) = +2 over the fully-neutral baseline
    assert score == score_base + 2
    assert any("Regime: bull" in n for n in notes)
    assert any("K-Score 60" in n for n in notes)
