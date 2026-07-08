"""Create Event Intelligence Platform tables.

New tables: economic_events, earnings_events, insider_transactions,
congress_trades, institutional_holdings, institutional_transactions,
political_events, catalyst_scores.

Revision ID: 003_event_intelligence_tables
Revises: 002_signals_dedup_index
Create Date: 2026-06-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_event_intelligence_tables"
down_revision: Union[str, None] = "002_signals_dedup_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "economic_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("country", sa.String(8), nullable=False, index=True),
        sa.Column("event_date", sa.DateTime, nullable=False, index=True),
        sa.Column("actual_value", sa.Float, nullable=True),
        sa.Column("expected_value", sa.Float, nullable=True),
        sa.Column("previous_value", sa.Float, nullable=True),
        sa.Column("importance", sa.String(16), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("event_type", "country", "event_date", name="uq_economic_event"),
    )

    op.create_table(
        "earnings_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("stock_id", sa.Integer, sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("report_date", sa.Date, nullable=False, index=True),
        sa.Column("period", sa.String(16), nullable=True),
        sa.Column("fiscal_year", sa.Integer, nullable=True),
        sa.Column("fiscal_quarter", sa.Integer, nullable=True),
        sa.Column("eps_estimate", sa.Float, nullable=True),
        sa.Column("eps_actual", sa.Float, nullable=True),
        sa.Column("revenue_estimate", sa.Float, nullable=True),
        sa.Column("revenue_actual", sa.Float, nullable=True),
        sa.Column("surprise_pct", sa.Float, nullable=True),
        sa.Column("revenue_surprise_pct", sa.Float, nullable=True),
        sa.Column("earnings_strength_score", sa.Float, nullable=True),
        sa.Column("post_earnings_return_1d", sa.Float, nullable=True),
        sa.Column("post_earnings_return_5d", sa.Float, nullable=True),
        sa.Column("fetched_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("stock_id", "fiscal_year", "fiscal_quarter", name="uq_earnings_stock_period"),
    )
    op.create_index("ix_earnings_stock_date", "earnings_events", ["stock_id", "report_date"])

    op.create_table(
        "insider_transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("stock_id", sa.Integer, sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("insider_name", sa.String(255), nullable=True),
        sa.Column("insider_role", sa.String(128), nullable=True),
        sa.Column("transaction_type", sa.String(32), nullable=False),
        sa.Column("shares", sa.BigInteger, nullable=True),
        sa.Column("price_per_share", sa.Float, nullable=True),
        sa.Column("total_value", sa.Float, nullable=True),
        sa.Column("transaction_date", sa.Date, nullable=False, index=True),
        sa.Column("filing_date", sa.Date, nullable=False, index=True),
        sa.Column("accession_number", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("accession_number", name="uq_insider_accession"),
    )
    op.create_index("ix_insider_stock_date", "insider_transactions", ["stock_id", "transaction_date"])

    op.create_table(
        "congress_trades",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("politician_name", sa.String(255), nullable=False, index=True),
        sa.Column("party", sa.String(32), nullable=True),
        sa.Column("chamber", sa.String(16), nullable=True),
        sa.Column("state", sa.String(8), nullable=True),
        sa.Column("ticker", sa.String(16), nullable=False, index=True),
        sa.Column("stock_id", sa.Integer, sa.ForeignKey("stocks.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("transaction_type", sa.String(32), nullable=False),
        sa.Column("amount_range", sa.String(64), nullable=True),
        sa.Column("amount_min", sa.Float, nullable=True),
        sa.Column("amount_max", sa.Float, nullable=True),
        sa.Column("trade_date", sa.Date, nullable=True, index=True),
        sa.Column("disclosure_date", sa.Date, nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("politician_name", "ticker", "trade_date", "transaction_type", name="uq_congress_trade"),
    )
    op.create_index("ix_congress_ticker_date", "congress_trades", ["ticker", "trade_date"])

    op.create_table(
        "institutional_holdings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fund_name", sa.String(255), nullable=False, index=True),
        sa.Column("fund_cik", sa.String(32), nullable=False, index=True),
        sa.Column("stock_id", sa.Integer, sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("period_date", sa.Date, nullable=False, index=True),
        sa.Column("shares", sa.BigInteger, nullable=True),
        sa.Column("value_usd", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("fund_cik", "stock_id", "period_date", name="uq_inst_holding"),
    )

    op.create_table(
        "institutional_transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fund_name", sa.String(255), nullable=False, index=True),
        sa.Column("fund_cik", sa.String(32), nullable=False),
        sa.Column("stock_id", sa.Integer, sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("period_date", sa.Date, nullable=False, index=True),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("shares_change", sa.BigInteger, nullable=True),
        sa.Column("value_change_usd", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("fund_cik", "stock_id", "period_date", name="uq_inst_txn"),
    )

    op.create_table(
        "political_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("stock_id", sa.Integer, sa.ForeignKey("stocks.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("event_type", sa.String(64), nullable=False, index=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("amount_usd", sa.Float, nullable=True),
        sa.Column("agency", sa.String(255), nullable=True),
        sa.Column("event_date", sa.Date, nullable=False, index=True),
        sa.Column("impact", sa.String(16), nullable=True),
        sa.Column("source", sa.String(64), nullable=True),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "catalyst_scores",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("stock_id", sa.Integer, sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("catalyst_score", sa.Float, nullable=True),
        sa.Column("earnings_score", sa.Float, nullable=True),
        sa.Column("insider_score", sa.Float, nullable=True),
        sa.Column("congress_score", sa.Float, nullable=True),
        sa.Column("institutional_score", sa.Float, nullable=True),
        sa.Column("economic_score", sa.Float, nullable=True),
        sa.Column("risk_score", sa.Float, nullable=True),
        sa.Column("composite_score", sa.Float, nullable=True),
        sa.Column("earnings_days_out", sa.Integer, nullable=True),
        sa.Column("last_insider_days", sa.Integer, nullable=True),
        sa.Column("last_congress_days", sa.Integer, nullable=True),
        sa.Column("computed_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("stock_id", name="uq_catalyst_stock"),
    )


def downgrade() -> None:
    op.drop_table("catalyst_scores")
    op.drop_table("political_events")
    op.drop_table("institutional_transactions")
    op.drop_table("institutional_holdings")
    op.drop_table("congress_trades")
    op.drop_table("insider_transactions")
    op.drop_table("earnings_events")
    op.drop_table("economic_events")
