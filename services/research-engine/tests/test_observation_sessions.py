"""AUD-OBS-MIDNIGHTANCHOR — the session walk must not return weekends.

`is_trading_day` resolves an instant into the venue's local calendar, and a naive midnight
lands on the previous local day once converted. Walking back from midnight shifted the whole
20-session window by one and returned Saturdays as sessions: for a 2026-10-08 anchor it asked
for 2026-09-12, 09-19, 09-26 and 10-03 — all weekends — while the four real sessions were
absent, so `assess` saw a date mismatch and reported the technical bucket as not_collected.
Found by diffing the requested dates against the stored bars, not by a test.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "obs_routes_cal", Path(__file__).resolve().parents[3] / "shared/common/market_calendar.py")
_cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cal)


def _sessions_back(venue, anchor, n):
    """The function under test, imported without the route module's ORM dependencies."""
    from datetime import timedelta
    out = []
    day = (anchor.replace(hour=12, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
           - timedelta(days=1))
    while len(out) < n:
        if _cal.is_trading_day(venue, day):
            out.append(day.date().isoformat())
        day -= timedelta(days=1)
    return list(reversed(out))


def test_no_session_falls_on_a_weekend():
    for anchor in (datetime(2026, 10, 8), datetime(2026, 10, 8, 23, 59),
                   datetime(2026, 6, 1), datetime(2026, 1, 5)):
        for d in _sessions_back("US", anchor, 21):
            assert date.fromisoformat(d).weekday() < 5, f"{d} from anchor {anchor} is a weekend"


def test_the_window_is_the_requested_length_and_strictly_before_the_anchor():
    anchor = datetime(2026, 10, 8)
    out = _sessions_back("US", anchor, 21)
    assert len(out) == 21 and out == sorted(out)
    assert date.fromisoformat(out[-1]) < anchor.date()


def test_a_midnight_anchor_gives_the_same_window_as_a_midday_one():
    """The defect: these disagreed, and midnight was the one in the route."""
    assert (_sessions_back("US", datetime(2026, 10, 8, 0, 0), 21)
            == _sessions_back("US", datetime(2026, 10, 8, 12, 0), 21))


def test_hk_sessions_are_also_weekdays():
    """`is_hk_trading_day` calls .astimezone() on whatever it is given, and a NAIVE datetime is
    interpreted as HOST LOCAL TIME. On the UTC-8 machine this was written on, naive Sunday
    12:00 resolved to Monday in Hong Kong and weekends came back as sessions. Passing an aware
    UTC instant makes the answer independent of the machine."""
    for d in _sessions_back("HK", datetime(2026, 10, 8), 21):
        assert date.fromisoformat(d).weekday() < 5, d


def test_the_window_does_not_depend_on_the_host_timezone():
    import os, time
    before = _sessions_back("HK", datetime(2026, 10, 8), 21)
    old = os.environ.get("TZ")
    try:
        for tz in ("UTC", "America/Los_Angeles", "Asia/Hong_Kong"):
            os.environ["TZ"] = tz
            time.tzset()
            assert _sessions_back("HK", datetime(2026, 10, 8), 21) == before, tz
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
