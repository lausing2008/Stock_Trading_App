"""Serialized startup DDL, and the timeouts that keep it from blocking the platform.

AUD-DEPLOY-MIGRATIONCONVOY (2026-10-12). Twelve backend containers each run `init_db()` at
startup, and `init_db()` issues ~70 `ALTER TABLE` statements. A twelve-service deploy therefore
pointed twelve concurrent DDL transactions at a live database. One connection left
`idle in transaction` by the rolling restart held a lock on `realtime_news_items`;
event-intelligence's `ALTER` queued behind it; and **that pending ACCESS EXCLUSIVE lock then
queued every later reader behind it, including queries against tables the migration never
mentions**. 25 sessions blocked and one service never finished startup.

Three independent things were wrong, so there are three settings here and not one:

1. **Nothing serialized the twelve.** `docker compose up -d` returns when a container has
   STARTED, not when it is healthy, so the deploy script's sequential loop does not extend to
   application startup. Serialization has to live in the application. `MIGRATION_LOCK_KEY` is a
   Postgres advisory lock taken for the whole of `init_db()`: the other eleven wait their turn
   rather than competing.

2. **A blocked ALTER waited forever, and took the database's readers with it.** This is the part
   that turned one stuck connection into a platform-wide stall. `LOCK_TIMEOUT_MS` bounds it: the
   statement fails instead of sitting in the lock queue, and a failure is recoverable and loud
   where an indefinite wait is neither. The timeout is generous relative to any real DDL here —
   every statement is `ADD COLUMN IF NOT EXISTS` or an index create, which take milliseconds on
   an uncontended table. Hitting it means contention, which is exactly when failing is right.

3. **An orphaned transaction could hold its lock indefinitely.** `IDLE_IN_TX_TIMEOUT_MS` is
   applied to every connection this application opens, so a container killed mid-transaction
   releases its locks on a timer instead of waiting for a human. It is deliberately scoped to
   this app's own connections rather than set on the database, because the observed orphan was
   one of ours and a database-wide setting would also apply to clients we do not control.

WHY THE LOCK WAIT IS SO MUCH LONGER THAN THE LOCK TIMEOUT. They answer different questions.
`LOCK_TIMEOUT_MS` asks "is this one table free?" and should give up quickly. `LOCK_WAIT_SECONDS`
asks "has the service ahead of me finished its migrations?", and the honest answer during a
twelve-service deploy is that it may legitimately take minutes. Waiting there is correct
behaviour, not a stall — the service is idle, holding nothing.
"""
from __future__ import annotations

# A fixed, arbitrary 64-bit key. Any value works provided every service uses the SAME one;
# it is written as a literal rather than hashed from a string so that reading two services'
# logs side by side shows the identical number.
MIGRATION_LOCK_KEY = 0x53544B41445F4C4B  # "STKAD_LK"

LOCK_TIMEOUT_MS = 15_000
IDLE_IN_TX_TIMEOUT_MS = 300_000
LOCK_WAIT_SECONDS = 900.0

# Postgres SQLSTATEs. 55P03 lock_not_available is what LOCK_TIMEOUT_MS produces; 57014 is a
# statement_timeout cancel, which is a different setting but the same "contended, try again"
# situation from a caller's point of view.
_RETRYABLE_SQLSTATES = ("55P03", "57014")


def is_postgres(dialect_name: str) -> bool:
    """Every statement in this module is Postgres-specific.

    Tests and local tooling may run against SQLite, where `SET LOCAL lock_timeout` is a syntax
    error. Returning False there has to skip the settings, NOT silently pretend they applied —
    which is why callers use this to choose a branch rather than to swallow an exception.
    """
    return (dialect_name or "").startswith("postgres")


def ddl_session_statements() -> tuple[str, ...]:
    """`SET LOCAL` statements every startup-DDL transaction issues before its first ALTER.

    `SET LOCAL` and not `SET`: these connections go back to a pool that also serves ordinary
    requests, and a 15-second `lock_timeout` leaking into request handling would turn ordinary
    row contention into user-visible errors. `LOCAL` ends the setting with the transaction.
    """
    return (
        f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'",
        f"SET LOCAL idle_in_transaction_session_timeout = '{IDLE_IN_TX_TIMEOUT_MS}ms'",
    )


def connect_options(database_url: str) -> dict:
    """`connect_args` for the engine — applies the idle-transaction timeout to EVERY connection.

    This is the half of the fix that addresses the orphan rather than the victim. The DDL
    timeouts above protect the migration from a stuck session; this protects every other session
    from becoming that stuck session, including the request-serving connections that have no
    `SET LOCAL` of their own.
    """
    if not is_postgres(database_url.split(":", 1)[0] if database_url else ""):
        return {}
    return {"options": f"-c idle_in_transaction_session_timeout={IDLE_IN_TX_TIMEOUT_MS}"}


def is_lock_contention(exc: BaseException) -> bool:
    """True when a DDL failure means "someone else held the lock", not "the statement is wrong".

    Read from the driver's SQLSTATE rather than the message text, because the message is
    localized and reworded between server versions while the code is not. The fallback to a
    text match exists only for drivers that do not surface `pgcode`; it widens what counts as
    retryable and never narrows it.
    """
    code = getattr(getattr(exc, "orig", None), "pgcode", None) or getattr(exc, "pgcode", None)
    if code:
        return str(code) in _RETRYABLE_SQLSTATES
    text = str(exc).lower()
    return "lock timeout" in text or "canceling statement due to lock timeout" in text


def retry_delay_seconds(attempt: int, max_attempts: int = 3) -> float | None:
    """Backoff for a contended migration, or None when the caller must stop and fail loudly.

    Returning None rather than raising keeps the decision at the call site, where the failure
    can be reported with the service's own context. A migration that cannot be applied must end
    as a startup failure: a container that serves traffic against a schema it failed to migrate
    is the silent half of this bug class, and the deploy script's health gate is what catches
    the loud half.
    """
    if attempt >= max_attempts:
        return None
    return float(2 ** attempt)
