"""Smoke tests for OHLCV validation."""
import pandas as pd

from src.services.ingestion import validate_ohlcv


def test_drops_invalid_high_low():
    df = pd.DataFrame(
        [
            {"ts": "2024-01-01", "open": 10, "high": 9, "low": 8, "close": 9, "volume": 100, "adj_close": 9},
            {"ts": "2024-01-02", "open": 10, "high": 12, "low": 8, "close": 11, "volume": 100, "adj_close": 11},
        ]
    )
    out = validate_ohlcv(df, "TEST")
    assert len(out) == 1
    assert out.iloc[0]["close"] == 11


def test_drops_negative_prices():
    df = pd.DataFrame(
        [{"ts": "2024-01-01", "open": -1, "high": 2, "low": 0, "close": 1, "volume": 100, "adj_close": 1}]
    )
    assert validate_ohlcv(df, "TEST").empty


# ── T230-CHARTING-PREMARKET: allow_zero_volume ────────────────────────────────
# yfinance's prepost=True intraday bars commonly report volume=0 for real pre/post-market
# trades. Without allow_zero_volume, every extended-hours bar was silently dropped by the
# strict volume>0 check below — discovered via a real production ingest (342/576 fetched
# AAPL 5m bars dropped, all zero-volume, all outside 9:30-16:00 ET).

def test_zero_volume_bar_dropped_by_default():
    df = pd.DataFrame(
        [{"ts": "2024-01-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 0, "adj_close": 10}]
    )
    assert validate_ohlcv(df, "TEST").empty


def test_zero_volume_bar_kept_when_allowed():
    df = pd.DataFrame(
        [{"ts": "2024-01-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 0, "adj_close": 10}]
    )
    out = validate_ohlcv(df, "TEST", allow_zero_volume=True)
    assert len(out) == 1


def test_allow_zero_volume_still_drops_bad_invariants():
    # allow_zero_volume must only relax the volume check — every other invariant (high>=low
    # etc.) still applies to extended-hours bars.
    df = pd.DataFrame(
        [{"ts": "2024-01-01", "open": 10, "high": 9, "low": 8, "close": 9, "volume": 0, "adj_close": 9}]
    )
    assert validate_ohlcv(df, "TEST", allow_zero_volume=True).empty
