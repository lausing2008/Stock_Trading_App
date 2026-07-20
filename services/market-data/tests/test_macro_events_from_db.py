"""Regression test for T249-MARKETMOVER-P0.

_macro_events_from_db() reads the real release-date calendar from economic_events' *_release
rows (synced from FRED's own fred/release/dates endpoint via event-intelligence's
sync_fred_release_dates()) — replacing the previously hardcoded, already-once-wrong
_MACRO_2026 CPI/NFP/PCE/GDP dates as the preferred source, falling back to the hardcoded list
only when the DB has no row yet for a given (type, date-range).
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import db as _db_module
from src.api.routes import _macro_events_from_db, _MACRO_TYPE_TO_RELEASE_EVENT_TYPE, _MACRO_2026


class _ComparableStub:
    """Stands in for db.EconomicEvent's stubbed (MagicMock) class attributes —
    e.g. EconomicEvent.event_date — so that eagerly-evaluated .where() clause
    construction (col.in_(...), col >= today, col <= cutoff) doesn't raise
    TypeError against a plain MagicMock, which supports no rich comparison
    protocol by default. Every comparison/membership op just returns a marker;
    the real filtering behavior under test happens in the fake session's
    canned scalars().all() result, not in the (never really executed) query
    object itself."""

    def __ge__(self, other):
        return "ge-clause"

    def __le__(self, other):
        return "le-clause"

    def in_(self, other):
        return "in-clause"


def _fake_row(event_type, event_date, title="Test Release", importance="high"):
    return SimpleNamespace(
        event_type=event_type,
        event_date=datetime(event_date.year, event_date.month, event_date.day, 8, 30, tzinfo=timezone.utc),
        title=title,
        importance=importance,
    )


def _session_returning(rows, monkeypatch):
    monkeypatch.setattr(_db_module.EconomicEvent, "event_date", _ComparableStub(), raising=False)
    monkeypatch.setattr(_db_module.EconomicEvent, "event_type", _ComparableStub(), raising=False)
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = rows
    return session


def test_cpi_release_row_maps_to_the_hardcoded_type_name(monkeypatch):
    """The exact mapping this function exists for: a real cpi_release DB row must surface as
    type='cpi' (matching _MACRO_2026's naming) so downstream consumers don't need to know
    about the internal *_release event_type naming."""
    today = date(2026, 7, 15)
    cutoff = date(2026, 9, 1)
    row = _fake_row("cpi_release", date(2026, 7, 14), title="CPI Release")
    session = _session_returning([row], monkeypatch)

    events, covered_type_months = _macro_events_from_db(session, today, cutoff)

    assert covered_type_months == {("cpi", 2026, 7)}
    assert len(events) == 1
    assert events[0]["type"] == "cpi"
    assert events[0]["date"] == "2026-07-14"
    assert events[0]["title"] == "CPI Release"


def test_events_have_the_same_shape_as_the_macro_2026_fallback_path(monkeypatch):
    """The DB-sourced events must have every field the _MACRO_2026 fallback path adds
    (days_to_event/symbol/name/market/sector) so events_calendar()'s response has one
    consistent shape regardless of which source a given event came from."""
    today = date(2026, 7, 15)
    cutoff = date(2026, 9, 1)
    row = _fake_row("nfp_release", date(2026, 8, 7), title="Jobs Report (NFP)")
    session = _session_returning([row], monkeypatch)

    events, _ = _macro_events_from_db(session, today, cutoff)

    ev = events[0]
    for key in ("type", "date", "title", "description", "impact", "days_to_event", "symbol", "name", "market", "sector"):
        assert key in ev, f"missing field: {key}"
    assert ev["days_to_event"] == (date(2026, 8, 7) - today).days
    assert ev["symbol"] is None


def test_no_db_rows_returns_empty_types_so_fallback_path_is_used(monkeypatch):
    """No rows in the DB yet (e.g. before the first sync_fred_release_dates() run) must
    correctly signal an empty covered_type_months set, so events_calendar()'s fallback to
    _MACRO_2026 actually engages instead of silently dropping the event."""
    today = date(2026, 7, 15)
    cutoff = date(2026, 9, 1)
    session = _session_returning([], monkeypatch)

    events, covered_type_months = _macro_events_from_db(session, today, cutoff)

    assert events == []
    assert covered_type_months == set()


def test_covered_type_months_is_scoped_per_month_not_per_type(monkeypatch):
    """AUD250-MACRO-CALENDAR-FALLBACK-GRANULARITY: a single DB row for one month of a type
    must NOT mark every other month of that same type as covered — each month needs its own
    entry in the returned set, so a later month with no DB row still correctly falls back to
    the hardcoded _MACRO_2026 entry for that month instead of being silently dropped."""
    today = date(2026, 7, 15)
    cutoff = date(2026, 12, 31)
    # Only a July CPI row exists in the DB — August/September/etc. CPI releases have no
    # DB row yet (e.g. sync_fred_release_dates()'s 180-day sync window hasn't reached them).
    row = _fake_row("cpi_release", date(2026, 7, 14), title="CPI Release")
    session = _session_returning([row], monkeypatch)

    _, covered_type_months = _macro_events_from_db(session, today, cutoff)

    assert ("cpi", 2026, 7) in covered_type_months
    assert ("cpi", 2026, 8) not in covered_type_months
    assert ("cpi", 2026, 9) not in covered_type_months


def test_two_different_months_of_the_same_type_both_tracked(monkeypatch):
    """Confirms the set accumulates multiple (type, year, month) entries correctly when the
    DB genuinely does have rows for more than one month of the same type."""
    today = date(2026, 7, 15)
    cutoff = date(2026, 12, 31)
    rows = [
        _fake_row("cpi_release", date(2026, 7, 14), title="CPI Release"),
        _fake_row("cpi_release", date(2026, 8, 13), title="CPI Release"),
    ]
    session = _session_returning(rows, monkeypatch)

    _, covered_type_months = _macro_events_from_db(session, today, cutoff)

    assert covered_type_months == {("cpi", 2026, 7), ("cpi", 2026, 8)}


def test_every_macro_2026_fallback_type_has_a_release_mapping_or_is_fomc():
    """Guards against a future _MACRO_2026 entry silently never being replaceable by a real
    DB row because _MACRO_TYPE_TO_RELEASE_EVENT_TYPE wasn't updated to match — every distinct
    "type" in the hardcoded fallback list must either be "fomc" (which has no FRED release
    equivalent, by design) or have an entry in the mapping table."""
    types_in_fallback = {ev["type"] for ev in _MACRO_2026}
    unmapped = types_in_fallback - set(_MACRO_TYPE_TO_RELEASE_EVENT_TYPE) - {"fomc"}
    assert not unmapped, f"_MACRO_2026 has types with no DB-release mapping: {unmapped}"
