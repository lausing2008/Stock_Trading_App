"""Tests for AUD-PTH08-PERSISTENTGATELOG: _persist_scan_log() (paper_trading_engine.py) and its
wiring into _write_gate_block()/_write_no_entry_summary(), plus the new paper_entry_scan_logs
purge step in scheduler.py.

An independent audit (PT-H08, docs/audits/2026-09-19-paper-trading-horizon-threshold-audit.md)
and this project's own live investigation of a portfolio that had gone 16+ days without a trade
both hit the same wall: the ONLY record of why a scan blocked/skipped entries lived in Redis
keys with a 4-hour TTL, long expired by the time anyone actually asked. _persist_scan_log()
writes the same data to a durable table instead.

AUD-CONNPOOL-NESTEDSESSION (2026-09-22 production incident): the ORIGINAL version of
_persist_scan_log() opened its own `with SessionLocal() as session:` here — a SEPARATE
connection from the pool, on top of the one paper_trading_step()'s own outer session already
held for its entire multi-portfolio scan loop. Every portfolio hitting "no entry" (the exact
condition this logging exists to diagnose) opened an extra connection every cycle; with most
portfolios in that state and a small shared pool (5 + 10 overflow = 15, split across a dozen
other 1-minute jobs), this exhausted the pool within hours — confirmed live via a py-spy thread
dump showing a worker stuck exactly at this call's own session.commit(), and QueuePool timeout
errors cascading into unrelated jobs platform-wide. Fixed to REUSE the caller's already-open
session (via `session.begin_nested()`, a SAVEPOINT — so a failure here rolls back only this one
insert, not the caller's whole in-progress transaction) instead of opening a new one.

This file confirms it actually persists a row (via a real in-memory SQLite engine + the real
PaperEntryScanLog model, matching test_drawdown_alert.py's established technique for testing a
DB-writing function in this stubbed-db test environment), that it fails silently like the Redis
writes it accompanies WITHOUT poisoning the caller's own session, that it never opens a second
connection, and that both call sites are actually wired to it.
"""
import pathlib
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util

from sqlalchemy import create_engine, select, Integer
from sqlalchemy.orm import Session, sessionmaker

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_scanlog", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_scanlog"] = _models
_spec.loader.exec_module(_models)

# The real column is BigInteger (BIGSERIAL in Postgres, matching SignalOutcomeHorizon's own
# precedent) — SQLite only treats a PK column as the rowid-autoincrement alias when its
# declared type is exactly "INTEGER", not "BIGINT", so a plain create_all() here would reject
# every insert with a NOT NULL constraint failure on `id`. Test-only: swap the type before
# create_all so this in-memory engine can actually exercise the insert path; production
# (Postgres) is unaffected.
_models.PaperEntryScanLog.__table__.c.id.type = Integer()

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[_models.PaperPortfolio.__table__, _models.PaperEntryScanLog.__table__]
)
_TestSessionLocal = sessionmaker(bind=_ENGINE)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

PaperEntryScanLog = _models.PaperEntryScanLog

_ENGINE_SOURCE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_ENGINE_SOURCE = _ENGINE_SOURCE_PATH.read_text()


def _extract_persist_scan_log():
    start = _ENGINE_SOURCE.index("def _persist_scan_log(")
    end = _ENGINE_SOURCE.index("\n\ndef _write_gate_block(", start)
    func_source = _ENGINE_SOURCE[start:end]
    namespace = {"PaperEntryScanLog": PaperEntryScanLog}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_persist_scan_log"]


_persist_scan_log = _extract_persist_scan_log()


def _write_gate_block_body() -> str:
    start = _ENGINE_SOURCE.index("def _write_gate_block(")
    end = _ENGINE_SOURCE.index("\n\n_SKIP_REASON_LABEL", start)
    return _ENGINE_SOURCE[start:end]


def _write_no_entry_summary_body() -> str:
    start = _ENGINE_SOURCE.index("def _write_no_entry_summary(")
    end = _ENGINE_SOURCE.index("\n\ndef _clear_no_entry_summary(", start)
    return _ENGINE_SOURCE[start:end]


def test_persist_scan_log_writes_a_durable_gate_block_row():
    with Session(_ENGINE) as session:
        _persist_scan_log(session, 42, portfolio_gate="drawdown", portfolio_gate_reason="down 12% from peak")
        session.commit()  # matches the caller's own eventual commit, not a commit this function makes itself

    with Session(_ENGINE) as session:
        rows = session.execute(
            select(PaperEntryScanLog).where(PaperEntryScanLog.portfolio_id == 42)
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].portfolio_gate == "drawdown"
    assert rows[0].portfolio_gate_reason == "down 12% from peak"
    assert rows[0].candidates_seen is None
    assert rows[0].skip_tally is None


def test_persist_scan_log_writes_a_durable_no_entry_summary_row():
    with Session(_ENGINE) as session:
        _persist_scan_log(session, 43, candidates_seen=7, skip_tally={"kscore": 5, "stop_cooldown": 2})
        session.commit()

    with Session(_ENGINE) as session:
        rows = session.execute(
            select(PaperEntryScanLog).where(PaperEntryScanLog.portfolio_id == 43)
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].portfolio_gate is None
    assert rows[0].candidates_seen == 7
    assert rows[0].skip_tally == {"kscore": 5, "stop_cooldown": 2}


def test_persist_scan_log_never_opens_its_own_session():
    """AUD-CONNPOOL-NESTEDSESSION: the exact regression this fix prevents — the live CODE must
    not reference SessionLocal at all (a second, independent connection from the pool), only
    the `session` parameter the caller already has open. Checks only the code after the
    docstring, which legitimately mentions "SessionLocal()" in prose while explaining the fix."""
    start = _ENGINE_SOURCE.index("def _persist_scan_log(")
    end = _ENGINE_SOURCE.index("\n\ndef _write_gate_block(", start)
    full = _ENGINE_SOURCE[start:end]
    code_only = full[full.index('"""', full.index('"""') + 3) + 3:]
    assert "SessionLocal()" not in code_only
    assert "def _persist_scan_log(\n    session," in full, "session must be the first parameter"


def test_persist_scan_log_uses_a_savepoint_not_the_outer_transaction_directly():
    """A bare session.add() with a swallowed exception would leave a REAL SQLAlchemy session in
    its post-flush-error "inert, needs rollback" state — silently breaking every later use of
    the same session for the rest of the scan cycle. begin_nested() (a SAVEPOINT) confines a
    failure to just this one insert."""
    start = _ENGINE_SOURCE.index("def _persist_scan_log(")
    end = _ENGINE_SOURCE.index("\n\ndef _write_gate_block(", start)
    body = _ENGINE_SOURCE[start:end]
    assert "with session.begin_nested():" in body
    try_idx = body.index("try:")
    nested_idx = body.index("with session.begin_nested():")
    assert try_idx < nested_idx


def test_persist_scan_log_fails_silently_when_the_insert_raises():
    """Must not raise — this is informational-only, like the Redis writes it accompanies."""
    class _ExplodingSession:
        def begin_nested(self):
            raise RuntimeError("db unavailable")

    _persist_scan_log(_ExplodingSession(), 99, portfolio_gate="drawdown", portfolio_gate_reason="x")


def test_a_failed_persist_does_not_poison_the_callers_real_session():
    """The whole point of the savepoint: after _persist_scan_log() fails (e.g. a constraint
    violation on the insert itself), the SAME session must still be usable for the caller's own
    subsequent work in this scan cycle — not left in a broken, rollback-required state."""
    with Session(_ENGINE) as session:
        # Force a real failure inside the nested block: skip_tally must be JSON-serializable;
        # a raw, non-serializable object triggers a real error during flush.
        _persist_scan_log(session, 44, candidates_seen=1, skip_tally={"bad": object()})
        # The session must still accept further real work after that failure.
        _persist_scan_log(session, 44, candidates_seen=2, skip_tally={"kscore": 1})
        session.commit()

    with Session(_ENGINE) as session:
        rows = session.execute(
            select(PaperEntryScanLog).where(PaperEntryScanLog.portfolio_id == 44)
        ).scalars().all()
    # Only the SECOND (valid) call's row survived — the first failed insert was rolled back to
    # its own savepoint, not silently left half-applied, and did not block the second from committing.
    assert len(rows) == 1
    assert rows[0].candidates_seen == 2


def test_write_gate_block_calls_persist_scan_log_with_the_session_gate_and_reason():
    body = _write_gate_block_body()
    assert "def _write_gate_block(session, portfolio_id: int, gate: str, reason: str)" in body
    assert "_persist_scan_log(session, portfolio_id, portfolio_gate=gate, portfolio_gate_reason=reason)" in body
    # Must run even if the Redis write above it fails — outside the try/except, not inside.
    try_idx = body.index("try:")
    except_idx = body.index("except Exception:")
    persist_idx = body.index("_persist_scan_log(session, portfolio_id")
    assert persist_idx > except_idx > try_idx


def test_write_no_entry_summary_calls_persist_scan_log_with_the_session_and_tally():
    body = _write_no_entry_summary_body()
    assert "def _write_no_entry_summary(session, portfolio_id: int, candidates_seen: int, skip_tally: dict[str, int])" in body
    assert "_persist_scan_log(session, portfolio_id, candidates_seen=candidates_seen, skip_tally=skip_tally)" in body
    try_idx = body.index("try:")
    except_idx = body.index("except Exception:")
    persist_idx = body.index("_persist_scan_log(session, portfolio_id")
    assert persist_idx > except_idx > try_idx


def test_every_write_gate_block_call_site_passes_the_scan_session():
    """Regression guard: all 14 real call sites (all inside _scan_for_entries(), which already
    has `session` in scope) must pass it through — a single site left on the old shape would
    just be a straight TypeError at runtime, but this catches it at test time instead."""
    count = _ENGINE_SOURCE.count("_write_gate_block(session, portfolio.id,")
    assert count == 14, f"expected 14 call sites passing session, found {count}"


def test_write_no_entry_summary_call_site_passes_the_scan_session():
    assert "_write_no_entry_summary(session, portfolio.id, len(buy_signals), _skip_tally)" in _ENGINE_SOURCE


def test_paper_entry_scan_log_imported_from_db():
    import_start = _ENGINE_SOURCE.index("from db import (")
    import_end = _ENGINE_SOURCE.index(")", import_start)
    assert "PaperEntryScanLog" in _ENGINE_SOURCE[import_start:import_end]


def test_purge_old_data_deletes_paper_entry_scan_logs_older_than_90_days():
    scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
    source = scheduler_path.read_text()
    start = source.index("def _purge_old_data(")
    end = source.index("\ndef ", start + 1)
    body = source[start:end]
    assert "DELETE FROM paper_entry_scan_logs WHERE scanned_at < NOW() - INTERVAL '90 days'" in body
    assert "paper_entry_scan_logs_deleted=resscan.rowcount" in body
    # Same single-transaction discipline as the pre-existing three deletes.
    assert body.count("session.commit()") == 1
    commit_idx = body.index("session.commit()")
    scan_delete_idx = body.index("DELETE FROM paper_entry_scan_logs")
    assert scan_delete_idx < commit_idx
