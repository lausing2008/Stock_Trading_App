"""F02 (2026-10-26 RZY/AI review) — every generation records which daily bars it used.

Behavioural, against the REAL shared exchange calendar (this suite's conftest stubs `common`,
so `market_calendar` is loaded by path and installed for the duration of each test).
"""
import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from generators import input_contract as ic  # noqa: E402

_ROOT = Path(__file__).resolve().parents[3]
NY, HK = ZoneInfo("America/New_York"), ZoneInfo("Asia/Hong_Kong")


@pytest.fixture(autouse=True)
def real_calendar(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "common.market_calendar", _ROOT / "shared/common/market_calendar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setitem(sys.modules, "common.market_calendar", mod)
    monkeypatch.setattr(sys.modules["common"], "market_calendar", mod, raising=False)


def _bars(*days: date) -> pd.DataFrame:
    return pd.DataFrame({"ts": [pd.Timestamp(d) for d in days], "close": 1.0})


def _sessions(end: date, n: int = 10) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def test_a_mid_session_us_bar_is_provisional():
    """THE F02 CASE: Monday 11:00 ET, today's row is forming."""
    mon = date(2026, 10, 26)
    got = ic.daily_input_contract(_bars(*_sessions(mon)), "US",
                                  datetime(2026, 10, 26, 11, 0, tzinfo=NY))
    assert got["basis"] == ic.PROVISIONAL and got["last_bar_status"] == "forming"
    assert got["last_bar_date"] == "2026-10-26"
    assert got["last_completed_session"] == "2026-10-23"
    assert got["completed_sessions_missing"] == 0, "Friday is present; nothing is missing"
    assert got["weekly_last_bucket_partial"] is True
    assert got["close_ingest_verified"] is None, "never claimed"


def test_after_the_close_the_same_bar_is_session_closed():
    mon = date(2026, 10, 26)
    got = ic.daily_input_contract(_bars(*_sessions(mon)), "US",
                                  datetime(2026, 10, 26, 16, 30, tzinfo=NY))
    assert got["basis"] == ic.SESSION_CLOSED and got["last_bar_status"] == "completed"
    assert got["last_completed_session"] == "2026-10-26"
    assert got["completed_sessions_missing"] == 0
    assert got["weekly_last_bucket_partial"] is True, "Tue-Fri still to come this week"


def test_friday_close_read_on_the_weekend_completes_the_week():
    fri = date(2026, 10, 23)
    got = ic.daily_input_contract(_bars(*_sessions(fri)), "US",
                                  datetime(2026, 10, 25, 12, 0, tzinfo=NY))
    assert got["basis"] == ic.SESSION_CLOSED
    assert got["completed_sessions_missing"] == 0
    assert got["weekly_last_bucket_partial"] is False


def test_missing_completed_sessions_are_counted_not_called_fresh():
    """The old staleness check allows 3 calendar days; two missed sessions pass it silently."""
    wed = date(2026, 10, 21)
    got = ic.daily_input_contract(_bars(*_sessions(wed)), "US",
                                  datetime(2026, 10, 23, 17, 0, tzinfo=NY))
    assert got["basis"] == ic.SESSION_CLOSED
    assert got["completed_sessions_missing"] == 2


def test_a_forming_bar_is_compared_on_the_previous_bar_for_missing_sessions():
    """Today forming, but yesterday's completed bar absent: one completed session is missing."""
    got = ic.daily_input_contract(_bars(date(2026, 10, 22), date(2026, 10, 26)), "US",
                                  datetime(2026, 10, 26, 11, 0, tzinfo=NY))
    assert got["basis"] == ic.PROVISIONAL
    assert got["completed_sessions_missing"] == 1


def test_hk_lunch_break_is_still_forming_and_close_is_hkt():
    d = date(2026, 10, 27)
    got = ic.daily_input_contract(_bars(*_sessions(d)), "HK",
                                  datetime(2026, 10, 27, 12, 30, tzinfo=HK))
    assert got["basis"] == ic.PROVISIONAL
    assert got["last_bar_session_close"] == "2026-10-27T08:00:00+00:00"
    after = ic.daily_input_contract(_bars(*_sessions(d)), "HK",
                                    datetime(2026, 10, 27, 16, 5, tzinfo=HK))
    assert after["basis"] == ic.SESSION_CLOSED


def test_us_early_close_completes_at_13_00():
    d = date(2026, 11, 27)
    df = _bars(*[x for x in _sessions(d) if x != date(2026, 11, 26)])
    got = ic.daily_input_contract(df, "US", datetime(2026, 11, 27, 13, 5, tzinfo=NY))
    assert got["basis"] == ic.SESSION_CLOSED
    assert got["completed_sessions_missing"] == 0, "Thanksgiving is not a session"


def test_a_future_dated_or_non_session_bar_is_not_given_a_basis():
    fut = ic.daily_input_contract(_bars(date(2026, 10, 27)), "US",
                                  datetime(2026, 10, 26, 20, 0, tzinfo=NY))
    assert fut["basis"] == ic.UNKNOWN and fut["last_bar_status"] == "future"
    sat = ic.daily_input_contract(_bars(date(2026, 10, 24)), "US",
                                  datetime(2026, 10, 26, 20, 0, tzinfo=NY))
    assert sat["basis"] == ic.UNKNOWN and sat["last_bar_status"] == "not_a_session"


def test_an_unreadable_frame_is_unknown_not_fresh():
    for df in (pd.DataFrame(), pd.DataFrame({"ts": [None]})):
        got = ic.daily_input_contract(df, "US", datetime(2026, 10, 26, 20, 0, tzinfo=NY))
        assert got["basis"] == ic.UNKNOWN
        assert got["contract_version"] == ic.CONTRACT_VERSION


def test_the_generator_records_the_contract_in_shared_reasons():
    """Wiring: recorded right after TA, in the shared reasons every style copies."""
    import ast
    src = (Path(__file__).resolve().parents[1] / "src/generators/signals.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "generate_all_signals")
    body = ast.unparse(fn)
    assert "reasons['input_contract'] = daily_input_contract(" in body
    assert body.index("reasons['input_contract']") < body.index("base_reasons={**reasons")
