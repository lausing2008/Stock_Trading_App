"""Tables for durable measurement records. Times are naive UTC, the platform's storage convention.

Rows are written once and never rewritten; on PostgreSQL a trigger refuses UPDATE (schema.py).
DELETE is left to an explicit retention policy, which does not exist yet.
"""
from sqlalchemy import (JSON, Boolean, CheckConstraint, Column, Date, DateTime, Float, Index, Integer,
                        MetaData, String, Table, Text)

metadata = MetaData()

#: One row per `price_risk_watch.detect` call. A scheduled run writes two (symbol, benchmark)
#: sharing `run_id`. A run that raised writes a `failed` row, so a missing scan is visible.
price_risk_scans = Table(
    "price_risk_scans", metadata,
    Column("scan_id", String(32), primary_key=True),
    Column("run_id", String(32), nullable=False),
    Column("scope", String(16), nullable=False),
    Column("mode", String(8)),
    Column("status", String(16), nullable=False),
    Column("trigger", String(32), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("finished_at", DateTime, nullable=False),
    Column("us_session_date", Date),
    Column("us_regular_session", Boolean),
    Column("hk_session_date", Date),
    Column("hk_regular_session", Boolean),
    Column("subjects", Integer),
    Column("measured", Integer),
    Column("excluded", Integer),
    Column("events", Integer),
    Column("receipts", Integer),
    Column("intraday_quotes", Integer),
    Column("episodes_closed", Integer),
    #: Per market: subjects, measured by live quote, measured from settled bars, excluded,
    #: unusable window. Live-quote ages (minutes) summarised per market.
    Column("coverage", JSON),
    Column("exclusions", JSON),          # factor -> count
    Column("excluded_subjects", JSON),   # [{symbol, market, factor, reason, as_of}]
    Column("quote_refusals", JSON),      # {"counts": {status: n}, "symbols": {status: [..]}}
    Column("events_detail", JSON),       # [{event_id, subject, rule, step, ...}]
    Column("suppressions", JSON),        # {"counts": {reason: n}, "items": [...]}
    Column("config", JSON),
    Column("config_hash", String(64)),
    Column("config_errors", JSON),
    Column("delivery_enabled", Boolean),
    Column("release_sha", String(64), nullable=False),
    Column("code_fingerprint", String(64), nullable=False),
    Column("identity", JSON),
    Column("error", Text),
    CheckConstraint("status IN ('completed', 'failed')", name="ck_price_risk_scans_status"),
    CheckConstraint("scope IN ('symbol', 'benchmark')", name="ck_price_risk_scans_scope"),
    Index("ix_price_risk_scans_started_at", "started_at"),
    Index("ix_price_risk_scans_run_id", "run_id"),
)

#: One row per `_open_paper_trade` attempt, opened or rejected, organic or conditional order.
#: `freshness` is `not_evaluated` when the attempt stopped before the exposure reservation, the
#: only step that values open positions. A period with no evaluated attempt is "no opportunity
#: to evaluate", never a pass.
paper_entry_receipts = Table(
    "paper_entry_receipts", metadata,
    Column("receipt_id", String(32), primary_key=True),
    Column("decided_at", DateTime, nullable=False),
    Column("portfolio_id", Integer, nullable=False),
    Column("market", String(4)),
    Column("horizon", String(16)),
    Column("symbol", String(32), nullable=False),
    Column("sector", String(64)),
    Column("origin", String(24)),
    Column("session_date", Date),
    Column("regular_session", Boolean),
    Column("outcome", String(16), nullable=False),
    Column("reason", String(64), nullable=False),
    Column("trade_id", Integer),
    Column("freshness", String(24), nullable=False),
    Column("reservation_reason", String(64)),
    Column("would_block_on_fresh_marks", Boolean),
    Column("decision_would_change", Boolean),
    Column("fresh_marks_enforced", Boolean),
    Column("candidate_price", Float),
    Column("candidate_price_source", String(32)),
    Column("candidate_price_fetched_at", DateTime),
    Column("candidate_price_session_date", Date),
    Column("position_value", Float),
    Column("equity", Float),
    #: Every open position valued for the sector check: symbol, price, provenance ("live" or the
    #: substitute), the price's source, fetched_at and session_date, shares and value.
    Column("marks", JSON),
    Column("fallback_marks", Integer),
    Column("fallback_value", Float),
    Column("committed", Float),
    Column("reserved", Float),
    Column("projected_pct", Float),
    Column("cap_pct", Float),
    Column("release_sha", String(64), nullable=False),
    Column("code_fingerprint", String(64), nullable=False),
    CheckConstraint("outcome IN ('opened', 'rejected')", name="ck_paper_entry_receipts_outcome"),
    CheckConstraint("freshness IN ('not_evaluated', 'no_sector_positions', 'all_marks_live', "
                    "'fallback_marks', 'unvaluable')", name="ck_paper_entry_receipts_freshness"),
    Index("ix_paper_entry_receipts_decided_at", "decided_at"),
    Index("ix_paper_entry_receipts_portfolio", "portfolio_id", "decided_at"),
)
