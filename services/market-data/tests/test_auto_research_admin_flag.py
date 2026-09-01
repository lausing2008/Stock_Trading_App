"""Tests for CLAUDE-API-COST-AUDIT (2026-07-28) — the admin.py side of the auto_research_enabled
global feature flag: ConfigRequest field, GET /admin/feature-flags(/public), and POST
/admin/config's write branch. Mirrors the existing broker_enabled flag's own 4 touch points
exactly (this file had zero prior tests for either flag).

admin.py genuinely imports under this test environment's stubbed conftest.py (fastapi/pydantic
are real, installed packages here) — so this is a direct, real behavioral test against the
actual route functions, not source-text extraction.
"""
from unittest.mock import MagicMock

from src.api import admin


class _FakeRedis:
    """Minimal in-memory Redis stand-in — just enough for .get/.set/.delete."""

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

    def exists(self, key):
        return 1 if key in self._store else 0


def _admin_user():
    return MagicMock()


def test_feature_flags_default_to_false_when_redis_key_unset(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags(_admin_user())
    assert result["auto_research_enabled"] is False
    assert result["broker_enabled"] is False


def test_feature_flags_public_also_reports_auto_research_enabled(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_AUTO_RESEARCH_ENABLED, "1")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags_public()
    assert result["auto_research_enabled"] is True


def test_update_config_writes_auto_research_enabled_true(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(auto_research_enabled=True)
    result = admin.update_config(req, _admin_user())
    assert result == {"status": "ok"}
    assert fake.get(admin._REDIS_AUTO_RESEARCH_ENABLED) == "1"


def test_update_config_writes_auto_research_enabled_false():
    fake = _FakeRedis()
    fake.set(admin._REDIS_AUTO_RESEARCH_ENABLED, "1")
    import unittest.mock as _um
    with _um.patch.object(admin, "_get_redis", lambda: fake):
        req = admin.ConfigRequest(auto_research_enabled=False)
        admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_AUTO_RESEARCH_ENABLED) == "0"


def test_update_config_omitting_auto_research_enabled_leaves_it_untouched():
    """A request that doesn't mention auto_research_enabled at all (None, the pydantic
    default) must not touch the existing Redis value — same contract as every other
    optional field on ConfigRequest."""
    fake = _FakeRedis()
    fake.set(admin._REDIS_AUTO_RESEARCH_ENABLED, "1")
    import unittest.mock as _um
    with _um.patch.object(admin, "_get_redis", lambda: fake):
        req = admin.ConfigRequest(claude_api_key="sk-test")
        admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_AUTO_RESEARCH_ENABLED) == "1"  # unchanged
    assert fake.get(admin._REDIS_CLAUDE_KEY) == "sk-test"


def test_update_config_with_only_auto_research_enabled_still_fetches_redis():
    """The guard deciding whether to call _get_redis() at all must include
    auto_research_enabled — a request setting ONLY this field (no other AI-key/broker
    field) must still actually reach Redis, not silently no-op."""
    fake = _FakeRedis()
    import unittest.mock as _um
    with _um.patch.object(admin, "_get_redis", lambda: fake) as mock_get_redis:
        req = admin.ConfigRequest(auto_research_enabled=True)
        admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_AUTO_RESEARCH_ENABLED) == "1"


def test_redis_key_literal_matches_schedulers_own_constant():
    """admin.py and scheduler.py each hardcode this Redis key independently (this repo's
    established convention of not cross-importing private Redis-key constants between
    services/ and api/ files) — they must still agree on the literal string, or the flag
    written here would never be read there."""
    import pathlib
    scheduler_source = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
    ).read_text()
    assert admin._REDIS_AUTO_RESEARCH_ENABLED in scheduler_source
