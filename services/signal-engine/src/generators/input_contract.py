"""F02 (2026-10-26 RZY/AI review) — what daily bars a signal was actually computed from.

The generator reads the last 400 daily bars and computes every indicator from them as given.
During a session the newest bar is the FORMING day: its "close" is the latest price, its
volume partial. That reading is deliberately kept — intraday refreshes are a product — but it
is not a completed-session observation, and nothing recorded which kind a signal was. A
first-fire snapshot taken at 10:30 and one taken after the close were indistinguishable to the
outcome evaluator and to calibration.

This module only DESCRIBES the input; it changes no indicator, score or signal. Recorded per
generation in `reasons["input_contract"]`, so the first-fire snapshot freezes it with the rest.

Daily bars are stored at naive midnight of the venue's own session date (see the HK timestamp
fix in market-data's base adapter), so a bar's session date is `ts.date()`.

What is NOT established here: that the stored close of a closed session was ingested after
the close. The price table records no ingest time, so `session_closed` means the venue's clock
had passed the close — a necessary condition for a final bar, not proof of one.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

CONTRACT_VERSION = "f02-input-v1"
_TZ = {"US": ZoneInfo("America/New_York"), "HK": ZoneInfo("Asia/Hong_Kong")}

PROVISIONAL = "provisional_intraday"
SESSION_CLOSED = "session_closed"
UNKNOWN = "unknown"


def _calendar():
    # Imported lazily: the module must stay importable where `common` is stubbed.
    from common import market_calendar
    return market_calendar


def _last_completed_session(market: str, now: datetime) -> date | None:
    cal = _calendar()
    d = now.astimezone(_TZ[market]).date()
    for _ in range(20):
        close = cal.session_close(market, d)
        if close is not None and now >= close:
            return d
        d -= timedelta(days=1)
    return None


def _sessions_after(market: str, older: date, newer: date) -> int:
    """Sessions in (older, newer]."""
    cal = _calendar()
    n, d = 0, older + timedelta(days=1)
    while d <= newer and n < 60:
        n += cal.session_bounds(market, d) is not None
        d += timedelta(days=1)
    return n


def _session_later_in_week(market: str, d: date) -> bool:
    cal = _calendar()
    end = d + timedelta(days=6 - d.weekday())
    nxt = d + timedelta(days=1)
    while nxt <= end:
        if cal.session_bounds(market, nxt) is not None:
            return True
        nxt += timedelta(days=1)
    return False


def daily_input_contract(df: pd.DataFrame, market: str, now: datetime | None = None) -> dict:
    """Describe the newest daily bar a generation used, against the venue's calendar."""
    now = now or datetime.now(timezone.utc)
    market = "HK" if str(market).upper() == "HK" else "US"
    out = {
        "contract_version": CONTRACT_VERSION,
        "market": market,
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "basis": UNKNOWN,
        "last_bar_date": None,
        "last_bar_status": UNKNOWN,
        "last_bar_session_close": None,
        "last_completed_session": None,
        "completed_sessions_missing": None,
        "weekly_last_bucket_partial": None,
        "close_ingest_verified": None,
    }
    try:
        last = pd.to_datetime(df["ts"]).max()
        if pd.isna(last):
            return out
        bar_date = last.date()
    except Exception:
        return out
    out["last_bar_date"] = bar_date.isoformat()

    try:
        cal = _calendar()
        close = cal.session_close(market, bar_date)
        last_done = _last_completed_session(market, now)
    except Exception:
        return out
    out["last_completed_session"] = last_done.isoformat() if last_done else None

    if close is None:
        # A bar dated on a non-session day (holiday/weekend row) — not a session observation.
        out["last_bar_status"] = "not_a_session"
        return out
    out["last_bar_session_close"] = close.astimezone(timezone.utc).isoformat(timespec="seconds")
    open_ = cal.session_open(market, bar_date)
    if now < open_:
        out["last_bar_status"] = "future"
        return out
    closed = now >= close
    out["last_bar_status"] = "completed" if closed else "forming"
    out["basis"] = SESSION_CLOSED if closed else PROVISIONAL

    # Data availability: completed sessions the frame should hold but does not. A forming bar
    # is today; the comparison is against the newest bar whose session HAS closed.
    newest_closed = bar_date if closed else _previous_bar_date(df, bar_date)
    if last_done is not None and newest_closed is not None:
        out["completed_sessions_missing"] = _sessions_after(market, newest_closed, last_done)

    out["weekly_last_bucket_partial"] = (not closed) or _session_later_in_week(market, bar_date)
    return out


def _previous_bar_date(df: pd.DataFrame, bar_date: date) -> date | None:
    try:
        dates = sorted({t.date() for t in pd.to_datetime(df["ts"])})
        earlier = [d for d in dates if d < bar_date]
        return earlier[-1] if earlier else None
    except Exception:
        return None
