"""Tests for the macro_llm_reaction_enabled / earnings_llm_impact_enabled admin flags —
following test_auto_research_admin_flag.py's established real-behavioral-test pattern exactly
(admin.py genuinely imports under this test environment's stubbed conftest.py).

macro_llm_reaction_enabled defaults ON (the feature has been live since T249-P2) — its Redis
semantics are inverted from every other flag in this file: unset/None must report/behave as
enabled, only an explicit "0" disables it. earnings_llm_impact_enabled defaults OFF (brand new
feature), same convention as auto_research_enabled.
"""
from unittest.mock import MagicMock

from src.api import admin


class _FakeRedis:
    def __init__(self):
        self._store: dict[str, str] = {}

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    def delete(self, key):
        self._store.pop(key, None)


def _admin_user():
    return MagicMock()


# ── macro_llm_reaction_enabled — default ON ─────────────────────────────────────────

def test_macro_llm_reaction_defaults_to_true_when_unset(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags_public()
    assert result["macro_llm_reaction_enabled"] is True


def test_macro_llm_reaction_reports_false_only_when_explicitly_off(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_MACRO_LLM_ENABLED, "0")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags_public()
    assert result["macro_llm_reaction_enabled"] is False


def test_update_config_can_turn_macro_llm_reaction_off(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(macro_llm_reaction_enabled=False)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_MACRO_LLM_ENABLED) == "0"


def test_update_config_can_turn_macro_llm_reaction_back_on(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_MACRO_LLM_ENABLED, "0")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(macro_llm_reaction_enabled=True)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_MACRO_LLM_ENABLED) == "1"


# ── earnings_llm_impact_enabled — default OFF ───────────────────────────────────────

def test_earnings_llm_impact_defaults_to_false_when_unset(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags_public()
    assert result["earnings_llm_impact_enabled"] is False


def test_update_config_writes_earnings_llm_impact_enabled_true(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(earnings_llm_impact_enabled=True)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_EARNINGS_LLM_ENABLED) == "1"


def test_update_config_omitting_new_flags_leaves_them_untouched(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_EARNINGS_LLM_ENABLED, "1")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(claude_api_key="sk-test")
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_EARNINGS_LLM_ENABLED) == "1"  # unchanged


def test_update_config_with_only_new_flags_still_fetches_redis(monkeypatch):
    """The guard deciding whether to call _get_redis() at all must include both new fields —
    a request setting ONLY one of them must still actually reach Redis."""
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(earnings_llm_impact_enabled=True)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_EARNINGS_LLM_ENABLED) == "1"


def test_redis_key_literals_match_schedulers_own_constants():
    """admin.py and scheduler.py each hardcode these Redis keys independently — they must
    still agree on the literal strings, or a flag written here would never be read there."""
    import pathlib
    scheduler_source = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
    ).read_text()
    assert admin._REDIS_MACRO_LLM_ENABLED in scheduler_source
    assert admin._REDIS_EARNINGS_LLM_ENABLED in scheduler_source


def test_redis_key_literal_matches_event_intelligence_earnings_py():
    """admin.py's earnings flag key must also match event-intelligence's own detection-side
    gate (a different service, same hardcoded-literal-not-cross-imported convention)."""
    import pathlib
    earnings_source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "event-intelligence" / "src" / "services" / "earnings.py"
    ).read_text()
    assert admin._REDIS_EARNINGS_LLM_ENABLED in earnings_source
