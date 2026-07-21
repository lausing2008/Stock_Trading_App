"""Tests for T232-SIG10's bearish pillar mirror (`bearish_pillars_active` and its 4
sub-scores) added to `_ta_score()`'s reasons dict.

This is an observability-only feature: it is NOT wired into any live gate/compression yet
(see the comment block in signals.py directly above the bearish pillar code for why —
current SELL outcome data is 96%+ bull-regime with zero bear/high_vol samples, so there is
nothing to calibrate a real min_pillars_for_sell gate against). These tests only verify the
new feature computes sane, correctly-mirrored values — not that it changes any live signal
(it deliberately does not).

Uses its own synthetic-DataFrame helper rather than importing from test_signal_generator.py,
since that file has a pre-existing, unrelated ImportError (`_decide` no longer exists in
signals.py — renamed to `_decide_style` at some point) that would block collection of any
test added there.
"""
import numpy as np
import pandas as pd
import pytest

from src.generators.signals import _ta_score


def _make_df(n: int = 300, trend: float = 0.0, seed: int = 0, noise: float = 1.0) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame. `trend` is a per-bar drift added to every step (not a
    per-bar-average like test_signal_generator.py's helper) — this produces a much cleaner,
    more consistently-directional series, needed here to isolate bullish-only vs. bearish-only
    behavior without a random walk's inherent local reversals muddying the read."""
    rng = np.random.default_rng(seed)
    close = 100 + (np.ones(n) * trend + rng.normal(0, noise, n)).cumsum()
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "close": close,
            "high": close + np.abs(rng.normal(0, 0.5, n)),
            "low": close - np.abs(rng.normal(0, 0.5, n)),
            "open": close + rng.normal(0, 0.3, n),
            "volume": rng.integers(1_600_000, 2_400_000, n).astype(float),
        }
    )


# ── Presence / shape ──────────────────────────────────────────────────────────

def test_bearish_pillar_keys_present():
    df = _make_df(n=300, trend=0.3, seed=1)
    _, reasons = _ta_score(df)
    expected = {
        "bearish_pillars_active",
        "bearish_pillar_trend",
        "bearish_pillar_momentum",
        "bearish_pillar_volume",
        "bearish_pillar_structure",
        "bearish_trend",
        "macd_zero_cross_down",
        "stoch_rsi_cross_down",
    }
    assert expected.issubset(reasons.keys())


def test_bearish_pillars_active_is_valid_count():
    df = _make_df(n=300, trend=0.3, seed=1)
    _, reasons = _ta_score(df)
    assert 0 <= reasons["bearish_pillars_active"] <= 4
    assert reasons["bearish_pillars_active"] == sum(
        1 for k in (
            "bearish_pillar_trend", "bearish_pillar_momentum",
            "bearish_pillar_volume", "bearish_pillar_structure",
        ) if reasons[k] >= 0.5
    )


@pytest.mark.parametrize("key", [
    "bearish_pillar_trend", "bearish_pillar_momentum",
    "bearish_pillar_volume", "bearish_pillar_structure",
])
def test_bearish_pillar_subscores_in_range(key):
    df = _make_df(n=300, trend=-0.2, seed=2)
    _, reasons = _ta_score(df)
    assert 0.0 <= reasons[key] <= 1.0


# ── Directional sanity — the core mirror-correctness property ────────────────

def test_strong_uptrend_has_few_bearish_pillars_active():
    """A clean, strong, low-noise uptrend should score near-zero on the bearish mirror —
    this is the property that broke during development (bb_pct_b near the UPPER Bollinger
    Band extreme was wrongly counted as bearish structural evidence) and was fixed by
    restricting bb_bear_score to the LOWER band extreme only (bb_pct_b <= 0.2)."""
    df = _make_df(n=300, trend=0.5, seed=0, noise=0.3)
    _, reasons = _ta_score(df)
    assert reasons["bearish_pillars_active"] <= 1, (
        f"strong uptrend should have <=1 bearish pillars active, "
        f"got {reasons['bearish_pillars_active']} "
        f"(sub-scores: trend={reasons['bearish_pillar_trend']}, "
        f"momentum={reasons['bearish_pillar_momentum']}, "
        f"volume={reasons['bearish_pillar_volume']}, "
        f"structure={reasons['bearish_pillar_structure']})"
    )


def test_strong_downtrend_has_more_bearish_than_bullish_pillars():
    df = _make_df(n=300, trend=-0.5, seed=0, noise=0.3)
    _, reasons = _ta_score(df)
    assert reasons["bearish_pillars_active"] > reasons["independent_pillars_active"]


def test_strong_uptrend_has_more_bullish_than_bearish_pillars():
    df = _make_df(n=300, trend=0.5, seed=0, noise=0.3)
    _, reasons = _ta_score(df)
    assert reasons["independent_pillars_active"] > reasons["bearish_pillars_active"]


def test_bearish_pillar_structure_not_triggered_by_upper_band_extreme():
    """Regression test for the exact bug caught during development: a steady uptrend pins
    bb_pct_b near 1.0 (upper-band extreme). The bearish structure pillar must treat this as
    a bullish extreme, not bearish evidence — only the LOWER band extreme (bb_pct_b <= 0.2)
    should register as bearish structural evidence."""
    df = _make_df(n=300, trend=0.5, seed=0, noise=0.3)
    _, reasons = _ta_score(df)
    assert reasons["bb_pct_b"] > 0.8, "fixture must actually reach the upper-band extreme"
    assert reasons["bearish_pillar_structure"] < 0.5


# ── Hard-override trend cases (mirrors the bullish death/golden-cross overrides) ─────────

def test_bearish_pillar_trend_zero_on_golden_cross_or_supertrend_cross_up():
    """A fresh golden cross or supertrend cross-up is a hard override to 0.0 bearish trend
    (mirrors p_trend's own death_cross_event/st_cross_down hard override to 0.0 bullish)."""
    df = _make_df(n=300, trend=0.6, seed=3, noise=0.2)
    _, reasons = _ta_score(df)
    if reasons.get("golden_cross_event") or reasons.get("supertrend_cross_up"):
        assert reasons["bearish_pillar_trend"] == 0.0


# ── Bounds robustness ──────────────────────────────────────────────────────────

def test_bearish_pillars_handles_short_data_without_raising():
    df = _make_df(n=30, seed=4)
    _, reasons = _ta_score(df)
    assert 0 <= reasons["bearish_pillars_active"] <= 4
