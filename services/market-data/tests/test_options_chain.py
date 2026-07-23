"""Tests for T230-DATA-OPTIONS-CHAIN's GET /stocks/{symbol}/options-chain.

CORRECTION to this tracker item's original claim: no paid Polygon.io tier is needed — the
existing GET /stocks/{symbol}/options-flow already calls yfinance's t.option_chain(exp) and
fetches the full calls/puts DataFrames, then discards almost all of it into a top-3-per-side
"unusual activity" summary. This new endpoint exposes the full strike matrix for one expiry
instead, reusing the exact same yfinance data source.

routes.py can't be imported directly in this test environment (its import chain pulls in
common.config/db, none of which conftest.py stubs for real) — _options_chain_rows()'s real
source is extracted and exec()'d against a real pandas DataFrame, matching this repo's
established source-text-extraction technique for functions in files with this exact
import constraint (see test_backfill_realized_ev.py's docstring for the same reasoning).
Real pandas is used (not mocked) so this test exercises the actual sort_values/fillna
behavior, not a hand-copied re-implementation that could silently drift from it.
"""
import pathlib

import pandas as pd

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_options_chain_rows():
    start = _ROUTES_SOURCE.index("def _options_chain_rows(")
    end = _ROUTES_SOURCE.index('\n@router.get("/{symbol}/options-chain")', start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace = {"pd": pd}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_options_chain_rows"]


_options_chain_rows = _extract_options_chain_rows()


def _make_chain_df(**cols):
    return pd.DataFrame(cols)


# ── source-text checks: the endpoint + reused data source really exist ────────────────────────

def test_endpoint_is_registered():
    assert '@router.get("/{symbol}/options-chain")' in _ROUTES_SOURCE
    assert "def get_options_chain(" in _ROUTES_SOURCE


def test_endpoint_reuses_the_same_yfinance_option_chain_call_as_options_flow():
    """The whole point of this fix: no new/paid data source — same t.option_chain(exp) call
    get_options_flow() already makes, just not thrown away this time."""
    start = _ROUTES_SOURCE.index("def get_options_chain(")
    end = _ROUTES_SOURCE.index("\n@router.get", start + 1)
    body = _ROUTES_SOURCE[start:end]
    assert "t.option_chain(exp)" in body
    assert "yf.Ticker(sym)" in body


# ── behavioral checks against the real, extracted _options_chain_rows() ───────────────────────

def test_rows_are_sorted_by_strike_ascending():
    df = _make_chain_df(
        strike=[110, 90, 100], bid=[0.5, 5.0, 2.0], ask=[0.6, 5.2, 2.1],
        lastPrice=[0.55, 5.1, 2.05], volume=[10, 20, 30], openInterest=[100, 200, 300],
        impliedVolatility=[0.30, 0.45, 0.38], inTheMoney=[False, True, False],
    )
    rows = _options_chain_rows(df)
    assert [r["strike"] for r in rows] == [90.0, 100.0, 110.0]


def test_field_mapping_and_iv_is_converted_to_a_percent():
    df = _make_chain_df(
        strike=[100], bid=[1.5], ask=[1.6], lastPrice=[1.55], volume=[42],
        openInterest=[500], impliedVolatility=[0.357], inTheMoney=[True],
    )
    row = _options_chain_rows(df)[0]
    assert row == {
        "strike": 100.0, "bid": 1.5, "ask": 1.6, "last_price": 1.55,
        "volume": 42, "oi": 500, "iv": 35.7, "itm": True,
    }


def test_nan_values_degrade_to_zero_not_crash():
    """A thinly-traded contract can have NaN bid/ask/volume — must not raise or leak a NaN
    into the JSON response (float('nan') is not valid JSON, same class of bug already fixed
    once this session for updown_vol_ratio's Infinity case)."""
    df = _make_chain_df(
        strike=[50], bid=[float("nan")], ask=[float("nan")], lastPrice=[float("nan")],
        volume=[float("nan")], openInterest=[float("nan")], impliedVolatility=[float("nan")],
        inTheMoney=[False],
    )
    row = _options_chain_rows(df)[0]
    assert row["bid"] == 0.0
    assert row["ask"] == 0.0
    assert row["volume"] == 0
    assert row["oi"] == 0
    assert row["iv"] == 0.0


def test_empty_dataframe_returns_empty_list():
    df = _make_chain_df(
        strike=[], bid=[], ask=[], lastPrice=[], volume=[], openInterest=[],
        impliedVolatility=[], inTheMoney=[],
    )
    assert _options_chain_rows(df) == []


def test_itm_field_is_a_real_bool_not_a_numpy_bool():
    """json.dumps() chokes on numpy.bool_ — must be a plain Python bool."""
    df = _make_chain_df(
        strike=[100], bid=[1.0], ask=[1.1], lastPrice=[1.05], volume=[10],
        openInterest=[100], impliedVolatility=[0.3], inTheMoney=[True],
    )
    row = _options_chain_rows(df)[0]
    assert row["itm"] is True
    assert isinstance(row["itm"], bool)


def test_volume_and_oi_are_plain_ints_not_floats():
    df = _make_chain_df(
        strike=[100], bid=[1.0], ask=[1.1], lastPrice=[1.05], volume=[10.0],
        openInterest=[500.0], impliedVolatility=[0.3], inTheMoney=[False],
    )
    row = _options_chain_rows(df)[0]
    assert row["volume"] == 10
    assert isinstance(row["volume"], int)
    assert row["oi"] == 500
    assert isinstance(row["oi"], int)
