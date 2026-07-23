"""Regression tests for T232-DL-DUALSCORER-DEBT's fallback-hardening fix.

_should_enter() (the DE-outage fallback gate) was missing three scoring layers that
decision-engine's scorer.py already has: regime-as-a-direct-score, the pre-regime
early-warning penalty, and K-Score as a direct +/-1 layer. Under normal operation
decision-engine is authoritative (decision_engine_mode="primary") and _should_enter()'s
verdict is only used when DE is unreachable — but during exactly that outage window, the
fallback was measurably weaker than DE for the same inputs. These tests isolate just the
three new layers with otherwise-neutral inputs so a regression in any one of them is caught
without needing to reconstruct decision-engine's full request/response cycle.

Also covers the 4 DE-only hard rejects (AUD232-021 market-hours/holiday guard, AUD232-005
time-of-day gate + extended-move block, AUD232-060 regime-based R:R stiffening) — these were
already ported into _should_enter() in an earlier pass but had no dedicated regression tests
of their own (the tracker text describing them as still "todo" was stale; the code was not).
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.services.paper_trading_engine import _should_enter

# Captured BEFORE the autouse fixture below ever patches it, so tests that need the REAL
# market-hours logic (not the fixture's always-True stub) can restore it explicitly.
import src.services.paper_trading_engine as _pte_module
_REAL_IS_MARKET_HOURS = _pte_module._is_market_hours


@pytest.fixture(autouse=True)
def _always_market_hours(monkeypatch):
    """_should_enter()'s first hard-reject depends on wall-clock time via _is_market_hours()
    — pin it to always pass so these tests aren't flaky depending on when they run.

    Also pins `datetime.now()` to a fixed, safe mid-session instant (noon ET on a Monday) —
    NOT just _is_market_hours(). A real flakiness was found here: any test that doesn't
    explicitly test the time-of-day gate (i.e. every test EXCEPT the ones in the "AUD232-005:
    time-of-day gate" section below, which use _mock_local_time to test specific times) was
    still vulnerable to the REAL wall-clock landing inside the 9:30-10:00 or 15:45-16:00 ET
    gate windows — this fixture mocking _is_market_hours alone does not protect against the
    separate, later time-of-day-gate check, which reads real time directly. Caught when this
    test file started failing at exactly 9:48 AM ET on a real run, unrelated to any code
    change in that session."""
    import src.services.paper_trading_engine as pte
    monkeypatch.setattr(pte, "_is_market_hours", lambda market="US", as_of=None: True)

    _safe_noon = datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc)  # noon ET on a Monday

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return _safe_noon.astimezone(tz) if tz else _safe_noon

    monkeypatch.setattr(pte, "datetime", _FixedDatetime)


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
                 signal_data_overrides=None, max_open_corr=None, as_of=None):
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    if cfg_overrides:
        cfg.update(cfg_overrides)
    if game_plan_overrides:
        game_plan.update(game_plan_overrides)
    if signal_data_overrides:
        signal_data.update(signal_data_overrides)
    should_enter, score, notes = _should_enter(
        "TEST", signal_data, live_price, game_plan, cfg, live_regime, kscore=kscore,
        max_open_corr=max_open_corr, as_of=as_of,
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


# ── AUD232-021: market-hours/holiday hard reject (DE-only, ported) ───────────────────

def test_market_closed_hard_rejects_with_score_negative_99_and_no_further_checks(monkeypatch):
    import src.services.paper_trading_engine as pte
    monkeypatch.setattr(pte, "_is_market_hours", lambda market="US", as_of=None: False)
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    should_enter, score, notes = pte._should_enter("TEST", signal_data, live_price, game_plan, cfg, None)
    assert should_enter is False
    assert score == -99
    assert any("Market closed" in n for n in notes)


def test_market_open_does_not_hard_reject_on_this_check():
    # The autouse fixture already pins _is_market_hours to True — a neutral candidate must
    # clear this specific check (though other hard rejects/score floor still apply downstream).
    should_enter, score, notes = _score_only()
    assert not any("Market closed" in n for n in notes)


# ── AUD232-005: time-of-day gate hard reject (DE-only, ported) ───────────────────────

def _mock_local_time(monkeypatch, hour, minute, market="US"):
    """_should_enter()'s time-of-day gate calls datetime.now(timezone.utc).astimezone(tz) —
    patch the module's `datetime` so `.now()` returns a fixed instant that converts to the
    given local hour:minute in the target market's timezone."""
    tz = ZoneInfo("America/New_York") if market != "HK" else ZoneInfo("Asia/Hong_Kong")
    local_dt = datetime(2026, 6, 15, hour, minute, tzinfo=tz)  # a Monday, not a holiday
    utc_dt = local_dt.astimezone(timezone.utc)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc_dt.astimezone(tz) if tz else utc_dt

    import src.services.paper_trading_engine as pte
    monkeypatch.setattr(pte, "datetime", _FixedDatetime)


def test_first_30_minutes_of_session_is_a_hard_reject(monkeypatch):
    _mock_local_time(monkeypatch, 9, 45)  # 09:45 ET -> 15 min after 09:30 open
    should_enter, score, notes = _score_only()
    assert should_enter is False
    assert score == -99
    assert any("Time-of-day gate" in n and "first 30 min" in n for n in notes)


def test_last_15_minutes_of_session_is_a_hard_reject(monkeypatch):
    _mock_local_time(monkeypatch, 15, 50)  # 15:50 ET -> 10 min before 16:00 close
    should_enter, score, notes = _score_only()
    assert should_enter is False
    assert score == -99
    assert any("Time-of-day gate" in n and "last 15 min" in n for n in notes)


def test_mid_session_time_does_not_hit_the_time_of_day_gate(monkeypatch):
    _mock_local_time(monkeypatch, 12, 0)  # noon ET -> comfortably mid-session
    should_enter, score, notes = _score_only()
    assert not any("Time-of-day gate" in n for n in notes)


def test_time_of_day_gate_boundary_is_exclusive_of_the_safe_side(monkeypatch):
    # 570 mins = 09:30 exactly (market open) must NOT be gated; 600 mins = 10:00 exactly is
    # the first minute that's safe again.
    _mock_local_time(monkeypatch, 10, 0)
    _, _, notes_at_1000 = _score_only()
    assert not any("Time-of-day gate" in n for n in notes_at_1000)

    _mock_local_time(monkeypatch, 9, 59)
    _, score_at_959, notes_at_959 = _score_only()
    assert score_at_959 == -99
    assert any("first 30 min" in n for n in notes_at_959)


# ── AUD232-005: extended-move 6% hard reject (DE-only, ported) ───────────────────────

def test_price_more_than_6_percent_above_breakout_is_a_hard_reject():
    # Widen stop/take_profit so R:R still clears its own floor at the new, higher live_price —
    # the extended-move check runs AFTER the R:R hard reject, so a stale stop/take_profit left
    # over from a lower live_price would trip that earlier check first and mask this one.
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    game_plan.update({"breakout": 100.0, "stop": 100.0, "take_profit": 130.0})
    should_enter, score, notes = _should_enter(
        "TEST", signal_data, 107.0, game_plan, cfg, None,
    )
    assert should_enter is False
    assert score == -99
    assert any("extended move" in n for n in notes)


def test_price_comfortably_below_the_6_percent_threshold_does_not_reject():
    # 5.5% above breakout — deliberately not exactly 6.0% (floating-point (live/breakout-1)*100
    # can land a hair on either side of an exact boundary; a clearly-below value is what
    # actually matters here, not pinning the exact boundary).
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    game_plan.update({"breakout": 100.0, "stop": 96.5, "take_profit": 125.5})
    should_enter, score, notes = _should_enter(
        "TEST", signal_data, 105.5, game_plan, cfg, None,
    )
    assert not any("extended move" in n for n in notes)


def test_price_below_breakout_does_not_hit_the_extension_check():
    should_enter, score, notes = _score_only()  # default game_plan has breakout=103, live_price=100
    assert not any("extended move" in n for n in notes)


def test_extension_threshold_is_configurable_via_cfg():
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    game_plan.update({"breakout": 100.0, "stop": 98.0, "take_profit": 116.0})  # rr = 13/5 = 2.6
    cfg["max_breakout_extension_pct"] = 2.0
    should_enter, score, notes = _should_enter(
        "TEST", signal_data, 103.0, game_plan, cfg, None,  # 3% above breakout
    )
    assert should_enter is False
    assert any("extended move" in n for n in notes)


# ── AUD232-060: regime-based R:R stiffening hard reject (DE-only, ported) ────────────

def test_choppy_regime_raises_the_minimum_rr_floor():
    # Baseline R:R of 2.0 (from _neutral_inputs: stop_dist=5, take_profit-live_price=10)
    # clears the neutral-regime floor (2.0) but not the choppy-regime floor (3.0 uncalibrated).
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    should_enter, score, notes = _should_enter(
        "TEST", signal_data, live_price, game_plan, cfg, {"state": "choppy"},
    )
    assert should_enter is False
    assert score == -99
    assert any("R:R" in n and "below minimum" in n for n in notes)


def test_risk_off_regime_also_raises_the_minimum_rr_floor():
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    should_enter, score, notes = _should_enter(
        "TEST", signal_data, live_price, game_plan, cfg, {"state": "risk_off"},
    )
    assert should_enter is False
    assert any("R:R" in n and "below minimum" in n for n in notes)


def test_same_rr_passes_in_neutral_regime_that_fails_in_choppy():
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    should_enter_neutral, score_neutral, notes_neutral = _should_enter(
        "TEST", signal_data, live_price, game_plan, cfg, {"state": "neutral"},
    )
    assert not any("below minimum" in n for n in notes_neutral)


def test_choppy_regime_rr_floor_can_be_cleared_with_a_wider_take_profit():
    live_price, game_plan, signal_data, cfg = _neutral_inputs()
    game_plan["take_profit"] = 130.0  # rr = (130-100)/5 = 6.0, clears the 3.0 choppy floor
    should_enter, score, notes = _should_enter(
        "TEST", signal_data, live_price, game_plan, cfg, {"state": "choppy"},
    )
    assert not any("below minimum" in n for n in notes)


# ── T258-PORTFOLIO-CORRELATION-PREENTRY: advisory correlation penalty ────────────────

def test_high_correlation_with_open_position_subtracts_one_point():
    _, score_high, notes = _score_only(max_open_corr=0.85)
    _, score_none, _ = _score_only(max_open_corr=None)
    assert score_high == score_none - 1
    assert any("High correlation" in n and "0.85" in n for n in notes)


def test_correlation_at_exactly_the_threshold_does_not_penalize():
    _, score, notes = _score_only(max_open_corr=0.8)
    assert not any("High correlation" in n for n in notes)


def test_correlation_below_threshold_does_not_penalize():
    _, score_low, notes = _score_only(max_open_corr=0.3)
    _, score_none, _ = _score_only(max_open_corr=None)
    assert score_low == score_none
    assert not any("High correlation" in n for n in notes)


def test_no_correlation_data_leaves_score_untouched():
    _, score, notes = _score_only(max_open_corr=None)
    assert not any("High correlation" in n for n in notes)


def test_strongly_negative_correlation_does_not_penalize():
    """Only a strong POSITIVE correlation reduces diversification — a hedge (negative
    correlation) with an open position is the opposite of a concentration risk."""
    _, score, notes = _score_only(max_open_corr=-0.9)
    assert not any("High correlation" in n for n in notes)


def test_correlation_layer_stacks_with_kscore_and_regime_independently():
    _, score, notes = _score_only(live_regime={"state": "bull"}, kscore=60.0, max_open_corr=0.9)
    _, score_base, _ = _score_only(live_regime=None, kscore=None, max_open_corr=None)
    # bull (+1) + high kscore (+1) + high correlation (-1) = +1 over the fully-neutral baseline
    assert score == score_base + 1
    assert any("Regime: bull" in n for n in notes)
    assert any("K-Score 60" in n for n in notes)
    assert any("High correlation" in n for n in notes)


# ── T233-SELFIMPROVE-PHASE2b: `as_of` parameter — historical replay correctness ───────────
#
# Live-verification against production found that a historical backtest replay of
# _should_enter() (via gate_harness.py) returned ZERO entered trades no matter what, because
# the market-hours/time-of-day/macro-blackout hard rejects all read the REAL wall-clock
# "now" — completely unrelated to whichever historical date was being replayed. `as_of`
# fixes this: when passed, every wall-clock check below resolves against IT instead of the
# real current time. When omitted (every live caller), behavior is byte-identical to before
# this parameter existed — these tests confirm both halves of that claim.

def test_as_of_none_defaults_to_the_real_wall_clock_unchanged(monkeypatch):
    """Backward compatibility: an as_of=None call must resolve exactly like the pre-fix
    code (a bare `datetime.now(timezone.utc)`), not silently behave differently."""
    import src.services.paper_trading_engine as pte
    fixed_now = datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc)  # noon ET, safely mid-session

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now

    monkeypatch.setattr(pte, "datetime", _FixedDatetime)
    monkeypatch.setattr(pte, "_is_market_hours", lambda market="US", as_of=None: True)
    should_enter, score, notes = _score_only(as_of=None)
    assert not any("Market closed" in n for n in notes)
    assert not any("Time-of-day gate" in n for n in notes)


def test_as_of_overrides_the_real_wall_clock_for_the_market_hours_gate(monkeypatch):
    """The actual bug fix: passing a historical as_of during market hours (in that
    timezone) must clear the gate even if the REAL current wall-clock time (also mocked
    here, to a value that would otherwise fail the gate) would not.

    Must un-patch the autouse `_always_market_hours` fixture's OWN `_is_market_hours`
    override first — that fixture unconditionally stubs it to always return True for
    every other test in this file, which would make this specific test pass regardless
    of whether the real as_of-forwarding logic works at all. Caught via adversarial
    verification: sabotaging the as_of= argument at the real call site inside
    _should_enter() did NOT make an earlier version of this test fail, because it was
    unknowingly exercising the autouse fixture's always-True stub, never the real
    function — restoring the REAL _is_market_hours() here is what makes this test
    actually test the fix.

    Deliberately uses a WEEKDAY 3am ET for the mocked "real now" (not a weekend) — an
    even earlier draft used a UTC timestamp that happened to convert to a Sunday in ET,
    which fails the market-hours gate for an unrelated reason (weekend) regardless of
    whether the as_of override actually works — a second, independent false-confidence
    trap caught the same way."""
    import src.services.paper_trading_engine as pte
    monkeypatch.setattr(pte, "_is_market_hours", _REAL_IS_MARKET_HOURS)
    # Real "now" mocked to 3am ET on a WEEKDAY Monday (market closed, but not a weekend
    # closure) — as_of below overrides this per-call to a genuinely open moment.
    real_now_market_closed = datetime(2026, 6, 15, 3, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_now_market_closed.astimezone(tz) if tz else real_now_market_closed

    monkeypatch.setattr(pte, "datetime", _FixedDatetime)
    historical_as_of = datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc)  # noon ET, same Monday
    should_enter, score, notes = _score_only(as_of=historical_as_of)
    assert not any("Market closed" in n for n in notes)


def test_as_of_first_30_minutes_of_session_still_hard_rejects():
    """The time-of-day gate must be evaluated AS OF as_of, not real "now" — a historical
    as_of landing in the first 30 minutes of the session must still reject, exactly as the
    live gate would for a live candidate at that same local time."""
    as_of_9_45_et = datetime(2026, 6, 15, 13, 45, tzinfo=timezone.utc)  # 09:45 ET
    should_enter, score, notes = _score_only(as_of=as_of_9_45_et)
    assert should_enter is False
    assert any("Time-of-day gate" in n for n in notes)


def test_as_of_mid_session_does_not_hit_the_time_of_day_gate():
    as_of_noon_et = datetime(2026, 6, 15, 17, 0, tzinfo=timezone.utc)  # noon ET
    should_enter, score, notes = _score_only(as_of=as_of_noon_et)
    assert not any("Time-of-day gate" in n for n in notes)
