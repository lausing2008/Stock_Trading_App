"""Conversation persistence. Every query is scoped to the authenticated owner.

There is deliberately no function here that reads a conversation by id alone: each one takes
`user_id`, so forgetting the ownership check is not something a new caller can do by accident.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from db import User
from db.models import CopilotConversation, CopilotMessage

#: Retention, stated rather than copied from another product: conversations idle for longer than
#: this are purged by `purge_expired()`. Owners can delete any conversation sooner.
COPILOT_RETENTION_DAYS = 180
#: Assistant answers per user per UTC day. Bounds one account; the token budget bounds them all.
DEFAULT_DAILY_ANSWERS = 60
#: Prior turns sent back to the model as conversation context.
HISTORY_TURNS = 6


def daily_answer_limit() -> int:
    try:
        return max(0, int(os.getenv("COPILOT_DAILY_ANSWERS_PER_USER", DEFAULT_DAILY_ANSWERS)))
    except ValueError:
        return DEFAULT_DAILY_ANSWERS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def user_id_for(session, username: str) -> int | None:
    row = session.execute(select(User.id).where(User.username == username,
                                                User.is_active.is_(True))).first()
    return row[0] if row else None


def answers_today(session, user_id: int, now: datetime | None = None) -> int:
    now = now or _utcnow()
    start = datetime(now.year, now.month, now.day)
    return session.execute(
        select(func.count(CopilotMessage.id))
        .where(CopilotMessage.user_id == user_id, CopilotMessage.role == "assistant",
               CopilotMessage.created_at >= start)).scalar_one()


def replay(session, user_id: int, request_id: str) -> CopilotMessage | None:
    """The assistant turn already stored for this request, if a retry reached us twice."""
    return session.execute(
        select(CopilotMessage).where(CopilotMessage.user_id == user_id,
                                     CopilotMessage.request_id == request_id,
                                     CopilotMessage.role == "assistant")).scalars().first()


def get_conversation(session, user_id: int, conversation_id: int) -> CopilotConversation | None:
    c = session.get(CopilotConversation, conversation_id)
    return c if c is not None and c.user_id == user_id else None


def open_conversation(session, user_id: int, subject_key: str, title: str,
                      conversation_id: int | None) -> CopilotConversation | None:
    """The owner's existing conversation, or a new one. None if the id is not theirs/subject's."""
    if conversation_id is not None:
        c = get_conversation(session, user_id, conversation_id)
        if c is None or c.subject_key != subject_key:
            return None
        return c
    c = CopilotConversation(user_id=user_id, subject_key=subject_key, title=title[:200],
                            created_at=_utcnow(), updated_at=_utcnow())
    session.add(c)
    session.flush()
    return c


def history(session, conversation: CopilotConversation, limit: int = HISTORY_TURNS) -> list[dict]:
    rows = session.execute(
        select(CopilotMessage).where(CopilotMessage.conversation_id == conversation.id)
        .order_by(CopilotMessage.created_at.desc(), CopilotMessage.id.desc())
        .limit(limit)).scalars().all()
    return [{"role": m.role, "content": m.content, "request_id": m.request_id}
            for m in reversed(rows)]


def messages(session, conversation: CopilotConversation) -> list[CopilotMessage]:
    return list(session.execute(
        select(CopilotMessage).where(CopilotMessage.conversation_id == conversation.id)
        .order_by(CopilotMessage.created_at.asc(), CopilotMessage.id.asc())).scalars().all())


def list_conversations(session, user_id: int, subject_key: str | None = None,
                       limit: int = 20) -> list[CopilotConversation]:
    q = select(CopilotConversation).where(CopilotConversation.user_id == user_id)
    if subject_key:
        q = q.where(CopilotConversation.subject_key == subject_key)
    return list(session.execute(
        q.order_by(CopilotConversation.updated_at.desc()).limit(limit)).scalars().all())


def delete_conversation(session, user_id: int, conversation_id: int) -> bool:
    c = get_conversation(session, user_id, conversation_id)
    if c is None:
        return False
    # Explicit child delete as well as the FK cascade: SQLite does not enforce ON DELETE
    # CASCADE unless a pragma is set, and "deleted" must mean deleted on every engine.
    session.execute(delete(CopilotMessage).where(CopilotMessage.conversation_id == c.id))
    session.delete(c)
    return True


def purge_expired(session, now: datetime | None = None) -> int:
    cutoff = (now or _utcnow()) - timedelta(days=COPILOT_RETENTION_DAYS)
    ids = [r[0] for r in session.execute(
        select(CopilotConversation.id).where(CopilotConversation.updated_at < cutoff)).all()]
    if not ids:
        return 0
    session.execute(delete(CopilotMessage).where(CopilotMessage.conversation_id.in_(ids)))
    session.execute(delete(CopilotConversation).where(CopilotConversation.id.in_(ids)))
    return len(ids)


def serialise_message(m: CopilotMessage) -> dict:
    g = m.grounding or {}
    return {
        "id": m.id, "role": m.role, "content": m.content, "request_id": m.request_id,
        "status": m.status, "created_at": m.created_at.isoformat() if m.created_at else None,
        "evidence_refs": g.get("evidence_refs", []), "limitations": g.get("limitations", []),
        "data_as_of": g.get("data_as_of"), "rejection_reasons": g.get("rejection_reasons", []),
        "model": m.model,
    }


def serialise_conversation(c: CopilotConversation) -> dict:
    return {"id": c.id, "subject_key": c.subject_key, "title": c.title,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}
