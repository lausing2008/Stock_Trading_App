"""Tests for AUD-PTH08-PERSISTENTGATELOG: _persist_scan_log() (paper_trading_engine.py) and its
wiring into _write_gate_block()/_write_no_entry_summary(), plus the new paper_entry_scan_logs
purge step in scheduler.py.

An independent audit (PT-H08, docs/audits/2026-09-19-paper-trading-horizon-threshold-audit.md)
and this project's own live investigation of a portfolio that had gone 16+ days without a trade
both hit the same wall: the ONLY record of why a scan blocked/skipped entries lived in Redis
keys with a 4-hour TTL, long expired by the time anyone actually asked. _persist_scan_log()
writes the same data to a durable table instead — this file confirms it actually persists a row
(via a real in-memory SQLite engine + the real PaperEntryScanLog model, matching
test_drawdown_alert.py's established technique for testing a DB-writing function in this
stubbed-db test environment), that it fails silently like the Redis writes it accompanies
(never allowed to break a real trading scan), and that both call sites are actually wired to it.
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


def _extract_persist_scan_log(session_local):
    start = _ENGINE_SOURCE.index("def _persist_scan_log(")
    end = _ENGINE_SOURCE.index("\n\ndef _write_gate_block(", start)
    func_source = _ENGINE_SOURCE[start:end]
    namespace = {
        "SessionLocal": session_local,
        "PaperEntryScanLog": PaperEntryScanLog,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_persist_scan_log"]


def _write_gate_block_body() -> str:
    start = _ENGINE_SOURCE.index("def _write_gate_block(")
    end = _ENGINE_SOURCE.index("\n\n_SKIP_REASON_LABEL", start)
    return _ENGINE_SOURCE[start:end]


def _write_no_entry_summary_body() -> str:
    start = _ENGINE_SOURCE.index("def _write_no_entry_summary(")
    end = _ENGINE_SOURCE.index("\n\ndef _clear_no_entry_summary(", start)
    return _ENGINE_SOURCE[start:end]


def test_persist_scan_log_writes_a_durable_gate_block_row():
    persist_scan_log = _extract_persist_scan_log(_TestSessionLocal)
    persist_scan_log(42, portfolio_gate="drawdown", portfolio_gate_reason="down 12% from peak")

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
    persist_scan_log = _extract_persist_scan_log(_TestSessionLocal)
    persist_scan_log(43, candidates_seen=7, skip_tally={"kscore": 5, "stop_cooldown": 2})

    with Session(_ENGINE) as session:
        rows = session.execute(
            select(PaperEntryScanLog).where(PaperEntryScanLog.portfolio_id == 43)
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].portfolio_gate is None
    assert rows[0].candidates_seen == 7
    assert rows[0].skip_tally == {"kscore": 5, "stop_cooldown": 2}


def test_persist_scan_log_fails_silently_when_the_session_raises():
    class _ExplodingSessionLocal:
        def __call__(self):
            raise RuntimeError("db unavailable")

    persist_scan_log = _extract_persist_scan_log(_ExplodingSessionLocal())
    # Must not raise — this is informational-only, like the Redis writes it accompanies.
    persist_scan_log(99, portfolio_gate="drawdown", portfolio_gate_reason="x")


def test_write_gate_block_calls_persist_scan_log_with_the_same_gate_and_reason():
    body = _write_gate_block_body()
    assert "_persist_scan_log(portfolio_id, portfolio_gate=gate, portfolio_gate_reason=reason)" in body
    # Must run even if the Redis write above it fails — outside the try/except, not inside.
    try_idx = body.index("try:")
    except_idx = body.index("except Exception:")
    persist_idx = body.index("_persist_scan_log(portfolio_id")
    assert persist_idx > except_idx > try_idx


def test_write_no_entry_summary_calls_persist_scan_log_with_the_same_tally():
    body = _write_no_entry_summary_body()
    assert "_persist_scan_log(portfolio_id, candidates_seen=candidates_seen, skip_tally=skip_tally)" in body
    try_idx = body.index("try:")
    except_idx = body.index("except Exception:")
    persist_idx = body.index("_persist_scan_log(portfolio_id")
    assert persist_idx > except_idx > try_idx


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
