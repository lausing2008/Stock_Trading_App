"""AQ06 delivery metadata write, in the same row-lock order signal generation uses.

signal-engine persists one stock per transaction, upserting its horizons SHORT -> SWING ->
LONG -> GROWTH and holding each row until commit. This writer updates many signal rows in one
transaction; in any other order the two can each hold a row the other wants, and PostgreSQL
aborts one of them (40P01, docs/audits/2026-10-27-signal-upsert-delivery-deadlock-review.md).

Ordering by (stock_id, generation rank) removes that cycle: when this writer waits on a row a
generator holds, everything it already holds for that stock ranks lower, which the generator
has already passed. The order is read from the rows themselves, never from subscription order,
dict insertion order, horizon spelling or numeric id.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update

# Must equal the order signal-engine's generate_all_signals() returns and persists horizons in
# (generators/signals.py); test_signal_delivery_lock_order.py fails if the two drift apart.
GENERATION_HORIZON_ORDER = ("SHORT", "SWING", "LONG", "GROWTH")
_RANK = {h: i for i, h in enumerate(GENERATION_HORIZON_ORDER)}


def _horizon_name(horizon) -> str:
    return getattr(horizon, "value", horizon)


def canonical_write_order(targets) -> list[int]:
    """Signal ids from (id, stock_id, horizon) rows, in generation lock order.

    An unknown horizon sorts after every known one rather than raising: refusing would drop
    the whole run's delivery evidence. The drift test is what keeps the list complete.
    """
    def key(row):
        name = _horizon_name(row[2])
        return (row[1], _RANK.get(name, len(_RANK)), name, row[0])
    return [row[0] for row in sorted(targets, key=key)]


def persist_delivery_decisions(session, signal_model, decisions: dict[int, dict], *,
                               decided_at: datetime, version: str) -> int:
    """One UPDATE per selected Signal row, in canonical order; returns the rows written.

    The SELECT takes no row locks. Ids whose row no longer exists are skipped, which is what
    an UPDATE matching nothing did before.
    """
    if not decisions:
        return 0
    targets = session.execute(
        select(signal_model.id, signal_model.stock_id, signal_model.horizon)
        .where(signal_model.id.in_(list(decisions)))
    ).all()
    ordered = canonical_write_order(targets)
    for signal_id in ordered:
        decision = decisions[signal_id]
        session.execute(
            update(signal_model).where(signal_model.id == signal_id).values(
                alert_delivery_state=decision["state"],
                alert_delivered_recipient_count=decision["recipients"],
                alert_delivery_decided_at=decided_at,
                alert_delivery_version=version,
            )
        )
    return len(ordered)
