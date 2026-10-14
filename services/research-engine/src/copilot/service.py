"""One copilot turn: admit, build evidence, ask, check, persist.

NO DATABASE CONNECTION IS HELD ACROSS THE MODEL CALL. A model call takes seconds to a minute;
holding a pooled connection through it is the nested-session shape that exhausted the pool on
2026-09-22 (docs/incidents/db-connection-pool-exhaustion.md). So the turn runs in three phases:
read and admit in one short session, call the model with no session open, write the result in
a second short session.

BOUNDS. One answer is at most two model calls (a draft and one repair). Each call reserves
token capacity before it runs and settles against the provider's reported usage. A per-user
daily answer count bounds any single account. Calls made with the user's OWN key are not
charged to the shared budget — it exists to bound the platform's spend, not theirs.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from db.models import CopilotMessage

from . import grounding as G
from . import llm as L
from . import store as St
from .packet import PACKET_VERSION, build_packet

MAX_QUESTION_CHARS = 1000
MAX_ATTEMPTS = 2


class CopilotError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class TurnRequest:
    username: str
    symbol: str
    question: str
    request_id: str
    conversation_id: int | None
    provider: str
    model: str
    api_key: str                  # resolved: the user's own, or the admin shared key
    uses_shared_key: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _evidence_refs(packet, keys: list[str]) -> list[dict]:
    """Each cited key, resolved to the evidence records behind it — what the UI links to."""
    records = packet.book.records
    heads = {h["key"]: h for h in packet.headlines}
    out = []
    for k in keys:
        q = packet.quantities[k]
        f = packet.fields.get(q.field)
        evs = []
        for eid in (heads[k]["evidence_id"],) if k in heads else (f.evidence_ids if f else []):
            r = records.get(eid) or {}
            evs.append({"id": eid, "source": r.get("source"),
                        "observed_period": r.get("observed_period"),
                        "published_at": r.get("published_at"), "note": r.get("note")})
        out.append({"key": k, "label": (f.label if f else None) or q.field,
                    "display": q.display, "state": q.state, "note": q.note,
                    "url": heads[k].get("url") if k in heads else None,
                    "evidence": evs[:3]})
    return out


def _limitations(packet, model_lims: list[str]) -> list[str]:
    out = list(model_lims)
    for m in packet.missing():
        line = f"{m['label']}: {m['state'].lower()} — {m['reason']}"
        if line not in out:
            out.append(line)
    return out


def _alternating(hist: list[dict], *, exclude_request: str) -> list[dict]:
    """Prior turns as the providers require them: starting with a user turn, roles alternating.

    The CURRENT request's own stored question is excluded (a retry would otherwise send it
    twice), and a turn whose answer failed leaves two user turns in a row — those are merged
    rather than sent as-is, which the Anthropic API rejects.
    """
    out: list[dict] = []
    for m in hist:
        if m.get("request_id") == exclude_request or not m["content"]:
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1] = {"role": m["role"], "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append({"role": m["role"], "content": m["content"]})
    while out and out[0]["role"] != "user":
        out.pop(0)
    # The new question is appended as a user turn, so the history must end on an assistant one.
    if out and out[-1]["role"] == "user":
        out.pop()
    return out


async def answer(req: TurnRequest, *, session_factory, llm_call=L.call, budget=None,
                 usage_logger=None, now: datetime | None = None) -> dict:
    """Run one turn and return {conversation_id, message}. Raises CopilotError for the API."""
    question = (req.question or "").strip()
    if not question:
        raise CopilotError(400, "question is empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise CopilotError(400, f"question is longer than {MAX_QUESTION_CHARS} characters")
    if not req.request_id or len(req.request_id) > 64:
        raise CopilotError(400, "request_id is required (at most 64 characters)")
    now = now or _utcnow()

    # ── Phase 1: admit and read ───────────────────────────────────────────────
    with session_factory() as s:
        user_id = St.user_id_for(s, req.username)
        if user_id is None:
            raise CopilotError(403, "user not found or inactive")
        prior = St.replay(s, user_id, req.request_id)
        if prior is not None:
            return {"conversation_id": prior.conversation_id,
                    "message": St.serialise_message(prior), "replayed": True}
        limit = St.daily_answer_limit()
        if St.answers_today(s, user_id, now) >= limit:
            raise CopilotError(429, f"daily copilot limit reached ({limit} answers per UTC day)")
        packet = build_packet(s, req.symbol, now=now)
        if packet is None:
            raise CopilotError(404, f"{req.symbol} is not a tracked symbol")
        # The user turn may already exist: a retry after a failed model call reuses it — and
        # its conversation, because a failed FIRST turn never returned a conversation id to
        # the client, so the retry arrives without one.
        existing_user = s.query(CopilotMessage).filter_by(
            user_id=user_id, request_id=req.request_id, role="user").first()
        conv_hint = req.conversation_id
        if existing_user is not None and conv_hint is None:
            conv_hint = existing_user.conversation_id
        conv = St.open_conversation(s, user_id, packet.subject_key,
                                    title=question[:120], conversation_id=conv_hint)
        if conv is None:
            raise CopilotError(404, "conversation not found")
        hist = St.history(s, conv)
        if existing_user is None:
            s.add(CopilotMessage(conversation_id=conv.id, user_id=user_id, role="user",
                                 content=question, request_id=req.request_id, created_at=now))
        elif existing_user.conversation_id != conv.id:
            raise CopilotError(409, "request_id was already used in another conversation")
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            raise CopilotError(409, "this request is already being answered")
        conv_id = conv.id

    # ── Phase 2: ask (no session open) ────────────────────────────────────────
    messages = _alternating(hist, exclude_request=req.request_id)
    messages.append({"role": "user", "content": L.user_turn(packet.model_view(), question)})
    result: G.GroundingResult | None = None
    reasons: list[str] = []
    total_in = total_out = 0
    model_used = req.model
    t0 = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        res = None
        if budget is not None and req.uses_shared_key:
            res = budget.reserve("\n".join(m["content"] for m in messages) + L.SYSTEM_PROMPT,
                                 L.MAX_OUTPUT_TOKENS, scope=budget.SCOPE_COPILOT_CHAT)
            if not res.allowed:
                raise CopilotError(429, f"copilot budget unavailable: {res.reason}")
        a0 = time.monotonic()
        try:
            reply = await llm_call(req.provider, req.api_key, req.model, messages)
        except L.LlmError as exc:
            if res is not None:
                budget.reconcile(res, None, outcome=(budget.OUTCOME_AMBIGUOUS if exc.ambiguous
                                                     else budget.OUTCOME_FAILED))
            if usage_logger:
                usage_logger(model=req.model, usage=None, status="error",
                             duration_ms=int((time.monotonic() - a0) * 1000),
                             http_status=exc.status, error=str(exc),
                             context={"symbol": req.symbol, "attempt": attempt})
            raise CopilotError(502, str(exc))
        u = reply.usage or {}
        if res is not None:
            actual = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
            budget.reconcile(res, actual if reply.usage else None,
                             outcome=budget.OUTCOME_OK if reply.usage else budget.OUTCOME_AMBIGUOUS)
        if usage_logger:
            usage_logger(model=reply.model, usage=reply.usage, status="ok",
                         duration_ms=int((time.monotonic() - a0) * 1000),
                         http_status=reply.http_status,
                         context={"symbol": req.symbol, "attempt": attempt})
        total_in += u.get("input_tokens") or 0
        total_out += u.get("output_tokens") or 0
        model_used = reply.model
        result = G.check(G.parse_draft(reply.text), packet)
        if result.accepted:
            break
        reasons = result.reasons
        messages = messages + [{"role": "assistant", "content": reply.text},
                               {"role": "user", "content": L.repair_turn(reasons)}]

    accepted = bool(result and result.accepted)
    if accepted:
        content = result.answer
        status = "grounded"
        refs = _evidence_refs(packet, result.used_keys)
        lims = _limitations(packet, result.limitations)
    else:
        content = G.fallback_answer(packet, reasons)
        status = "rejected"
        shown = [k for k in ("price.close", "price.date", "return.5s", "return.20s",
                             "trend.structure", "signal.action", "signal.strength",
                             "signal.time", "kscore.score", "earnings.date", "report.version")
                 if k in packet.quantities]
        refs = _evidence_refs(packet, shown)
        lims = _limitations(packet, [])
    grounding = {
        "packet_version": PACKET_VERSION, "packet_fingerprint": packet.fingerprint,
        "data_as_of": packet.data_as_of, "evidence_refs": refs, "limitations": lims,
        "rejection_reasons": [] if accepted else reasons,
    }

    # ── Phase 3: write ────────────────────────────────────────────────────────
    with session_factory() as s:
        msg = CopilotMessage(
            conversation_id=conv_id, user_id=St.user_id_for(s, req.username), role="assistant",
            content=content, request_id=req.request_id, status=status, grounding=grounding,
            model=(model_used or "")[:64], input_tokens=total_in or None,
            output_tokens=total_out or None, duration_ms=int((time.monotonic() - t0) * 1000),
            created_at=_utcnow())
        s.add(msg)
        conv = s.get(St.CopilotConversation, conv_id)
        if conv is not None:
            conv.updated_at = _utcnow()
        try:
            s.commit()
        except IntegrityError:
            # A concurrent duplicate finished first. Serve what it stored, once.
            s.rollback()
            prior = St.replay(s, msg.user_id, req.request_id)
            if prior is None:
                raise
            return {"conversation_id": prior.conversation_id,
                    "message": St.serialise_message(prior), "replayed": True}
        return {"conversation_id": conv_id, "message": St.serialise_message(msg),
                "replayed": False}


def new_request_id() -> str:
    return uuid.uuid4().hex
