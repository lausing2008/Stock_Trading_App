"""Add sourced product events and per-user alert decisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "005_news_impact_events"
down_revision = "004_rankings_nullable_value_growth"
branch_labels = None
depends_on = None


def upgrade():
    # Runtime init_db() also creates new tables. Permit migrations after that first startup.
    existing = inspect(op.get_bind())
    if not existing.has_table("options_flow_recipient_decisions"):
        op.create_table("options_flow_recipient_decisions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("option_chain", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("fired_date", sa.Date(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(128)),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "option_chain", "fired_date", name="uq_flow_recipient_contract_day"))
        op.create_index("ix_options_flow_recipient_decisions_user_id", "options_flow_recipient_decisions", ["user_id"])
        op.create_index("ix_options_flow_recipient_decisions_state", "options_flow_recipient_decisions", ["state"])
    if not inspect(op.get_bind()).has_table("news_impact_events"):
        op.create_table("news_impact_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("event_key", sa.String(128), nullable=False, unique=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("observed_url", sa.Text()),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("impacts", sa.JSON(), nullable=False),
        sa.Column("evidence_status", sa.String(64), nullable=False))
    if not inspect(op.get_bind()).has_table("news_impact_receipts"):
        op.create_table("news_impact_receipts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("news_impact_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("matched_symbols", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(256)),
        sa.Column("outbox_event_id", sa.String(255)),
        sa.Column("decided_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", "user_id", name="uq_news_impact_event_user"))
        op.create_index("ix_news_impact_receipts_event_id", "news_impact_receipts", ["event_id"])
        op.create_index("ix_news_impact_receipts_user_id", "news_impact_receipts", ["user_id"])
        op.create_index("ix_news_impact_receipts_state", "news_impact_receipts", ["state"])


def downgrade():
    op.drop_table("news_impact_receipts")
    op.drop_table("news_impact_events")
    op.drop_table("options_flow_recipient_decisions")
