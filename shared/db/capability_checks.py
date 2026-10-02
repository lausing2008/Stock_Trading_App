"""M24 — the actual capability matrix: real schema and constraint checks, not ledger entries.

Each check answers a CAPABILITY question ("can the outbox enqueue idempotently") rather than a
migration question ("did statement X run"). A table can exist with its uniqueness constraint
missing, or exist and be unreadable by this role — both are capability failures that a ledger
entry reports as success.

Every check returns `(Readiness, evidence)` and NEVER raises for an expected condition. A
permission error and a missing table are different facts: the first is UNKNOWN (we were not
allowed to look), the second is NOT_READY (we looked, and it is absent).
"""
from __future__ import annotations

from common.capabilities import Capability, Impact, Readiness, Requirement


def _table_exists(engine, table: str):
    def _check():
        from sqlalchemy import inspect as _sa_inspect
        try:
            names = set(_sa_inspect(engine).get_table_names())
        except Exception as exc:                        # noqa: BLE001
            # Could not look. That is not evidence of absence.
            return Readiness.UNKNOWN, f"could not inspect schema: {type(exc).__name__}: {exc}"[:200]
        if table in names:
            return Readiness.READY, f"table {table!r} present"
        return Readiness.NOT_READY, f"table {table!r} is absent"
    return _check


def _unique_constraint(engine, table: str, column: str):
    """A UNIQUE constraint is the capability here, not the column.

    The outbox's idempotency IS the constraint — without it duplicate enqueues succeed and the
    guarantee the whole design rests on is silently gone, while the table still exists and every
    insert still works.
    """
    def _check():
        from sqlalchemy import inspect as _sa_inspect
        try:
            insp = _sa_inspect(engine)
            if table not in set(insp.get_table_names()):
                return Readiness.NOT_READY, f"table {table!r} is absent"
            uniques = list(insp.get_unique_constraints(table))
            indexes = [i for i in insp.get_indexes(table) if i.get("unique")]
        except Exception as exc:                        # noqa: BLE001
            return Readiness.UNKNOWN, f"could not inspect constraints: {type(exc).__name__}"[:200]
        # EXACT KEY COLUMNS, not "contains". A unique (event_id, other_column) permits many
        # rows per event_id and would have reported ready — a latent false-ready defect found
        # by review on 2026-10-02. Production's constraints are genuinely single-column, so
        # nothing was mis-reported; the check simply could not have told the difference.
        partials = []
        for c in uniques + indexes:
            cols = list(c.get("column_names") or [])
            if cols != [column]:
                if column in cols:
                    partials.append(cols)
                continue
            # A PARTIAL index constrains only the rows its predicate admits. `WHERE col IS NOT
            # NULL` is the intended shape here (uniqueness among assigned ids); any other
            # predicate narrows the guarantee in a way this check cannot evaluate, so it is
            # reported rather than accepted silently.
            pred = (c.get("dialect_options") or {}).get("postgresql_where")
            if pred is None:
                return Readiness.READY, f"single-column unique constraint on {table}.{column}"
            text_pred = str(pred).lower().replace(" ", "")
            if "isnotnull" in text_pred:
                return Readiness.READY, (
                    f"unique on {table}.{column} for assigned (non-null) values; uniqueness is "
                    f"NOT presence — a row may still carry no value")
            return Readiness.UNKNOWN, (
                f"{table}.{column} has a unique index with predicate {pred!r}; this check "
                f"cannot establish which rows it covers")
        if partials:
            return Readiness.NOT_READY, (
                f"{table}.{column} appears only in COMPOSITE unique keys {partials} — those "
                f"permit many rows per {column}, so idempotency is not enforced")
        return Readiness.NOT_READY, (
            f"{table}.{column} has NO unique constraint — duplicate rows would be accepted, so "
            f"idempotency is not enforced even though the table exists")
    return _check


def _columns_exist(engine, table: str, columns: tuple[str, ...]):
    def _check():
        from sqlalchemy import inspect as _sa_inspect
        try:
            insp = _sa_inspect(engine)
            if table not in set(insp.get_table_names()):
                return Readiness.NOT_READY, f"table {table!r} is absent"
            have = {c["name"] for c in insp.get_columns(table)}
        except Exception as exc:                        # noqa: BLE001
            return Readiness.UNKNOWN, f"could not inspect columns: {type(exc).__name__}"[:200]
        missing = [c for c in columns if c not in have]
        if missing:
            return Readiness.NOT_READY, f"{table} is missing {', '.join(missing)}"
        return Readiness.READY, f"{table} has {', '.join(columns)}"
    return _check


def build_matrix(engine) -> list[Capability]:
    """The five capabilities this platform's newer machinery depends on."""
    return [
        Capability(
            name="outbox_enqueue",
            blocks="earnings-phase notifications cannot be queued; the legacy sender continues",
            impact=Impact.DELIVERY,
            recovery=("nothing is queued, so nothing is lost while the legacy sender owns "
                      "delivery; enqueueing resumes when the table returns"),
            requirements=[
                Requirement("notification_outbox table", "the durable queue itself",
                            _table_exists(engine, "notification_outbox")),
                Requirement("event_id uniqueness",
                            "the idempotency guarantee — without it duplicates are accepted",
                            _unique_constraint(engine, "notification_outbox", "event_id")),
            ],
        ),
        Capability(
            name="outbox_drain",
            blocks="queued notifications cannot be delivered; they remain queued, not lost",
            impact=Impact.DELIVERY,
            recovery="rows stay pending and are drained once the prerequisite returns",
            requirements=[
                Requirement("lease and state columns",
                            "claiming, expiry and the acceptance/crash gap",
                            _columns_exist(engine, "notification_outbox",
                                           ("state", "lease_owner", "lease_expires_at",
                                            "dispatch_started_at", "attempts"))),
            ],
        ),
        Capability(
            name="broker_submission",
            blocks="broker orders cannot be submitted through the durable path",
            impact=Impact.ENTRY,
            recovery=("intents remain `pending` and are submitted once the prerequisite "
                      "returns; no order is placed twice because the claim is compare-and-set"),
            requirements=[
                Requirement("submission state columns",
                            "intent identity and submission state BEFORE any broker side effect",
                            _columns_exist(engine, "paper_trades",
                                           ("broker_submission_state", "broker_client_order_id",
                                            "broker_submission_path", "broker_submit_attempts"))),
                Requirement("client order id uniqueness",
                            "one identity per intent, so a retry cannot mint a second",
                            _unique_constraint(engine, "paper_trades",
                                               "broker_client_order_id")),
            ],
        ),
        Capability(
            name="exposure_reservation",
            blocks="new paper ENTRIES are refused — the concentration cap cannot be enforced",
            impact=Impact.ENTRY,
            recovery=("entries are refused rather than taken without a cap; existing positions "
                      "are untouched and protective exits are unaffected"),
            requirements=[
                Requirement("portfolio_exposure_reservations table",
                            "atomic reserve/consume for the concentration cap",
                            _table_exists(engine, "portfolio_exposure_reservations")),
                Requirement("intent_id uniqueness",
                            "one reservation per proposed entry",
                            _unique_constraint(engine, "portfolio_exposure_reservations",
                                               "intent_id")),
            ],
        ),
        Capability(
            name="submission_reconciliation",
            blocks=("unknown broker outcomes cannot be resolved — they accumulate unreviewed, "
                    "holding exposure"),
            # RECONCILIATION, not ENTRY: establishing what happened must not be gated by an
            # entry-side prerequisite, or a degraded entry path would also blind the thing that
            # cleans up after it.
            impact=Impact.RECONCILIATION,
            recovery=("unknown rows are retained and remain reviewable; none is auto-retried, "
                      "so nothing is duplicated while reconciliation is unavailable"),
            requirements=[
                Requirement("order identity columns",
                            "matching an unknown submission to the broker's own record",
                            _columns_exist(engine, "paper_trades",
                                           ("broker_order_id", "broker_client_order_id",
                                            "broker_submitted_at"))),
            ],
        ),
    ]
