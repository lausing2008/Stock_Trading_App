"""Tests for T257-OVERNIGHT-FLOW-BRIEF Phase 2's compute_options_flow() — the aggregation
logic that mirrors GET /{symbol}/options-flow's own math (routes.py's get_options_flow()) so
the live endpoint and this EOD-persisted snapshot never silently disagree.

options_flow_snapshot.py imports `db` (OptionsFlowSnapshot) and `sqlalchemy.dialects.postgresql`
at module level for its DB-facing functions — conftest.py already stubs both as MagicMock for
the whole test session, so the module imports cleanly; only compute_options_flow() itself (pure
aggregation over a mocked yfinance.Ticker, no DB dependency) is exercised here.
upsert_options_flow_snapshot()/get_latest_options_flow() are not covered — thin DB-facing glue
with nothing to unit-test meaningfully against a MagicMock session, matching the established
precedent in test_volume_area.py for the sibling VolumeAreaLevel table.
"""
from unittest.mock import MagicMock, patch

import pandas as pd

from src.services.options_flow_snapshot import compute_options_flow


def _make_chain(calls_rows: list[dict], puts_rows: list[dict]):
    """Build a fake yfinance option_chain()-shaped object with real pandas DataFrames —
    matching the real chain.calls/chain.puts column names get_options_flow() itself reads
    (volume, openInterest, lastPrice, strike, impliedVolatility, inTheMoney)."""
    cols = ["volume", "openInterest", "lastPrice", "strike", "impliedVolatility", "inTheMoney"]
    calls_df = pd.DataFrame(calls_rows, columns=cols) if calls_rows else pd.DataFrame(columns=cols)
    puts_df = pd.DataFrame(puts_rows, columns=cols) if puts_rows else pd.DataFrame(columns=cols)
    chain = MagicMock()
    chain.calls = calls_df
    chain.puts = puts_df
    return chain


def _make_ticker(expiries: list[str], chains: dict):
    """chains: {expiry: (calls_rows, puts_rows)}"""
    ticker = MagicMock()
    ticker.options = expiries
    ticker.option_chain.side_effect = lambda exp: _make_chain(*chains[exp])
    return ticker


def _row(volume, oi, last_price, strike=100.0, iv=0.5, itm=False):
    return {"volume": volume, "openInterest": oi, "lastPrice": last_price, "strike": strike,
            "impliedVolatility": iv, "inTheMoney": itm}


def test_no_options_listed_returns_none():
    ticker = _make_ticker([], {})
    with patch("yfinance.Ticker", return_value=ticker):
        assert compute_options_flow("XYZ") is None


def test_zero_volume_on_all_contracts_returns_none():
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(0, 100, 1.0)], [_row(0, 100, 1.0)]),
    })
    with patch("yfinance.Ticker", return_value=ticker):
        assert compute_options_flow("XYZ") is None


def test_call_heavy_flow_produces_strongly_bullish_sentiment():
    # 500 call volume vs 100 put volume -> cp_ratio = 5.0, sufficient_put_vol (>=100) -> strongly_bullish
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(500, 600, 2.0)], [_row(100, 150, 1.0)]),
    })
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.cp_ratio == 5.0
    assert result.call_volume == 500
    assert result.put_volume == 100
    assert result.sentiment == "strongly_bullish"


def test_put_heavy_flow_produces_bearish_sentiment():
    # 50 call volume vs 200 put volume -> cp_ratio = 0.25 -> bearish
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(50, 100, 1.0)], [_row(200, 250, 2.0)]),
    })
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.cp_ratio == 0.25
    assert result.sentiment == "bearish"


def test_cp_ratio_is_capped_at_10():
    # 5000 call volume vs 1 put volume -> raw ratio 5000, must cap at 10.0
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(5000, 6000, 1.0)], [_row(1, 5, 0.5)]),
    })
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.cp_ratio == 10.0


def test_near_zero_put_volume_does_not_falsely_declare_bullish():
    """A near-zero put volume usually means illiquid options, not extreme bullishness —
    sufficient_put_vol (>=100) must gate the bullish/bearish tiers, matching get_options_flow()'s
    own comment on this exact reasoning."""
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(500, 600, 2.0)], [_row(2, 10, 0.5)]),  # put_volume=2, well under 100
    })
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.sentiment == "neutral"  # NOT strongly_bullish, despite a huge raw cp_ratio


def test_call_premium_and_put_premium_are_aggregated_across_the_full_chain():
    """call_premium/put_premium are the two fields get_options_flow() does NOT already
    aggregate (it only tracks per-contract premium inside its own top-10 "unusual activity"
    list) — this must sum volume * lastPrice * 100 across EVERY contract, not just the
    "unusual" ones."""
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": (
            [_row(100, 200, 2.0), _row(50, 100, 1.0)],   # calls: 100*2*100 + 50*1*100 = 25000
            [_row(150, 200, 1.5)],                          # puts: 150*1.5*100 = 22500
        ),
    })
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.call_premium == 25000.0
    assert result.put_premium == 22500.0


def test_whale_detection_uses_the_same_500k_threshold_as_get_options_flow():
    # 1000 volume * 6.0 lastPrice * 100 = 600,000 -> a real whale (>500k)
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(1000, 1200, 6.0)], [_row(100, 150, 1.0)]),
    })
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.whale_count == 1
    assert result.top_whale_premium == 600_000.0


def test_no_whale_when_all_premiums_are_below_threshold():
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(10, 50, 1.0)], [_row(10, 50, 1.0)]),
    })
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.whale_count == 0
    assert result.top_whale_premium == 0.0


def test_fetch_error_fails_open_returns_none():
    with patch("yfinance.Ticker", side_effect=RuntimeError("rate limited")):
        assert compute_options_flow("XYZ") is None


def test_a_single_expiry_fetch_failure_does_not_abort_the_whole_computation():
    """One bad expiry (e.g. a transient fetch error for that specific date) must not abort
    aggregation across the other, successfully-fetched expiries."""
    ticker = MagicMock()
    ticker.options = ["2026-08-15", "2026-08-22"]

    def _side_effect(exp):
        if exp == "2026-08-15":
            raise RuntimeError("transient error for this expiry")
        return _make_chain([_row(500, 600, 2.0)], [_row(100, 150, 1.0)])

    ticker.option_chain.side_effect = _side_effect
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.call_volume == 500
    assert result.put_volume == 100
