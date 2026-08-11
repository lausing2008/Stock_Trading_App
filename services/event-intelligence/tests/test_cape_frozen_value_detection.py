"""Tests for AUD264-CAPE-STALE-FLAG-UNREACHABLE.

get_latest_cape()'s original stale flag (age_days > 45) is structurally unreachable while the
daily sync job keeps succeeding: multpl.com's Atom feed is a genuine live "current value"
reading (confirmed live — its own <content> block literally says "Current", stamped with a
real intraday timestamp), and the CAPE value really does fluctuate daily with the market
(confirmed against real production data: 42.39/42.12/42.19/... across consecutive days) — so
reading_date correctly advances every day the sync succeeds, keeping age_days near 0 forever.
This left a genuinely frozen VALUE (multpl serving the same stale number under a fresh-looking
date, e.g. if the feed silently broke but kept 200-ing with cached content) with no detector.

Fix: a second, independent check — how many of the most recent readings share the exact same
cape_value. valuation.py can't be imported directly (SessionLocal/CapeReading come from the
stubbed db module) — SessionLocal is monkeypatched to a fake context manager returning
controlled fake rows, matching this repo's established technique for exercising a DB-backed
function without a real database.
"""
from unittest.mock import MagicMock
from datetime import date, timedelta

import pytest

from src.services import valuation as v


class _FakeRow:
    def __init__(self, reading_date, cape_value, source="multpl"):
        self.reading_date = reading_date
        self.cape_value = cape_value
        self.source = source


class _FakeSession:
    """Returns `latest` for the single-row query (.scalar_one_or_none()) and `recent` for the
    multi-row query (.scalars().all()) — get_latest_cape() makes exactly these two queries."""

    def __init__(self, latest, recent):
        self._latest = latest
        self._recent = recent
        self._call_count = 0

    def execute(self, *_args, **_kwargs):
        self._call_count += 1
        result = MagicMock()
        if self._call_count == 1:
            result.scalar_one_or_none.return_value = self._latest
        else:
            result.scalars.return_value.all.return_value = self._recent
        return result

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _rows(cape_values: list[float], start: date | None = None) -> list[_FakeRow]:
    """Builds newest-first rows, one per consecutive day, with the given cape_values."""
    start = start or date.today()
    return [_FakeRow(start - timedelta(days=i), v_) for i, v_ in enumerate(cape_values)]


@pytest.fixture
def patch_session(monkeypatch):
    def _apply(rows):
        latest = rows[0] if rows else None
        monkeypatch.setattr(v, "SessionLocal", lambda: _FakeSession(latest, rows))
    return _apply


class TestFrozenValueDetection:
    def test_frozen_value_days_counts_consecutive_matching_readings(self, patch_session):
        rows = _rows([42.0, 42.0, 42.0, 41.5, 41.0])
        patch_session(rows)
        result = v.get_latest_cape()
        assert result["frozen_value_days"] == 3

    def test_frozen_value_days_is_1_when_the_value_just_changed(self, patch_session):
        rows = _rows([42.0, 41.5, 41.0])
        patch_session(rows)
        result = v.get_latest_cape()
        assert result["frozen_value_days"] == 1

    def test_stale_fires_when_the_value_has_been_frozen_for_the_full_threshold(self, patch_session):
        """The exact regression this fix targets: age_days stays near 0 (fresh reading_date
        every day) while the VALUE itself never changes — must still flag stale=True."""
        rows = _rows([42.0] * v._FROZEN_VALUE_STALE_DAYS)
        patch_session(rows)
        result = v.get_latest_cape()
        assert result["age_days"] == 0  # confirms this is NOT caught by the age-based check
        assert result["stale"] is True

    def test_stale_does_not_fire_on_normal_daily_fluctuation(self, patch_session):
        """The exact case that was previously mis-handled (or would be, under a naive "any
        stale-looking heuristic"): real production data with a genuinely changing value must
        never be flagged stale just because the sync job is running smoothly every day."""
        rows = _rows([42.39, 42.12, 42.19, 42.28, 41.51, 40.91, 40.62])
        patch_session(rows)
        result = v.get_latest_cape()
        assert result["stale"] is False

    def test_frozen_value_just_below_the_threshold_does_not_trigger_stale(self, patch_session):
        rows = _rows([42.0] * (v._FROZEN_VALUE_STALE_DAYS - 1) + [41.5])
        patch_session(rows)
        result = v.get_latest_cape()
        assert result["frozen_value_days"] == v._FROZEN_VALUE_STALE_DAYS - 1
        assert result["stale"] is False

    def test_age_based_stale_check_is_still_reachable_and_independent(self, patch_session):
        """A genuinely dead feed (no new rows in 46+ days) must still be caught by the
        original age_days check — this fix ADDS a detector, it doesn't replace the existing
        one."""
        old_date = date.today() - timedelta(days=46)
        rows = [_FakeRow(old_date, 42.0)]
        patch_session(rows)
        result = v.get_latest_cape()
        assert result["age_days"] == 46
        assert result["frozen_value_days"] == 1  # only one row exists at all
        assert result["stale"] is True

    def test_returns_none_when_no_reading_exists_at_all(self, patch_session):
        patch_session([])
        assert v.get_latest_cape() is None
