"""Tests for the theme_forecast_email_enabled admin flag — following
test_earnings_macro_llm_admin_flags.py's/test_auto_research_admin_flag.py's established
real-behavioral-test pattern exactly (admin.py genuinely imports under this test environment's
stubbed conftest.py). theme_forecast_email_enabled defaults OFF, matching every other brand-new
opt-in Claude-calling feature added since CLAUDE-API-COST-AUDIT (auto_research_enabled/
earnings_llm_impact_enabled) — NOT macro_llm_reaction_enabled's inverted "unset = on" semantics.
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


def test_defaults_to_false_when_unset(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags_public()
    assert result["theme_forecast_email_enabled"] is False


def test_get_feature_flags_admin_also_reports_the_flag(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_THEME_FORECAST_ENABLED, "1")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags(_admin_user())
    assert result["theme_forecast_email_enabled"] is True


def test_update_config_writes_theme_forecast_email_enabled_true(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(theme_forecast_email_enabled=True)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_THEME_FORECAST_ENABLED) == "1"


def test_update_config_can_turn_it_back_off(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_THEME_FORECAST_ENABLED, "1")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(theme_forecast_email_enabled=False)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_THEME_FORECAST_ENABLED) == "0"


def test_update_config_omitting_the_flag_leaves_it_untouched(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_THEME_FORECAST_ENABLED, "1")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(claude_api_key="sk-test")
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_THEME_FORECAST_ENABLED) == "1"  # unchanged


def test_update_config_with_only_this_flag_still_fetches_redis(monkeypatch):
    """The guard deciding whether to call _get_redis() at all must include this field too —
    a request setting ONLY this flag must still actually reach Redis (the exact
    T232-CONFIGGAP-style regression class this repo has hit before whenever a new field is
    added but forgotten in the guard condition)."""
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(theme_forecast_email_enabled=True)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_THEME_FORECAST_ENABLED) == "1"


def test_redis_key_literal_matches_schedulers_own_constant():
    """admin.py and scheduler.py each hardcode this Redis key independently — they must still
    agree on the literal string, or a flag written here would never be read there."""
    import pathlib
    scheduler_source = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
    ).read_text()
    assert admin._REDIS_THEME_FORECAST_ENABLED in scheduler_source
