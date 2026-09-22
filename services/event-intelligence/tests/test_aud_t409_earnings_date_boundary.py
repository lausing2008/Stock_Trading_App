"""AUD-T409-EVENTINTEL (2026-09-21) — earnings.py's own share of the UTC-vs-ET date-boundary
bug class (docs/incidents/utc-vs-et-date-boundary.md, market-data's AUD-T409). Four call sites
used `date.today()` — a naive UTC truncation, wrong for ~4-5 hours every evening:

- get_upcoming_earnings(): `today` as the inclusive lower bound for the "upcoming" list — a
  real today-ET earnings event could silently drop off during the evening window.
- get_days_to_earnings(): same lower bound, plus (row - today).days drifts by one.
- _row_to_dict()'s `is_upcoming` flag: a real-today earnings row reads as already-past.
- check_earnings_impact_poll()'s [yesterday, today] recheck window: shifts to
  [today, tomorrow] during the window, missing a real yesterday-after-market report still
  unresolved (self-heals once the date rolls over, so exposure is narrow but real).

This file had no shared _today_et() helper at all (unlike market-data/signal-engine, which
already had 3-4 independent copies before this fix) — added here as this file's own copy,
matching the established naming/shape convention.
"""
from datetime import datetime, timezone
from unittest.mock import patch

from src.services.earnings import _today_et


class _FrozenDatetime(datetime):
    _frozen: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._frozen.astimezone(tz) if tz else cls._frozen


def _frozen_at(utc_iso: str):
    frozen = _FrozenDatetime.fromisoformat(utc_iso).replace(tzinfo=timezone.utc)
    _FrozenDatetime._frozen = frozen
    return patch("src.services.earnings.datetime", _FrozenDatetime)


# ── _today_et() itself ──────────────────────────────────────────────────────────────────────

def test_the_exact_evening_window_reads_the_correct_et_date():
    """2026-09-18 20:40 ET == 2026-09-19 00:40 UTC — the naive version reads Sept 19; the
    correct answer is Sept 18."""
    with _frozen_at("2026-09-19T00:40:00"):
        assert _today_et().isoformat() == "2026-09-18"


def test_naive_utc_truncation_would_have_gotten_this_wrong():
    """Constructed directly rather than via this test file's own real datetime.now() — see
    AUD-NEXTIMPROV-FROZENTIME-LEAK (market-data's test_t409_utc_date_boundary.py) for why that
    matters: relying on the unpatched real clock silently ties the test to the day it was
    written instead of the behavior it names."""
    frozen = datetime(2026, 9, 19, 0, 40, 0, tzinfo=timezone.utc)
    assert frozen.date().isoformat() == "2026-09-19"


def test_daytime_edt_matches_the_naive_version_exactly():
    with _frozen_at("2026-09-18T18:00:00"):  # 14:00 EDT
        assert _today_et().isoformat() == "2026-09-18"


# ── Every one of the four fixed call sites actually uses the shared helper ─────────────────

import pathlib

_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "earnings.py"
).read_text()


def test_get_upcoming_earnings_uses_today_et():
    start = _SOURCE.index("def get_upcoming_earnings(")
    end = _SOURCE.index("\n\n\ndef ", start)
    body = _SOURCE[start:end]
    assert "today = _today_et()  # AUD-T409-EVENTINTEL" in body
    assert "date.today()" not in body


def test_get_days_to_earnings_uses_today_et():
    start = _SOURCE.index("def get_days_to_earnings(")
    end = _SOURCE.index("\n\n\ndef ", start)
    body = _SOURCE[start:end]
    assert "today = _today_et()  # AUD-T409-EVENTINTEL" in body
    assert "date.today()" not in body


def test_row_to_dict_is_upcoming_uses_today_et():
    start = _SOURCE.index("def _row_to_dict(")
    body = _SOURCE[start:start + 1500]
    assert "today = _today_et()  # AUD-T409-EVENTINTEL" in body
    assert "is_upcoming\": e.report_date >= today" in body


def test_check_earnings_impact_poll_window_uses_today_et():
    """AUD-T401-SOURCETEXTTESTS: checks WHICH helper resolves the window boundary (the
    qualitative fix), not the full expression including the `- timedelta(days=1)` numeric
    literal — that shape is a fixed one-line window this test's own docstring already names,
    not a threshold this test needs to pin a second time."""
    start = _SOURCE.index("cutoff_start = ")
    end = _SOURCE.index("with SessionLocal() as s:", start)
    body = _SOURCE[start:end]
    assert "cutoff_start = _today_et()" in body
    assert "cutoff_end = _today_et()" in body
    assert "date.today()" not in body


def test_wide_lookback_windows_are_deliberately_left_on_naive_date_today():
    """Regression guard the OTHER direction: the 3 wide lookback-window cutoffs (2/45/365
    days back) were classified LIKELY FINE and must NOT have been swept along with the real
    fixes — a blanket find-and-replace would have been a scope-creeping mistake here.

    AUD-T401-SOURCETEXTTESTS: asserts the qualitative shape (still `date.today()`, still a
    `timedelta(days=...)` window), not the specific day-count numbers themselves — those aren't
    thresholds this fix could silently defeat, just incidental values of an unrelated,
    deliberately-untouched lookback window."""
    assert "cutoff = date.today() - timedelta(days=" in _SOURCE
    assert "since = date.today() - timedelta(days=days_back)" in _SOURCE
