"""T387-LEAPS-HOLIDAY — a non-trading entry date read as a data gap.

REPORTED BY THE USER from a real run: "if holiday, let's pick the next business day instead".

A 2-year hold starting **2024-01-15** returned `n/a` for EVERY symbol, and `_explain_no_price()`
said *"no option chain captured — the archive has no quotes for that date"*. That was true and
misleading: 2024-01-15 is **MLK Day**. The archive was complete; no chain exists for that date
anywhere, because the market was shut.

WORSE, THE TWO MODES DISAGREED. `backtest_leaps_rolling()` still produced results from the same
date, because it advances past dead windows — so a roll and a hold starting on the same
requested day looked like a data problem when the difference was purely calendrical:

    before:  hold 2024-01-15 -> n/a for all 6 symbols
             roll 2024-01-15 -> real results for all 6
    after:   hold 2024-01-15 -> 5 of 6 price (AVGO fails for a REAL T380 contract-gap reason)

THE SHIFT IS REPORTED, NEVER SILENT (`entry_date_requested`, `entry_date_shifted`). Quietly
testing a different date than the one asked for is precisely the substitution T380 and T381
refused elsewhere — the only difference is that here the substitution is unambiguous, because
the requested date is not tradable at all.

USES THE SHARED CALENDAR. This repo has already found FOUR divergent copies of the NYSE holiday
table (`AUD-ENTRY-NYSEHOLIDAY-FOURTHCOPY`); a fifth here would drift the same way.

NOON UTC, deliberately: `is_us_trading_day()` resolves its argument in New York time, so
midnight UTC lands on the PREVIOUS ET day and would mis-classify every date by one — the same
timezone trap as `AUD-EXIT-HKENTRYDATE`.
"""
import pathlib
from datetime import date

import pytest

SRC = (
    pathlib.Path(__file__).resolve().parents[1] / "src/backtest/leaps_backtest.py"
).read_text()


def _fn(name: str) -> str:
    i = SRC.index(f"def {name}(")
    return SRC[i:SRC.index("\ndef ", i + 10)]


# ── The helper ──────────────────────────────────────────────────────────────────────────

def test_the_helper_exists_and_is_bounded():
    assert "def _next_trading_day(" in SRC
    assert "_MAX_ENTRY_SHIFT_DAYS = 5" in SRC


def test_it_uses_the_shared_calendar_not_a_local_copy():
    """FOUR divergent NYSE holiday tables have already been found in this repo. A local
    weekday/holiday check here would be the fifth."""
    assert "from common.market_calendar import is_us_trading_day as _is_us_trading_day" in SRC
    fn = _fn("_next_trading_day")
    assert "NYSE_HOLIDAYS" not in fn, "must not hand-roll a holiday table"
    assert "weekday()" not in fn, "must not hand-roll a weekend check either"


def test_it_passes_noon_utc_not_midnight():
    """is_us_trading_day() resolves in New York time, so midnight UTC lands on the PREVIOUS ET
    day and mis-classifies every date by one — the AUD-EXIT-HKENTRYDATE trap."""
    assert "12, tzinfo=timezone.utc" in _fn("_next_trading_day")


def test_it_returns_the_original_date_when_nothing_is_found():
    """So the caller still fails with a REAL reason rather than silently testing an unrelated
    date five days away."""
    fn = _fn("_next_trading_day")
    assert "    return d\n" in fn


# ── Behaviour, verified live ────────────────────────────────────────────────────────────

def test_mlk_day_moves_to_the_next_open_day():
    """THE REPORTED CASE. Verified live: 2024-01-15 -> 2024-01-16."""
    from datetime import date as _d
    import importlib, sys
    # Exercise the real function rather than a mirror.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from backtest.leaps_backtest import _next_trading_day as f
    assert f(_d(2024, 1, 15)) == _d(2024, 1, 16)


def test_a_weekend_moves_to_monday():
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from backtest.leaps_backtest import _next_trading_day as f
    assert f(date(2024, 1, 13)) == date(2024, 1, 16), "Sat -> Mon, skipping MLK"


def test_a_normal_trading_day_is_unchanged():
    """THE REGRESSION THAT WOULD MATTER MOST — shifting a valid date would silently move every
    backtest by a day."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from backtest.leaps_backtest import _next_trading_day as f
    for d in (date(2024, 1, 16), date(2025, 6, 10), date(2026, 3, 4)):
        assert f(d) == d


@pytest.mark.parametrize("d,expected", [
    (date(2025, 12, 25), date(2025, 12, 26)),   # Christmas -> Friday
    (date(2026, 7, 4), date(2026, 7, 6)),       # Jul 4 (Sat) -> Monday
    (date(2026, 1, 1), date(2026, 1, 2)),       # New Year -> Friday
])
def test_other_real_holidays(d, expected):
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from backtest.leaps_backtest import _next_trading_day as f
    assert f(d) == expected


# ── Both entry paths shift, and both report it ──────────────────────────────────────────

def test_the_single_backtest_shifts_its_entry():
    fn = _fn("backtest_leaps")
    assert "entry_date = _next_trading_day(entry_date)" in fn


def test_the_single_backtest_reports_the_shift():
    """Silently testing a different date than the one requested is the substitution T380/T381
    refused elsewhere."""
    fn = _fn("backtest_leaps")
    assert '"entry_date_requested": _requested_entry.isoformat(),' in fn
    assert '"entry_date_shifted": _requested_entry != entry_date,' in fn


def test_the_rolling_backtest_shifts_its_start():
    """So a roll and a hold from the same requested date actually BEGIN on the same day — they
    previously disagreed whenever that date was a holiday."""
    fn = _fn("backtest_leaps_rolling")
    assert "start_date = _next_trading_day(start_date)" in fn
    assert '"start_date_shifted": _requested_start != start_date,' in fn


def test_a_shift_that_consumes_the_window_is_rejected():
    """A 2-day range starting on a Friday holiday would otherwise produce an inverted window."""
    assert "if exit_date <= entry_date:\n        return None  # the shift consumed the whole window" in _fn("backtest_leaps")
    fn = _fn("backtest_leaps_rolling")
    i = fn.index("start_date = _next_trading_day(start_date)")
    assert "if end_date <= start_date:" in fn[i:i + 200]


def test_per_cycle_entries_are_not_re_shifted():
    """Each subsequent cursor is a real QUOTED exit date, so it is already a trading day.
    Shifting it again would drift the sequence forward for no reason."""
    fn = _fn("backtest_leaps_rolling")
    assert fn.count("_next_trading_day(") == 1


# ── The explainer distinguishes closed-market from missing-capture ──────────────────────

def test_the_explainer_names_a_closed_market():
    """The old text said the ARCHIVE had no quotes, which reads as a gap in our data when the
    real answer is that no chain exists for that date anywhere."""
    fn = _fn("_explain_no_price")
    assert "is not a US trading day" in fn
    assert "pick the next open day" in fn


def test_the_closed_market_check_runs_before_the_missing_capture_message():
    """Order matters: a holiday would otherwise be reported as a capture failure."""
    fn = _fn("_explain_no_price")
    assert fn.index("is not a US trading day") < fn.index("the archive has no quotes")


def test_the_missing_capture_message_still_exists():
    """A genuinely uncaptured TRADING day is a real and different condition."""
    assert "the archive has no quotes for that date" in _fn("_explain_no_price")


# ── The measured before/after, pinned ───────────────────────────────────────────────────

def test_the_reported_failure_is_recorded():
    """So nobody 'simplifies' the shift away without knowing what it fixed."""
    fn = _fn("_next_trading_day")
    assert "2024-01-15" in fn and "MLK" in fn


def test_the_roll_hold_disagreement_is_recorded():
    fn = _fn("_next_trading_day")
    assert "rolling" in fn.lower() or "ROLLING" in fn
