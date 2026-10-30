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
    conn.execute(text("DROP TABLE IF EXISTS price_risk_scans, paper_entry_receipts CASCADE"))


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


def main():
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError, IntegrityError
    from sqlalchemy.orm import Session
    from src.services.receipts import schema
    from src.services.receipts.record import record_entry_receipt, record_price_risk_scan

    with pg.begin() as conn:
        schema.create(conn)
    with pg.begin() as conn:
        schema.create(conn)  # idempotent: startup runs it on every boot

    with Session(pg) as s:
        scan_id = record_price_risk_scan(s, _scan())
        receipt_id = record_entry_receipt(s, _receipt())
        s.commit()
    assert receipt_id is not None
    with pg.connect() as conn:
        row = conn.execute(text("SELECT excluded_subjects FROM price_risk_scans WHERE scan_id=:i"),
                           {"i": scan_id}).scalar_one()
        assert row == [{"symbol": "X", "as_of": "2026-10-30"}]

    for table, key, value in (("price_risk_scans", "scan_id", scan_id),
                              ("paper_entry_receipts", "receipt_id", receipt_id)):
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

    with pg.begin() as conn:  # deletion is left to a future retention policy, not refused
        conn.execute(text("DELETE FROM paper_entry_receipts WHERE symbol='AFTER'"))


try:
    main()
finally:
    with pg.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS price_risk_scans, paper_entry_receipts CASCADE"))
    pg.dispose()
print("receipts PostgreSQL probe passed")
