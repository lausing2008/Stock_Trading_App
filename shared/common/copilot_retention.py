"""Copilot conversation retention: one rule, used by the store and by the scheduled purge.

Kept in `shared/` because the rule is enforced by market-data's scheduler (where every other
retention purge runs) while the conversations are written by research-engine. One constant,
so the documented period and the deleted period cannot drift apart.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Conversations idle for longer than this are deleted, messages included.
COPILOT_RETENTION_DAYS = 180


def purge_expired(session, now: datetime | None = None) -> int:
    """Delete conversations whose last activity is older than the retention period.

    Messages are deleted explicitly as well as by the FK cascade: SQLite does not enforce
    ON DELETE CASCADE without a pragma, and "deleted" must mean deleted on every engine.
    The caller commits.
    """
    from sqlalchemy import delete, select
    from db.models import CopilotConversation, CopilotMessage

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(days=COPILOT_RETENTION_DAYS)
    ids = [r[0] for r in session.execute(
        select(CopilotConversation.id).where(CopilotConversation.updated_at < cutoff)).all()]
    if not ids:
        return 0
    session.execute(delete(CopilotMessage).where(CopilotMessage.conversation_id.in_(ids)))
    session.execute(delete(CopilotConversation).where(CopilotConversation.id.in_(ids)))
    return len(ids)
