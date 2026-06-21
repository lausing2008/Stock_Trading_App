"""Add unique index on signals (stock_id, horizon, date_trunc('day', ts)).

Prevents duplicate signals per stock+horizon per calendar day at the DB level.
Existing duplicates are deduplicated first (keep the row with the latest ts).

Revision ID: 002_signals_dedup_index
Revises: 001_baseline
Create Date: 2026-06-21
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "002_signals_dedup_index"
down_revision: Union[str, None] = "001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Remove duplicate rows: keep the latest ts per (stock_id, horizon, day).
    conn.execute(text("""
        DELETE FROM signals
        WHERE id NOT IN (
            SELECT DISTINCT ON (stock_id, horizon, date_trunc('day', ts)) id
            FROM signals
            ORDER BY stock_id, horizon, date_trunc('day', ts), ts DESC
        )
    """))

    conn.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_stock_horizon_day
        ON signals (stock_id, horizon, date_trunc('day', ts))
    """))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_signals_stock_horizon_day")
