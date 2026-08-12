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


# ── AUD265-CPRATIO-CENSORED-BREAKS-RANKING ──────────────────────────────────────────────────

def test_cp_ratio_uncapped_preserves_the_real_ratio_past_the_display_cap():
    """The whole point of this field: a symbol whose real call/put ratio is far past 10.0
    must NOT collapse to the same stored value as one whose real ratio is only slightly past
    10.0 — cp_ratio_uncapped must carry the real, unclamped number through to persistence."""
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(5000, 6000, 1.0)], [_row(1, 5, 0.5)]),
    })
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.cp_ratio == 10.0  # display/sentiment scale — correctly capped
    assert result.cp_ratio_uncapped == 5000.0  # ranking/history scale — the real ratio


def test_cp_ratio_uncapped_matches_capped_when_the_real_ratio_is_already_under_10():
    """Below the cap, both fields must agree exactly — the cap only ever changes the DISPLAY
    value once the real ratio actually exceeds 10.0."""
    ticker = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(500, 600, 2.0)], [_row(100, 150, 1.0)]),
    })
    with patch("yfinance.Ticker", return_value=ticker):
        result = compute_options_flow("XYZ")
    assert result is not None
    assert result.cp_ratio == result.cp_ratio_uncapped == 5.0


def test_sentiment_classification_still_uses_the_capped_scale_not_the_uncapped_one():
    """Two symbols with wildly different uncapped ratios (5000 vs 50) must still classify to
    the SAME sentiment tier, since both clear the 10.0 cap identically — sentiment must never
    start reading the uncapped field, or a future change to it could silently shift tier
    boundaries the sentiment ladder was never calibrated against."""
    ticker_a = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(5000, 6000, 1.0)], [_row(100, 150, 0.5)]),  # cp_ratio_uncapped=50.0
    })
    ticker_b = _make_ticker(["2026-08-15"], {
        "2026-08-15": ([_row(5000, 6000, 1.0)], [_row(500, 600, 1.0)]),  # cp_ratio_uncapped=10.0
    })
    with patch("yfinance.Ticker", return_value=ticker_a):
        result_a = compute_options_flow("XYZ")
    with patch("yfinance.Ticker", return_value=ticker_b):
        result_b = compute_options_flow("XYZ")
    assert result_a is not None and result_b is not None
    assert result_a.cp_ratio_uncapped == 50.0
    assert result_b.cp_ratio_uncapped == 10.0
    assert result_a.cp_ratio == result_b.cp_ratio == 10.0
    assert result_a.sentiment == result_b.sentiment == "strongly_bullish"


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


# ── AUD265-GAMMA-ASSUMES-SORTED-EXPIRIES ────────────────────────────────────────────────────

def test_out_of_order_expiries_still_aggregate_the_nearest_4_not_whatever_order_yfinance_returned():
    """t.options ordering is an undocumented yfinance implementation detail — if it were ever
    NOT chronologically ascending, expiries[:4] on an unsorted list would silently aggregate
    the wrong 4 dates (e.g. skip the true nearest expiry and include a far one instead). Feeds
    5 expiries in a deliberately shuffled, non-ascending order; only the 4 CHRONOLOGICALLY
    nearest should ever be fetched — the 5th (chronologically farthest) must never be touched."""
    ticker = MagicMock()
    # Deliberately out of order: nearest (08-01) is neither first nor last in this list.
    ticker.options = ["2026-09-01", "2026-08-08", "2026-08-01", "2026-08-22", "2026-08-15"]
    fetched_expiries = []

    def _side_effect(exp):
        fetched_expiries.append(exp)
        return _make_chain([_row(100, 100, 1.0)], [_row(100, 100, 1.0)])

    ticker.option_chain.side_effect = _side_effect
    with patch("yfinance.Ticker", return_value=ticker):
        compute_options_flow("XYZ")
    assert sorted(fetched_expiries) == ["2026-08-01", "2026-08-08", "2026-08-15", "2026-08-22"]
    assert "2026-09-01" not in fetched_expiries
