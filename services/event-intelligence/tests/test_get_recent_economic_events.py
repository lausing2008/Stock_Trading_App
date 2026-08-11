"""Tests for AUD264-ECON-ENDPOINT-FILTERS-HIGH-ONLY's get_recent_economic_events() fix.

get_recent_economic_events() previously existed with NO route exposing it at all (confirmed
via grep — zero callers anywhere in this codebase or the frontend) AND a hardcoded
`importance == "high"` filter that would have silently hidden 5 of 10 already-synced release
types (retail_sales/consumer_conf/housing_starts/jobless_claims/gdp, all tagged "medium" in
_FRED_RELEASES/_FRED_SERIES) even once wired up. Both fixed together: the filter now takes a
`min_importance` param defaulting to "medium" (includes both "high" and "medium" — there is no
lower tier in this codebase), and a new GET /events/economic/recent route exposes it.

economic.py imports cleanly in this test environment except for its real SQLAlchemy/db
dependency (stubbed wholesale by conftest.py for Docker-only dependencies) — this uses the
same real-in-memory-SQLite-plus-real-EconomicEvent-model technique already established in
test_macro_reaction_backfill.py/test_earnings_backfill_report_dates.py: pop the stub, build a
real engine against the real shared/db/models.py, restore the stub immediately after import.

routes.py (the new GET /events/economic/recent route) needs common.jwt_auth, which conftest.py
does not stub for real — covered via source-text regression checks instead, matching this
repo's established pattern for exactly this constraint.
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
_spec = importlib.util.spec_from_file_location("db_models_under_test_recent_econ", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_recent_econ"] = _models
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
from src.services import economic  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_table():
    """All tests in this file share one module-level in-memory engine — SQLAlchemy's
    create_engine() does a dynamic dialect lookup at CALL time, not just import time, so it
    can't be built lazily inside a test after the stub restoration above. EconomicEvent has a
    real UNIQUE(event_type, country, event_date) constraint, so a row from an earlier test
    reusing the same (type, date) pair would collide with a later test's own insert — clear
    the table before every test to keep them independent."""
    with _SessionLocal() as session:
        session.query(_models.EconomicEvent).delete()
        session.commit()
    yield


def _make_event(session, event_type, event_date, importance, country="US", actual_value=1.0):
    ev = _models.EconomicEvent(
        event_type=event_type, title=event_type, country=country,
        event_date=datetime(event_date.year, event_date.month, event_date.day, tzinfo=timezone.utc),
        actual_value=actual_value, importance=importance,
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


class TestGetRecentEconomicEvents:
    def test_default_min_importance_includes_both_high_and_medium(self):
        """The core fix: previously hardcoded to high-only, silently hiding every medium-tier
        release type (retail_sales/consumer_conf/housing_starts/jobless_claims/gdp) even
        though they're real, already-synced event types."""
        today = date.today()
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "EconomicEvent", _models.EconomicEvent):
            with _SessionLocal() as session:
                _make_event(session, "cpi_release", today, "high")
                _make_event(session, "retail_sales_release", today, "medium")
            result = economic.get_recent_economic_events(days=30, country="US")
        types = {e["event_type"] for e in result}
        assert types == {"cpi_release", "retail_sales_release"}

    def test_explicit_high_only_excludes_medium(self):
        """A caller that genuinely only wants FOMC/CPI/NFP-grade releases can still get the
        old high-only behavior by passing min_importance="high" explicitly."""
        today = date.today()
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "EconomicEvent", _models.EconomicEvent):
            with _SessionLocal() as session:
                _make_event(session, "cpi_release", today, "high")
                _make_event(session, "retail_sales_release", today, "medium")
            result = economic.get_recent_economic_events(days=30, country="US", min_importance="high")
        types = {e["event_type"] for e in result}
        assert types == {"cpi_release"}

    def test_unknown_min_importance_falls_back_to_medium_tier_not_a_crash(self):
        """An unrecognized min_importance value must degrade to the safe, inclusive default
        rather than raising or silently returning zero rows."""
        today = date.today()
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "EconomicEvent", _models.EconomicEvent):
            with _SessionLocal() as session:
                _make_event(session, "retail_sales_release", today, "medium")
            result = economic.get_recent_economic_events(days=30, country="US", min_importance="bogus")
        assert len(result) == 1

    def test_events_outside_the_days_window_are_excluded(self):
        old = date.today() - timedelta(days=90)
        recent = date.today() - timedelta(days=1)
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "EconomicEvent", _models.EconomicEvent):
            with _SessionLocal() as session:
                _make_event(session, "cpi_release", old, "high")
                _make_event(session, "gdp_release", recent, "medium")
            result = economic.get_recent_economic_events(days=30, country="US")
        types = {e["event_type"] for e in result}
        assert types == {"gdp_release"}

    def test_country_filter_is_respected(self):
        today = date.today()
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "EconomicEvent", _models.EconomicEvent):
            with _SessionLocal() as session:
                _make_event(session, "cpi_release", today, "high", country="US")
                _make_event(session, "hk_cpi_release", today, "high", country="HK")
            result = economic.get_recent_economic_events(days=30, country="US")
        types = {e["event_type"] for e in result}
        assert types == {"cpi_release"}

    def test_returned_dict_shape_is_unchanged_by_the_fix(self):
        today = date.today()
        with patch.object(economic, "SessionLocal", _SessionLocal), \
             patch.object(economic, "select", _real_select), \
             patch.object(economic, "EconomicEvent", _models.EconomicEvent):
            with _SessionLocal() as session:
                _make_event(session, "cpi_release", today, "high", actual_value=3.2)
            result = economic.get_recent_economic_events(days=30, country="US")
        assert result[0].keys() == {
            "event_type", "title", "event_date", "actual_value", "expected_value", "importance",
        }
        assert result[0]["actual_value"] == 3.2
        assert result[0]["importance"] == "high"


# ── GET /events/economic/recent — source-text regression checks ───────────────────────────

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def test_route_is_registered():
    assert '@router.get("/events/economic/recent")' in _ROUTES_SOURCE
    assert "def get_economic_recent(" in _ROUTES_SOURCE


def test_route_calls_the_fixed_function_with_a_caller_supplied_min_importance():
    start = _ROUTES_SOURCE.index("def get_economic_recent(")
    end = _ROUTES_SOURCE.index("\n@router.get", start + 1)
    body = _ROUTES_SOURCE[start:end]
    assert "economic.get_recent_economic_events(days, country, min_importance)" in body


def test_route_requires_auth():
    start = _ROUTES_SOURCE.index("def get_economic_recent(")
    end = _ROUTES_SOURCE.index("\n@router.get", start + 1)
    body = _ROUTES_SOURCE[start:end]
    assert "Depends(get_current_username)" in body


def test_min_importance_query_param_is_constrained_to_the_two_real_tiers():
    """Must not silently accept an arbitrary string that would fall through to the function's
    own defensive fallback every time — the route-level pattern constraint should reject an
    invalid value before it ever reaches get_recent_economic_events()."""
    start = _ROUTES_SOURCE.index("def get_economic_recent(")
    end = _ROUTES_SOURCE.index("\n@router.get", start + 1)
    body = _ROUTES_SOURCE[start:end]
    assert 'pattern="^(high|medium)$"' in body
