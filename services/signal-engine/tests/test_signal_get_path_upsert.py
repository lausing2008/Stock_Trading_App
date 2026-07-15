"""Regression test for T247-SIGNALENGINE-GETPATH-UPSERT-CONFLICT.

GET /signals/{symbol}'s persist=True path previously guarded only against re-inserting an
IDENTICAL signal already stored today (DI-1's same-day guard), then fell through to a plain
session.add(Signal(...)) with no ON CONFLICT handling for every other case. Since the real DB
has a unique index uq_signals_stock_horizon_day on (stock_id, horizon, date_trunc('day', ts)),
a same-day signal that genuinely CHANGED value (a real price move, or this same function's own
catalyst-nudge re-evaluation a few lines above) would raise an unhandled IntegrityError,
500ing the whole request and rolling back every horizon's signal in the same commit.

routes.py can't be imported directly here — it does `from common.jwt_auth import
get_current_username`, and conftest.py's blanket `common` stub is a bare MagicMock, not a real
package, so submodule imports fail (same constraint as event-intelligence's routes.py). This
test instead reads the actual source text and verifies the GET-path upsert now uses the same
INSERT ... ON CONFLICT ... DO UPDATE statement _bulk_persist() already uses, with no bare
`session.add(Signal(...))` remaining in that code path.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_SOURCE = _ROUTES_PATH.read_text()


def _get_path_upsert_block() -> str:
    """Grab the source slice for the GET-path persist block, delimited by the fix's own
    comment tag and the trailing session.commit(), so this test is robust to unrelated edits
    elsewhere in this large file."""
    start = _SOURCE.index("T247-SIGNALENGINE-GETPATH-UPSERT-CONFLICT")
    end = _SOURCE.index("session.commit()", start) + len("session.commit()")
    return _SOURCE[start:end]


def test_get_path_persist_uses_on_conflict_do_update_not_bare_insert():
    """The exact bug scenario: the GET-path persist block must use a real upsert, not a bare
    session.add() that can violate the unique index on a same-day value change."""
    block = _get_path_upsert_block()
    assert "ON CONFLICT" in block
    assert "DO UPDATE SET" in block
    # The comment describing the OLD bug legitimately mentions "session.add(Signal(...))" in
    # prose — check for the actual CODE line (no leading "a plain"/no trailing ")) with") to
    # avoid a false failure on the explanatory comment itself.
    code_lines = [ln for ln in block.splitlines() if not ln.strip().startswith("#")]
    assert not any("session.add(Signal(" in ln for ln in code_lines)


def test_get_path_upsert_targets_the_real_unique_index():
    block = _get_path_upsert_block()
    assert "ON CONFLICT (stock_id, horizon, date_trunc('day', ts))" in block


def test_get_path_upsert_uses_cast_not_double_colon_syntax():
    """BUG-6 (documented in CLAUDE.md): SQLAlchemy text() named params bound immediately before
    a PostgreSQL `::type` cast silently fail to bind. The upsert must use CAST(:param AS type)
    exclusively, matching _bulk_persist()'s already-correct convention."""
    block = _get_path_upsert_block()
    assert "::signaltype" not in block
    assert "::signalhorizon" not in block
    assert "CAST(:sig AS signaltype)" in block
    assert "CAST(:hor AS signalhorizon)" in block


def test_get_path_upsert_updates_ts_to_now_so_ts_desc_ordering_stays_correct():
    """DO UPDATE SET ts = NOW() must be present — otherwise a same-day signal-value change
    would silently keep an old ts, breaking every downstream query that reads Signal.ts.desc()
    to mean 'most recent update'."""
    block = _get_path_upsert_block()
    assert "ts                  = NOW()" in block or "ts = NOW()" in block
