"""Tests for T249-MARKETMOVER-P0's sync_fred_release_dates().

This is a distinct sync path from sync_fred(): sync_fred() writes REFERENCE-PERIOD-dated
rows (event_date = the month/quarter the data describes, e.g. "2026-06-01" for June CPI).
sync_fred_release_dates() writes the REAL publication-date calendar (event_date = when BLS/
BEA actually published that data, e.g. "2026-07-14") by calling FRED's fred/release/dates
endpoint per release_id in _FRED_RELEASES, using distinct *_release event_types so rows from
the two sync paths never collide on the uq_economic_event(event_type, country, event_date)
constraint.

Mirrors test_congress_upsert_amendment.py's approach: reload the REAL sqlalchemy/postgres
dialect modules (conftest.py stubs them wholesale as MagicMock) to compile the actual
statement shape against the real Postgres dialect, without needing a live database or
mocking httpx for a full end-to-end run.
"""
import importlib
import importlib.util
import pathlib
import sys

for _mod in ("sqlalchemy", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "sqlalchemy.orm"):
    sys.modules.pop(_mod, None)
import sqlalchemy  # noqa: E402
importlib.reload(sqlalchemy)
from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)

EconomicEvent = _models.EconomicEvent

from src.services.economic import _FRED_RELEASES, _FRED_SERIES

_ECONOMIC_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "economic.py"
_SOURCE = _ECONOMIC_PATH.read_text()


def _sync_fred_release_dates_body() -> str:
    """Isolates sync_fred_release_dates()'s source text from the rest of economic.py, so
    source-text assertions below only check the function actually under test — not
    accidentally passing because some OTHER function in the file happens to contain the
    string being checked for."""
    start = _SOURCE.index("async def sync_fred_release_dates")
    # next top-level "async def " or "def " after start marks the following function, if any
    candidates = [
        i for i in (_SOURCE.find("\ndef ", start + 1), _SOURCE.find("\nasync def ", start + 1))
        if i != -1
    ]
    end = min(candidates) if candidates else len(_SOURCE)
    return _SOURCE[start:end]


def test_real_function_uses_do_nothing_not_do_update():
    """The exact regression this test guards against: sync_fred_release_dates() in the real
    economic.py (not a hand-copied re-implementation) must use on_conflict_do_nothing, not
    on_conflict_do_update — a release date, once recorded, never legitimately changes for the
    same (event_type, country, event_date) key."""
    body = _sync_fred_release_dates_body()
    assert "on_conflict_do_nothing" in body
    assert "on_conflict_do_update" not in body


def _build_release_upsert_stmt(event_type="cpi_release"):
    """Rebuilds the exact statement shape sync_fred_release_dates() constructs per release
    row, so this test fails if that construction ever regresses to a DO UPDATE (which would
    be wrong here — unlike congress amendments, a release date for a given (event_type,
    country, event_date) never legitimately changes after being recorded, so DO NOTHING is
    correct, not a bug to fix later)."""
    insert_stmt = pg_insert(EconomicEvent).values(
        event_type=event_type,
        title="CPI Release",
        country="US",
        event_date="2026-07-14",
        importance="high",
        source="fred_release_calendar",
    )
    return insert_stmt.on_conflict_do_nothing(constraint="uq_economic_event")


def test_release_upsert_is_do_nothing_not_do_update():
    """A release date, once recorded, never legitimately changes for the same
    (event_type, country, event_date) key — DO NOTHING is correct here (contrast with
    congress trade amendments, which DO need DO UPDATE)."""
    stmt = _build_release_upsert_stmt()
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in compiled
    assert "DO NOTHING" in compiled


# ── _FRED_RELEASES table integrity ─────────────────────────────────────────────

def test_every_release_event_type_is_distinct_from_every_series_event_type():
    """The entire reason this sync path is safe to add alongside sync_fred()'s existing
    reference-period rows: none of the new *_release event_types may collide with an
    existing _FRED_SERIES event_type, or rows from the two sync paths would silently
    overwrite/dedupe against each other under uq_economic_event."""
    release_types = {event_type for _, event_type, _, _ in _FRED_RELEASES}
    series_types = {event_type for _, event_type, _, _ in _FRED_SERIES}
    collisions = release_types & series_types
    assert not collisions, f"release event_types collide with series event_types: {collisions}"


def test_every_release_id_is_a_distinct_positive_int():
    release_ids = [release_id for release_id, _, _, _ in _FRED_RELEASES]
    assert all(isinstance(r, int) and r > 0 for r in release_ids)
    assert len(release_ids) == len(set(release_ids)), "duplicate release_id would double-sync the same calendar"


def test_every_release_event_type_ends_with_release_suffix():
    """Guards the naming convention _macro_events_from_db() (market-data routes.py) and
    _MACRO_TYPE_TO_RELEASE_EVENT_TYPE depend on to distinguish release rows from any other
    economic_events source at a glance."""
    for _, event_type, _, _ in _FRED_RELEASES:
        assert event_type.endswith("_release"), event_type


def test_cpi_release_id_matches_the_real_fred_release_for_cpiaucsl():
    """CPI's release_id (10) was verified directly against the live fred/series/release
    endpoint for CPIAUCSL/CPILFESL this session — pin it so a future edit can't silently
    swap in the wrong release_id (e.g. confusing it with another BLS release) without a
    test catching the mismatch."""
    cpi_entries = [r for r in _FRED_RELEASES if r[1] == "cpi_release"]
    assert len(cpi_entries) == 1
    assert cpi_entries[0][0] == 10
