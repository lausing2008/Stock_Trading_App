"""AI proxy — routes chat requests to Claude (Anthropic) or DeepSeek.

The caller supplies their own API key in the request body; the gateway
forwards it to the upstream AI provider and returns the assistant reply.
No keys are stored server-side.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/ai", tags=["ai"])


class AiMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class AiChatRequest(BaseModel):
    provider: str           # "claude" | "deepseek"
    model: str
    api_key: str
    messages: list[AiMessage]
    system: str | None = None
    max_tokens: int = 2048


class AiChatResponse(BaseModel):
    content: str
    model: str
    provider: str


@router.post("/chat", response_model=AiChatResponse)
async def ai_chat(req: AiChatRequest) -> AiChatResponse:
    if not req.api_key.strip():
        raise HTTPException(400, "API key is required")
    if req.provider == "claude":
        return await _claude(req)
    if req.provider == "deepseek":
        return await _deepseek(req)
    raise HTTPException(400, f"Unknown provider: {req.provider!r}. Use 'claude' or 'deepseek'.")


async def _claude(req: AiChatRequest) -> AiChatResponse:
    headers = {
        "x-api-key": req.api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict = {
        "model": req.model,
        "max_tokens": req.max_tokens,
        "messages": [{"role": m.role, "content": m.content} for m in req.messages],
    }
    if req.system:
        body["system"] = req.system

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
    except httpx.TimeoutException:
        raise HTTPException(504, "Claude API timed out after 120 s")
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Network error contacting Claude: {exc}")

    if r.status_code != 200:
        try:
            detail = r.json().get("error", {}).get("message", r.text)
        except Exception:
            detail = r.text
        raise HTTPException(r.status_code, f"Claude error: {detail}")

    try:
        data = r.json()
        content = data["content"][0]["text"]
    except Exception as exc:
        raise HTTPException(502, f"Failed to parse Claude response: {exc}")

    return AiChatResponse(content=content, model=data.get("model", req.model), provider="claude")


async def _deepseek(req: AiChatRequest) -> AiChatResponse:
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    if req.system:
        messages = [{"role": "system", "content": req.system}] + messages

    headers = {
        "Authorization": f"Bearer {req.api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": req.model,
        "max_tokens": req.max_tokens,
        "messages": messages,
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post("https://api.deepseek.com/v1/chat/completions", headers=headers, json=body)
    except httpx.TimeoutException:
        raise HTTPException(504, "DeepSeek API timed out after 120 s")
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Network error contacting DeepSeek: {exc}")

    if r.status_code != 200:
        try:
            detail = r.json().get("error", {}).get("message", r.text)
        except Exception:
            detail = r.text
        raise HTTPException(r.status_code, f"DeepSeek error: {detail}")

    try:
        data = r.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise HTTPException(502, f"Failed to parse DeepSeek response: {exc}")

    return AiChatResponse(content=content, model=data.get("model", req.model), provider="deepseek")
