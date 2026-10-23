"""Expected candle grid for a regular session, and completeness measured against it (457).

Row counts are not completeness: an extra off-grid snapshot row can hide a missing on-grid
candle (79 rows = 78 + 1 snapshot, or 77 valid + 2 snapshots). Completeness is therefore the
set of EXPECTED grid timestamps present, with off-grid rows reported separately.

The grid comes from the exchange calendar: an NYSE early close (13:00 ET) has 42 five-minute
candles, not 78; HKEX has a lunch break (12:00-13:00 HKT) and 66 candles.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from common.market_calendar import _HK_SESSION, session_bounds

_TZ = {"US": ZoneInfo("America/New_York"), "HK": ZoneInfo("Asia/Hong_Kong")}
# HK lunch break from the calendar's own session spans (morning end -> afternoon start).
_HK_LUNCH = (divmod(_HK_SESSION[0][1], 60), divmod(_HK_SESSION[1][0], 60))
_MINUTES = {"5m": 5, "15m": 15, "1h": 60}


def expected_grid(market: str, day: date, timeframe: str = "5m") -> list[datetime]:
    """UTC-aware candle open times expected for `day`'s regular session ([] if no session)."""
    bounds = session_bounds(market, day)
    if bounds is None:
        return []
    step = timedelta(minutes=_MINUTES[timeframe])
    open_, close = bounds
    out, t = [], open_
    tz = _TZ["HK" if market.upper() == "HK" else "US"]
    lunch = None
    if market.upper() == "HK":
        lunch = (datetime(day.year, day.month, day.day, *_HK_LUNCH[0], tzinfo=tz),
                 datetime(day.year, day.month, day.day, *_HK_LUNCH[1], tzinfo=tz))
    while t + step <= close:
        if not (lunch and lunch[0] <= t < lunch[1]):
            out.append(t.astimezone(timezone.utc))
        t += step
    return out


def completeness(rows_ts, market: str, day: date, timeframe: str = "5m", until: datetime | None = None) -> dict:
    """Present/missing grid candles and off-grid rows for one symbol-session.

    `rows_ts`: stored candle timestamps (naive UTC or aware) for that symbol and timeframe.
    `until`: only candles COMPLETED by then are expected (a midday check does not count the
    afternoon as missing).
    """
    grid = expected_grid(market, day, timeframe)
    if until is not None:
        step = timedelta(minutes=_MINUTES[timeframe])
        grid = [g for g in grid if g + step <= until.astimezone(timezone.utc)]
    grid_set = {g.replace(tzinfo=None) for g in grid}
    stored = {(t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t) for t in rows_ts}
    if grid:
        lo, hi = min(grid_set), max(grid_set) + timedelta(minutes=_MINUTES[timeframe])
        stored = {t for t in stored if lo <= t < hi}
    missing = sorted(grid_set - stored)
    off_grid = sorted(stored - grid_set)
    return {"expected": len(grid_set), "present": len(grid_set) - len(missing), "missing": missing,
            "off_grid": off_grid,
            "final_candle_missing": bool(grid) and max(grid_set) in set(missing),
            "last_expected": max(grid_set) if grid_set else None}
