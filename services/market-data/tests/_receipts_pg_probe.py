"""Body of test_receipts_postgres.py, run in a subprocess so the conftest stubs (which replace
sqlalchemy with a mock) do not apply. RECEIPTS_PG_URL must name a DISPOSABLE database."""
import os
import pathlib
import sys
import types
from datetime import date, datetime

import pytest

PG_URL = os.environ["RECEIPTS_PG_URL"]
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "services" / "market-data")]
log = types.ModuleType("common.logging")
log.get_logger = lambda *a, **k: types.SimpleNamespace(error=lambda *a, **k: None)
sys.modules["common.logging"] = log
from sqlalchemy import create_engine, text  # noqa: E402

pg = create_engine(PG_URL)
with pg.begin() as conn:
    conn.execute(text("DROP TABLE IF EXISTS price_risk_scans, paper_entry_receipts, rzy_rebuild_triggers CASCADE"))


def _scan(**over):
    return {"run_id": "r1", "scope": "symbol", "status": "completed", "trigger": "test",
            "started_at": datetime(2026, 11, 2, 15, 5), "finished_at": datetime(2026, 11, 2, 15, 6),
            "excluded_subjects": [{"symbol": "X", "as_of": date(2026, 10, 30)}],
            "release_sha": "abc", "code_fingerprint": "f" * 64, **over}


def _receipt(**over):
    return {"decided_at": datetime(2026, 11, 2, 15, 5), "portfolio_id": 1, "symbol": "NVDA",
            "outcome": "rejected", "reason": "sector_cap", "freshness": "all_marks_live",
            "marks": [{"symbol": "AMD", "fetched_at": "2026-11-02T15:04:00+00:00"}],
            "release_sha": "abc", "code_fingerprint": "f" * 64, **over}


def _trigger(**over):
    return {"observed_at": datetime(2026, 11, 2, 15, 5), "recorded_at": datetime(2026, 11, 2, 15, 6),
            "stream_key": "MU:5m:h", "symbol": "MU", "timeframe": "5m", "outcome": "rebuilt",
            "reason": "stored_completed_bar_changed", "change_class": "material_ohlc",
            "before": {"close": 1.0}, "after": {"close": 1.1},
            "release_sha": "abc", "code_fingerprint": "f" * 64, **over}


def main():
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, IntegrityError
    from sqlalchemy.orm import Session
    from src.services.receipts import schema
    from src.services.receipts.record import record_entry_receipt, record_price_risk_scan, record_rzy_trigger

    with pg.begin() as conn:
        schema.create(conn)
    with pg.begin() as conn:
        schema.create(conn)  # idempotent: startup runs it on every boot

    with Session(pg) as s:
        scan_id = record_price_risk_scan(s, _scan())
        receipt_id = record_entry_receipt(s, _receipt())
        trigger_id = record_rzy_trigger(s, _trigger())
        s.commit()
    assert receipt_id is not None and trigger_id is not None
    with pg.connect() as conn:
        row = conn.execute(text("SELECT excluded_subjects FROM price_risk_scans WHERE scan_id=:i"),
                           {"i": scan_id}).scalar_one()
        assert row == [{"symbol": "X", "as_of": "2026-10-30"}]

    for table, key, value in (("price_risk_scans", "scan_id", scan_id),
                              ("paper_entry_receipts", "receipt_id", receipt_id),
                              ("rzy_rebuild_triggers", "trigger_id", trigger_id)):
        with pytest.raises(DBAPIError, match="never rewritten"):
            with pg.begin() as conn:
                conn.execute(text(f"UPDATE {table} SET release_sha='x' WHERE {key}=:v"), {"v": value})

    with pytest.raises(IntegrityError):
        with Session(pg) as s:
            record_price_risk_scan(s, _scan(status="maybe"))
            s.commit()

    # A failed receipt (bad freshness value) is logged and skipped; the SAVEPOINT keeps the
    # caller's transaction usable, so the entry's own writes still commit.
    with Session(pg) as s:
        assert record_entry_receipt(s, _receipt(freshness="bogus")) is None
        record_entry_receipt(s, _receipt(symbol="AFTER"))
        s.commit()
    with pg.connect() as conn:
        symbols = conn.execute(text("SELECT symbol FROM paper_entry_receipts ORDER BY symbol")).scalars().all()
    assert symbols == ["AFTER", "NVDA"]

    # A trigger row with an unknown class is refused by the database, logged, and does not
    # poison the scan's transaction.
    with Session(pg) as s:
        assert record_rzy_trigger(s, _trigger(change_class="volume_only")) is None
        record_rzy_trigger(s, _trigger(symbol="AFTER"))
        s.commit()
    with pg.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM rzy_rebuild_triggers")).scalar_one() == 2

    with pg.begin() as conn:  # deletion is left to a future retention policy, not refused
        conn.execute(text("DELETE FROM paper_entry_receipts WHERE symbol='AFTER'"))


try:
    main()
finally:
    with pg.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS price_risk_scans, paper_entry_receipts, rzy_rebuild_triggers CASCADE"))
    pg.dispose()
print("receipts PostgreSQL probe passed")
