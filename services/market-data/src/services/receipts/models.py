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

#: One row per Little RZY stream rebuild, and one per stream whose rebuild the 30-minute throttle
#: deferred (written when the deferral is first recorded, as the checkpoint marks it). The
#: generation archives that hold the same before/after are pruned to five per stream; these rows
#: are not. `checkpoint_updated_at` is when the stream last advanced before the trigger, so a
#: revision can be placed before or after the scan that consumed its bar.
rzy_rebuild_triggers = Table(
    "rzy_rebuild_triggers", metadata,
    Column("trigger_id", String(32), primary_key=True),
    Column("observed_at", DateTime, nullable=False),      # the scan's data cutoff
    Column("recorded_at", DateTime, nullable=False),      # wall clock at write
    Column("stream_key", String(96), nullable=False),
    Column("symbol", String(32), nullable=False),
    Column("timeframe", String(8), nullable=False),
    Column("config_hash", String(24)),
    Column("outcome", String(24), nullable=False),
    Column("reason", String(64), nullable=False),
    Column("change_class", String(24), nullable=False),
    Column("prior_generation", Integer),
    Column("new_generation", Integer),
    Column("bar_time", String(40)),
    Column("bar_closed_at", String(40)),
    Column("bar_age_seconds", Float),                     # observed_at - the revised bar's close
    Column("fields_changed", JSON),                       # subset of open/high/low/close/closed_at/price_basis
    Column("max_abs_change", Float),
    Column("max_rel_change", Float),
    Column("volume_before", Float),
    Column("volume_after", Float),
    Column("before", JSON),                               # the consumed bar, OHLC/volume/close time/basis
    Column("after", JSON),                                # the same bar as stored now
    Column("detail", JSON),                               # the workflow's own detail for non-bar reasons
    Column("checkpoint_last_bar", String(40)),
    Column("checkpoint_updated_at", DateTime),
    Column("generation_started_at", String(40)),
    Column("pending_since", String(40)),                  # an earlier throttled deferral, if any
    Column("throttle_due", String(40)),
    Column("release_sha", String(64), nullable=False),
    Column("code_fingerprint", String(64), nullable=False),
    CheckConstraint("outcome IN ('rebuilt', 'deferred_by_throttle')", name="ck_rzy_rebuild_triggers_outcome"),
    CheckConstraint("change_class IN ('material_ohlc', 'rounding_ohlc', 'closed_at', 'price_basis', "
                    "'gap_after_checkpoint', 'bar_missing', 'off_grid', 'legacy_quarantine', 'other')",
                    name="ck_rzy_rebuild_triggers_class"),
    Index("ix_rzy_rebuild_triggers_observed_at", "observed_at"),
    Index("ix_rzy_rebuild_triggers_stream", "stream_key", "observed_at"),
)
