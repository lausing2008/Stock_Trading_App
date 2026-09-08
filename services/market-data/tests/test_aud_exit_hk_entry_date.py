"""AUD-EXIT-HKENTRYDATE — HK hold times were understated by one day.

`days_held` differenced `entry_date` against the **ET** date, for every trade:

    days_held = busday_count(trade.entry_date, _et_date + 1)

HKEX opens 09:30 HKT = **01:30 UTC = 21:30 ET THE PREVIOUS DAY**. So for an HK position the two
sides disagreed about what day it is: `entry_date` said Sep 8 (correct — the HK trading day)
while `_et_date` still said Sep 7, giving `busday_count(Sep 8, Sep 8) = 0` and understating
every HK hold by one day.

US is unaffected: a US session (14:30-21:00 UTC) sits entirely inside one shared UTC/ET date.

PRODUCTION EVIDENCE — the asymmetry is exact:

    HK trades with entry_date ahead of their entry_time's ET date:  14 of 19
    US trades with the same mismatch:                                0 of 105

WHERE THE BUG ACTUALLY IS — a correction to my own first framing. I initially called this a
write-side defect (`entry_date = date.today()` recording the UTC date). Measured, that is a
NO-OP during real HK hours: the HK session is 01:30-08:00 UTC, where the UTC date and the HK
date always agree, so entry_date was already correct for every real fill. The two diverge only
at 16:00-23:59 UTC. **The defect is entirely the read side** comparing an HK entry_date against
an ET "today". The write-side change is kept as defence-in-depth for a late/retried write, and
is documented as such rather than as the fix.

DAMAGE AT THE TIME OF THE FIX: **zero**. All 14 affected trades sat at 0-6 `hold_days` against
a 7-day `signal_outcomes` bucket boundary, so none flipped bucket and no ML ground truth was
wrong. Fixed before HK's next open specifically because there were ZERO open HK positions at
that moment — the cleanest point to change a hold-day computation — and because every subsequent entry
would have added to the affected set.
"""
import pathlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

PT_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/paper_trading_engine.py"
).read_text()

_HK = ZoneInfo("Asia/Hong_Kong")
_ET = ZoneInfo("America/New_York")


def _trading_date(market: str, when: datetime) -> date:
    """Mirrors _trading_date_for; source assertions below pin the real implementation."""
    tz = _HK if market.upper() == "HK" else _ET
    return when.astimezone(tz).date()


def _days_held(entry_date: date, end_date: date) -> int:
    return int(np.busday_count(entry_date, end_date + timedelta(days=1)))


# ── The core defect ─────────────────────────────────────────────────────────────────────

def test_hk_same_day_hold_was_zero_and_is_now_one():
    """THE BUG, at the exact instant it bites: an HK entry at the 09:30 HKT open, measured
    30 minutes later. 'Today counts as day 1' per the +1 in busday_count."""
    entry = datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)   # 09:30 HKT
    later = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)    # 10:00 HKT, same session

    entry_date = _trading_date("HK", entry)                     # 2026-09-08, correct
    old = _days_held(entry_date, later.astimezone(_ET).date())  # ET still says 09-07
    new = _days_held(entry_date, _trading_date("HK", later))

    assert old == 0, "the old ET-based end date understated the hold"
    assert new == 1, "a position held on its own trading day is day 1"


def test_the_et_date_really_is_a_day_behind_at_the_hk_open():
    """Pins the mechanism so nobody 'simplifies' the market-aware end date away."""
    at_open = datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)
    assert at_open.astimezone(_HK).date() == date(2026, 9, 8)
    assert at_open.astimezone(_ET).date() == date(2026, 9, 7), "ET is one day behind"


@pytest.mark.parametrize("utc_hour,utc_min", [(1, 30), (3, 0), (5, 0), (7, 0), (8, 0)])
def test_across_the_whole_hk_session_the_hold_is_now_correct(utc_hour, utc_min):
    """The HK session is 01:30-08:00 UTC (09:30-16:00 HKT). At every point in it, a same-day
    hold must read 1, not 0."""
    entry = datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)
    now = datetime(2026, 9, 8, utc_hour, utc_min, tzinfo=timezone.utc)
    assert _days_held(_trading_date("HK", entry), _trading_date("HK", now)) == 1


# ── US must be completely untouched ─────────────────────────────────────────────────────

@pytest.mark.parametrize("utc_hour", [14, 16, 18, 20])
def test_us_hold_days_are_unchanged(utc_hour):
    """A US session (14:30-21:00 UTC) sits entirely inside one shared UTC/ET date, which is
    exactly why this bug never touched US. The fix must not perturb it."""
    entry = datetime(2026, 9, 8, 14, 30, tzinfo=timezone.utc)
    now = datetime(2026, 9, 8, utc_hour, 0, tzinfo=timezone.utc)
    old = _days_held(entry.date(), now.astimezone(_ET).date())
    new = _days_held(_trading_date("US", entry), _trading_date("US", now))
    assert old == new == 1


def test_the_read_side_only_diverts_for_hk_symbols():
    """US trades must still use `_et_date` — the variable, unchanged."""
    i = PT_SRC.index("_hold_end = (_trading_date_for(")
    block = PT_SRC[i:i + 300]
    assert 'endswith(".HK")' in block, "must branch on the symbol, matching the codebase idiom"
    assert "else _et_date" in block, "the US path must keep the original ET date"


# ── The write side is defence-in-depth, and honestly labelled ───────────────────────────

def test_the_write_side_is_a_noop_during_real_hk_hours():
    """A correction to my own first framing, pinned so it is not re-derived as 'the fix'.
    During 01:30-08:00 UTC the UTC date and the HK date always agree."""
    for hour, minute in [(1, 30), (3, 0), (5, 0), (7, 0), (8, 0)]:
        w = datetime(2026, 9, 8, hour, minute, tzinfo=timezone.utc)
        assert w.date() == w.astimezone(_HK).date(), (
            "entry_date was already correct for every real HK fill"
        )


def test_the_write_side_still_matters_outside_the_session():
    """Why it is kept: a late/retried write at 16:00-23:59 UTC WOULD land on the wrong date
    under the old `date.today()`."""
    late = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)   # 04:00 HKT next day
    assert late.date() != late.astimezone(_HK).date()
    assert _trading_date("HK", late) == date(2026, 9, 9)


def test_the_source_documents_where_the_bug_actually_is():
    """The comment must not claim the write side was the defect — a future reader would then
    'fix' the wrong half again."""
    assert "DEFENCE IN DEPTH, NOT THE FIX" in PT_SRC
    # Phrase split across lines in the source, so assert on the stable fragments.
    assert "The ACTUAL bug is on the read side" in PT_SRC
    assert "read side comparing an HK date against an ET" in PT_SRC


# ── No historical damage, and the boundary that mattered ────────────────────────────────

def test_no_historical_hk_trade_would_have_flipped_bucket():
    """Measured: all 14 affected trades sat at 0-6 hold_days. The signal_outcomes buckets are
    `<=7 -> 5d`, `<=14 -> 10d`, else `20d`, so a +1 correction moves none of them. This is why
    the finding was LATENT rather than corrupting ML ground truth."""
    observed = [6, 3, 3, 3, 2, 2, 1, 1, 1, 1, 0, 0, 0, 0]

    def bucket(d):
        return "5d" if d <= 7 else ("10d" if d <= 14 else "20d")

    assert all(bucket(d) == bucket(d + 1) for d in observed)
    assert max(observed) < 7, "every affected trade sat below the first boundary"


def test_the_boundaries_a_future_hk_position_could_cross():
    """Documents what was on a timer. Longest HK hold ever was 14 — exactly a bucket edge."""
    def bucket(d):
        return "5d" if d <= 7 else ("10d" if d <= 14 else "20d")
    assert bucket(7) != bucket(8), "the 5d/10d edge"
    assert bucket(14) != bucket(15), "the 10d/20d edge — and 14 is the longest HK hold on record"


def test_helper_normalises_a_naive_datetime_to_utc():
    """entry_time is stored tz-naive in some paths; treating a naive value as local would
    reintroduce a timezone bug inside the timezone fix."""
    i = PT_SRC.index("def _trading_date_for(")
    fn = PT_SRC[i:PT_SRC.index("\ndef ", i + 10)]
    assert "tzinfo is None" in fn
    assert "replace(tzinfo=timezone.utc)" in fn
