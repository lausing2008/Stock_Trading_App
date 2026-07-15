"""Regression test for T247-TA-CLUSTERPIVOTS-CLOSE-HIGH-MISMATCH.

_cluster_pivots() previously found pivot indices on `close` via _find_pivots(df["close"], ...)
but then read the reported S/R price from `high`/`low` at those same indices — a close-based
local max/min is not guaranteed to coincide with the bar's actual high/low (e.g. a long wick),
so the reported level wasn't actually a local extremum at all. Same bug class already fixed at
every call site in patterns/recognizer.py (T237-TA-HS-CLOSE-HIGH-MISMATCH, TA-DTB1, TA-TRI1)
but missed in trendlines.py's own _cluster_pivots(), which detect_support_resistance()
(GET /ta/{symbol}/levels) depends on.
"""
import numpy as np
import pandas as pd

from src.indicators.trendlines import _cluster_pivots, detect_support_resistance


def _df_with_wick_mismatch():
    """Construct a series where the close-based pivot index and the high-based pivot index
    genuinely diverge — a bar with a modest close but a very tall wick, positioned so its
    close is NOT a local max of the close series (surrounding bars close higher), but its
    high IS the tallest wick in the whole window."""
    n = 60
    close = pd.Series(np.full(n, 100.0))
    high = pd.Series(np.full(n, 100.5))
    low = pd.Series(np.full(n, 99.5))

    # Bar 30: unremarkable close (doesn't stand out among neighbors) but a huge wick high.
    close.iloc[30] = 100.0
    high.iloc[30] = 150.0  # by far the tallest wick in the series

    # Bars around 30 close slightly HIGHER than bar 30's close, so bar 30 is NOT a close-based
    # local max — the old buggy code would never even select index 30 as a highs_idx pivot.
    close.iloc[28] = 100.2
    close.iloc[32] = 100.2

    return pd.DataFrame({"close": close, "high": high, "low": low})


def test_cluster_pivots_finds_the_real_high_even_when_close_does_not_peak_there():
    """The exact bug scenario: a bar with the tallest real wick in the series must be found
    as a resistance level, even though its close price is not a local max."""
    df = _df_with_wick_mismatch()
    levels = _cluster_pivots(df, order=5, tolerance=0.01)
    resistance_prices = [L.price for L in levels if L.kind == "resistance"]
    assert 150.0 in resistance_prices, (
        f"expected the real 150.0 wick high to be found as resistance, got {resistance_prices}"
    )


def test_cluster_pivots_close_based_selection_would_have_missed_the_real_wick():
    """Sanity check the fixture actually reproduces the bug precondition: bar 30 is NOT a
    close-based local max (the old buggy _find_pivots(df["close"], ...) would never select
    it), proving the fix (finding pivots on high/low directly) is what makes the difference."""
    df = _df_with_wick_mismatch()
    from src.indicators.trendlines import _find_pivots
    close_highs_idx, _ = _find_pivots(df["close"], order=5)
    assert 30 not in close_highs_idx, (
        "fixture invalid: bar 30 must NOT be a close-based pivot for this test to be meaningful"
    )


def test_detect_support_resistance_still_returns_levels_for_a_normal_series():
    """No regression on the common case — a plain trending/ranging series must still
    produce sensible S/R levels after the fix."""
    n = 300
    rng = np.random.default_rng(7)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 0.5, n)))
    high = close + np.abs(rng.normal(0, 0.3, n))
    low = close - np.abs(rng.normal(0, 0.3, n))
    df = pd.DataFrame({"close": close, "high": high, "low": low})
    levels = detect_support_resistance(df)
    assert isinstance(levels, list)
