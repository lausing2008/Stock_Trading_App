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


# T258-NEWS-INTELLIGENCE: same admin-configured-credential pattern as get_admin_ai_key() above,
# but Alpaca's news WebSocket needs a KEY+SECRET pair (OAuth-style API key ID + secret key),
# not a single bearer token — hence a separate pair of Redis keys instead of reusing
# _REDIS_KEYS/get_admin_ai_key("alpaca") for a single string.
_ALPACA_KEY_REDIS = "stockai:admin:alpaca_api_key"
_ALPACA_SECRET_REDIS = "stockai:admin:alpaca_secret_key"


def get_alpaca_credentials() -> tuple[str, str]:
    """Return (api_key, secret_key) for Alpaca's news WebSocket, or ("", "") if unset/unavailable.

    Fail-open, matching get_admin_ai_key()'s exact contract — a Redis outage or an unconfigured
    key must never raise; the news-intelligence service treats ("", "") as "Alpaca source
    disabled," not an error worth surfacing distinctly (the RSS/EDGAR sources keep working
    regardless of whether Alpaca is configured).
    """
    try:
        r = get_redis()
        key = (r.get(_ALPACA_KEY_REDIS) or "").strip()
        secret = (r.get(_ALPACA_SECRET_REDIS) or "").strip()
        return key, secret
    except Exception:
        return "", ""


# MPE-06/MPE-07: Unusual Whales — a single bearer token (confirmed live against the real
# https://api.unusualwhales.com/api/openapi spec: securitySchemes.authorization = {scheme:
# bearer, type: http}), matching get_admin_ai_key()'s single-string shape, not Alpaca's
# key+secret pair.
_UW_KEY_REDIS = "stockai:admin:unusual_whales_api_key"
_UW_ENABLED_REDIS = "stockai:admin:feature:unusual_whales_enabled"


def get_unusual_whales_key() -> str:
    """Return the admin-configured Unusual Whales API key from Redis, or "" if unset/unavailable.

    Fail-open, matching get_admin_ai_key()'s exact contract. Callers must ALSO check
    is_unusual_whales_enabled() separately — a key being present does not by itself mean the
    feature is turned on (matches every other opt-in-flag-gated integration in this codebase,
    e.g. auto_research_enabled/risk_check_enabled — a real credential existing is not the same
    as the admin having actually enabled the feature it powers)."""
    try:
        return (get_redis().get(_UW_KEY_REDIS) or "").strip()
    except Exception:
        return ""


def is_unusual_whales_enabled() -> bool:
    """Default OFF, matching every other opt-in paid/external-data feature in this codebase
    (auto_research_enabled, risk_check_enabled, earnings_llm_forecast_enabled) — a real,
    metered, per-request-cost API must never be silently on by default."""
    try:
        return get_redis().get(_UW_ENABLED_REDIS) == "1"
    except Exception:
        return False
