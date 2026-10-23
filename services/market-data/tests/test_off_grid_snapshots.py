"""Tracker 457: yfinance's current-quote row is excluded from the candle series, never rounded."""
import pathlib
from datetime import datetime

from src.services.ingestion import exclude_off_grid_snapshots, is_off_grid

ING = (pathlib.Path(__file__).resolve().parents[1] / "src/services/ingestion.py").read_text()


def _row(ts, px=10.0, vol=0.0):
    return {"ts": ts, "open": px, "high": px, "low": px, "close": px, "volume": vol}


def test_the_oct_22_shapes_are_excluded_and_real_candles_kept():
    rows = [_row(datetime(2026, 10, 22, 13, 30), vol=1000), _row(datetime(2026, 10, 22, 13, 30, 1)),
            _row(datetime(2026, 10, 22, 1, 54)), _row(datetime(2026, 10, 22, 8, 39, 54)),
            _row(datetime(2026, 10, 22, 13, 35), vol=900)]
    kept, dropped = exclude_off_grid_snapshots(rows, "5m")
    assert [r["ts"] for r in kept] == [datetime(2026, 10, 22, 13, 30), datetime(2026, 10, 22, 13, 35)]
    assert len(dropped) == 3
    # The genuine 09:30 candle is untouched — nothing was rounded onto it.
    assert kept[0]["volume"] == 1000


def test_daily_and_hourly_rows_follow_their_own_grid():
    assert exclude_off_grid_snapshots([_row(datetime(2026, 10, 22))], "1d")[1] == []
    assert is_off_grid(datetime(2026, 10, 22, 13, 30), "1h") is False          # US hourly starts at :30
    assert is_off_grid(datetime(2026, 10, 22, 13, 30, 5), "1h") is True
    assert is_off_grid(datetime(2026, 10, 22, 13, 45), "15m") is False
    assert is_off_grid(datetime(2026, 10, 22, 13, 50), "15m") is True


def test_snapshots_are_removed_before_authority_and_the_write():
    i = ING.index("rows, _snapshots = exclude_off_grid_snapshots(rows, timeframe)")
    assert i < ING.index("authorize_bar_writes(\n", i) and i < ING.index("pg_insert(Price).values(rows)", i)


def test_hk_on_grid_candles_are_kept_and_an_all_snapshot_fetch_writes_nothing():
    hk = [_row(datetime(2026, 10, 22, 1, 30), vol=500), _row(datetime(2026, 10, 22, 1, 54))]   # 09:30 kept, 09:54 quote dropped
    kept, dropped = exclude_off_grid_snapshots(hk, "5m")
    assert [r["ts"] for r in kept] == [datetime(2026, 10, 22, 1, 30)] and len(dropped) == 1
    premarket_quotes = [_row(datetime(2026, 10, 22, 8, 39, 54)), _row(datetime(2026, 10, 22, 8, 41, 17))]
    assert exclude_off_grid_snapshots(premarket_quotes, "5m") == ([], premarket_quotes)


def test_the_excluded_count_is_reported_on_both_return_paths():
    assert ING.count('"off_grid_excluded": len(_snapshots)') == 2
    assert ING.count("off_grid_excluded=len(_snapshots)") == 2
