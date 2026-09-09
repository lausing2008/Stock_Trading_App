"""AUD-OUTCOMES-CALENDARDAYSTALE — a calendar-day threshold on a trading-day process.

REPORTED BY THE USER: "signal outcome is failed in health check".

The alert was firing correctly against its own rule, and **nothing was broken**. Last evaluation
was Fri 2026-09-04; the check then counted Sat, Sun, and **Labor Day Mon 09-07** as elapsed and
tripped its `> 3` calendar-day threshold on Tue 09-08.

The pipeline was verified healthy at the time: calling the endpoint the way the scheduler does
(with the service token) returns `200 OK` with

    evaluated: 0    skipped_open: 740    skipped_no_price: 5    failed: 0

i.e. **740 signals were still inside their hold window** — nothing was DUE, not anything broken.

The job only runs on the US post-close path, which is itself gated on `_is_us_trading_day()`. So
a calendar-day rule fires on any ordinary long weekend: Fri -> Sat -> Sun -> Mon-holiday is 4
calendar days with absolutely nothing wrong.

SAME CLASS AS `AUD-DIGEST-HOLIDAYBLIND`: a calendar rule applied to a trading-day process. That
one sent 13 emails on Labor Day; this one raised a false alarm about it. Both come from the same
assumption that consecutive dates are consecutive market days.

A FALSE POSITIVE OF MY OWN, worth recording: while investigating I called the endpoint with a
bare `httpx.post` (no header), got a **401**, and briefly concluded I had found a missing
service-to-service auth header — the exact bug class `docs/incidents/service-to-service-auth-headers.md`
documents. That was wrong. `_post()` DOES inject `_service_token()`; my direct call simply did
not. It is the kind of false positive that leads to "fixing" working code.

THE FIX CHANGES THE UNIT, NOT THE TOLERANCE: still 3, now counted in trading days.
"""
from datetime import date

import pytest

import src.services.scheduler as sched


# ── The reported false alarm ────────────────────────────────────────────────────────────

def test_the_exact_scenario_that_fired_is_now_silent():
    """THE BUG. Fri 2026-09-04 -> Tue 2026-09-08, with Labor Day on the Monday: 4 CALENDAR days
    but only 1 TRADING day."""
    elapsed = sched._trading_days_between(date(2026, 9, 4), date(2026, 9, 8))
    assert elapsed == 1
    assert elapsed <= sched._OUTCOME_STALE_TRADING_DAYS, "must not alert on a long weekend"


def test_the_old_calendar_rule_would_have_fired():
    """Pins why the fix was needed, so nobody 'simplifies' it back to calendar days."""
    assert (date(2026, 9, 8) - date(2026, 9, 4)).days == 4
    assert 4 > 3, "the old > 3 calendar-day threshold tripped on a perfectly normal weekend"


def test_an_ordinary_weekend_is_also_silent():
    """Fri -> Mon with no holiday is 3 calendar days and 0 trading days."""
    assert sched._trading_days_between(date(2026, 9, 4), date(2026, 9, 7)) == 0


# ── A real outage must still fire ───────────────────────────────────────────────────────

@pytest.mark.parametrize("start,end,expected_trading", [
    (date(2026, 9, 1), date(2026, 9, 8), 4),    # a week of silence
    (date(2026, 8, 24), date(2026, 9, 8), 10),  # two weeks
])
def test_a_genuine_outage_still_alerts(start, end, expected_trading):
    """The fix must not trade a noisy alert for a silent one — that would be worse."""
    elapsed = sched._trading_days_between(start, end)
    assert elapsed == expected_trading
    assert elapsed > sched._OUTCOME_STALE_TRADING_DAYS


def test_the_tolerance_is_unchanged():
    """3 was the old calendar threshold and is the new trading threshold. Changing BOTH the unit
    and the number in one edit would make any behaviour change impossible to attribute."""
    assert sched._OUTCOME_STALE_TRADING_DAYS == 3


# ── Counting semantics ──────────────────────────────────────────────────────────────────

def test_same_day_is_zero():
    assert sched._trading_days_between(date(2026, 9, 8), date(2026, 9, 8)) == 0


def test_the_start_day_is_excluded_and_the_end_day_counted():
    """"Days since the last evaluation" means days AFTER it — the evaluation day itself is not
    elapsed time."""
    assert sched._trading_days_between(date(2026, 9, 8), date(2026, 9, 9)) == 1


def test_an_end_before_the_start_is_zero_not_negative():
    """Clock skew or a future-dated row must not produce a negative that silently disables the
    alert by comparing < threshold."""
    assert sched._trading_days_between(date(2026, 9, 8), date(2026, 9, 1)) == 0


def test_holidays_inside_a_long_gap_are_excluded():
    """Thanksgiving week: Thu 11-26 is a holiday, so Mon 11-23 -> Mon 11-30 is 7 calendar days
    but only 4 trading days."""
    assert (date(2026, 11, 30) - date(2026, 11, 23)).days == 7
    assert sched._trading_days_between(date(2026, 11, 23), date(2026, 11, 30)) == 4


# ── It must use the shared calendar, not a private copy ─────────────────────────────────

def test_it_uses_the_shared_market_calendar():
    """A private weekday check would drift from the guard that decides whether the job runs at
    all — and this session has already found FOUR copies of the same holiday constant."""
    import pathlib
    src = pathlib.Path(sched.__file__).read_text()
    i = src.index("def _trading_days_between(")
    fn = src[i:src.index("\ndef ", i + 10)]
    assert "from common.market_calendar import is_us_trading_day" in fn
    assert "_NYSE_HOLIDAYS" not in fn, "must not hand-roll a holiday check here"


def test_it_avoids_a_midnight_timezone_flip():
    """is_us_trading_day takes a datetime and resolves it in ET. Passing midnight UTC would land
    on the PREVIOUS ET day and miscount — the same timezone trap as AUD-EXIT-HKENTRYDATE."""
    import pathlib
    src = pathlib.Path(sched.__file__).read_text()
    i = src.index("def _trading_days_between(")
    fn = src[i:src.index("\ndef ", i + 10)]
    assert "12, tzinfo=timezone.utc" in fn, "noon UTC is the same ET date for every date"


def test_both_units_are_logged_when_it_fires():
    """An operator seeing the alert needs to know it is 4 TRADING days, not 4 calendar days —
    otherwise the next person re-derives this same investigation."""
    import pathlib
    src = pathlib.Path(sched.__file__).read_text()
    i = src.index('log.error("outcomes.evaluation_stale"')
    block = src[i:i + 400]
    assert "trading_days_since_last_eval" in block
    assert "calendar_days_since_last_eval" in block


def test_the_CALL_SITE_actually_uses_trading_days():
    """A GAP MY OWN SABOTAGE TESTING EXPOSED.

    Every test above exercises `_trading_days_between` directly, so reverting the CALL SITE back
    to `_days_since_eval = _cal_days` left all 13 passing while restoring the exact false alarm.
    A correct helper nobody calls is worth nothing — pin the wiring, not just the arithmetic.
    """
    import pathlib
    src = pathlib.Path(sched.__file__).read_text()
    i = src.index('log.error("outcomes.evaluation_stale"')
    block = src[max(0, i - 900):i]
    assert "_days_since_eval = _trading_days_between(" in block, (
        "the staleness comparison must use TRADING days, not the calendar-day count"
    )
    assert "_days_since_eval = _cal_days" not in block, (
        "_cal_days is for LOGGING context only — it must never drive the threshold"
    )
    assert "if _days_since_eval > _OUTCOME_STALE_TRADING_DAYS:" in src
