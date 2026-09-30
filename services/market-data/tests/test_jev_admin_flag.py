"""Exercise the Jev configuration contract; no provider or trading pipeline is involved."""
from unittest.mock import MagicMock

import pytest
from src.api import admin


class Redis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def exists(self, key):
        return key in self.values


@pytest.fixture
def redis(monkeypatch):
    client = Redis()
    monkeypatch.setattr(admin, "_get_redis", lambda: client)
    monkeypatch.setattr(admin, "_provider_key_presence", lambda: {})
    return client


@pytest.mark.parametrize("stored", [None, "0", "true", "invalid"])
def test_only_explicit_enable_is_on(redis, stored):
    if stored is not None:
        redis.set(admin._REDIS_JEV_ENABLED, stored)
    assert admin.get_feature_flags(MagicMock())["jev_enabled"] is False
    assert admin.get_feature_flags_public()["jev_enabled"] is False


def test_flag_only_request_round_trips_on_and_off(redis):
    redis.set(admin._REDIS_UW_ENABLED, "1")
    for enabled in (True, False):
        result = admin.update_config(admin.ConfigRequest(jev_enabled=enabled), MagicMock())
        assert result == {"status": "ok"}
        assert admin.get_feature_flags(MagicMock())["jev_enabled"] is enabled
        assert admin.get_feature_flags_public()["jev_enabled"] is enabled
        assert redis.get(admin._REDIS_UW_ENABLED) == "1"


@pytest.mark.parametrize("payload", [{}, {"jev_enabled": None}, {"broker_enabled": False}])
def test_omission_and_null_preserve_preference(redis, payload):
    redis.set(admin._REDIS_JEV_ENABLED, "1")
    admin.update_config(admin.ConfigRequest(**payload), MagicMock())
    assert redis.get(admin._REDIS_JEV_ENABLED) == "1"


def test_write_failure_is_not_reported_as_success(redis, monkeypatch):
    def fail(*args):
        raise ConnectionError("Redis unavailable")
    monkeypatch.setattr(redis, "set", fail)
    with pytest.raises(ConnectionError):
        admin.update_config(admin.ConfigRequest(jev_enabled=True), MagicMock())
    assert admin.get_feature_flags_public()["jev_enabled"] is False
