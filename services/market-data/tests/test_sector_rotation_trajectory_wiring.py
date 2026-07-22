"""Regression tests for T258-SECTOR-ROTATION-TRAJECTORY's wiring into
_compute_sector_rotation() (services/market-data/src/services/scheduler.py).

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler/ingestion.py/paper_trading_engine.py, none of which conftest.py stubs — matching
the exact constraint already documented in test_premarket_brief.py/test_price_alert_price_
check.py's own docstrings). conftest.py also stubs sqlalchemy/db as MagicMock(), so a
MagicMock() attribute access never raises NameError/AttributeError — an actual missing import
or undefined name inside this function would NOT be caught by importing and running it under
the stubbed harness, only by reading the source directly (matching test_scheduler_static_
names.py's established pattern for this exact risk class).

sector_trajectory.py itself (the pure classification logic this wiring calls into) has its own
full, directly-importable test suite in test_sector_trajectory.py — these tests only cover the
NEW scheduler.py glue code: persisting SectorRotationSnapshot rows, querying the prior snapshot,
and folding the resulting trajectory back into the same Redis payload.
"""
import pathlib

_SCHEDULER_SOURCE = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
).read_text()


def _sector_rotation_body() -> str:
    start = _SCHEDULER_SOURCE.index("def _compute_sector_rotation(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def test_sector_rotation_function_uses_only_locally_imported_names():
    """_compute_sector_rotation() references SectorRotationSnapshot/SessionLocal/SectorRank/
    build_trajectories/rank_sectors — all must be imported (locally, matching this function's
    own established local-import convention for json/SessionLocal/text) inside the function
    body itself, not assumed to exist at module level. A MagicMock()-stubbed test run would not
    catch a missing import here, so this is a source-text check."""
    body = _sector_rotation_body()
    for name in ("SectorRotationSnapshot", "SessionLocal", "SectorRank", "build_trajectories", "rank_sectors"):
        assert name in body, f"{name} used in _compute_sector_rotation() body"
    assert "from db import SectorRotationSnapshot, SessionLocal" in body
    assert "from .sector_trajectory import SectorRank, build_trajectories, rank_sectors" in body


def test_sector_rotation_upserts_on_conflict_of_sector_and_as_of():
    """Must be idempotent (safe to re-run for the same week without duplicate rows) via
    ON CONFLICT DO UPDATE keyed on (sector, as_of) — matching the same idempotent-upsert
    convention already established by volume_area.py's compute_value_area_levels_for_stocks()
    for the same class of dated-snapshot table."""
    body = _sector_rotation_body()
    assert "on_conflict_do_update(" in body
    conflict_idx = body.index("on_conflict_do_update(")
    conflict_call = body[conflict_idx:body.index(")", body.index("index_elements", conflict_idx)) + 1]
    assert '"sector"' in conflict_call
    assert '"as_of"' in conflict_call


def test_sector_rotation_queries_the_snapshot_from_four_weeks_ago():
    """The trajectory comparison must be against a REAL persisted prior snapshot (~28 days
    back), not an arbitrary or hardcoded date — otherwise every trajectory would silently
    compare against the wrong week."""
    body = _sector_rotation_body()
    assert "timedelta(days=28)" in body
    assert "sector_rotation_snapshots" in body


def test_sector_rotation_folds_trajectory_into_the_same_redis_payload():
    """The existing stockai:sector_rotation Redis key must gain the new trajectory/rank/
    prior_rank fields directly on the SAME `rotation` dict already being cached — nothing
    that reads this key today should need a new fetch or a schema migration to see the new
    fields; a caller that doesn't know about them yet just ignores the extra keys."""
    body = _sector_rotation_body()
    setex_idx = body.index('_get_redis().setex("stockai:sector_rotation"')
    trajectory_fold_idx = body.index('rotation[sector]["trajectory"]')
    assert trajectory_fold_idx < setex_idx, (
        "trajectory must be folded into `rotation` BEFORE the existing Redis setex call, "
        "not written to a separate/new key"
    )


def test_sector_rotation_persist_happens_inside_the_same_session_as_the_read():
    """The SectorRotationSnapshot writes must happen inside the same `with SessionLocal()`
    block the rotation query itself uses, with an explicit sess.commit() — not a separate,
    unguarded session that could silently never persist if something else in the function
    raises first."""
    body = _sector_rotation_body()
    with_idx = body.index("with SessionLocal() as sess:")
    commit_idx = body.index("sess.commit()")
    assert with_idx < commit_idx
    # the upsert loop itself must be inside the same indented block as the session, not
    # dedented back out to module scope after the `with` exits
    insert_idx = body.index("_pg_insert(SectorRotationSnapshot)")
    assert with_idx < insert_idx < commit_idx


def test_sector_rotation_insufficient_data_branch_uses_the_correct_key_names():
    """AUD-T258-SECTORKEY: the insufficient-data branch (recent_kscore or prior_kscore is
    None) must write recent_kscore/prior_kscore — NOT the wrong "recent"/"prior" keys — since
    rank_sectors() reads data.get("recent_kscore"), and a sector with a real current K-score
    but no prior data (newly rankable) must not be silently dropped from ranking just because
    this branch used the wrong dict keys."""
    body = _sector_rotation_body()
    branch_idx = body.index("if row.recent_kscore is None or row.prior_kscore is None:")
    next_branch_idx = body.index("delta = float(row.recent_kscore) - float(row.prior_kscore)")
    branch_body = body[branch_idx:next_branch_idx]
    # only look at the actual dict literal assignment, not the explanatory comment above it
    dict_start = branch_body.index("rotation[row.sector] = {")
    dict_body = branch_body[dict_start:]
    assert '"recent_kscore"' in dict_body
    assert '"prior_kscore"' in dict_body
    assert '"recent":' not in dict_body
    assert '"prior":' not in dict_body


def test_sector_rotation_prior_snapshot_query_has_a_lower_bound_not_just_upper():
    """AUD-T258-STALESNAPSHOT: the prior-snapshot lookup must have BOTH an upper bound
    (>= 4 weeks old) and a lower bound (not more than ~8 weeks old) — without a floor, a gap
    in the weekly job could silently pick a months-old snapshot and still present it as a
    genuine ~4-week trajectory."""
    body = _sector_rotation_body()
    assert "eight_weeks_ago" in body
    assert "timedelta(days=56)" in body
    query_idx = body.index("SELECT MAX(as_of) FROM sector_rotation_snapshots")
    query_end = body.index('"""', query_idx)
    query_text = body[query_idx:query_end]
    assert "as_of <= :cutoff" in query_text
    assert "as_of >= :floor" in query_text
