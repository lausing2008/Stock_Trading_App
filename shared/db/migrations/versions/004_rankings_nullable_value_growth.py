"""Make rankings.value and rankings.growth nullable.

compute_kscore() legitimately returns None for value/growth when a stock lacks
sufficient fundamentals data (price-proxy fallback, KS-4) — the KScoreComponents
dataclass itself declares these as `float | None`. The columns were NOT NULL,
so any bulk ranking refresh batch containing even one such stock failed the
whole INSERT with NotNullViolation. Because _persist_rankings() had no logging
at the time (see T232-RANKSTALE), this silently stalled rankings for both
markets for 10+ days with no visible error anywhere.

Revision ID: 004_rankings_nullable_value_growth
Revises: 003_event_intelligence_tables
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_rankings_nullable_value_growth"
down_revision: Union[str, None] = "003_event_intelligence_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("rankings", "value", existing_type=sa.Float(), nullable=True)
    op.alter_column("rankings", "growth", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    op.alter_column("rankings", "growth", existing_type=sa.Float(), nullable=False)
    op.alter_column("rankings", "value", existing_type=sa.Float(), nullable=False)
