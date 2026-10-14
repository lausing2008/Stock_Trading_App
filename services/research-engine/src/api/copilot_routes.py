"""Copilot endpoints: ask about a stock, list/read/delete your own conversations.

Identity comes from the JWT (`get_current_username`), never from the body. Reading a
conversation returns every stored turn with its evidence, which is also the export format.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from common.jwt_auth import get_current_username
from common.logging import get_logger
from db import SessionLocal

from ..copilot import service as C
from ..copilot import store as St
from .ai_proxy import _admin_key, _admin_model
from .routes import _sanitise_symbol

log = get_logger("research-engine.copilot")
router = APIRouter(prefix="/copilot", tags=["copilot"])


class CopilotChatRequest(BaseModel):
    symbol: str
    question: str = Field(..., max_length=C.MAX_QUESTION_CHARS)
    request_id: str = Field(..., min_length=8, max_length=64)
    conversation_id: int | None = None
    provider: str = "claude"
    model: str = ""
    api_key: str = ""     # optional — falls back to the admin shared key, as /ai/chat does


def _usage_logger(**kw):
    from common.llm_usage import CALL_SITE_COPILOT_CHAT, log_llm_call
    log_llm_call(service="research-engine", call_site=CALL_SITE_COPILOT_CHAT, **kw)


@router.post("/chat")
async def copilot_chat(req: CopilotChatRequest, username: str = Depends(get_current_username)):
    try:
        sym = _sanitise_symbol(req.symbol)
    except ValueError:
        raise HTTPException(400, f"Invalid symbol: {req.symbol!r}")
    if req.provider not in ("claude", "deepseek"):
        raise HTTPException(400, f"Unknown provider: {req.provider!r}")
    own_key = req.api_key.strip()
    api_key = own_key or _admin_key(req.provider)
    if not api_key:
        raise HTTPException(400, "No AI API key configured. Ask the admin to set a shared key "
                                 "in Settings → AI Assistant, or add your own key in Settings.")
    from common import llm_budget
    turn = C.TurnRequest(
        username=username, symbol=sym, question=req.question, request_id=req.request_id,
        conversation_id=req.conversation_id, provider=req.provider,
        model=req.model.strip() or _admin_model(req.provider), api_key=api_key,
        uses_shared_key=not own_key)
    try:
        return await C.answer(turn, session_factory=SessionLocal, budget=llm_budget,
                              usage_logger=_usage_logger)
    except C.CopilotError as exc:
        if exc.status >= 500:
            log.warning("copilot.turn_failed", symbol=sym, status=exc.status, error=exc.message)
        raise HTTPException(exc.status, exc.message)


class CopilotCancelRequest(BaseModel):
    request_id: str = Field(..., min_length=8, max_length=64)


@router.post("/cancel")
def copilot_cancel(req: CopilotCancelRequest, username: str = Depends(get_current_username)):
    """Stop any FURTHER model call for this question.

    A provider request already running cannot be recalled: it completes, is billed, and its
    answer is stored (visible when the conversation is reopened). What this prevents is the
    repair call that would otherwise follow a rejected draft.
    """
    return {"cancelled": C.cancel(SessionLocal, username, req.request_id),
            "note": "a model call already in progress still completes and is counted"}


@router.get("/conversations")
def list_conversations(symbol: str | None = None, limit: int = Query(20, ge=1, le=100),
                       username: str = Depends(get_current_username)):
    subject = None
    if symbol:
        try:
            subject = f"stock:{_sanitise_symbol(symbol)}"
        except ValueError:
            raise HTTPException(400, f"Invalid symbol: {symbol!r}")
    with SessionLocal() as s:
        uid = St.user_id_for(s, username)
        if uid is None:
            raise HTTPException(403, "user not found or inactive")
        rows = St.list_conversations(s, uid, subject, limit)
        return {"conversations": [St.serialise_conversation(c) for c in rows],
                "retention_days": St.COPILOT_RETENTION_DAYS}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, username: str = Depends(get_current_username)):
    with SessionLocal() as s:
        uid = St.user_id_for(s, username)
        c = St.get_conversation(s, uid, conversation_id) if uid is not None else None
        if c is None:
            raise HTTPException(404, "conversation not found")
        return {"conversation": St.serialise_conversation(c),
                "messages": [St.serialise_message(m) for m in St.messages(s, c)],
                "retention_days": St.COPILOT_RETENTION_DAYS}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, username: str = Depends(get_current_username)):
    with SessionLocal() as s:
        uid = St.user_id_for(s, username)
        if uid is None or not St.delete_conversation(s, uid, conversation_id):
            raise HTTPException(404, "conversation not found")
        s.commit()
        return {"deleted": conversation_id}
