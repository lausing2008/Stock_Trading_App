"""Tests for T230-CHARTING-PREMARKET's _classify_session() in ingestion.py.

yfinance's prepost=True returns pre-market (04:00-09:30 ET), regular (09:30-16:00 ET), and
post-market (16:00-20:00 ET) bars interleaved in one dataframe for US intraday timeframes.
_classify_session() labels each bar's timestamp so the chart can render extended-hours bars
distinctly. HK has no pre/post-market session — always REGULAR regardless of clock time.
"""
from datetime import datetime

from src.services.ingestion import _classify_session


def test_hk_is_always_regular_regardless_of_time():
    # 02:00 UTC on a Tuesday is 10:00 HKT (regular session) but also would be 22:00 ET the
    # prior day (well outside any US extended-hours window) — HK must ignore US clock logic
    # entirely and just always return REGULAR.
    ts = datetime(2026, 7, 14, 2, 0)
    assert _classify_session(ts, "HK") == "REGULAR"


def test_us_premarket_bar():
    # 08:00 ET = 12:00 UTC (July, EDT = UTC-4)
    ts = datetime(2026, 7, 14, 12, 0)
    assert _classify_session(ts, "US") == "PRE"


def test_us_regular_session_open_bar():
    # 09:30 ET = 13:30 UTC — the exact regular-session open boundary, inclusive
    ts = datetime(2026, 7, 14, 13, 30)
    assert _classify_session(ts, "US") == "REGULAR"


def test_us_regular_session_mid_day_bar():
    # 12:00 ET = 16:00 UTC
    ts = datetime(2026, 7, 14, 16, 0)
    assert _classify_session(ts, "US") == "REGULAR"


def test_us_regular_session_close_boundary_is_exclusive():
    # 16:00 ET = 20:00 UTC — regular session close, should NOT still be REGULAR-via-open-check;
    # the postmarket branch takes over exactly at the boundary.
    ts = datetime(2026, 7, 14, 20, 0)
    assert _classify_session(ts, "US") == "POST"


def test_us_postmarket_bar():
    # 18:00 ET = 22:00 UTC
    ts = datetime(2026, 7, 14, 22, 0)
    assert _classify_session(ts, "US") == "POST"


def test_us_outside_extended_hours_window_falls_back_to_regular():
    # 02:00 ET = 06:00 UTC — before 04:00 ET premarket open entirely; yfinance shouldn't
    # return bars this early, but if it ever does, classify as REGULAR rather than
    # fabricating a session label with no real meaning.
    ts = datetime(2026, 7, 14, 6, 0)
    assert _classify_session(ts, "US") == "REGULAR"


def test_us_premarket_open_boundary_is_inclusive():
    # 04:00 ET = 08:00 UTC — the exact premarket-open boundary
    ts = datetime(2026, 7, 14, 8, 0)
    assert _classify_session(ts, "US") == "PRE"
