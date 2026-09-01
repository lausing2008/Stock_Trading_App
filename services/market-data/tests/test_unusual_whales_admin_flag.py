"""Tests for the unusual_whales_enabled admin flag + unusual_whales_api_key credential —
following test_theme_forecast_admin_flag.py's/test_earnings_macro_llm_admin_flags.py's
established real-behavioral-test pattern exactly (admin.py genuinely imports under this test
environment's stubbed conftest.py). unusual_whales_enabled defaults OFF, matching every other
new opt-in external-data feature (auto_research_enabled/earnings_llm_impact_enabled/
theme_forecast_email_enabled) — NOT macro_llm_reaction_enabled's inverted "unset = on" semantics.

The key/unshare pair mirrors test_alpaca_admin_flag-style coverage (Alpaca is the closest
existing precedent with a real credential field alongside its own on/off semantics), since
Unusual Whales — like Alpaca, unlike theme_forecast — has a real API key to store/clear, not
just a bare on/off switch.
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

    def exists(self, key):
        return 1 if key in self._store else 0


def _admin_user():
    return MagicMock()


def test_defaults_to_false_when_unset(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags_public()
    assert result["unusual_whales_enabled"] is False


def test_get_feature_flags_admin_also_reports_the_flag(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_UW_ENABLED, "1")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags(_admin_user())
    assert result["unusual_whales_enabled"] is True


def test_key_set_is_false_when_no_key_has_ever_been_saved(monkeypatch):
    """The Settings page needs a presence-only signal to show 'already configured' without
    ever re-displaying (or losing, on a page refresh) the real saved secret."""
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    assert admin.get_feature_flags_public()["unusual_whales_key_set"] is False
    assert admin.get_feature_flags(_admin_user())["unusual_whales_key_set"] is False


def test_key_set_is_true_once_a_key_has_been_saved(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_UW_KEY, "real-secret-value-never-returned")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    assert admin.get_feature_flags_public()["unusual_whales_key_set"] is True
    assert admin.get_feature_flags(_admin_user())["unusual_whales_key_set"] is True


def test_key_set_never_leaks_the_real_secret_value(monkeypatch):
    """The whole point of this field — a presence boolean, never the value itself. Confirms
    the real returned dict never contains the actual saved secret anywhere in its values."""
    fake = _FakeRedis()
    secret = "sk-real-secret-do-not-leak"
    fake.set(admin._REDIS_UW_KEY, secret)
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    result = admin.get_feature_flags_public()
    assert result["unusual_whales_key_set"] is True
    assert secret not in result.values()


def test_key_set_becomes_false_again_after_unshare(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_UW_KEY, "real-secret-value")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(unshare_unusual_whales_key=True)
    admin.update_config(req, _admin_user())
    assert admin.get_feature_flags_public()["unusual_whales_key_set"] is False


def test_update_config_writes_unusual_whales_enabled_true(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(unusual_whales_enabled=True)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_UW_ENABLED) == "1"


def test_update_config_can_turn_it_back_off(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_UW_ENABLED, "1")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(unusual_whales_enabled=False)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_UW_ENABLED) == "0"


def test_update_config_omitting_the_flag_leaves_it_untouched(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_UW_ENABLED, "1")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(claude_api_key="sk-test")
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_UW_ENABLED) == "1"  # unchanged


def test_update_config_with_only_this_flag_still_fetches_redis(monkeypatch):
    """The guard deciding whether to call _get_redis() at all must include this field too —
    a request setting ONLY this flag must still actually reach Redis (the exact
    T232-CONFIGGAP-style regression class this repo has hit before whenever a new field is
    added but forgotten in the guard condition)."""
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(unusual_whales_enabled=True)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_UW_ENABLED) == "1"


def test_update_config_writes_the_api_key(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(unusual_whales_api_key="uw-real-token")
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_UW_KEY) == "uw-real-token"


def test_update_config_with_only_the_api_key_still_fetches_redis(monkeypatch):
    """Same T232-CONFIGGAP-style guard check as the flag above, but for the key field —
    a request setting ONLY the key (no flag) must still reach Redis."""
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(unusual_whales_api_key="uw-real-token")
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_UW_KEY) == "uw-real-token"


def test_unshare_deletes_the_key(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_UW_KEY, "uw-real-token")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(unshare_unusual_whales_key=True)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_UW_KEY) is None


def test_unshare_alone_still_fetches_redis(monkeypatch):
    fake = _FakeRedis()
    fake.set(admin._REDIS_UW_KEY, "uw-real-token")
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(unshare_unusual_whales_key=True)
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_UW_KEY) is None


def test_setting_the_key_does_not_implicitly_enable_the_feature(monkeypatch):
    """A key being present must not by itself flip the feature on — the flag and the
    credential are two independent fields, matching get_unusual_whales_key()'s/
    is_unusual_whales_enabled()'s own documented "callers must check both separately"
    contract in shared/common/ai_keys.py."""
    fake = _FakeRedis()
    monkeypatch.setattr(admin, "_get_redis", lambda: fake)
    req = admin.ConfigRequest(unusual_whales_api_key="uw-real-token")
    admin.update_config(req, _admin_user())
    assert fake.get(admin._REDIS_UW_KEY) == "uw-real-token"
    assert fake.get(admin._REDIS_UW_ENABLED) is None


def test_redis_key_literals_match_ai_keys_pys_own_constants():
    """admin.py and shared/common/ai_keys.py each hardcode these two Redis keys
    independently — they must still agree on the literal strings, or a key/flag written
    here would never be read by get_unusual_whales_key()/is_unusual_whales_enabled()."""
    import pathlib
    ai_keys_source = (
        pathlib.Path(__file__).resolve().parents[3] / "shared" / "common" / "ai_keys.py"
    ).read_text()
    assert admin._REDIS_UW_KEY in ai_keys_source
    assert admin._REDIS_UW_ENABLED in ai_keys_source
