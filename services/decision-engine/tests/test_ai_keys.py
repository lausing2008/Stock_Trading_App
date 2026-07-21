"""Tests for shared/common/ai_keys.py — the single source of truth for the admin-configured
Claude/DeepSeek API key lookup, consolidated from 6 independent copies that had accumulated
across decision-engine/event-intelligence/market-data/research-engine (AUD-DUPLOGIC).

Loaded via exec()-from-source (matching this repo's established technique for shared/common
modules under a stubbed `common` package, e.g. test_hard_rejects.py's conviction-gate mocks)
rather than a real import, since `common` is stubbed as a bare MagicMock() by every decision-
engine test file — `from .redis_client import get_redis` (a relative import) inside ai_keys.py
would resolve against that stub, not the real module, if imported normally in this harness.
"""
import pathlib
from unittest.mock import MagicMock

_ai_keys_path = (
    pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "ai_keys.py"
)
_ai_keys_source = _ai_keys_path.read_text()

# Strip the relative import (`from .redis_client import get_redis`) — exec() has no package
# context to resolve it against — and inject a stub `get_redis` name directly, patched per test.
_source_no_relative_import = _ai_keys_source.replace(
    "from .redis_client import get_redis", ""
)

_namespace: dict = {"get_redis": None}
exec(compile(_source_no_relative_import, str(_ai_keys_path), "exec"), _namespace)
get_admin_ai_key = _namespace["get_admin_ai_key"]


def _mock_redis(value_by_key: dict):
    class _FakeRedis:
        def get(self, key):
            return value_by_key.get(key)
    _namespace["get_redis"] = lambda: _FakeRedis()


def test_returns_claude_key_from_redis():
    _mock_redis({"stockai:admin:claude_api_key": "real-claude-key"})
    assert get_admin_ai_key("claude") == "real-claude-key"


def test_returns_deepseek_key_from_redis():
    _mock_redis({"stockai:admin:deepseek_api_key": "real-deepseek-key"})
    assert get_admin_ai_key("deepseek") == "real-deepseek-key"


def test_defaults_to_claude_when_provider_omitted():
    _mock_redis({"stockai:admin:claude_api_key": "default-provider-key"})
    assert get_admin_ai_key() == "default-provider-key"


def test_returns_empty_string_when_key_unset():
    _mock_redis({})
    assert get_admin_ai_key("claude") == ""


def test_strips_whitespace_from_stored_key():
    _mock_redis({"stockai:admin:claude_api_key": "  key-with-padding  \n"})
    assert get_admin_ai_key("claude") == "key-with-padding"


def test_whitespace_only_value_normalizes_to_empty_string():
    _mock_redis({"stockai:admin:claude_api_key": "   "})
    assert get_admin_ai_key("claude") == ""


def test_redis_failure_returns_empty_string_not_raise():
    def _broken():
        raise ConnectionError("redis unavailable")
    _namespace["get_redis"] = _broken
    assert get_admin_ai_key("claude") == ""


def test_unknown_provider_falls_back_to_claude_key():
    """_REDIS_KEYS.get(provider, _REDIS_KEYS["claude"]) — an unrecognized provider string
    must not raise or silently return a wrong-but-plausible key; it degrades to the claude
    key, matching the dict's own .get() fallback default."""
    _mock_redis({"stockai:admin:claude_api_key": "fallback-key"})
    assert get_admin_ai_key("openai") == "fallback-key"


def test_claude_and_deepseek_keys_are_independent():
    _mock_redis({
        "stockai:admin:claude_api_key": "claude-key",
        "stockai:admin:deepseek_api_key": "deepseek-key",
    })
    assert get_admin_ai_key("claude") == "claude-key"
    assert get_admin_ai_key("deepseek") == "deepseek-key"
