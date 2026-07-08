"""Baseline — stamps the current schema state managed by _run_migrations().

All schema changes up to 2026-06-19 are managed by shared/db/session.py
_run_migrations(). This revision is a no-op that marks the Alembic baseline.
New schema changes after this point should be added as Alembic revisions
instead of inline SQL in _run_migrations().

Revision ID: 001_baseline
Revises:
Create Date: 2026-06-19
"""
from typing import Sequence, Union

revision: str = "001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentional no-op: all prior schema changes are in _run_migrations().
    pass


def downgrade() -> None:
    pass
