"""AUD-PROVIDERKEY-INMEMORY: set_runtime_key()/get_runtime_key() (services/market-data/src/
adapters/registry.py) used to be backed by a plain in-process dict, unlike every other
admin-configured provider credential (Claude/DeepSeek/Alpaca/Unusual Whales — all Redis-backed
via shared/common/ai_keys.py). A Polygon/Alpha Vantage key entered on the Settings page reached
the adapter for the life of the current uvicorn process, then silently vanished on the next
deploy/restart with no error or "unset" signal anywhere. Now Redis-backed to match every
sibling provider's persistence contract.
"""
from unittest.mock import patch

from src.adapters import registry


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


def test_set_then_get_runtime_key_round_trips_through_redis():
    fake = _FakeRedis()
    with patch.object(registry, "_redis", return_value=fake):
        registry.set_runtime_key("polygon", "abc123")
        assert registry.get_runtime_key("polygon") == "abc123"
        assert fake.store["stockai:admin:provider_key:polygon"] == "abc123"


def test_get_runtime_key_survives_a_fresh_process_reading_the_same_redis():
    """The whole point of the fix: a second, independent read (simulating a post-restart
    process with no in-memory state at all) must still see the key, since it never lived
    in-memory in the first place."""
    fake = _FakeRedis()
    with patch.object(registry, "_redis", return_value=fake):
        registry.set_runtime_key("alpha_vantage", "xyz789")
    # A brand new call, same fake Redis backing store — nothing carried over in-process.
    with patch.object(registry, "_redis", return_value=fake):
        assert registry.get_runtime_key("alpha_vantage") == "xyz789"


def test_get_runtime_key_returns_none_when_unset():
    fake = _FakeRedis()
    with patch.object(registry, "_redis", return_value=fake):
        assert registry.get_runtime_key("polygon") is None


def test_keys_for_different_providers_do_not_collide():
    fake = _FakeRedis()
    with patch.object(registry, "_redis", return_value=fake):
        registry.set_runtime_key("polygon", "poly-key")
        registry.set_runtime_key("alpha_vantage", "av-key")
        assert registry.get_runtime_key("polygon") == "poly-key"
        assert registry.get_runtime_key("alpha_vantage") == "av-key"


def test_set_runtime_key_fails_open_on_redis_error():
    class _BrokenRedis:
        def set(self, *a, **kw):
            raise ConnectionError("redis down")

    with patch.object(registry, "_redis", return_value=_BrokenRedis()):
        registry.set_runtime_key("polygon", "abc123")  # must not raise


def test_get_runtime_key_fails_open_to_none_on_redis_error():
    class _BrokenRedis:
        def get(self, *a, **kw):
            raise ConnectionError("redis down")

    with patch.object(registry, "_redis", return_value=_BrokenRedis()):
        assert registry.get_runtime_key("polygon") is None
