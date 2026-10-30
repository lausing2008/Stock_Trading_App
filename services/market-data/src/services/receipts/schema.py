"""Create the measurement tables at market-data startup, the way the Intraday tables are created."""
from __future__ import annotations

from sqlalchemy import text

from .models import metadata

_TABLES = ("price_risk_scans", "paper_entry_receipts")

_PG_FUNCTION = """
CREATE OR REPLACE FUNCTION measurement_refuse_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% rows are written once and never rewritten', TG_TABLE_NAME;
END $$
"""


def create(conn) -> None:
    metadata.create_all(conn)
    if conn.dialect.name == "postgresql":
        conn.execute(text(_PG_FUNCTION))
        for t in _TABLES:
            conn.execute(text(f"DROP TRIGGER IF EXISTS {t}_no_update ON {t}"))
            conn.execute(text(f"CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} "
                              f"FOR EACH ROW EXECUTE FUNCTION measurement_refuse_update()"))


def ensure_schema() -> None:
    from db.session import _ddl_begin, _startup_schema_lock
    with _startup_schema_lock():
        with _ddl_begin() as conn:
            create(conn)


#: None until startup ran; then "ok" or "failed: <reason>".
SCHEMA_STATUS: str | None = None


def ensure_schema_safely() -> str:
    """A failure is recorded and printed, and never takes market-data down: the scan job then
    fails loudly on its first write, and entry receipts log an error per attempt."""
    global SCHEMA_STATUS
    try:
        ensure_schema()
        SCHEMA_STATUS = "ok"
    except Exception as exc:  # noqa: BLE001 - recorded and surfaced, never silent
        SCHEMA_STATUS = f"failed: {type(exc).__name__}: {exc}"[:500]
        print(f"[receipts] schema not ensured: {SCHEMA_STATUS}")
    return SCHEMA_STATUS
