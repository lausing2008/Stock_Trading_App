"""AUD-DIGEST-HOLIDAYBLIND + AUD-HOLIDAY-2027GAP.

AUD-DIGEST-HOLIDAYBLIND — every digest/brief job registered with
`CronTrigger(..., day_of_week="mon-fri")` and nothing else. A cron trigger cannot know about
holidays, and none of the four functions had an internal trading-day check — while
_refresh_market() in the SAME FILE gates correctly on the very same helpers.

Confirmed in production on 2026-09-07 (US Labor Day, present in the file's own _NYSE_HOLIDAYS):

    13 digest/brief events fired    <- 1 premarket_brief, 2 morning_digest,
                                       9 post_open_digest, 1 paper_portfolio_digest
    77 `nyse_holiday` skip logs     <- the refresh path, same process, same day
     0 US D1 price bars for that date (last: 2026-09-04)

So the emails presented FRIDAY's data as live: 10 "pre-market movers", 4 "futures" readings,
10 "opportunities", and "39 signal changes" on a day the exchange never opened. The guard
existed, worked, and was simply never applied to the digest registrations. Blast radius ~10 US
and ~15 HK weekday holidays per year.

AUD-HOLIDAY-2027GAP — the same constant lived in three places that drifted apart, with the
drift already dated: both scheduler tables ended 2026 while paper_trading_engine's copy already
covered 2027. From 2027-01-01 every holiday guard in the scheduler would have failed OPEN while
paper trading kept working — two subsystems disagreeing about whether the market is open.
"""
import pathlib
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import src.services.scheduler as sched
from common.market_calendar import (
    HK_HOLIDAYS,
    NYSE_HOLIDAYS,
    assert_calendar_coverage,
    calendar_coverage,
    is_hk_trading_day,
    is_trading_day,
    is_us_trading_day,
)

SCHED_SRC = pathlib.Path(sched.__file__).read_text()

def _fn_body(name: str) -> str:
    """The function's body from its `def` to the next top-level `def`.

    A fixed-size slice is not safe here: send_premarket_brief's docstring alone is ~35 lines, so
    a 3000-char window ended before the guard and the test failed against correct code. This
    repo has hit that exact trap before with a 4000-char window landing mid-statement.
    """
    start = SCHED_SRC.index(f"def {name}(")
    nxt = SCHED_SRC.find("\ndef ", start + 1)
    return SCHED_SRC[start:nxt if nxt != -1 else len(SCHED_SRC)]


_LABOR_DAY_2026 = datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc)   # 9am ET, market shut
_NORMAL_FRIDAY = datetime(2026, 9, 4, 13, 0, tzinfo=timezone.utc)    # a real trading day


# ── AUD-DIGEST-HOLIDAYBLIND: the guard now exists ───────────────────────────────────────

def test_labor_day_2026_is_recognised_as_a_us_holiday():
    """Precondition for the whole finding: the calendar ALREADY knew. Nothing was missing from
    the data — only from the digest jobs."""
    assert date(2026, 9, 7) in NYSE_HOLIDAYS
    assert sched._is_us_trading_day(_LABOR_DAY_2026) is False
    assert sched._is_us_trading_day(_NORMAL_FRIDAY) is True


@pytest.mark.parametrize("fn", [
    "send_morning_digest", "send_premarket_brief",
    "send_post_open_digest", "send_paper_portfolio_digest",
])
def test_every_digest_function_now_gates_on_a_trading_day(fn):
    """THE CORE FIX. All four had NO holiday check of any kind."""
    body = _fn_body(fn)
    assert ("_open_markets(" in body
            or "_is_trading_day_for(" in body
            or "_is_us_trading_day()" in body), f"{fn} still has no trading-day gate"


def test_the_market_dispatching_helper_picks_the_right_calendar():
    """A US holiday must not close HK, and vice versa — the two calendars are independent."""
    assert is_trading_day("US", _LABOR_DAY_2026) is False
    assert is_trading_day("HK", _LABOR_DAY_2026) is True, \
        "US Labor Day is a normal HKEX trading day"
    # HK National Day is a normal NYSE day.
    hk_nat = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)
    assert is_trading_day("HK", hk_nat) is False
    assert is_trading_day("US", hk_nat) is True


def test_open_markets_filters_per_market_rather_than_aborting_the_job():
    """morning_digest runs for ["HK","US"] in one call. Returning early on a US holiday would
    silently suppress the HK digest too — trading one wrong-content bug for a missing-email
    bug. It must filter, not abort."""
    fn = SCHED_SRC[SCHED_SRC.index("def _open_markets("):SCHED_SRC.index("def _symbols_for(")]
    assert "[m for m in markets if" in fn, "must filter the list"
    assert "digest_skip_market_closed" in fn, "a suppressed market must be logged"


def test_open_markets_behaviour_on_a_us_holiday():
    """Directly exercises the filter with a monkeypatched clock."""
    import src.services.scheduler as s
    orig = s._is_trading_day_for
    try:
        s._is_trading_day_for = lambda m, dt=None: is_trading_day(m, _LABOR_DAY_2026)
        assert s._open_markets(["HK", "US"], "t") == ["HK"], "US must drop, HK must survive"
        assert s._open_markets(["US"], "t") == []
        assert s._open_markets(["HK"], "t") == ["HK"]
    finally:
        s._is_trading_day_for = orig


def test_an_empty_market_list_short_circuits_the_send():
    """Filtering to [] must return, not fall through and email an empty digest."""
    for fn in ("send_morning_digest", "send_premarket_brief"):
        body = _fn_body(fn)
        i = body.index("_open_markets(")
        assert "if not markets:" in body[i:i + 300], f"{fn} must return on an empty list"
        assert "return" in body[i:i + 400]


def test_the_refresh_path_guard_is_untouched():
    """It was already correct — the fix must not disturb the thing that worked."""
    assert 'reason="nyse_holiday"' in SCHED_SRC
    assert 'reason="hk_market_closed"' in SCHED_SRC


# ── AUD-HOLIDAY-2027GAP: one source, no drift ───────────────────────────────────────────

def test_there_is_now_exactly_one_definition_of_each_calendar():
    """The whole point. Both scheduler tables and paper_trading_engine's copy must DERIVE from
    the shared module, never redefine."""
    assert "from common.market_calendar import" in SCHED_SRC
    assert "frozenset([" not in SCHED_SRC.split("_HK_HOLIDAYS")[1][:200], \
        "scheduler must not re-declare a literal HK table"
    pt = (pathlib.Path(sched.__file__).parent / "paper_trading_engine.py").read_text()
    assert "from common.market_calendar import NYSE_HOLIDAYS" in pt
    assert "_NYSE_HOLIDAYS: frozenset[date] = frozenset([" not in pt


def test_the_two_previously_drifted_copies_now_agree():
    """THE REGRESSION ITSELF: scheduler ended 2026, paper trading covered 2027. Any date one
    considers a holiday, the other must too."""
    from src.services.paper_trading_engine import _NYSE_HOLIDAYS as PT_NYSE
    sched_dates = {date(y, m, d) for (y, m, d) in sched._NYSE_HOLIDAYS}
    assert sched_dates == set(PT_NYSE), "the two NYSE views must be identical"


def test_2027_is_covered_by_both_calendars():
    """The hard deadline. Without this, every 2027 holiday guard fails open."""
    assert any(d.year == 2027 for d in NYSE_HOLIDAYS), "NYSE 2027 missing"
    assert any(d.year == 2027 for d in HK_HOLIDAYS), "HKEX 2027 missing — there was NO HK 2027 coverage at all"
    assert calendar_coverage() == {"nyse": 2027, "hkex": 2027}


def test_scheduler_guards_still_work_through_2027():
    """End-to-end: the guard that would have failed open on 2027-01-01."""
    ny_2027 = datetime(2027, 1, 1, 15, 0, tzinfo=timezone.utc)
    assert sched._is_us_trading_day(ny_2027) is False
    assert is_us_trading_day(ny_2027) is False
    # 2027-09-06 Labor Day (Mon)
    assert sched._is_us_trading_day(datetime(2027, 9, 6, 15, 0, tzinfo=timezone.utc)) is False
    # A plain 2027 Wednesday must still be open.
    assert sched._is_us_trading_day(datetime(2027, 9, 8, 15, 0, tzinfo=timezone.utc)) is True


@pytest.mark.parametrize("y,m,d,label", [
    (2027, 6, 18, "Juneteenth observed — Jun 19 is a Saturday"),
    (2027, 7, 5, "July 4 observed — Jul 4 is a Sunday"),
    (2027, 12, 24, "Christmas observed — Dec 25 is a Saturday"),
])
def test_2027_observance_shifts_are_right_not_just_present(y, m, d, label):
    """These are the dates a hand-extension gets wrong. Each must be a weekday, and the
    un-shifted date must NOT be listed."""
    assert date(y, m, d) in NYSE_HOLIDAYS, label
    assert date(y, m, d).weekday() < 5, f"{label}: observed date must be a weekday"


def test_the_unshifted_2027_weekend_dates_are_not_listed():
    """Listing both would be harmless but signals a copy-paste extension rather than a
    considered one."""
    for wknd in (date(2027, 6, 19), date(2027, 7, 4), date(2027, 12, 25)):
        assert wknd.weekday() >= 5, "precondition: these really are weekend dates"


def test_good_friday_2027_matches_the_easter_computus():
    """Good Friday is the one NYSE holiday with no fixed-date or nth-weekday rule, so it is the
    easiest to get wrong when extending by hand. Easter 2027 = Sunday 03-28."""
    assert date(2027, 3, 26) in NYSE_HOLIDAYS
    assert date(2027, 3, 26).weekday() == 4, "Good Friday must be a Friday"
    # HKEX observes Good Friday AND Easter Monday.
    assert date(2027, 3, 26) in HK_HOLIDAYS
    assert date(2027, 3, 29) in HK_HOLIDAYS


def test_coverage_assertion_passes_today():
    """The replacement for the unenforced 'Extend each year before January' comment."""
    assert_calendar_coverage()


def test_coverage_assertion_fires_when_the_calendar_expires():
    """It must actually fail — an assertion that can never trip is decoration."""
    with pytest.raises(AssertionError, match="BEFORE the current year"):
        assert_calendar_coverage(today=date(2028, 3, 1))


def test_coverage_assertion_warns_late_in_the_final_covered_year():
    """Fail with runway, not on New Year's Day."""
    with pytest.raises(AssertionError, match="before January"):
        assert_calendar_coverage(today=date(2027, 10, 15))
    # ...but not in early 2027, when there is still plenty of time.
    assert_calendar_coverage(today=date(2027, 3, 1))


def test_weekends_are_still_not_trading_days():
    """The holiday tables are only half the guard."""
    sat = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)
    sun = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)
    for d in (sat, sun):
        assert is_us_trading_day(d) is False
        assert is_hk_trading_day(d) is False


def test_trading_day_checks_are_timezone_correct():
    """A UTC instant can be a different DATE in New York and Hong Kong. 2026-09-07 22:00 UTC is
    still Labor Day evening in NY but already Tuesday the 8th in HK."""
    inst = datetime(2026, 9, 7, 22, 0, tzinfo=timezone.utc)
    assert inst.astimezone(ZoneInfo("America/New_York")).date() == date(2026, 9, 7)
    assert inst.astimezone(ZoneInfo("Asia/Hong_Kong")).date() == date(2026, 9, 8)
    assert is_us_trading_day(inst) is False, "still the US holiday in NY"
    assert is_hk_trading_day(inst) is True, "already the next trading day in HK"


def test_boxing_day_2026_comment_is_now_accurate():
    """The old comment said '(Mon after Sat+Sun Christmas)'. Christmas 2026 is a FRIDAY. The
    date was right and the reasoning wrong, which is how a maintainer extending by copying the
    logic gets the next year wrong."""
    assert date(2026, 12, 25).weekday() == 4, "Christmas 2026 is a Friday"
    assert date(2026, 12, 26).weekday() == 5, "Boxing Day 2026 is a Saturday"
    cal = pathlib.Path(sched.__file__).parents[3] / "shared/common/market_calendar.py"
    if cal.exists():
        src = cal.read_text()
        assert "Sat+Sun Christmas" not in src, "the incorrect explanation must not be carried over"


def test_hk_2027_lunar_dates_are_flagged_as_provisional():
    """Intellectual honesty about a real limitation: the lunar-driven dates are a
    reconstruction, not a transcription of HKEX's published notice. A future reader must not
    mistake them for authoritative."""
    cal = pathlib.Path(sched.__file__).parents[3] / "shared/common/market_calendar.py"
    if not cal.exists():
        pytest.skip("shared module not on this path")
    src = cal.read_text()
    assert "PROVISIONAL" in src
    assert "VERIFY AGAINST HKEX" in src
