"""Create the Intraday tables and, on PostgreSQL, the triggers that make the contract a database
rule rather than an application habit (design §9). Idempotent; run at market-data startup under
the platform's DDL timeouts. Creates new objects only: no ALTER of any existing table or type.
"""
from datetime import datetime, timezone

from sqlalchemy import text

from .contract import FINAL, OBSERVED_RUN  # noqa: F401  (FINAL documents the state classes)
from .models import TABLES, metadata, runs

_GUARDED = [t.name for t in TABLES]

_PG_FUNCTIONS = [
    # Insert-only: every UPDATE is refused, by anyone.
    """CREATE OR REPLACE FUNCTION intraday_deny_update() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN RAISE EXCEPTION 'intraday_insert_only: UPDATE of % refused', TG_TABLE_NAME; END $$""",
    # DELETE only inside the named retention transaction. Not a permission boundary (one shared
    # credential); a guard against accidental deletion.
    """CREATE OR REPLACE FUNCTION intraday_guard_delete() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
         IF coalesce(current_setting('stockai.intraday_retention', true), '') <> 'on' THEN
           RAISE EXCEPTION 'intraday_delete_refused: % outside intraday_retention', TG_TABLE_NAME;
         END IF;
         RETURN OLD;
       END $$""",
    # Append validation: serialise on the setup row, then require the next seq from the latest
    # state. The pair CHECK and the partial unique indexes stay as independent backstops.
    """CREATE OR REPLACE FUNCTION intraday_validate_append() RETURNS trigger LANGUAGE plpgsql AS $$
       DECLARE last_seq integer; last_state text;
       BEGIN
         PERFORM 1 FROM intraday_setups WHERE id = NEW.setup_id FOR UPDATE;
         IF NOT FOUND THEN RAISE EXCEPTION 'intraday_append: setup % does not exist', NEW.setup_id; END IF;
         SELECT seq, to_state INTO last_seq, last_state FROM intraday_setup_transitions
           WHERE setup_id = NEW.setup_id ORDER BY seq DESC LIMIT 1;
         IF last_seq IS NULL THEN
           IF NOT (NEW.seq = 1 AND NEW.from_state IS NULL AND NEW.to_state = 'detected') THEN
             RAISE EXCEPTION 'intraday_append: first transition must be seq 1 NULL->detected';
           END IF;
         ELSIF NOT (NEW.seq = last_seq + 1 AND NEW.from_state IS NOT DISTINCT FROM last_state) THEN
           RAISE EXCEPTION 'intraday_append: expected seq % from %, got seq % from %',
             last_seq + 1, last_state, NEW.seq, NEW.from_state;
         END IF;
         RETURN NEW;
       END $$""",
    # Membership closes at the seal: no evaluation or setup for a sealed (run, identity, session).
    """CREATE OR REPLACE FUNCTION intraday_refuse_sealed() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
         IF EXISTS (SELECT 1 FROM intraday_session_seals s WHERE s.run_key = NEW.run_key
                    AND s.identity_hash = NEW.identity_hash AND s.session_date = NEW.session_date) THEN
           RAISE EXCEPTION 'intraday_sealed: % % % is sealed', NEW.run_key, NEW.identity_hash, NEW.session_date;
         END IF;
         RETURN NEW;
       END $$""",
    # Deferred, at commit: a setup belongs to a 'setup' evaluation and has its seq-1 transition.
    """CREATE OR REPLACE FUNCTION intraday_check_setup_complete() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
         IF NOT EXISTS (SELECT 1 FROM intraday_evaluations e WHERE e.id = NEW.evaluation_id AND e.result = 'setup') THEN
           RAISE EXCEPTION 'intraday_setup: evaluation % is not a setup result', NEW.evaluation_id;
         END IF;
         IF NOT EXISTS (SELECT 1 FROM intraday_setup_transitions t WHERE t.setup_id = NEW.id AND t.seq = 1) THEN
           RAISE EXCEPTION 'intraday_setup: setup % committed without its first transition', NEW.id;
         END IF;
         RETURN NULL;
       END $$""",
    # Deferred, at commit: every 'setup' evaluation has exactly one setup (unique evaluation_id).
    """CREATE OR REPLACE FUNCTION intraday_check_eval_has_setup() RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
         IF NEW.result = 'setup' AND NOT EXISTS (SELECT 1 FROM intraday_setups s WHERE s.evaluation_id = NEW.id) THEN
           RAISE EXCEPTION 'intraday_evaluation: setup result % has no setup', NEW.id;
         END IF;
         RETURN NULL;
       END $$""",
]


def _pg_triggers():
    out = []
    for t in _GUARDED:
        out += [f"DROP TRIGGER IF EXISTS {t}_deny_update ON {t}",
                f"CREATE TRIGGER {t}_deny_update BEFORE UPDATE ON {t} FOR EACH ROW EXECUTE FUNCTION intraday_deny_update()",
                f"DROP TRIGGER IF EXISTS {t}_guard_delete ON {t}",
                f"CREATE TRIGGER {t}_guard_delete BEFORE DELETE ON {t} FOR EACH ROW EXECUTE FUNCTION intraday_guard_delete()"]
    out += [
        "DROP TRIGGER IF EXISTS intraday_transition_append ON intraday_setup_transitions",
        "CREATE TRIGGER intraday_transition_append BEFORE INSERT ON intraday_setup_transitions "
        "FOR EACH ROW EXECUTE FUNCTION intraday_validate_append()",
    ]
    for t in ("intraday_evaluations", "intraday_setups"):
        out += [f"DROP TRIGGER IF EXISTS {t}_sealed ON {t}",
                f"CREATE TRIGGER {t}_sealed BEFORE INSERT ON {t} FOR EACH ROW EXECUTE FUNCTION intraday_refuse_sealed()"]
    out += [
        "DROP TRIGGER IF EXISTS intraday_setup_complete ON intraday_setups",
        "CREATE CONSTRAINT TRIGGER intraday_setup_complete AFTER INSERT ON intraday_setups "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION intraday_check_setup_complete()",
        "DROP TRIGGER IF EXISTS intraday_eval_has_setup ON intraday_evaluations",
        "CREATE CONSTRAINT TRIGGER intraday_eval_has_setup AFTER INSERT ON intraday_evaluations "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION intraday_check_eval_has_setup()",
    ]
    return out


def create(conn) -> None:
    """Create tables (and on PostgreSQL the functions/triggers) and the observed sentinel, on an
    open connection inside the caller's transaction."""
    metadata.create_all(conn)
    if conn.dialect.name == "postgresql":
        for stmt in _PG_FUNCTIONS + _pg_triggers():
            conn.execute(text(stmt))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    conn.execute(insert(runs).values(run_key=OBSERVED_RUN, record_mode="observed", created_at=now)
                 .on_conflict_do_nothing(index_elements=["run_key"]))


def ensure_schema() -> None:
    """market-data startup: under the cross-service schema lock and the DDL timeouts."""
    from db.session import _ddl_begin, _startup_schema_lock
    with _startup_schema_lock():
        with _ddl_begin() as conn:
            create(conn)


#: None until startup ran; then "ok" or "failed: <reason>". Read by the API.
SCHEMA_STATUS: str | None = None


def ensure_schema_safely() -> str:
    global SCHEMA_STATUS
    try:
        ensure_schema()
        SCHEMA_STATUS = "ok"
    except Exception as exc:  # noqa: BLE001 - recorded and surfaced, never silent
        SCHEMA_STATUS = f"failed: {type(exc).__name__}: {exc}"[:500]
        print(f"[intraday] schema not ensured: {SCHEMA_STATUS}")
    return SCHEMA_STATUS
