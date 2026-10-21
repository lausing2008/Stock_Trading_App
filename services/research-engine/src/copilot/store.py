"""Conversation persistence. Every query is scoped to the authenticated owner.

There is deliberately no function here that reads a conversation by id alone: each one takes
`user_id`, so forgetting the ownership check is not something a new caller can do by accident.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import hashlib
import re

from sqlalchemy import delete, func, select, text, update

from common.copilot_retention import COPILOT_RETENTION_DAYS, purge_expired  # noqa: F401
from db import User
from db.models import CopilotConversation, CopilotMessage

#: A pending claim older than this is treated as abandoned (the process died mid-answer) and
#: may be taken over by a retry. Above the worst case of two model calls at llm.TIMEOUT_S each.
CLAIM_TTL_S = 300
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


def reserve_answer(session, user_id: int, now: datetime, limit: int) -> bool:
    """Admit one answer against the user's UTC-day limit. True if admitted. Caller commits.

    ONE CONDITIONAL UPSERT (R2). Two concurrent admissions cannot both read "under the limit":
    the increment happens only WHERE the stored count is still below it, and the second writer
    waits on the first one's row lock. The row is not tied to any conversation, so deleting a
    conversation does not give the allowance back.
    """
    if limit <= 0:
        return False
    row = session.execute(text(
        "INSERT INTO copilot_usage_day (user_id, day, admitted) VALUES (:u, :d, 1) "
        "ON CONFLICT (user_id, day) DO UPDATE SET admitted = copilot_usage_day.admitted + 1 "
        "WHERE copilot_usage_day.admitted < :lim RETURNING admitted"),
        {"u": user_id, "d": now.date(), "lim": limit}).first()
    return row is not None


def refund_answer(session, user_id: int, day) -> None:
    """Return an admission. ONLY for a turn that made no model call — see service.py."""
    session.execute(text(
        "UPDATE copilot_usage_day SET admitted = admitted - 1 "
        "WHERE user_id = :u AND day = :d AND admitted > 0"), {"u": user_id, "d": day})


def answers_today(session, user_id: int, now: datetime | None = None) -> int:
    """Admissions recorded today — in flight and completed, including deleted conversations."""
    now = now or _utcnow()
    return session.execute(text(
        "SELECT admitted FROM copilot_usage_day WHERE user_id = :u AND day = :d"),
        {"u": user_id, "d": now.date()}).scalar() or 0


def request_fingerprint(symbol: str, question: str) -> str:
    """What a request id is bound to (R4): the symbol and the question, whitespace-normalised."""
    norm = re.sub(r"\s+", " ", question.strip())
    return hashlib.sha256(f"{symbol.upper()}\n{norm}".encode()).hexdigest()


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
    return [{"role": m.role, "content": m.content, "request_id": m.request_id,
             "created_at": m.created_at.isoformat() if m.created_at else None}
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


def claim_retry(session, msg: CopilotMessage, now: datetime) -> bool:
    """Take over a stored user turn for a retry. True only for the one caller that wins.

    Claimable: a FAILED turn (no call is running), or a pending/cancelled one whose claim is
    older than CLAIM_TTL_S (its process died). ONE CONDITIONAL UPDATE, so two simultaneous
    retries cannot both win: the database applies
    the WHERE clause to the current row, and the loser sees rowcount 0. A turn still being
    answered (a pending claim younger than CLAIM_TTL_S) is never taken over.
    """
    stale = now - timedelta(seconds=CLAIM_TTL_S)
    res = session.execute(
        update(CopilotMessage)
        .where(CopilotMessage.id == msg.id,
               (CopilotMessage.status == "failed")
               # A CANCELLED turn may still have a call running — cancel stops the NEXT call,
               # not the current one — so it is claimable only once that call has timed out.
               | (CopilotMessage.status.in_(("pending", "cancelled"))
                  & (CopilotMessage.claimed_at < stale)))
        .values(status="pending", claimed_at=now))
    return res.rowcount == 1


def set_user_turn_status(session, user_id: int, request_id: str, status: str,
                         only_if: tuple[str, ...] | None = None) -> bool:
    q = update(CopilotMessage).where(CopilotMessage.user_id == user_id,
                                     CopilotMessage.request_id == request_id,
                                     CopilotMessage.role == "user")
    if only_if:
        q = q.where(CopilotMessage.status.in_(only_if))
    return session.execute(q.values(status=status)).rowcount == 1


def user_turn_status(session, user_id: int, request_id: str) -> str | None:
    row = session.execute(
        select(CopilotMessage.status).where(CopilotMessage.user_id == user_id,
                                            CopilotMessage.request_id == request_id,
                                            CopilotMessage.role == "user")).first()
    return row[0] if row else None


#: Evidence records returned inline per citation. The rest stay stored and retrievable.
EVIDENCE_PREVIEW = 5


def _receipts(m: CopilotMessage) -> dict:
    """Draft save state lives in copilot_draft_receipts, not in the message JSON (review S4)."""
    from sqlalchemy.orm import object_session
    from db.models import CopilotDraftReceipt
    sess = object_session(m)
    if sess is None or m.id is None or not (m.grounding or {}).get("drafts"):
        return {}
    return {r.draft_id: r for r in sess.execute(
        select(CopilotDraftReceipt).where(CopilotDraftReceipt.message_id == m.id)).scalars()}


def ref_evidence(g: dict, r: dict) -> list:
    """One citation's evidence list, in either storage format.

    Since packet v4 (plan mode) each record is stored ONCE per message in
    `grounding["evidence_records"]` and a citation holds `evidence_ids`. Measured: a plan answer
    citing ten rules stored 948 evidence entries (416 KB), because every rule repeated the same
    hundred-odd price bars. Older messages carry the list inline under `evidence`.
    """
    if "evidence_ids" in r:
        recs = g.get("evidence_records") or {}
        return [recs[i] for i in r["evidence_ids"] if i in recs]
    return r.get("evidence") or []


def serialise_message(m: CopilotMessage) -> dict:
    g = m.grounding or {}
    rc = _receipts(m)
    refs = []
    for r in g.get("evidence_refs", []):
        ev = ref_evidence(g, r)
        refs.append({**{k: v for k, v in r.items() if k != "evidence_ids"},
                     "evidence": ev[:EVIDENCE_PREVIEW], "evidence_count": len(ev)})
    return {
        "id": m.id, "role": m.role, "content": m.content, "request_id": m.request_id,
        "status": m.status, "created_at": m.created_at.isoformat() if m.created_at else None,
        "evidence_refs": refs, "limitations": g.get("limitations", []),
        "price_as_of": g.get("price_as_of"), "source_times": g.get("source_times", {}),
        "packet_fingerprint": g.get("packet_fingerprint"),
        **({"plan_context": g["plan_context"]} if "plan_context" in g else {}),
        **({"plan_setups": g["plan_setups"], "selected_setup_id": g.get("selected_setup_id"),
            "plan_proposal": g.get("plan_proposal")}
           if "plan_setups" in g else {}),
        "rejection_reasons": g.get("rejection_reasons", []), "model": m.model,
        # The idempotency key stays server-side; the client confirms by draft id.
        "drafts": [{**{k: v for k, v in d.items() if k != "idempotency_key"},
                    **({"status": "saved", "result": rc[d["id"]].result}
                       if d["id"] in rc and rc[d["id"]].status == "saved"
                       else {"status": "saving"} if d["id"] in rc and rc[d["id"]].status == "executing"
                       else {})}
                   for d in g.get("drafts", [])],
        "dropped_drafts": g.get("dropped_drafts", []),
    }


def message_evidence(session, user_id: int, message_id: int, key: str) -> dict | None:
    """The COMPLETE evidence for one citation of one of the owner's answers (R6)."""
    m = session.get(CopilotMessage, message_id)
    if m is None or m.user_id != user_id or m.role != "assistant":
        return None
    g = m.grounding or {}
    for r in g.get("evidence_refs", []):
        if r.get("key") == key:
            return {"message_id": m.id, "key": key, "label": r.get("label"),
                    "display": r.get("display"), "evidence": ref_evidence(g, r)}
    return None


def serialise_conversation(c: CopilotConversation) -> dict:
    return {"id": c.id, "subject_key": c.subject_key, "title": c.title,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}
