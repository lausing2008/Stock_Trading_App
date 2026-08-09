"""Tests for AUD263-TUNED-PARAMS-SILENTLY-REVERT-ON-TTL — every tuned parameter (buy_threshold,
ml_weight_cap, adx_min, breadth_compression, ta_weights, conviction_weights) lives behind a
TTL'd Redis key; on expiry the read side silently falls back to the hardcoded default,
indistinguishable from "never tuned at all". _mark_tuned()/_tuning_staleness() write/read a
companion no-TTL marker so the two states become distinguishable.

signals_shared.py imports directly under this test environment's pytest/conftest.py setup —
direct behavioral tests against the real module.
"""
from src.api import signals_shared


class _FakeRedis:
    def __init__(self, seed: dict[str, str] | None = None):
        self._store = dict(seed or {})

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value):
        self._store[key] = value

    def exists(self, key):
        return 1 if key in self._store else 0


def test_mark_tuned_writes_todays_date_under_the_last_tuned_at_suffix(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(signals_shared, "_get_redis", lambda: fake)

    signals_shared._mark_tuned("stockai:signal_thresholds:SWING")

    assert fake._store["stockai:signal_thresholds:SWING:last_tuned_at"] == signals_shared.date.today().isoformat()


def test_mark_tuned_fails_open_on_a_redis_exception():
    class _BrokenRedis:
        def set(self, key, value):
            raise ConnectionError("redis unreachable")

    import unittest.mock as _mock
    with _mock.patch.object(signals_shared, "_get_redis", lambda: _BrokenRedis()):
        signals_shared._mark_tuned("stockai:signal_thresholds:SWING")  # must not raise


def test_never_tuned_reports_no_marker_and_not_reverted(monkeypatch):
    fake = _FakeRedis()  # both the marker and the value key are absent
    monkeypatch.setattr(signals_shared, "_get_redis", lambda: fake)

    result = signals_shared._tuning_staleness("stockai:signal_thresholds:SWING")

    assert result == {"last_tuned_at": None, "reverted": False}


def test_tuned_and_value_still_present_reports_not_reverted(monkeypatch):
    fake = _FakeRedis(seed={
        "stockai:signal_thresholds:SWING": "0.68",
        "stockai:signal_thresholds:SWING:last_tuned_at": "2026-08-01",
    })
    monkeypatch.setattr(signals_shared, "_get_redis", lambda: fake)

    result = signals_shared._tuning_staleness("stockai:signal_thresholds:SWING")

    assert result == {"last_tuned_at": "2026-08-01", "reverted": False}


def test_marker_present_but_value_expired_reports_reverted(monkeypatch):
    """The exact silent-reversion state this mechanism exists to detect: was successfully
    tuned at some point (marker exists, no TTL) but the value key's own TTL has since expired
    (value absent) — the read side would now silently be using the hardcoded default."""
    fake = _FakeRedis(seed={
        "stockai:signal_thresholds:SWING:last_tuned_at": "2026-07-01",
        # NOTE: "stockai:signal_thresholds:SWING" itself deliberately absent — expired.
    })
    monkeypatch.setattr(signals_shared, "_get_redis", lambda: fake)

    result = signals_shared._tuning_staleness("stockai:signal_thresholds:SWING")

    assert result == {"last_tuned_at": "2026-07-01", "reverted": True}


def test_tuning_staleness_fails_open_on_a_redis_exception():
    class _BrokenRedis:
        def get(self, key):
            raise ConnectionError("redis unreachable")

        def exists(self, key):
            raise ConnectionError("redis unreachable")

    import unittest.mock as _mock
    with _mock.patch.object(signals_shared, "_get_redis", lambda: _BrokenRedis()):
        result = signals_shared._tuning_staleness("stockai:signal_thresholds:SWING")

    # Fail OPEN to the non-alarming state — a Redis hiccup must never itself look like a
    # detected silent reversion.
    assert result == {"last_tuned_at": None, "reverted": False}


def test_different_keys_have_independent_markers(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(signals_shared, "_get_redis", lambda: fake)

    signals_shared._mark_tuned("stockai:signal_thresholds:SWING")

    assert signals_shared._tuning_staleness("stockai:signal_thresholds:SWING")["last_tuned_at"] is not None
    assert signals_shared._tuning_staleness("stockai:signal_thresholds:GROWTH")["last_tuned_at"] is None
