"""AQ04 — a window has one price basis or it has none.

THE DEFECT, in two services at once. `_adj_close` returned `adj_close.fillna(close)`. Where the
adjusted series has gaps that splices two bases into one series and INVENTS MOVEMENT the market
never made. The audit's witness: a constant raw series [100, 100, 100] with a partial adjusted
series [50, NULL, 50] becomes [50, 100, 50] — a +100% move followed by a -50% move out of a
stock that did not budge.

signal-engine read it for every SMA, MACD, ATR and return. ml-prediction read it for MODEL
TRAINING FEATURES, so there the invention was learned rather than merely displayed.

A SECOND MIXTURE in the same place: indicators needing a range took the adjusted CLOSE beside
the RAW high and low, so a true range was measured between prices that never coexisted.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.price_basis import resolve_ohlc, series, ADJUSTED, RAW  # noqa: E402


def _df(close, adj=None, high=None, low=None, open_=None):
    d = {"close": close}
    if adj is not None:
        d["adj_close"] = adj
    if high is not None:
        d["high"] = high
    if low is not None:
        d["low"] = low
    if open_ is not None:
        d["open"] = open_
    return pd.DataFrame(d)


# ---- the witness ------------------------------------------------------------------------------

def test_a_partial_adjusted_series_does_not_invent_movement():
    """THE AUDIT'S WITNESS, exactly."""
    df = _df([100.0, 100.0, 100.0], adj=[50.0, None, 50.0])
    got = resolve_ohlc(df)
    assert got["basis"] == RAW
    assert list(got["close"]) == [100.0, 100.0, 100.0]
    # The spliced series the old code produced:
    spliced = df["adj_close"].fillna(df["close"]).tolist()
    assert spliced == [50.0, 100.0, 50.0], "this is what used to be returned"
    assert list(got["close"]) != spliced


def test_the_invented_returns_are_gone():
    """Stated as the thing that actually mattered: the derived RETURNS."""
    df = _df([100.0, 100.0, 100.0], adj=[50.0, None, 50.0])
    got = resolve_ohlc(df)["close"].pct_change().dropna()
    assert list(got) == [0.0, 0.0], "a stock that did not move has no returns"
    spliced = df["adj_close"].fillna(df["close"]).pct_change().dropna()
    assert round(spliced.iloc[0], 4) == 1.0 and round(spliced.iloc[1], 4) == -0.5


def test_the_partial_case_says_why_it_fell_back():
    df = _df([100.0, 100.0, 100.0], adj=[50.0, None, 50.0])
    got = resolve_ohlc(df)
    assert got["adjusted_bars"] == 2 and got["total_bars"] == 3
    assert got["adjustment_applied"] is False
    assert "cannot hold two bases" in got["reason"]
    assert "invent movement" in got["reason"]


# ---- the two clean bases ------------------------------------------------------------------------

def test_a_fully_adjusted_window_uses_the_adjusted_basis():
    df = _df([100.0, 100.0], adj=[50.0, 50.0], high=[101.0, 101.0], low=[99.0, 99.0])
    got = resolve_ohlc(df)
    assert got["basis"] == ADJUSTED
    assert list(got["close"]) == [50.0, 50.0]


def test_the_whole_bar_is_restated_not_only_the_close():
    """THE SECOND MIXTURE. An adjusted close beside a raw high measures a range between two
    prices that never coexisted."""
    df = _df([100.0, 100.0], adj=[50.0, 50.0], high=[101.0, 101.0], low=[99.0, 99.0],
             open_=[100.5, 100.5])
    got = resolve_ohlc(df)
    assert list(got["high"]) == [50.5, 50.5], "high moved with the close"
    assert list(got["low"]) == [49.5, 49.5]
    assert list(got["open"]) == [50.25, 50.25]
    # The bar still makes sense: low <= close <= high.
    for i in range(2):
        assert got["low"][i] <= got["close"][i] <= got["high"][i]


def test_a_window_with_no_adjusted_close_uses_raw_and_says_so():
    df = _df([100.0, 101.0], high=[102.0, 103.0], low=[99.0, 100.0])
    got = resolve_ohlc(df)
    assert got["basis"] == RAW
    assert list(got["high"]) == [102.0, 103.0], "raw high is correct ON the raw basis"
    assert "no adjusted close is stored" in got["reason"]
    assert "remain in the series" in got["reason"], "the limitation is disclosed, not patched"


def test_an_all_null_adjusted_column_is_the_same_as_none():
    df = _df([100.0, 101.0], adj=[None, None])
    assert resolve_ohlc(df)["basis"] == RAW


# ---- corporate actions ---------------------------------------------------------------------------

def test_a_split_inside_the_window_is_reported_not_voided():
    """A moving factor is a real event. It is surfaced; it does not make the window unusable,
    because the whole bar is restated consistently."""
    df = _df([100.0, 50.0], adj=[50.0, 50.0], high=[101.0, 51.0], low=[99.0, 49.0])
    got = resolve_ohlc(df)
    assert got["basis"] == ADJUSTED
    assert got["factor_varies"] is True


def test_a_flat_factor_reports_no_action():
    df = _df([100.0, 100.0], adj=[50.0, 50.0])
    assert resolve_ohlc(df)["factor_varies"] is False


@pytest.mark.parametrize("bad", [0.0, None])
def test_an_unusable_close_cannot_produce_a_factor(bad):
    """Dividing by zero or NaN must not silently yield a usable adjusted bar."""
    df = _df([100.0, bad, 100.0], adj=[50.0, 50.0, 50.0])
    got = resolve_ohlc(df)
    assert got["basis"] == RAW, "one unusable bar makes the window partially covered"


# ---- the basis travels with the series ------------------------------------------------------------

def test_the_basis_is_returned_beside_the_series():
    """`_adj_close` returned a BARE SERIES, which is why two services consumed a spliced one
    with nowhere to notice."""
    s, basis = series(_df([100.0, 100.0], adj=[50.0, None]))
    assert basis == RAW and list(s) == [100.0, 100.0]
    s2, basis2 = series(_df([100.0, 100.0], adj=[50.0, 50.0]))
    assert basis2 == ADJUSTED and list(s2) == [50.0, 50.0]


# ---- both services use the one rule ---------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "services/signal-engine/src/generators/signals.py",
    "services/ml-prediction/src/features/builder.py",
])
def test_neither_service_keeps_its_own_splice(path):
    """The same defect existed in two files. One rule, or it comes back in one of them."""
    src = (Path(__file__).resolve().parents[2] / path).read_text()
    assert "price_basis" in src, f"{path} must use the shared rule"
    assert 'fillna(df["close"])' not in src, f"{path} still splices two bases"
    assert "resolve_ohlc" in src
