"""AI proxy — routes chat requests to Claude (Anthropic) or DeepSeek.

The caller may supply their own API key in the request body. If omitted,
the gateway falls back to the admin-configured shared key stored in Redis
(set via POST /admin/config in market-data with claude_api_key / deepseek_api_key).

This means regular users get full AI features without needing their own API keys.

T233-ARCH-AIPROXY-EXTRACT: moved here from api-gateway 2026-07-04. api-gateway is
meant to be a pure reverse proxy; this is a business feature (LLM chat) that only
lived there because the gateway happened to be the one service with outbound
internet access. research-engine already reads the same admin-configured
stockai:admin:claude_api_key/deepseek_api_key Redis keys for its own report-chat
feature, so this consolidates onto a service that already depends on the same
config rather than duplicating it a third time.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from common.config import get_settings
from common.jwt_auth import get_current_username

router = APIRouter(prefix="/ai", tags=["ai"])

_settings = get_settings()
_REDIS_CLAUDE_KEY     = "stockai:admin:claude_api_key"
_REDIS_DEEPSEEK_KEY   = "stockai:admin:deepseek_api_key"
_REDIS_CLAUDE_MODEL   = "stockai:admin:claude_model"
_REDIS_DEEPSEEK_MODEL = "stockai:admin:deepseek_model"


def _get_redis():
    from common.redis_client import get_redis as _get_pool_redis
    return _get_pool_redis()


def _admin_key(provider: str) -> str:
    """Return the admin-stored fallback API key for provider, or ''.

    AUD-DUPLOGIC: delegates to common.ai_keys.get_admin_ai_key() — one of 6 independent
    copies of this same lookup that had accumulated across decision-engine/event-intelligence/
    market-data/research-engine (see shared/common/ai_keys.py's own module docstring).
    """
    from common.ai_keys import get_admin_ai_key
    return get_admin_ai_key(provider)


def _admin_model(provider: str) -> str:
    rkey = _REDIS_CLAUDE_MODEL if provider == "claude" else _REDIS_DEEPSEEK_MODEL
    default = "claude-sonnet-4-6" if provider == "claude" else "deepseek-chat"
    try:
        return _get_redis().get(rkey) or default
    except Exception:
        return default


class AiMessage(BaseModel):
    role: str
    content: str


class AiChatRequest(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""   # optional — falls back to admin shared key in Redis
    messages: list[AiMessage]
    system: str | None = None
    max_tokens: int = Field(default=2048, ge=1, le=4096)
    temperature: float = 0.2


class AiChatResponse(BaseModel):
    content: str
    model: str
    provider: str


@router.post("/chat", response_model=AiChatResponse)
async def ai_chat(req: AiChatRequest, _: str = Depends(get_current_username)) -> AiChatResponse:
    api_key = req.api_key.strip() or _admin_key(req.provider)
    if not api_key:
        raise HTTPException(
            400,
            "No AI API key configured. "
            "Ask the admin to set a shared key in Settings → AI Assistant, "
            "or add your own key in Settings.",
        )
    model = req.model.strip() or _admin_model(req.provider)

    if req.provider == "claude":
        return await _claude(req, api_key, model)
    if req.provider == "deepseek":
        return await _deepseek(req, api_key, model)
    raise HTTPException(400, f"Unknown provider: {req.provider!r}")


async def _claude(req: AiChatRequest, api_key: str, model: str) -> AiChatResponse:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict = {
        "model": model,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
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
        raise HTTPException(502, f"Claude error: {detail}")

    try:
        data = r.json()
        content = data["content"][0]["text"]
    except Exception as exc:
        raise HTTPException(502, f"Failed to parse Claude response: {exc}")

    return AiChatResponse(content=content, model=data.get("model", model), provider="claude")


async def _deepseek(req: AiChatRequest, api_key: str, model: str) -> AiChatResponse:
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    if req.system:
        messages = [{"role": "system", "content": req.system}] + messages

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
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
        raise HTTPException(502, f"DeepSeek error: {detail}")

    try:
        data = r.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise HTTPException(502, f"Failed to parse DeepSeek response: {exc}")

    return AiChatResponse(content=content, model=data.get("model", model), provider="deepseek")
