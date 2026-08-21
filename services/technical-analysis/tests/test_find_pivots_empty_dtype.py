"""Regression test for BUG-TALEVELS-EMPTYPIVOTS-FLOATIDX.

_find_pivots() built its highs/lows return values from plain Python lists via
`np.array(highs), np.array(lows)`. numpy's own default dtype inference for an EMPTY list is
float64, not an integer type. _cluster_pivots() then does `df["high"].values[highs_idx]` —
indexing a real array with a float64 array raises a raw, unhandled IndexError ("arrays used as
indices must be of integer (or boolean) type"). This only manifests when the pivot-detection
loop finds ZERO local extrema — most commonly a thin-history stock with fewer than
2*order+1 bars, or (more rarely) a genuinely flat/monotonic price series with no interior
local max/min at all.

Confirmed live in production: GET /ta/{symbol}/levels 500'd repeatedly for SSNLF and SKHYV,
both already flagged elsewhere in this app as possibly-delisted, thin-history symbols.

Fix: `np.array(highs, dtype=int)` / `np.array(lows, dtype=int)` — a no-op for the normal
(non-empty) case, since a real list of Python ints already produces an int64 array regardless
of the explicit dtype hint; this only changes behavior for the empty-list edge case.
"""
import numpy as np
import pandas as pd

from src.indicators.trendlines import _cluster_pivots, _find_pivots, detect_support_resistance


def _too_short_for_any_pivot(order: int = 5) -> pd.DataFrame:
    """Fewer than 2*order+1 bars — _find_pivots()'s own `range(order, n - order)` loop body
    never executes at all, guaranteeing an empty highs/lows result."""
    n = 2 * order  # one bar short of the minimum the loop needs to ever iterate
    return pd.DataFrame({
        "high": np.linspace(10.0, 10.5, n),
        "low": np.linspace(9.5, 10.0, n),
        "close": np.linspace(9.8, 10.2, n),
    })


def _strictly_monotonic_series(n: int = 60) -> pd.DataFrame:
    """A strictly increasing series has no interior local max/min anywhere — every window's
    own max/min is always its rightmost/leftmost element, never the CENTER element the pivot
    check (`vals[i] == window.max()`) requires, so this also guarantees zero pivots found
    despite having plenty of bars."""
    vals = np.arange(1.0, 1.0 + n)
    return pd.DataFrame({"high": vals + 0.5, "low": vals - 0.5, "close": vals})


def test_find_pivots_returns_integer_dtype_even_when_empty():
    df = _too_short_for_any_pivot()
    highs_idx, lows_idx = _find_pivots(df["high"], order=5)
    assert len(highs_idx) == 0
    assert len(lows_idx) == 0
    assert np.issubdtype(highs_idx.dtype, np.integer)
    assert np.issubdtype(lows_idx.dtype, np.integer)


def test_find_pivots_still_returns_integer_dtype_when_non_empty():
    """The fix must not change the dtype for the normal, already-working case."""
    close = pd.Series([1, 2, 3, 2, 1, 2, 3, 4, 3, 2, 1, 0, 1, 2, 3, 4, 5])
    highs_idx, lows_idx = _find_pivots(close, order=2)
    assert len(highs_idx) > 0 or len(lows_idx) > 0
    assert np.issubdtype(highs_idx.dtype, np.integer)
    assert np.issubdtype(lows_idx.dtype, np.integer)


def test_cluster_pivots_does_not_crash_on_a_too_short_history():
    """The exact real production failure mode: a thin-history stock with fewer bars than
    2*order+1 — must return an empty (not crashed) levels list."""
    df = _too_short_for_any_pivot(order=4)
    result = _cluster_pivots(df, order=4, tolerance=0.01)
    assert result == []


def test_cluster_pivots_does_not_crash_on_a_strictly_monotonic_series():
    df = _strictly_monotonic_series()
    result = _cluster_pivots(df, order=5, tolerance=0.01)
    assert result == []


def test_detect_support_resistance_does_not_crash_on_a_too_short_history():
    """End-to-end: the real function GET /ta/{symbol}/levels calls, against the exact input
    shape that crashed in production."""
    df = _too_short_for_any_pivot(order=4)
    # detect_support_resistance's own default order=5 with a too-short df — must not raise.
    result = detect_support_resistance(df, order=4)
    assert isinstance(result, list)
