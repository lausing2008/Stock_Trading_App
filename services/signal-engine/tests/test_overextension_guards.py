"""Tests for AUD232-BUY-FROM-TOP-1/2 — two new fields _ta_score() computes into Signal.reasons,
consumed by market-data's _is_conviction_buy() as hard disqualifiers (see
test_conviction_buy_overextension_guards.py in market-data/tests for the consumer side).

Root cause this closes: a real live BUY conviction alert fired on 0939.HK (2026-08-04) at
essentially the same overbought level the model had correctly been BLOCKING all week — the
existing stoch_rsi_overbought disqualifier (stoch_k > 0.80, a single-bar cutoff) flickered
False on one noisy tick (stoch_k 0.824 -> 0.735) while RSI stayed at 70 and price stayed
within 1.5% of its 20-day high — nothing about the real risk had actually changed.

stoch_rsi_still_hot: requires the PRIOR bar to have also been overbought before treating a
dip below 0.80 as genuine cooling.

near_recent_high_hot: an independent, non-stochastic-dependent signal — price within 3% of
its own 20-day high with RSI still >65 is genuinely still an extended entry.

Uses its own synthetic-DataFrame helper (matching test_bearish_pillars.py's own convention)
rather than importing from test_signal_generator.py, which has a pre-existing, unrelated
ImportError (`_decide` no longer exists in signals.py) that would block collection there.
"""
import numpy as np
import pandas as pd

from src.generators.signals import _ta_score


def _rally_with_optional_last_bar_dip(dip_pct: float = 0.0, seed: int = 3, n: int = 260, trend: float = 0.35) -> pd.DataFrame:
    """A realistic strong uptrend (trend + noise, matching test_bearish_pillars.py's own
    construction) — real day-to-day noise is what lets RSI settle into a genuinely elevated
    but not fully-saturated range (a pure monotonic ramp pins Wilder's RSI at exactly 100,
    leaving zero variance for the stochastic to measure, which doesn't reproduce the real
    bug). `dip_pct` optionally pulls just the FINAL close down by that fraction — this is
    what reproduces the real 0939.HK case: a single bar's small pullback (confirmed via
    direct probing: dip_pct=0.008 ≈ a 0.8% single-bar move, close to 0939.HK's real
    0.65-1.5% move) drops stoch_k below the 0.80 cutoff while RSI barely changes."""
    rng = np.random.default_rng(seed)
    close = 100 + (np.ones(n) * trend + rng.normal(0, 1.0, n)).cumsum()
    close = np.maximum(close, 1.0)
    if dip_pct:
        close[-1] = close[-1] * (1 - dip_pct)
    return pd.DataFrame(
        {
            "close": close,
            "high": close + 0.3,
            "low": close - 0.3,
            "open": close,
            "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
        }
    )


# ── stoch_rsi_still_hot ─────────────────────────────────────────────────────────────

def test_still_hot_true_at_a_genuine_overbought_peak():
    df = _rally_with_optional_last_bar_dip(dip_pct=0.0)
    _, reasons = _ta_score(df)
    assert reasons["stoch_rsi_overbought"] is True
    assert reasons["stoch_rsi_still_hot"] is True


def test_still_hot_true_on_a_single_tick_dip_below_the_cutoff():
    # This is the exact live bug: a single bar's small dip flips stoch_rsi_overbought to
    # False (stoch_k crosses below 0.80), but the prior bar was still overbought — the
    # original single-bar check alone would have let this through; still_hot must catch it.
    df = _rally_with_optional_last_bar_dip(dip_pct=0.008)
    _, reasons = _ta_score(df)
    assert reasons["stoch_rsi_overbought"] is False  # confirms this reproduces the flicker
    assert reasons["rsi"] > 65  # confirms this is still a genuinely hot stock, not a real reversal
    assert reasons["stoch_rsi_still_hot"] is True


def _rally_then_multi_bar_decline(decline_bars: int = 5, decline_pct: float = 0.08, seed: int = 3, n: int = 260, trend: float = 0.35) -> pd.DataFrame:
    """A genuine, sustained decline spread over several bars (not a single-tick dip) —
    confirms still_hot correctly clears once BOTH the current and prior bar have actually
    cooled, unlike the single-bar-flicker case above where only the very last bar dipped."""
    rng = np.random.default_rng(seed)
    close = 100 + (np.ones(n) * trend + rng.normal(0, 1.0, n)).cumsum()
    close = np.maximum(close, 1.0)
    decline = np.linspace(0, decline_pct, decline_bars)
    close[-decline_bars:] = close[-decline_bars:] * (1 - decline)
    return pd.DataFrame(
        {
            "close": close,
            "high": close + 0.3,
            "low": close - 0.3,
            "open": close,
            "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
        }
    )


def test_still_hot_false_after_a_genuine_multi_bar_decline():
    # Unlike the single-tick-flicker case (still_hot correctly stays True), a REAL multi-bar
    # decline must clear still_hot once both the current and prior bar have actually cooled —
    # confirms the new check doesn't stay permanently latched after real cooling begins.
    df = _rally_then_multi_bar_decline()
    _, reasons = _ta_score(df)
    assert reasons["stoch_rsi_overbought"] is False
    assert reasons["stoch_rsi_still_hot"] is False


def test_still_hot_does_not_crash_on_thin_history():
    df = _rally_with_optional_last_bar_dip(n=20)
    _, reasons = _ta_score(df)
    assert "stoch_rsi_still_hot" in reasons  # must not KeyError even on thin history


# ── near_recent_high_hot ─────────────────────────────────────────────────────────────

def test_near_recent_high_hot_true_at_the_same_dip_that_reproduces_the_live_bug():
    df = _rally_with_optional_last_bar_dip(dip_pct=0.008)
    _, reasons = _ta_score(df)
    assert reasons["pct_from_20d_high"] is not None
    assert reasons["pct_from_20d_high"] < 0.03
    assert reasons["rsi"] > 65
    assert reasons["near_recent_high_hot"] is True


def test_near_recent_high_hot_false_once_rsi_has_genuinely_cooled():
    # A larger dip (2%) pulls RSI down below the 65 floor even though price is still
    # nominally close to the 20-day high — isolates the RSI-still-hot half of the AND
    # condition from the distance-from-high half.
    df = _rally_with_optional_last_bar_dip(dip_pct=0.02)
    _, reasons = _ta_score(df)
    assert reasons["rsi"] < 65
    assert reasons["near_recent_high_hot"] is False


def test_near_recent_high_hot_false_once_price_is_genuinely_off_the_high():
    # A bigger single-bar move (5%) pushes distance-from-high past the 3% floor —
    # isolates the distance-from-high half of the AND condition.
    df = _rally_with_optional_last_bar_dip(dip_pct=0.05)
    _, reasons = _ta_score(df)
    assert reasons["pct_from_20d_high"] > 0.03
    assert reasons["near_recent_high_hot"] is False


def test_pct_from_20d_high_is_none_on_thin_history():
    df = _rally_with_optional_last_bar_dip(n=20)
    _, reasons = _ta_score(df)
    assert reasons["pct_from_20d_high"] is None
    assert reasons["near_recent_high_hot"] is False
