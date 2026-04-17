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
