"""Tests for BUG-POLYGONBUDGET (2026-08-07) — ingestion.py's Polygon-first adapter selection
sent a Polygon request for essentially every eligible US-incremental symbol, even though
Polygon's own free tier caps at 5 requests/minute. Confirmed live: 97% of Polygon calls in a
24h window were rate-limited, each one a wasted round-trip before the yfinance fallback that
was going to be needed anyway. _polygon_budget_available() tracks a real per-minute counter in
Redis so ingest_symbol() can skip straight to yfinance once the cycle's realistic Polygon
budget is already spent.

ingestion.py imports directly in this test environment (conftest.py stubs redis/db/yfinance
as MagicMock — this file only needs the fake Redis behavior, matching
test_auto_research_admin_flag.py's established _FakeRedis convention).
"""
from src.services import ingestion


class _FakeRedis:
    """Minimal in-memory Redis stand-in — just enough for .incr/.expire."""

    def __init__(self):
        self._store: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    def incr(self, key):
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    def expire(self, key, ttl):
        self.expire_calls.append((key, ttl))


def test_first_5_calls_this_minute_are_within_budget(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ingestion, "get_redis", lambda: fake)

    results = [ingestion._polygon_budget_available() for _ in range(5)]

    assert results == [True, True, True, True, True]


def test_6th_call_this_minute_exceeds_budget(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ingestion, "get_redis", lambda: fake)

    for _ in range(5):
        ingestion._polygon_budget_available()
    sixth = ingestion._polygon_budget_available()

    assert sixth is False


def test_every_call_after_the_budget_is_spent_stays_false(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ingestion, "get_redis", lambda: fake)

    for _ in range(5):
        ingestion._polygon_budget_available()
    later_results = [ingestion._polygon_budget_available() for _ in range(20)]

    assert all(r is False for r in later_results)


def test_ttl_is_set_only_on_the_first_increment_this_minute(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ingestion, "get_redis", lambda: fake)

    for _ in range(3):
        ingestion._polygon_budget_available()

    assert len(fake.expire_calls) == 1
    _key, ttl = fake.expire_calls[0]
    assert ttl > 60  # a little past 60s so a slow cycle can't undercount


def test_fails_open_on_a_redis_exception():
    class _BrokenRedis:
        def incr(self, key):
            raise ConnectionError("redis unreachable")

    import unittest.mock as _mock
    with _mock.patch.object(ingestion, "get_redis", lambda: _BrokenRedis()):
        result = ingestion._polygon_budget_available()

    # Fail OPEN — a Redis hiccup must never silently disable Polygon entirely; worst case is
    # one wasted Polygon call, same risk profile as before this fix existed.
    assert result is True


def test_polygon_budget_key_is_scoped_per_minute(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ingestion, "get_redis", lambda: fake)

    ingestion._polygon_budget_available()

    assert len(fake._store) == 1
    key = next(iter(fake._store))
    assert key.startswith(ingestion._POLYGON_BUDGET_KEY_PREFIX)
    # 12 digits: YYYYMMDDHHMM — minute granularity, not second or hour.
    suffix = key[len(ingestion._POLYGON_BUDGET_KEY_PREFIX):]
    assert len(suffix) == 12
    assert suffix.isdigit()


def test_ingest_symbol_adapter_selection_wires_the_budget_check():
    import inspect

    source = inspect.getsource(ingestion.ingest_symbol)
    # The budget check must run in the US-incremental branch specifically — after the
    # HK-always-yfinance and force/no-existing-bars-always-yfinance guards (both of which
    # correctly never touch Polygon at all), and before the real Polygon-first fallback list.
    assert "_polygon_budget_available()" in source
    budget_idx = source.index("_polygon_budget_available()")
    get_adapters_idx = source.index("get_adapters(market, timeframe)")
    assert budget_idx < get_adapters_idx, (
        "the budget check must be evaluated before falling through to the real "
        "Polygon-first get_adapters() call — otherwise it never actually gates anything"
    )
