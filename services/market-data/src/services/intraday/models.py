"""Intraday Stage C tables (design §9). Market-data's own MetaData: no other service creates
them, and nothing daily reads them. Every table is insert-only; on PostgreSQL triggers enforce
it (schema.py). Times are naive UTC, the platform's storage convention.
"""
from sqlalchemy import (JSON, BigInteger, CheckConstraint, Column, Date, DateTime, Float, ForeignKeyConstraint,
                        Index, Integer, MetaData, String, Table, UniqueConstraint, text)

from .contract import LEGAL_PAIRS, RESULTS, SESSION_TERMINAL, STATES

metadata = MetaData()
_ID = BigInteger().with_variant(Integer, "sqlite")


def _in(col, values):
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


def _pairs_check():
    parts = []
    by_from = {}
    for a, b in LEGAL_PAIRS:
        by_from.setdefault(a, []).append(b)
    for a, bs in sorted(by_from.items(), key=lambda kv: (kv[0] is not None, kv[0] or "")):
        lhs = "from_state IS NULL" if a is None else f"from_state = '{a}'"
        parts.append(f"({lhs} AND {_in('to_state', sorted(bs))})")
    return " OR ".join(parts)


runs = Table(
    "intraday_runs", metadata,
    Column("run_key", String(64), primary_key=True),
    Column("record_mode", String(8), nullable=False),
    Column("input_snapshot_hash", String(64)),
    Column("as_of", DateTime),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("run_key", "record_mode", name="uq_intraday_runs_mode"),
    CheckConstraint(_in("record_mode", ("observed", "replay")), name="ck_intraday_runs_mode"),
    CheckConstraint("(record_mode = 'observed') = (run_key = 'observed')", name="ck_intraday_runs_observed"),
    CheckConstraint("record_mode = 'observed' OR (input_snapshot_hash IS NOT NULL AND as_of IS NOT NULL)",
                    name="ck_intraday_runs_replay_identity"),
)

identities = Table(
    "intraday_identities", metadata,
    Column("identity_hash", String(64), primary_key=True),
    Column("strategy_id", String(32), nullable=False),
    Column("strategy_version", String(32), nullable=False),
    Column("strategy_source_hash", String(64), nullable=False),
    Column("contract_version", String(32), nullable=False),
    Column("config_hash", String(64), nullable=False),
    Column("config", JSON, nullable=False),
)

batches = Table(
    "intraday_evaluation_batches", metadata,
    Column("batch_id", String(64), primary_key=True),
    Column("run_key", String(64), nullable=False),
    Column("record_mode", String(8), nullable=False),
    Column("identity_hash", String(64), nullable=False),
    Column("code_version", String(64), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("data_cutoff", DateTime, nullable=False),
    ForeignKeyConstraint(["run_key", "record_mode"], ["intraday_runs.run_key", "intraday_runs.record_mode"],
                         name="fk_intraday_batch_run"),
    ForeignKeyConstraint(["identity_hash"], ["intraday_identities.identity_hash"], name="fk_intraday_batch_identity"),
    UniqueConstraint("batch_id", "run_key", "identity_hash", name="uq_intraday_batch_lineage"),
)

evaluations = Table(
    "intraday_evaluations", metadata,
    Column("id", _ID, primary_key=True, autoincrement=True),
    Column("evaluation_key", String(64), nullable=False, unique=True),
    Column("batch_id", String(64), nullable=False),
    Column("run_key", String(64), nullable=False),
    Column("identity_hash", String(64), nullable=False),
    Column("stock_id", Integer, nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("session_date", Date, nullable=False),
    Column("trigger_bar_open", DateTime, nullable=False),
    Column("result", String(32), nullable=False),
    Column("detected_at", DateTime, nullable=False),
    Column("data_cutoff", DateTime, nullable=False),
    ForeignKeyConstraint(["batch_id", "run_key", "identity_hash"],
                         ["intraday_evaluation_batches.batch_id", "intraday_evaluation_batches.run_key",
                          "intraday_evaluation_batches.identity_hash"], name="fk_intraday_eval_batch"),
    UniqueConstraint("id", "run_key", "identity_hash", name="uq_intraday_eval_lineage"),
    CheckConstraint(_in("result", RESULTS), name="ck_intraday_eval_result"),
)
Index("ix_intraday_eval_page", evaluations.c.run_key, evaluations.c.identity_hash, evaluations.c.session_date,
      evaluations.c.trigger_bar_open, evaluations.c.symbol, evaluations.c.evaluation_key)

setups = Table(
    "intraday_setups", metadata,
    Column("id", _ID, primary_key=True, autoincrement=True),
    Column("occurrence_key", String(64), nullable=False, unique=True),
    Column("evaluation_id", _ID, nullable=False, unique=True),
    Column("run_key", String(64), nullable=False),
    Column("identity_hash", String(64), nullable=False),
    Column("stock_id", Integer, nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("session_date", Date, nullable=False),
    Column("trigger_bar_open", DateTime, nullable=False),
    Column("direction", String(8), nullable=False),
    Column("entry_reference", Float, nullable=False),
    Column("stop", Float, nullable=False),
    Column("target", Float, nullable=False),
    Column("risk_per_share", Float, nullable=False),
    Column("reasons", JSON, nullable=False),
    Column("detected_at", DateTime, nullable=False),
    Column("actionable_at", DateTime),
    Column("entry_expires_at", DateTime, nullable=False),
    Column("last_entry_at", DateTime, nullable=False),
    Column("liquidation_at", DateTime, nullable=False),
    Column("session_close", DateTime, nullable=False),
    ForeignKeyConstraint(["evaluation_id", "run_key", "identity_hash"],
                         ["intraday_evaluations.id", "intraday_evaluations.run_key",
                          "intraday_evaluations.identity_hash"], name="fk_intraday_setup_eval"),
    UniqueConstraint("id", "run_key", name="uq_intraday_setup_lineage"),
    CheckConstraint(_in("direction", ("bullish", "bearish")), name="ck_intraday_setup_direction"),
)
Index("ix_intraday_setup_page", setups.c.run_key, setups.c.identity_hash, setups.c.session_date,
      setups.c.trigger_bar_open, setups.c.symbol, setups.c.occurrence_key)

inputs = Table(
    "intraday_setup_inputs", metadata,
    Column("id", _ID, primary_key=True, autoincrement=True),
    Column("setup_id", _ID, nullable=False),
    Column("run_key", String(64), nullable=False),
    Column("role", String(8), nullable=False),
    Column("timeframe", String(4), nullable=False),
    Column("bar_open", DateTime, nullable=False),
    Column("source", String(16), nullable=False),
    Column("bar_values", JSON, nullable=False),
    #: contract.EVIDENCE_VERSION payload: raw and transformed hashed fields, provider identity and
    #: basis, ordered derived constituents -- every hash below recomputes from this alone (IC-03)
    Column("evidence", JSON, nullable=False),
    Column("raw_hash", String(64)),
    Column("source_hash", String(64)),
    Column("input_hash", String(64), nullable=False),
    Column("derived_hash", String(64)),
    Column("readable_at", DateTime, nullable=False),
    Column("input_read_at", DateTime, nullable=False),
    #: observed: the evaluator's real read time; replay_logical: the replay's data cutoff
    Column("read_clock_basis", String(16), nullable=False),
    Column("first_available_at", DateTime),
    Column("availability_basis", String(32), nullable=False),
    Column("provider_published_at", DateTime),
    ForeignKeyConstraint(["setup_id", "run_key"], ["intraday_setups.id", "intraday_setups.run_key"],
                         name="fk_intraday_input_setup"),
    UniqueConstraint("setup_id", "role", "timeframe", "bar_open", name="uq_intraday_input"),
    CheckConstraint(_in("role", ("trigger", "context", "lookback")), name="ck_intraday_input_role"),
    CheckConstraint(_in("source", ("native", "derived_from_5m")), name="ck_intraday_input_source"),
    CheckConstraint("first_available_at IS NOT NULL OR availability_basis = 'unknown_exact_version'",
                    name="ck_intraday_input_availability"),
    CheckConstraint("readable_at <= input_read_at", name="ck_intraday_input_read_after_readable"),
    CheckConstraint(_in("read_clock_basis", ("observed", "replay_logical")), name="ck_intraday_input_read_clock"),
    CheckConstraint("(run_key = 'observed') = (read_clock_basis = 'observed')", name="ck_intraday_input_clock_mode"),
)

transitions = Table(
    "intraday_setup_transitions", metadata,
    Column("id", _ID, primary_key=True, autoincrement=True),
    Column("setup_id", _ID, nullable=False),
    Column("run_key", String(64), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("from_state", String(40)),
    Column("to_state", String(40), nullable=False),
    Column("evidence_eligible_at", DateTime),
    Column("evidence_observed_at", DateTime),
    Column("decided_at", DateTime, nullable=False),
    #: when the research event happened (a fill's bar open, a cancellation's effect); decided_at
    #: is when it was decided/recorded. effective_at <= decided_at; both never go backwards (IC-02)
    Column("effective_at", DateTime, nullable=False),
    Column("clock_basis", String(16), nullable=False),
    Column("reason", String(40)),
    Column("evidence", JSON),
    Column("idempotency_key", String(64), nullable=False, unique=True),
    ForeignKeyConstraint(["setup_id", "run_key"], ["intraday_setups.id", "intraday_setups.run_key"],
                         name="fk_intraday_transition_setup"),
    UniqueConstraint("setup_id", "seq", name="uq_intraday_transition_seq"),
    CheckConstraint(_pairs_check(), name="ck_intraday_transition_pair"),
    CheckConstraint(_in("to_state", STATES), name="ck_intraday_transition_state"),
    CheckConstraint(_in("clock_basis", ("observed", "replay_logical")), name="ck_intraday_transition_clock"),
    CheckConstraint("(seq = 1) = (from_state IS NULL)", name="ck_intraday_transition_first"),
    CheckConstraint("(run_key = 'observed') = (clock_basis = 'observed')", name="ck_intraday_transition_clock_mode"),
    CheckConstraint("effective_at <= decided_at", name="ck_intraday_transition_effective_before_decided"),
    CheckConstraint("clock_basis <> 'observed' OR ((evidence_eligible_at IS NULL OR evidence_observed_at IS NULL "
                    "OR evidence_eligible_at <= evidence_observed_at) AND (evidence_observed_at IS NULL "
                    "OR evidence_observed_at <= decided_at))", name="ck_intraday_transition_observed_order"),
)
Index("uq_intraday_one_session_terminal", transitions.c.setup_id, unique=True,
      postgresql_where=text(_in("to_state", SESSION_TERMINAL)), sqlite_where=text(_in("to_state", SESSION_TERMINAL)))
Index("uq_intraday_one_reconciled", transitions.c.setup_id, unique=True,
      postgresql_where=text("to_state = 'reconciled'"), sqlite_where=text("to_state = 'reconciled'"))

outcomes = Table(
    "intraday_outcomes", metadata,
    Column("id", _ID, primary_key=True, autoincrement=True),
    Column("outcome_key", String(64), nullable=False, unique=True),
    Column("setup_id", _ID, nullable=False),
    Column("run_key", String(64), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("window", String(8), nullable=False),
    Column("evaluator_version", String(32), nullable=False),
    Column("evidence_hash", String(64), nullable=False),
    Column("supersedes_id", _ID),
    Column("status", String(32), nullable=False),
    Column("outcome_values", JSON, nullable=False),
    Column("recorded_at", DateTime, nullable=False),
    ForeignKeyConstraint(["setup_id", "run_key"], ["intraday_setups.id", "intraday_setups.run_key"],
                         name="fk_intraday_outcome_setup"),
    UniqueConstraint("id", "setup_id", "kind", "window", "run_key", name="uq_intraday_outcome_chain"),
    ForeignKeyConstraint(["supersedes_id", "setup_id", "kind", "window", "run_key"],
                         ["intraday_outcomes.id", "intraday_outcomes.setup_id", "intraday_outcomes.kind",
                          "intraday_outcomes.window", "intraday_outcomes.run_key"],
                         name="fk_intraday_outcome_predecessor"),
    UniqueConstraint("supersedes_id", name="uq_intraday_outcome_one_successor"),
    CheckConstraint("(kind = 'forecast' AND \"window\" IN ('30','60','120')) OR (kind = 'trade' AND \"window\" = 'trade')",
                    name="ck_intraday_outcome_window"),
)
Index("uq_intraday_outcome_root", outcomes.c.setup_id, outcomes.c.kind, outcomes.c.window, unique=True,
      postgresql_where=text("supersedes_id IS NULL"), sqlite_where=text("supersedes_id IS NULL"))

seals = Table(
    "intraday_session_seals", metadata,
    Column("run_key", String(64), primary_key=True),
    Column("identity_hash", String(64), primary_key=True),
    Column("session_date", Date, primary_key=True),
    Column("sealed_at", DateTime, nullable=False),
)

evaluation_counts = Table(
    "intraday_evaluation_counts", metadata,
    Column("run_key", String(64), primary_key=True),
    Column("identity_hash", String(64), primary_key=True),
    Column("session_date", Date, primary_key=True),
    Column("result", String(32), primary_key=True),
    Column("count", Integer, nullable=False),
    Column("archived_at", DateTime, nullable=False),
    ForeignKeyConstraint(["run_key", "identity_hash", "session_date"],
                         ["intraday_session_seals.run_key", "intraday_session_seals.identity_hash",
                          "intraday_session_seals.session_date"], name="fk_intraday_counts_seal"),
    CheckConstraint(_in("result", RESULTS), name="ck_intraday_counts_result"),
)

TABLES = (runs, identities, batches, evaluations, setups, inputs, transitions, outcomes, seals, evaluation_counts)
