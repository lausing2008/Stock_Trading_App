"""Shared AI provider API key lookup — single source of truth for the admin-configured
Claude/DeepSeek keys every LLM-calling service reads.

Consolidates 6 independent copies of this exact lookup that had silently accumulated across
decision-engine (llm_scorer.py, risk_agent.py), event-intelligence (macro_reaction.py),
market-data (news.py), and research-engine (routes.py, ai_proxy.py) — each written by copying
an earlier one and drifting slightly (some checked `.strip()` truthiness, some didn't; some had
a cfg-dict/settings-attr fallback, some had a bare `""`). In practice every fallback path was
already dead: `Settings` never had a `claude_api_key`/`deepseek_api_key` field, and no caller's
`cfg` dict ever populated one either — so every real call already reduced to "read Redis, or
empty string." This module makes that the one actual implementation instead of six near-copies
of it.

Usage:
    from common.ai_keys import get_admin_ai_key
    key = get_admin_ai_key("claude")  # or "deepseek"
"""
from .redis_client import get_redis

_REDIS_KEYS = {
    "claude": "stockai:admin:claude_api_key",
    "deepseek": "stockai:admin:deepseek_api_key",
}


def get_admin_ai_key(provider: str = "claude") -> str:
    """Return the admin-configured API key for `provider` from Redis, or "" if unset/unavailable.

    Fail-open by design (matches every prior copy's own contract) — a Redis outage or an
    unconfigured key must never raise; callers already treat "" as "AI features unavailable
    right now," never as an error condition worth surfacing distinctly.
    """
    rkey = _REDIS_KEYS.get(provider, _REDIS_KEYS["claude"])
    try:
        key = get_redis().get(rkey) or ""
        return key.strip()
    except Exception:
        return ""
