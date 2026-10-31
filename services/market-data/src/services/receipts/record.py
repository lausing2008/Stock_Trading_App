"""Write measurement records.

THE TWO WRITES FAIL DIFFERENTLY, on purpose.

A price-risk scan record is written in the detection job's own transaction, beside the events it
describes. If it cannot be written the whole run rolls back and the job reports an error: an event
without its scan record would break the trial's accounting, and detection is shadow-only, so a
lost run costs a measurement, never an alert.

A paper entry receipt is written inside a SAVEPOINT of the entry's transaction and a failure is
logged, never raised. Measurement must not change whether a paper position opens. It is the same
transaction (no second connection: see docs/incidents/db-connection-pool-exhaustion.md), so a
receipt rolls back together with an entry that rolls back, which is the correct record of it.

An RZY rebuild trigger follows the receipt rule: a savepoint of the scan's own transaction,
logged and never raised, because measurement must not change whether a stream rebuilds.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import insert

from common.logging import get_logger

from .models import paper_entry_receipts, price_risk_scans, rzy_rebuild_triggers

log = get_logger(__name__)


def new_id() -> str:
    return uuid.uuid4().hex


def _naive(value):
    if isinstance(value, datetime) and value.tzinfo is not None:
        from datetime import timezone
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _jsonable(value):
    """JSON columns get plain values: dates as ISO strings, sets as sorted lists."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row(table, values: dict) -> dict:
    out = {}
    for col in table.columns:
        if col.name not in values:
            continue
        v = values[col.name]
        out[col.name] = _jsonable(v) if col.type.__class__.__name__ == "JSON" else _naive(v)
    return out


def record_price_risk_scan(session, values: dict) -> str:
    """Insert one scan row. Raises on failure (see module docstring)."""
    values = {"scan_id": new_id(), **values}
    session.execute(insert(price_risk_scans).values(**_row(price_risk_scans, values)))
    return values["scan_id"]


def record_entry_receipt(session, values: dict) -> str | None:
    """Insert one entry receipt in a savepoint. Returns its id, or None after logging the error."""
    values = {"receipt_id": new_id(), **values}
    try:
        with session.begin_nested():
            session.execute(insert(paper_entry_receipts).values(**_row(paper_entry_receipts, values)))
    except Exception as exc:  # noqa: BLE001 - measurement never changes the entry; logged, never silent
        log.error("paper.entry_receipt_failed", symbol=values.get("symbol"),
                  portfolio_id=values.get("portfolio_id"), outcome=values.get("outcome"),
                  error=f"{type(exc).__name__}: {exc}"[:300])
        return None
    return values["receipt_id"]


def record_rzy_trigger(session, values: dict) -> str | None:
    """Insert one rebuild-trigger row in a savepoint. Returns its id, or None after logging."""
    values = {"trigger_id": new_id(), **values}
    try:
        with session.begin_nested():
            session.execute(insert(rzy_rebuild_triggers).values(**_row(rzy_rebuild_triggers, values)))
    except Exception as exc:  # noqa: BLE001 - measurement never changes the rebuild; logged, never silent
        log.error("rzy.rebuild_trigger_record_failed", stream_key=values.get("stream_key"),
                  outcome=values.get("outcome"), error=f"{type(exc).__name__}: {exc}"[:300])
        return None
    return values["trigger_id"]
