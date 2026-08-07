"""Tests for AUD264-RELEASE-POLL-COVERS-4-OF-10's one-time backfill —
backfill_release_actual_values(). Found while doing a post-fix data-correctness sweep: the
original fix (adding the 5 missing series mappings) only prevents FUTURE staleness —
check_release_day_fast_poll() only ever queries for releases due TODAY, so the 203 past-dated
*_release rows already missing actual_value across all 10 types (confirmed live in production,
not just the 113 fed_funds_release the original finding cited) will never be revisited by the
normal poll cycle.

macro_reaction.py imports directly in this test environment (no common.jwt_auth dependency,
unlike routes.py) — this uses the same real-in-memory-SQLite-plus-real-EconomicEvent-model
technique already established in test_earnings_backfill_report_dates.py (db is stubbed
wholesale by conftest.py for Docker-only dependencies — this pops that stub, builds a real
engine, and restores it immediately after import).
"""
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

_STUBBED_MODULES = (
    "common", "common.config", "common.logging",
    "db", "db.session",
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql",
    "psycopg2",
)
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib

from sqlalchemy import Integer, create_engine, select as _real_select
from sqlalchemy.orm import sessionmaker

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_macro_backfill", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_macro_backfill"] = _models
_spec.loader.exec_module(_models)

_models.EconomicEvent.__table__.c.id.type = Integer()  # SQLite autoincrement needs plain INTEGER PK

_engine = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(_engine, tables=[_models.EconomicEvent.__table__])
_SessionLocal = sessionmaker(bind=_engine)

for _mod, _val in _saved_stubs.items():
    if _val is not None:
        sys.modules[_mod] = _val
    else:
        sys.modules.pop(_mod, None)

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
sys.modules.setdefault("common.logging", MagicMock())
sys.modules.setdefault("db", MagicMock())

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.services import macro_reaction  # noqa: E402


def _run(coro):
    import asyncio
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_table():
    """All tests in this file share one module-level in-memory engine (see the header comment
    for why — SQLAlchemy's create_engine() does a dynamic dialect lookup at CALL time, not
    just import time, so it can't be built lazily inside a test after the stub restoration
    above). EconomicEvent has a real UNIQUE(event_type, country, event_date) constraint, so a
    row from an earlier test reusing the same (type, date) pair would collide with a later
    test's own insert — clear the table before every test to keep them independent."""
    with _SessionLocal() as session:
        session.query(_models.EconomicEvent).delete()
        session.commit()
    yield


def _make_event(session, event_type, event_date, actual_value=None):
    ev = _models.EconomicEvent(
        event_type=event_type, title=event_type, country="US",
        event_date=datetime(event_date.year, event_date.month, event_date.day, tzinfo=timezone.utc),
        actual_value=actual_value,
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


def _fred_response(value, prev_value=None, status_code=200):
    resp = MagicMock(status_code=status_code)
    obs = [{"value": str(value)}]
    if prev_value is not None:
        obs.append({"value": str(prev_value)})
    resp.json.return_value = {"observations": obs}
    return resp


class TestBackfillReleaseActualValues:
    def test_fills_a_past_dated_row_missing_actual_value(self):
        with patch.object(macro_reaction, "SessionLocal", _SessionLocal), \
             patch.object(macro_reaction, "EconomicEvent", _models.EconomicEvent), \
             patch.object(macro_reaction, "select", _real_select), \
             patch.object(macro_reaction._settings, "fred_api_key", "fake-key", create=True), \
             patch.object(macro_reaction.httpx, "get", return_value=_fred_response(3.72, 3.88)):
            with _SessionLocal() as session:
                _make_event(session, "fed_funds_release", date(2026, 1, 16))
            result = _run(macro_reaction.backfill_release_actual_values())

        assert result["filled"] == 1
        with _SessionLocal() as session:
            ev = session.query(_models.EconomicEvent).filter_by(event_type="fed_funds_release").one()
            assert ev.actual_value == 3.72
            assert ev.previous_value == 3.88

    def test_never_generates_a_reaction_for_a_backfilled_row(self):
        """The core design decision this fix makes: a backfilled past release must NOT get an
        LLM reaction, since generate_reaction() reads the CURRENT market regime — framing a
        months-old release through today's market context would be actively misleading."""
        with patch.object(macro_reaction, "SessionLocal", _SessionLocal), \
             patch.object(macro_reaction, "EconomicEvent", _models.EconomicEvent), \
             patch.object(macro_reaction, "select", _real_select), \
             patch.object(macro_reaction._settings, "fred_api_key", "fake-key", create=True), \
             patch.object(macro_reaction.httpx, "get", return_value=_fred_response(2.5)):
            with _SessionLocal() as session:
                _make_event(session, "cpi_release", date(2026, 2, 13))
            _run(macro_reaction.backfill_release_actual_values())

        with _SessionLocal() as session:
            ev = session.query(_models.EconomicEvent).filter_by(event_type="cpi_release").one()
            assert ev.reaction_text is None
            assert ev.reaction_generated_at is None

    def test_ignores_future_dated_rows_entirely(self):
        """A release scheduled for the future genuinely has no actual_value yet — that's not
        a bug to backfill, it's the correct pending state. Must not be touched or counted."""
        with patch.object(macro_reaction, "SessionLocal", _SessionLocal), \
             patch.object(macro_reaction, "EconomicEvent", _models.EconomicEvent), \
             patch.object(macro_reaction, "select", _real_select), \
             patch.object(macro_reaction._settings, "fred_api_key", "fake-key", create=True), \
             patch.object(macro_reaction.httpx, "get", return_value=_fred_response(9.99)) as mock_get:
            future_date = (datetime.now(timezone.utc) + timedelta(days=30)).date()
            with _SessionLocal() as session:
                _make_event(session, "gdp_release", future_date)
            result = _run(macro_reaction.backfill_release_actual_values())

        assert result["checked"] == 0
        assert result["filled"] == 0
        mock_get.assert_not_called()

    def test_ignores_rows_that_already_have_an_actual_value(self):
        with patch.object(macro_reaction, "SessionLocal", _SessionLocal), \
             patch.object(macro_reaction, "EconomicEvent", _models.EconomicEvent), \
             patch.object(macro_reaction, "select", _real_select), \
             patch.object(macro_reaction._settings, "fred_api_key", "fake-key", create=True), \
             patch.object(macro_reaction.httpx, "get", return_value=_fred_response(9.99)) as mock_get:
            with _SessionLocal() as session:
                _make_event(session, "nfp_release", date(2026, 2, 11), actual_value=150000.0)
            result = _run(macro_reaction.backfill_release_actual_values())

        assert result["checked"] == 0
        assert result["filled"] == 0
        mock_get.assert_not_called()
        with _SessionLocal() as session:
            ev = session.query(_models.EconomicEvent).filter_by(event_type="nfp_release").one()
            assert ev.actual_value == 150000.0  # untouched, not overwritten with the mocked 9.99

    def test_queries_fred_with_realtime_start_and_end_pinned_to_the_release_date(self):
        """The core mechanism this backfill depends on: pinning realtime_start/realtime_end to
        the SAME past date recovers the vintage value that was actually known then, not
        whatever FRED's series has been revised to show today."""
        with patch.object(macro_reaction, "SessionLocal", _SessionLocal), \
             patch.object(macro_reaction, "EconomicEvent", _models.EconomicEvent), \
             patch.object(macro_reaction, "select", _real_select), \
             patch.object(macro_reaction._settings, "fred_api_key", "fake-key", create=True), \
             patch.object(macro_reaction.httpx, "get", return_value=_fred_response(3.72)) as mock_get:
            with _SessionLocal() as session:
                _make_event(session, "fed_funds_release", date(2026, 1, 16))
            _run(macro_reaction.backfill_release_actual_values())

        _, kwargs = mock_get.call_args
        assert kwargs["params"]["realtime_start"] == "2026-01-16"
        assert kwargs["params"]["realtime_end"] == "2026-01-16"
        assert kwargs["params"]["series_id"] == "FEDFUNDS"

    def test_missing_value_in_fred_response_is_skipped_not_counted_as_filled(self):
        with patch.object(macro_reaction, "SessionLocal", _SessionLocal), \
             patch.object(macro_reaction, "EconomicEvent", _models.EconomicEvent), \
             patch.object(macro_reaction, "select", _real_select), \
             patch.object(macro_reaction._settings, "fred_api_key", "fake-key", create=True), \
             patch.object(macro_reaction.httpx, "get", return_value=_fred_response(".")):
            with _SessionLocal() as session:
                _make_event(session, "ppi_release", date(2026, 1, 30))
            result = _run(macro_reaction.backfill_release_actual_values())

        assert result["checked"] == 1
        assert result["filled"] == 0

    def test_non_200_fred_response_is_skipped_not_counted_as_filled(self):
        with patch.object(macro_reaction, "SessionLocal", _SessionLocal), \
             patch.object(macro_reaction, "EconomicEvent", _models.EconomicEvent), \
             patch.object(macro_reaction, "select", _real_select), \
             patch.object(macro_reaction._settings, "fred_api_key", "fake-key", create=True), \
             patch.object(macro_reaction.httpx, "get", return_value=_fred_response(3.72, status_code=500)):
            with _SessionLocal() as session:
                _make_event(session, "housing_starts_release", date(2026, 1, 27))
            result = _run(macro_reaction.backfill_release_actual_values())

        assert result["checked"] == 1
        assert result["filled"] == 0

    def test_one_symbols_failure_does_not_abort_the_whole_batch(self):
        with patch.object(macro_reaction, "SessionLocal", _SessionLocal), \
             patch.object(macro_reaction, "EconomicEvent", _models.EconomicEvent), \
             patch.object(macro_reaction, "select", _real_select), \
             patch.object(macro_reaction._settings, "fred_api_key", "fake-key", create=True), \
             patch.object(macro_reaction.httpx, "get", side_effect=[RuntimeError("network down"), _fred_response(3.72)]):
            with _SessionLocal() as session:
                _make_event(session, "fed_funds_release", date(2026, 1, 16))
                _make_event(session, "cpi_release", date(2026, 2, 13))
            result = _run(macro_reaction.backfill_release_actual_values())

        assert result["checked"] == 2
        assert result["filled"] == 1

    def test_no_api_key_returns_skip_without_crashing(self):
        with patch.object(macro_reaction._settings, "fred_api_key", "", create=True):
            result = _run(macro_reaction.backfill_release_actual_values())
        assert result == {"checked": 0, "filled": 0, "skipped": "no_api_key"}

    def test_multiple_release_types_all_processed_in_one_run(self):
        with patch.object(macro_reaction, "SessionLocal", _SessionLocal), \
             patch.object(macro_reaction, "EconomicEvent", _models.EconomicEvent), \
             patch.object(macro_reaction, "select", _real_select), \
             patch.object(macro_reaction._settings, "fred_api_key", "fake-key", create=True), \
             patch.object(macro_reaction.httpx, "get", return_value=_fred_response(1.0)):
            with _SessionLocal() as session:
                _make_event(session, "fed_funds_release", date(2026, 1, 16))
                _make_event(session, "cpi_release", date(2026, 2, 13))
                _make_event(session, "ppi_release", date(2026, 1, 30))
            result = _run(macro_reaction.backfill_release_actual_values())

        assert result["checked"] == 3
        assert result["filled"] == 3
