"""Single source of truth for NYSE + HKEX holiday calendars.

AUD-HOLIDAY-2027GAP: this module exists because the same constant had been copy-pasted into
three places that then DRIFTED APART, with the drift already live and dated:

    scheduler.py           _NYSE_HOLIDAYS  frozenset[tuple[int,int,int]]  ends 2026-12-25
    scheduler.py           _HK_HOLIDAYS    frozenset[tuple[int,int,int]]  ends 2026-12-28
    paper_trading_engine   _NYSE_HOLIDAYS  frozenset[date]                ends 2027-12-24

Two same-named frozensets, different element types, different coverage. From 2027-01-01 the
scheduler's `_is_us_trading_day()` / `_is_hk_trading_day()` would have returned True on every
2027 holiday — un-gating ingest, signal refresh, and alert checks against a closed market —
while paper trading kept working off the other table. The result would not have been an error
but a DISAGREEMENT between two subsystems about whether the market was open.

Each table carried the comment "Extend each year before January" with no test, alarm, or DQ
check enforcing it. `assert_calendar_coverage()` below replaces that honour system: it is
asserted by the test suite, so the build fails while there is still runway rather than silently
on New Year's Day.

The 2027 NYSE dates were independently verified (weekday + observance shift) rather than
trusted from the copy they came from: Juneteenth 2027-06-19 is a Saturday (observed Fri 06-18),
July 4 is a Sunday (observed Mon 07-05), Christmas is a Saturday (observed Fri 12-24).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

__all__ = [
    "NYSE_HOLIDAYS",
    "HK_HOLIDAYS",
    "MIN_COVERAGE_YEAR",
    "is_us_trading_day",
    "is_hk_trading_day",
    "is_trading_day",
    "calendar_coverage",
    "assert_calendar_coverage",
]

# NYSE. Observance rules: a holiday on Saturday is observed the preceding Friday, on Sunday the
# following Monday. Good Friday is a market holiday though not a federal one.
NYSE_HOLIDAYS: frozenset[date] = frozenset([
    # 2024
    date(2024,  1,  1), date(2024,  1, 15), date(2024,  2, 19), date(2024,  3, 29),
    date(2024,  5, 27), date(2024,  6, 19), date(2024,  7,  4), date(2024,  9,  2),
    date(2024, 11, 28), date(2024, 12, 25),
    # 2025
    date(2025,  1,  1), date(2025,  1, 20), date(2025,  2, 17), date(2025,  4, 18),
    date(2025,  5, 26), date(2025,  6, 19), date(2025,  7,  4), date(2025,  9,  1),
    date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026,  1,  1),  # New Year's Day
    date(2026,  1, 19),  # MLK Day
    date(2026,  2, 16),  # Presidents' Day
    date(2026,  4,  3),  # Good Friday
    date(2026,  5, 25),  # Memorial Day
    date(2026,  6, 19),  # Juneteenth
    date(2026,  7,  3),  # Independence Day observed (Jul 4 = Sat)
    date(2026,  9,  7),  # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
    # 2027 — every date below verified for weekday and observance shift.
    date(2027,  1,  1),  # New Year's Day (Fri)
    date(2027,  1, 18),  # MLK Day (3rd Mon)
    date(2027,  2, 15),  # Presidents' Day (3rd Mon)
    date(2027,  3, 26),  # Good Friday
    date(2027,  5, 31),  # Memorial Day (last Mon)
    date(2027,  6, 18),  # Juneteenth observed (Jun 19 = Sat)
    date(2027,  7,  5),  # Independence Day observed (Jul 4 = Sun)
    date(2027,  9,  6),  # Labor Day (1st Mon)
    date(2027, 11, 25),  # Thanksgiving (4th Thu)
    date(2027, 12, 24),  # Christmas observed (Dec 25 = Sat)
])

# HKEX. Lunar-calendar-driven dates (Lunar New Year, Ching Ming, Buddha's Birthday, Tuen Ng,
# Mid-Autumn, Chung Yeung) shift every year and cannot be computed from a weekday rule — they
# must be taken from HKEX's published calendar.
HK_HOLIDAYS: frozenset[date] = frozenset([
    # 2025
    date(2025,  1,  1), date(2025,  1, 29), date(2025,  1, 30), date(2025,  1, 31),
    date(2025,  4,  4), date(2025,  4, 18), date(2025,  4, 21), date(2025,  5,  1),
    date(2025,  5,  5), date(2025,  5, 31), date(2025,  7,  1), date(2025, 10,  1),
    date(2025, 10,  7), date(2025, 10, 29), date(2025, 12, 25), date(2025, 12, 26),
    # 2026
    date(2026,  1,  1),  # New Year's Day
    date(2026,  2, 17),  # Lunar New Year Day 1
    date(2026,  2, 18),  # Lunar New Year Day 2
    date(2026,  2, 19),  # Lunar New Year Day 3
    date(2026,  2, 20),  # Lunar New Year Day 4 (make-up)
    date(2026,  4,  3),  # Ching Ming Festival + Good Friday (both fall on Apr 3, 2026)
    date(2026,  4,  6),  # Easter Monday
    date(2026,  5,  1),  # Labour Day
    date(2026,  5, 25),  # Buddha's Birthday
    date(2026,  6, 19),  # Tuen Ng Festival
    date(2026,  7,  1),  # HKSAR Establishment Day
    date(2026, 10,  1),  # National Day
    date(2026, 10, 26),  # Chung Yeung Festival
    date(2026, 12, 25),  # Christmas Day (Fri)
    date(2026, 12, 28),  # Boxing Day observed (Mon; Boxing Day Dec 26 = Sat)
    # 2027 — PROVISIONAL, VERIFY AGAINST HKEX BEFORE 2027-01-01.
    #
    # Honest accounting of confidence, because these are not all equal. Good Friday (03-26) and
    # Easter Monday (03-29) are computed and confirmed against the Easter computus
    # (Easter 2027 = Sun 03-28). The fixed-date holidays (01-01, 05-01, 07-01, 10-01, Christmas)
    # follow directly from the calendar. But the LUNAR-driven dates — Lunar New Year, Ching
    # Ming, Buddha's Birthday, Tuen Ng, Mid-Autumn, Chung Yeung — cannot be derived from any
    # weekday rule and are a best reconstruction, not a transcription of HKEX's published
    # notice. They land on plausible weekdays but MUST be reconciled with the official calendar
    # when HKEX publishes it.
    #
    # This is still strictly better than the previous state (no 2027 HK coverage at all, which
    # would have failed OPEN on every 2027 holiday). A slightly-wrong closure date costs one
    # skipped refresh; a missing one runs a full cycle against a shut exchange.
    date(2027,  1,  1),  # New Year's Day (Fri)
    date(2027,  2,  8),  # Lunar New Year Day 1 (Mon)
    date(2027,  2,  9),  # Lunar New Year Day 2 (Tue)
    date(2027,  2, 10),  # Lunar New Year Day 3 (Wed)
    date(2027,  3, 26),  # Good Friday
    date(2027,  3, 29),  # Easter Monday
    date(2027,  4,  5),  # Ching Ming Festival (Mon)
    date(2027,  5,  1),  # Labour Day (Sat) — retained; the weekday guard makes it a no-op
    date(2027,  5, 13),  # Buddha's Birthday (Thu)
    date(2027,  6,  9),  # Tuen Ng Festival (Wed)
    date(2027,  7,  1),  # HKSAR Establishment Day (Thu)
    date(2027,  9, 16),  # Day after Mid-Autumn Festival (Thu)
    date(2027, 10,  1),  # National Day (Fri)
    date(2027, 10, 15),  # Chung Yeung Festival (Fri)
    date(2027, 12, 24),  # Christmas Eve — HKEX half-day, treated as closed for scheduling
    date(2027, 12, 27),  # Christmas Day observed (Dec 25 = Sat)
])

# The last year both calendars are fully populated. assert_calendar_coverage() fails once the
# current year reaches this, so the tables get extended with runway to spare.
MIN_COVERAGE_YEAR = 2027

_NY = ZoneInfo("America/New_York")
_HKT = ZoneInfo("Asia/Hong_Kong")


def is_us_trading_day(dt: datetime | None = None) -> bool:
    """True if the NYSE is open on the given instant's New York date."""
    d = (dt or datetime.now(timezone.utc)).astimezone(_NY).date()
    return d.weekday() < 5 and d not in NYSE_HOLIDAYS


def is_hk_trading_day(dt: datetime | None = None) -> bool:
    """True if HKEX is open on the given instant's Hong Kong date."""
    d = (dt or datetime.now(timezone.utc)).astimezone(_HKT).date()
    return d.weekday() < 5 and d not in HK_HOLIDAYS


def is_trading_day(market: str, dt: datetime | None = None) -> bool:
    """Market-dispatching form. Anything not 'HK' is treated as US."""
    return is_hk_trading_day(dt) if market.upper() == "HK" else is_us_trading_day(dt)


def calendar_coverage() -> dict[str, int]:
    """Latest year each calendar covers — for the DQ gauge and the coverage assertion."""
    return {
        "nyse": max(d.year for d in NYSE_HOLIDAYS),
        "hkex": max(d.year for d in HK_HOLIDAYS),
    }


def assert_calendar_coverage(today: date | None = None) -> None:
    """Raise if either calendar has run out of runway.

    Replaces the "Extend each year before January" comment that had no enforcement. Asserted by
    the test suite, so this fails during development rather than silently un-gating every
    holiday guard on New Year's Day.
    """
    now = today or datetime.now(timezone.utc).astimezone(_NY).date()
    cov = calendar_coverage()
    for name, last_year in cov.items():
        if last_year < now.year:
            raise AssertionError(
                f"{name} holiday calendar ends {last_year}, which is BEFORE the current year "
                f"{now.year} — every holiday guard is now failing open. Extend "
                f"shared/common/market_calendar.py immediately."
            )
        if last_year == now.year and now.month >= 10:
            raise AssertionError(
                f"{name} holiday calendar ends {last_year} and it is already month "
                f"{now.month}. Extend shared/common/market_calendar.py before January."
            )
