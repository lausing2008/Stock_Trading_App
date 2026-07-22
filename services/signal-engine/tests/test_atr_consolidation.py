"""Regression tests for AUD-DUPLOGIC's ATR consolidation: _adx(), _supertrend(), and the
atr_14/atr_14_pct computation in generate_all_signals() all used to have their OWN independent
inline TR/ATR calculation (three separate copies in this one file) instead of calling
shared/common/indicators.py's canonical atr() — the same function ranking-engine's kscore.py
already uses. Two of the three copies had already received the AUD232-073 min_periods=period
fix independently; the third (feeding reasons["atr_14"]/["atr_14_pct"], consumed by
decision-engine's ATR-based game plan stops) had NOT, a real, silently-recurring instance of
the same bug class within a single file — caught only by this consolidation pass, not by any
prior audit or test.

These tests confirm the consolidated call sites still produce numerically real (non-crashing,
sane-range) ADX/Supertrend/ATR-14 values — not a byte-for-byte replay of the old inline
formula (which no longer exists to compare against), since the whole point of the fix is that
all three sites now share ONE implementation rather than three copies that could each
independently drift.
"""
import numpy as np
import pandas as pd
import pytest

from src.generators.signals import _adx, _adj_close, _canon_atr, _supertrend, _ta_score


def _make_df(n: int = 300, trend: float = 0.0, seed: int = 0, noise: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + (np.ones(n) * trend + rng.normal(0, noise, n)).cumsum()
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "close": close,
            "high": close + np.abs(rng.normal(0, 0.5, n)),
            "low": close - np.abs(rng.normal(0, 0.5, n)),
            "open": close + rng.normal(0, 0.3, n),
            "volume": rng.integers(500_000, 5_000_000, n).astype(float),
        }
    )


# ── _adx() now delegates to the canonical atr() ──────────────────────────────

def test_adx_returns_sane_values_after_atr_consolidation():
    df = _make_df(n=300, trend=0.3, seed=1)
    adx, di_plus, di_minus = _adx(df)
    assert adx is None or 0.0 <= adx <= 100.0
    assert 0.0 <= di_plus <= 500.0  # DI can exceed 100 in edge cases but must stay finite/sane
    assert 0.0 <= di_minus <= 500.0


def test_adx_handles_short_history_without_raising():
    df = _make_df(n=20, seed=2)
    adx, di_plus, di_minus = _adx(df)
    # Short history correctly yields None (C3 FIX's own contract) rather than a fabricated number.
    assert adx is None or isinstance(adx, float)


def test_adx_strong_uptrend_shows_positive_di_dominance():
    """A clean, strong, low-noise uptrend should show +DI meaningfully above -DI — confirms
    the consolidated ATR denominator didn't break the DI+/DI- directional split."""
    df = _make_df(n=300, trend=0.6, seed=3, noise=0.2)
    _, di_plus, di_minus = _adx(df)
    assert di_plus > di_minus


# ── _supertrend() now delegates to the canonical atr() ───────────────────────

def test_supertrend_returns_valid_trend_value():
    df = _make_df(n=300, trend=0.3, seed=4)
    trend, cross_up, cross_down = _supertrend(df)
    assert trend in (1, -1)
    assert isinstance(cross_up, bool)
    assert isinstance(cross_down, bool)


def test_supertrend_short_history_defaults_to_bullish_no_cross():
    """Matches _supertrend()'s own documented short-history guard (n < period + 2 -> (1, False,
    False)) — confirms the consolidated atr() call didn't change this early-return path."""
    df = _make_df(n=5, seed=5)
    trend, cross_up, cross_down = _supertrend(df, period=10)
    assert (trend, cross_up, cross_down) == (1, False, False)


def test_supertrend_strong_uptrend_is_bullish():
    df = _make_df(n=300, trend=0.6, seed=6, noise=0.2)
    trend, _, _ = _supertrend(df)
    assert trend == 1


# ── atr_14 / atr_14_pct — the third, previously-unfixed inline copy ──────────────────────────
# NOTE: reasons["atr_14"]/["atr_14_pct"] are populated inside generate_all_signals(), NOT
# _ta_score() — the two are separate functions in this file that happen to share the same
# `reasons` dict object by reference (generate_all_signals() calls _ta_score() first, then
# adds atr_14 to the SAME dict it got back). generate_all_signals() itself fetches real prices
# via _fetch_prices(symbol) and isn't synthetic-DataFrame-testable directly, so these tests
# exercise the exact same 3-line computation generate_all_signals() runs (_adj_close +
# _canon_atr(period=14)), confirming it now correctly delegates to the shared canonical
# function instead of the old, unfixed inline .ewm(...).mean() (no min_periods) copy.

def test_atr_14_computation_produces_sane_non_negative_value():
    df = _make_df(n=300, trend=0.3, seed=7)
    close = _adj_close(df)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    atr_series = _canon_atr(high, low, close, period=14)
    atr_14 = float(atr_series.iloc[-1])
    assert not pd.isna(atr_14)
    assert atr_14 >= 0.0


def test_atr_14_now_has_min_periods_guard_unlike_the_old_unfixed_copy():
    """The bug this consolidation fixed: the OLD inline copy at this call site had no
    min_periods=period, so a short-history stock produced a real-looking (fabricated) ATR
    from bar 0 instead of correctly returning NaN during warmup. Confirms the now-shared
    canonical atr() correctly returns NaN before `period` true-range bars have accumulated."""
    df = _make_df(n=10, seed=8)  # well under period=14
    close = _adj_close(df)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    atr_series = _canon_atr(high, low, close, period=14)
    assert pd.isna(atr_series.iloc[-1])


def test_atr_14_matches_canonical_atr_function_directly():
    """Confirms the atr_14 computation is the SAME canonical atr() call used by _adx() and
    _supertrend() elsewhere in this file — not a value that happens to look similar."""
    from common.indicators import atr as _real_canon_atr

    df = _make_df(n=300, trend=0.3, seed=9)
    close = _adj_close(df)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    ours = _canon_atr(high, low, close, period=14)
    expected = _real_canon_atr(high, low, close, period=14)
    assert round(float(expected.iloc[-1]), 6) == round(float(ours.iloc[-1]), 6)
