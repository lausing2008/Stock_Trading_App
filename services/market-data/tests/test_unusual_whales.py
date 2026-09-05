"""Tests for MPE-06/MPE-07's unusual_whales.py client.

httpx AND tenacity are both stubbed as MagicMock by conftest.py (matching every other
outbound-HTTP-client test file in this repo) — with tenacity stubbed, the module's own
`@retry(...)`-decorated `_get()` becomes a bare MagicMock itself (verified directly: decorating
a real function with a MagicMock-stubbed `tenacity.retry` returns a MagicMock, not the original
function), so the parsing/caching layer (get_gex_levels/get_short_interest/_to_float) is tested
by mocking `_get()` directly at the call boundary — matching test_market_pulse.py's own
established `patch.object(news, "_get_redis", ...)` convention for this exact stubbing shape.

_get()'s own retry/rate-limit/auth-error classification logic is real, meaningful code that the
mocked-_get() tests above never exercise — TestGetFunctionRealHttpBehavior below pops the
httpx/tenacity/common.ai_keys stubs and imports a FRESH copy of the module with the real
packages, then mocks httpx.Client itself (not _get) to drive that logic for real, matching
test_risk_snapshots.py's/test_correlation_preentry.py's own established "pop specific stubs,
build against the real thing, restore immediately" technique for this exact constraint.
"""
import sys
from dataclasses import asdict
from unittest.mock import patch

from src.services import unusual_whales as uw


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


# ── is_available() ───────────────────────────────────────────────────────────────────────

def test_is_available_false_when_key_missing():
    with patch.object(uw, "is_unusual_whales_enabled", return_value=True), \
         patch.object(uw, "get_unusual_whales_key", return_value=""):
        assert uw.is_available() is False


def test_is_available_false_when_disabled_even_with_a_real_key():
    with patch.object(uw, "is_unusual_whales_enabled", return_value=False), \
         patch.object(uw, "get_unusual_whales_key", return_value="real-token"):
        assert uw.is_available() is False


def test_is_available_true_only_when_both_conditions_hold():
    with patch.object(uw, "is_unusual_whales_enabled", return_value=True), \
         patch.object(uw, "get_unusual_whales_key", return_value="real-token"):
        assert uw.is_available() is True


# ── _to_float() ───────────────────────────────────────────────────────────────────────────

def test_to_float_handles_real_numbers():
    assert uw._to_float(42.5) == 42.5
    assert uw._to_float("42.5") == 42.5
    assert uw._to_float(0) == 0.0


def test_to_float_degrades_none_and_empty_string_to_none():
    assert uw._to_float(None) is None
    assert uw._to_float("") is None


def test_to_float_degrades_non_numeric_to_none_not_a_crash():
    assert uw._to_float("not-a-number") is None
    assert uw._to_float([1, 2, 3]) is None


def test_to_float_degrades_real_nan_to_none():
    """UW-returned NaN must never leak into a JSON round-trip (the exact bug class already
    fixed once for updown_vol_ratio elsewhere in this app — json.dumps(float('nan')) emits a
    non-standard, JSON.parse-rejecting token)."""
    assert uw._to_float(float("nan")) is None


# ── get_gex_levels() — parsing/caching, with _get() mocked directly ────────────────────────

def test_gex_levels_returns_none_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_gex_levels("AAPL")
    assert result is None
    mock_get.assert_not_called()


def test_gex_levels_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_row = uw.GexLevels(call_wall=250.0, put_wall=200.0, gamma_flip=225.0,
                               gamma_magnet=230.0, as_of_date="2026-08-25")
    import json
    fake_redis.store["stockai:uw:gex:AAPL"] = json.dumps(asdict(cached_row))
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_gex_levels("AAPL")
    assert result == cached_row
    mock_get.assert_not_called()


def test_gex_levels_parses_a_real_dict_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value={
             "call_wall": "250.0", "put_wall": "200.0", "gamma_flip": "225.5",
             "gamma_magnet": "230.0", "date": "2026-08-25",
         }):
        result = uw.get_gex_levels("AAPL")
    assert result.call_wall == 250.0
    assert result.put_wall == 200.0
    assert result.gamma_flip == 225.5
    assert result.gamma_magnet == 230.0
    assert result.as_of_date == "2026-08-25"


def test_gex_levels_parses_a_real_list_response_using_the_first_row():
    """UW's real response for this endpoint is a list of per-expiry rows (confirmed live) —
    the first row must be the one used."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"call_wall": "250.0", "put_wall": "200.0", "gamma_flip": "225.0",
              "gamma_magnet": "230.0", "date": "2026-08-25"},
             {"call_wall": "999.0", "put_wall": "999.0", "gamma_flip": "999.0",
              "gamma_magnet": "999.0", "date": "2026-09-25"},
         ]):
        result = uw.get_gex_levels("AAPL")
    assert result.call_wall == 250.0
    assert result.as_of_date == "2026-08-25"


def test_gex_levels_returns_none_for_a_symbol_with_no_options():
    """An empty list (a real, common shape for a symbol with no listed options) must not
    crash — and must correctly degrade to None, not a fabricated GexLevels with every field
    None (a caller checking `if result:` needs a real None, not a truthy-but-empty object —
    GexLevels has no __bool__ override, so an all-None instance would still be truthy)."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[]):
        result = uw.get_gex_levels("XYZ")
    assert result is None


def test_gex_levels_returns_none_on_a_fetch_exception():
    """A single symbol's failure must never raise — fail-open, matching every other optional
    cross-service/external-data enrichment in this codebase."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        result = uw.get_gex_levels("AAPL")
    assert result is None


def test_gex_levels_writes_a_negative_cache_entry_too():
    """Caching the None result (not just real results) avoids re-hitting the metered API on
    every single call for a symbol confirmed to have no real GEX data."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        uw.get_gex_levels("XYZ")
    assert "stockai:uw:gex:XYZ" in fake_redis.store


# ── get_short_interest() — parsing/caching, with _get() mocked directly ────────────────────

def test_short_interest_returns_none_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_short_interest("GME")
    assert result is None
    mock_get.assert_not_called()


def test_short_interest_parses_a_real_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value={
             "short_interest": "12000000", "short_shares_available": "500000",
             "days_to_cover": "3.2", "fee_rate": "1.15", "rebate_rate": "-0.5",
             "si_float": "0.22", "total_float": "55000000", "market_date": "2026-08-15",
         }):
        result = uw.get_short_interest("GME")
    assert result.short_interest == 12_000_000.0
    assert result.days_to_cover == 3.2
    assert result.fee_rate == 1.15
    assert result.si_float == 0.22
    assert result.market_date == "2026-08-15"


def test_short_interest_returns_none_for_a_missing_symbol():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        result = uw.get_short_interest("ZZZZ")
    assert result is None


def test_short_interest_uses_the_6h_ttl_not_the_gex_15min_ttl():
    """Short-interest settles ~2x/month with a real reporting lag — caching it on the same
    15-min TTL as GEX would be pure waste, re-fetching data that hasn't actually changed."""
    class _SpyRedis(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.setex_calls = []
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl))
            super().setex(key, ttl, value)
    spy = _SpyRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=spy), \
         patch.object(uw, "_get", return_value={"short_interest": "1000"}):
        uw.get_short_interest("GME")
    assert len(spy.setex_calls) == 1
    assert spy.setex_calls[0][1] == uw._SHORT_INTEREST_TTL
    assert spy.setex_calls[0][1] != uw._GEX_TTL


# ── get_iv_rank() — parsing/caching, with _get() mocked directly (AUD-DECIDE4-EXPECTEDMOVE) ─

def test_iv_rank_returns_none_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_iv_rank("AAPL")
    assert result is None
    mock_get.assert_not_called()


def test_iv_rank_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_row = uw.IVRankData(volatility=0.35, iv_rank_1y=62.0, close=190.0, as_of_date="2026-09-02")
    import json
    fake_redis.store["stockai:uw:iv_rank:AAPL"] = json.dumps(asdict(cached_row))
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_iv_rank("AAPL")
    assert result == cached_row
    mock_get.assert_not_called()


def test_iv_rank_parses_a_real_response():
    """_get() already unwraps UW's real {"data": [...]} envelope once — get_iv_rank() must
    treat what it receives as already the row list, not re-unwrap a second "data" key."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"volatility": "0.35", "iv_rank_1y": "62.0", "close": "190.0", "date": "2026-09-02"},
         ]):
        result = uw.get_iv_rank("AAPL")
    assert result.volatility == 0.35
    assert result.iv_rank_1y == 62.0
    assert result.close == 190.0
    assert result.as_of_date == "2026-09-02"


def test_iv_rank_uses_the_first_row_of_multiple():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"volatility": "0.35", "iv_rank_1y": "62.0", "close": "190.0", "date": "2026-09-02"},
             {"volatility": "0.99", "iv_rank_1y": "99.0", "close": "999.0", "date": "2026-08-01"},
         ]):
        result = uw.get_iv_rank("AAPL")
    assert result.volatility == 0.35
    assert result.as_of_date == "2026-09-02"


def test_iv_rank_returns_none_for_an_empty_list_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[]):
        result = uw.get_iv_rank("XYZ")
    assert result is None


def test_iv_rank_returns_none_for_a_non_list_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        result = uw.get_iv_rank("XYZ")
    assert result is None


def test_iv_rank_returns_none_on_a_fetch_exception():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        result = uw.get_iv_rank("AAPL")
    assert result is None


def test_iv_rank_writes_a_negative_cache_entry_too():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[]):
        uw.get_iv_rank("XYZ")
    assert "stockai:uw:iv_rank:XYZ" in fake_redis.store


def test_iv_rank_uses_its_own_ttl_constant():
    class _SpyRedis(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.setex_calls = []
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl))
            super().setex(key, ttl, value)
    spy = _SpyRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=spy), \
         patch.object(uw, "_get", return_value=[{"volatility": "0.35"}]):
        uw.get_iv_rank("AAPL")
    assert len(spy.setex_calls) == 1
    assert spy.setex_calls[0][1] == uw._IV_RANK_TTL


# ── get_max_pain() — parsing/caching, with _get() mocked directly (AUD-MAXPAIN) ─────────────

def test_max_pain_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_max_pain("AAPL")
    assert result == []
    mock_get.assert_not_called()


def test_max_pain_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_rows = [uw.MaxPainRow(expiry="2026-10-02", max_pain=325.0)]
    import json
    from dataclasses import asdict
    fake_redis.store["stockai:uw:max_pain:AAPL"] = json.dumps([asdict(r) for r in cached_rows])
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_max_pain("AAPL")
    assert result == cached_rows
    mock_get.assert_not_called()


def test_max_pain_parses_a_real_response_with_multiple_expiries():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"expiry": "2026-10-02", "max_pain": "325.0"},
             {"expiry": "2026-10-09", "max_pain": "330.0"},
         ]):
        result = uw.get_max_pain("AAPL")
    assert len(result) == 2
    assert result[0].expiry == "2026-10-02"
    assert result[0].max_pain == 325.0
    assert result[1].expiry == "2026-10-09"
    assert result[1].max_pain == 330.0


def test_max_pain_returns_empty_list_for_a_non_list_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        result = uw.get_max_pain("XYZ")
    assert result == []


def test_max_pain_returns_empty_list_on_a_fetch_exception():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        result = uw.get_max_pain("AAPL")
    assert result == []


def test_max_pain_uses_its_own_ttl_constant():
    class _SpyRedis(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.setex_calls = []
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl))
            super().setex(key, ttl, value)
    spy = _SpyRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=spy), \
         patch.object(uw, "_get", return_value=[{"expiry": "2026-10-02", "max_pain": "325.0"}]):
        uw.get_max_pain("AAPL")
    assert len(spy.setex_calls) == 1
    assert spy.setex_calls[0][1] == uw._MAX_PAIN_TTL


# ── get_oi_per_strike() — parsing/caching, with _get() mocked directly (AUD-MAXPAIN) ────────

def test_oi_per_strike_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_oi_per_strike("AAPL")
    assert result == []
    mock_get.assert_not_called()


def test_oi_per_strike_parses_a_real_response_with_multiple_strikes():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"strike": "320.0", "call_oi": "1200", "put_oi": "800"},
             {"strike": "330.0", "call_oi": "2400", "put_oi": "500"},
         ]):
        result = uw.get_oi_per_strike("AAPL")
    assert len(result) == 2
    assert result[0].strike == 320.0
    assert result[0].call_oi == 1200.0
    assert result[0].put_oi == 800.0
    assert result[1].strike == 330.0


def test_oi_per_strike_returns_empty_list_for_a_non_list_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        result = uw.get_oi_per_strike("XYZ")
    assert result == []


def test_oi_per_strike_returns_empty_list_on_a_fetch_exception():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        result = uw.get_oi_per_strike("AAPL")
    assert result == []


def test_oi_per_strike_uses_its_own_ttl_constant():
    class _SpyRedis(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.setex_calls = []
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl))
            super().setex(key, ttl, value)
    spy = _SpyRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=spy), \
         patch.object(uw, "_get", return_value=[{"strike": "320.0"}]):
        uw.get_oi_per_strike("AAPL")
    assert len(spy.setex_calls) == 1
    assert spy.setex_calls[0][1] == uw._OI_PER_STRIKE_TTL


def test_oi_per_strike_cache_key_is_symbol_scoped():
    """Unlike get_greeks() (per-expiry), oi-per-strike is a whole-chain rollup — the cache key
    is correctly symbol-only, not symbol+expiry."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[{"strike": "320.0"}]):
        uw.get_oi_per_strike("AAPL")
    assert "stockai:uw:oi_per_strike:AAPL" in fake_redis.store


# ── get_nope() — parsing/caching, with _get() mocked directly (AUD-NOPE) ────────────────────

def test_nope_returns_none_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_nope("AAPL")
    assert result is None
    mock_get.assert_not_called()


def test_nope_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_row = uw.NopeReading(
        nope=0.42, nope_fill=0.38, call_delta=1500.0, put_delta=-900.0,
        call_vol=5000.0, put_vol=3200.0, stock_vol=1200000.0, timestamp="2026-09-04T14:32:00Z",
    )
    import json
    from dataclasses import asdict
    fake_redis.store["stockai:uw:nope:AAPL"] = json.dumps(asdict(cached_row))
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_nope("AAPL")
    assert result == cached_row
    mock_get.assert_not_called()


def test_nope_parses_a_real_dict_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value={
             "nope": "0.42", "nope_fill": "0.38", "call_delta": "1500.0", "put_delta": "-900.0",
             "call_vol": 5000, "put_vol": 3200, "stock_vol": 1200000,
             "timestamp": "2026-09-04T14:32:00Z",
         }):
        result = uw.get_nope("AAPL")
    assert result.nope == 0.42
    assert result.nope_fill == 0.38
    assert result.call_delta == 1500.0
    assert result.put_delta == -900.0
    assert result.call_vol == 5000.0
    assert result.put_vol == 3200.0
    assert result.stock_vol == 1200000.0
    assert result.timestamp == "2026-09-04T14:32:00Z"


def test_nope_parses_a_real_list_response_using_the_first_row():
    """Defensive handling matching get_gex_levels()'s own precedent for a shape UW might return
    as a list under some conditions, even though the documented contract is a single object."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"nope": "0.42", "timestamp": "2026-09-04T14:32:00Z"},
             {"nope": "0.99", "timestamp": "2026-09-04T14:31:00Z"},
         ]):
        result = uw.get_nope("AAPL")
    assert result.nope == 0.42


def test_nope_returns_none_for_a_missing_symbol():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        result = uw.get_nope("ZZZZ")
    assert result is None


def test_nope_returns_none_on_a_fetch_exception():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        result = uw.get_nope("AAPL")
    assert result is None


def test_nope_uses_its_own_short_ttl_not_the_15_minute_default():
    """The whole point of AUD-NOPE's design: a 60s TTL, not the 15-min TTL every other UW
    field on this route uses — NOPE is a per-MINUTE reading, and a 15-min cache would serve a
    stale intraday snapshot for 15x longer than the data itself remains current."""
    class _SpyRedis(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.setex_calls = []
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl))
            super().setex(key, ttl, value)
    spy = _SpyRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=spy), \
         patch.object(uw, "_get", return_value={"nope": "0.42"}):
        uw.get_nope("AAPL")
    assert len(spy.setex_calls) == 1
    assert spy.setex_calls[0][1] == uw._NOPE_TTL
    assert spy.setex_calls[0][1] == 60
    assert spy.setex_calls[0][1] != uw._GEX_TTL


def test_nope_writes_a_negative_cache_entry_too():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        uw.get_nope("ZZZZ")
    assert "stockai:uw:nope:ZZZZ" in fake_redis.store


# ── get_historical_earnings_moves() — parsing/caching (AUD-EARNINGSMOVE) ────────────────────

def test_earnings_moves_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_historical_earnings_moves("AAPL")
    assert result == []
    mock_get.assert_not_called()


def test_earnings_moves_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_rows = [uw.HistoricalEarningsMoveRow(
        report_date="2026-07-31", report_time="postmarket", expected_move=8.5,
        expected_move_perc=4.2, post_earnings_move_1d=3.1, post_earnings_move_1w=5.0,
        source="company",
    )]
    import json
    from dataclasses import asdict
    fake_redis.store["stockai:uw:earnings_moves:AAPL"] = json.dumps([asdict(r) for r in cached_rows])
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_historical_earnings_moves("AAPL")
    assert result == cached_rows
    mock_get.assert_not_called()


def test_earnings_moves_parses_a_real_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {
                 "report_date": "2026-07-31", "report_time": "postmarket",
                 "expected_move": "8.5", "expected_move_perc": "4.2",
                 "post_earnings_move_1d": "3.1", "post_earnings_move_1w": "5.0",
                 "source": "company",
             },
         ]):
        result = uw.get_historical_earnings_moves("AAPL")
    assert len(result) == 1
    row = result[0]
    assert row.report_date == "2026-07-31"
    assert row.report_time == "postmarket"
    assert row.expected_move == 8.5
    assert row.expected_move_perc == 4.2
    assert row.post_earnings_move_1d == 3.1
    assert row.post_earnings_move_1w == 5.0
    assert row.source == "company"


def test_earnings_moves_sorts_most_recent_first():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"report_date": "2026-01-30", "expected_move_perc": "3.0"},
             {"report_date": "2026-07-31", "expected_move_perc": "4.2"},
             {"report_date": "2026-04-30", "expected_move_perc": "3.5"},
         ]):
        result = uw.get_historical_earnings_moves("AAPL")
    assert [r.report_date for r in result] == ["2026-07-31", "2026-04-30", "2026-01-30"]


def test_earnings_moves_caps_at_the_given_limit():
    fake_redis = _FakeRedis()
    rows = [{"report_date": f"2026-0{i}-01", "expected_move_perc": "1.0"} for i in range(1, 9)]
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=rows):
        result = uw.get_historical_earnings_moves("AAPL", limit=3)
    assert len(result) == 3


def test_earnings_moves_default_limit_is_8():
    fake_redis = _FakeRedis()
    rows = [{"report_date": f"2020-{i:02d}-01", "expected_move_perc": "1.0"} for i in range(1, 13)]
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=rows):
        result = uw.get_historical_earnings_moves("AAPL")
    assert len(result) == 8


def test_earnings_moves_skips_rows_with_no_report_date():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"report_date": "2026-07-31", "expected_move_perc": "4.2"},
             {"expected_move_perc": "9.9"},  # no report_date -- must be dropped, not crash
         ]):
        result = uw.get_historical_earnings_moves("AAPL")
    assert len(result) == 1
    assert result[0].report_date == "2026-07-31"


def test_earnings_moves_returns_empty_list_for_a_non_list_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        result = uw.get_historical_earnings_moves("XYZ")
    assert result == []


def test_earnings_moves_returns_empty_list_on_a_fetch_exception():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        result = uw.get_historical_earnings_moves("AAPL")
    assert result == []


def test_earnings_moves_uses_its_own_6h_ttl():
    class _SpyRedis(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.setex_calls = []
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl))
            super().setex(key, ttl, value)
    spy = _SpyRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=spy), \
         patch.object(uw, "_get", return_value=[{"report_date": "2026-07-31"}]):
        uw.get_historical_earnings_moves("AAPL")
    assert len(spy.setex_calls) == 1
    assert spy.setex_calls[0][1] == uw._EARNINGS_MOVE_TTL
    assert spy.setex_calls[0][1] != uw._NOPE_TTL


# ── earnings_quarter_from_report_date() (AUD-TRANSCRIPT) ────────────────────────────────────

def test_quarter_from_date_q1():
    assert uw.earnings_quarter_from_report_date("2026-02-15") == "2026Q1"


def test_quarter_from_date_q2():
    assert uw.earnings_quarter_from_report_date("2026-05-01") == "2026Q2"


def test_quarter_from_date_q3():
    assert uw.earnings_quarter_from_report_date("2026-07-31") == "2026Q3"


def test_quarter_from_date_q4():
    assert uw.earnings_quarter_from_report_date("2026-12-01") == "2026Q4"


def test_quarter_from_date_accepts_a_real_date_object_not_just_a_string():
    from datetime import date
    assert uw.earnings_quarter_from_report_date(date(2026, 7, 31)) == "2026Q3"


def test_quarter_from_date_boundary_months():
    """The exact month-3/month-4 and similar quarter boundaries — a real off-by-one here would
    silently request the wrong quarter's transcript from UW."""
    assert uw.earnings_quarter_from_report_date("2026-03-31") == "2026Q1"
    assert uw.earnings_quarter_from_report_date("2026-04-01") == "2026Q2"
    assert uw.earnings_quarter_from_report_date("2026-06-30") == "2026Q2"
    assert uw.earnings_quarter_from_report_date("2026-09-30") == "2026Q3"
    assert uw.earnings_quarter_from_report_date("2026-10-01") == "2026Q4"


# ── get_earnings_transcript() — parsing/caching, with _get() mocked directly (AUD-TRANSCRIPT) ─

def test_transcript_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_earnings_transcript("AAPL", "2026Q3")
    assert result == []
    mock_get.assert_not_called()


def test_transcript_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_rows = [uw.TranscriptStatement(speaker="Tim Cook", title="CEO", content="Revenue grew...", sentiment=0.6)]
    import json
    from dataclasses import asdict
    fake_redis.store["stockai:uw:transcript:AAPL:2026Q3"] = json.dumps([asdict(r) for r in cached_rows])
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_earnings_transcript("AAPL", "2026Q3")
    assert result == cached_rows
    mock_get.assert_not_called()


def test_transcript_parses_a_real_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value={
             "quarter": "2026Q3", "ticker": "AAPL",
             "statements": [
                 {"speaker": "Tim Cook", "title": "CEO", "content": "Revenue grew 8%.", "sentiment": "0.6"},
                 {"speaker": "Analyst", "title": None, "content": "What drove the beat?", "sentiment": None},
             ],
         }):
        result = uw.get_earnings_transcript("AAPL", "2026Q3")
    assert len(result) == 2
    assert result[0].speaker == "Tim Cook"
    assert result[0].title == "CEO"
    assert result[0].content == "Revenue grew 8%."
    assert result[0].sentiment == 0.6
    assert result[1].speaker == "Analyst"
    assert result[1].sentiment is None


def test_transcript_returns_empty_list_when_statements_key_is_missing():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value={"quarter": "2026Q3", "ticker": "AAPL"}):
        result = uw.get_earnings_transcript("AAPL", "2026Q3")
    assert result == []


def test_transcript_returns_empty_list_for_a_non_dict_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        result = uw.get_earnings_transcript("XYZ", "2026Q3")
    assert result == []


def test_transcript_skips_non_dict_rows_in_the_statements_list_without_crashing():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value={"statements": [
             {"speaker": "Tim Cook", "content": "Real statement."},
             "not a dict",
             None,
         ]}):
        result = uw.get_earnings_transcript("AAPL", "2026Q3")
    assert len(result) == 1
    assert result[0].speaker == "Tim Cook"


def test_transcript_returns_empty_list_on_a_fetch_exception():
    """Includes the deliberately-indistinguishable case of a 403 from an account not on UW's
    own required Advanced+ tier for this endpoint — must fail open exactly like any other
    failure, never raise or crash the caller."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", side_effect=uw.UnusualWhalesAuthError("403")):
        result = uw.get_earnings_transcript("AAPL", "2026Q3")
    assert result == []


def test_transcript_cache_key_is_scoped_per_quarter_not_just_symbol():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value={"statements": [{"speaker": "x"}]}):
        uw.get_earnings_transcript("AAPL", "2026Q3")
    assert "stockai:uw:transcript:AAPL:2026Q3" in fake_redis.store
    assert "stockai:uw:transcript:AAPL:2026Q2" not in fake_redis.store


def test_transcript_uses_its_own_24h_ttl():
    class _SpyRedis(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.setex_calls = []
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl))
            super().setex(key, ttl, value)
    spy = _SpyRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=spy), \
         patch.object(uw, "_get", return_value={"statements": [{"speaker": "x"}]}):
        uw.get_earnings_transcript("AAPL", "2026Q3")
    assert len(spy.setex_calls) == 1
    assert spy.setex_calls[0][1] == uw._TRANSCRIPT_TTL
    assert spy.setex_calls[0][1] == 86400


# ── get_sector_seasonality() — parsing/caching, with _get() mocked directly (AUD-SEASONALITY) ─

def test_seasonality_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_sector_seasonality()
    assert result == []
    mock_get.assert_not_called()


def test_seasonality_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_rows = [uw.SeasonalityRow(
        ticker="SPY", month=5, avg_change=0.0038, median_change=0.0115, min_change=-0.0838,
        max_change=0.0666, positive_closes=13, positive_months_perc=0.8125, years=16,
    )]
    import json
    from dataclasses import asdict
    fake_redis.store["stockai:uw:seasonality:market"] = json.dumps([asdict(r) for r in cached_rows])
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_sector_seasonality()
    assert result == cached_rows
    mock_get.assert_not_called()


def test_seasonality_parses_a_real_response():
    """Uses UW's own real published example row from its OpenAPI spec."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {
                 "avg_change": "0.0038", "max_change": "0.0666", "median_change": "0.0115",
                 "min_change": "-0.0838", "month": 5, "positive_closes": 13,
                 "positive_months_perc": "0.8125", "ticker": "SPY", "years": 16,
             },
         ]):
        result = uw.get_sector_seasonality()
    assert len(result) == 1
    row = result[0]
    assert row.ticker == "SPY"
    assert row.month == 5
    assert row.avg_change == 0.0038
    assert row.median_change == 0.0115
    assert row.min_change == -0.0838
    assert row.max_change == 0.0666
    assert row.positive_closes == 13
    assert row.positive_months_perc == 0.8125
    assert row.years == 16


def test_seasonality_parses_multiple_tickers_and_months():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"ticker": "SPY", "month": 6, "avg_change": "-0.0068"},
             {"ticker": "XLRE", "month": 3, "avg_change": "0.0092"},
             {"ticker": "QQQ", "month": 2, "avg_change": "0.0044"},
         ]):
        result = uw.get_sector_seasonality()
    assert len(result) == 3
    assert {(r.ticker, r.month) for r in result} == {("SPY", 6), ("XLRE", 3), ("QQQ", 2)}


def test_seasonality_returns_empty_list_for_a_non_list_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        result = uw.get_sector_seasonality()
    assert result == []


def test_seasonality_returns_empty_list_on_a_fetch_exception():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        result = uw.get_sector_seasonality()
    assert result == []


def test_seasonality_skips_non_dict_rows_without_crashing():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"ticker": "SPY", "month": 5, "avg_change": "0.0038"},
             "not a dict",
             None,
         ]):
        result = uw.get_sector_seasonality()
    assert len(result) == 1
    assert result[0].ticker == "SPY"


def test_seasonality_takes_no_arguments():
    """A real, deliberate API shape difference from get_max_pain()/get_greeks() — UW's own
    /api/seasonality/market endpoint has zero query/path parameters, always returning its full
    fixed 13-ticker matrix in one call."""
    import inspect
    sig = inspect.signature(uw.get_sector_seasonality)
    assert len(sig.parameters) == 0


def test_seasonality_uses_its_own_24h_ttl():
    class _SpyRedis(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.setex_calls = []
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl))
            super().setex(key, ttl, value)
    spy = _SpyRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=spy), \
         patch.object(uw, "_get", return_value=[{"ticker": "SPY", "month": 5}]):
        uw.get_sector_seasonality()
    assert len(spy.setex_calls) == 1
    assert spy.setex_calls[0][1] == uw._SEASONALITY_TTL
    assert spy.setex_calls[0][1] == 86400


# ── get_greeks() — parsing/caching, with _get() mocked directly (AUD-GREEKS) ────────────────

def test_greeks_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_greeks("AAPL", "2026-10-01")
    assert result == []
    mock_get.assert_not_called()


def test_greeks_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_row = uw.StrikeGreeks(
        strike=190.0, call_delta=0.55, call_gamma=0.02, call_theta=-0.05, call_vega=0.12,
        call_vanna=0.01, call_charm=-0.001, put_delta=-0.45, put_gamma=0.02, put_theta=-0.04,
        put_vega=0.11, put_vanna=-0.01, put_charm=0.001,
    )
    import json
    from dataclasses import asdict
    fake_redis.store["stockai:uw:greeks:AAPL:2026-10-01"] = json.dumps([asdict(cached_row)])
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_greeks("AAPL", "2026-10-01")
    assert result == [cached_row]
    mock_get.assert_not_called()


def test_greeks_parses_a_real_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {
                 "strike": "190.0", "call_delta": "0.55", "call_gamma": "0.02",
                 "call_theta": "-0.05", "call_vega": "0.12", "call_vanna": "0.01",
                 "call_charm": "-0.001", "put_delta": "-0.45", "put_gamma": "0.02",
                 "put_theta": "-0.04", "put_vega": "0.11", "put_vanna": "-0.01",
                 "put_charm": "0.001",
             },
         ]):
        result = uw.get_greeks("AAPL", "2026-10-01")
    assert len(result) == 1
    row = result[0]
    assert row.strike == 190.0
    assert row.call_delta == 0.55
    assert row.put_delta == -0.45
    assert row.call_vanna == 0.01
    assert row.put_charm == 0.001


def test_greeks_passes_the_expiry_query_param():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[]) as mock_get:
        uw.get_greeks("AAPL", "2026-10-01")
    mock_get.assert_called_once_with("/api/stock/AAPL/greeks", params={"expiry": "2026-10-01"})


def test_greeks_returns_multiple_strikes_as_separate_rows():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"strike": "185.0", "call_delta": "0.7"},
             {"strike": "190.0", "call_delta": "0.55"},
             {"strike": "195.0", "call_delta": "0.4"},
         ]):
        result = uw.get_greeks("AAPL", "2026-10-01")
    assert [r.strike for r in result] == [185.0, 190.0, 195.0]


def test_greeks_returns_empty_list_for_a_non_list_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=None):
        result = uw.get_greeks("XYZ", "2026-10-01")
    assert result == []


def test_greeks_returns_empty_list_on_a_fetch_exception():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        result = uw.get_greeks("AAPL", "2026-10-01")
    assert result == []


def test_greeks_cache_key_is_scoped_per_expiry_not_just_symbol():
    """A different expiry for the same symbol must never read the wrong expiry's cached
    Greeks — the cache key must include expiry, not just the symbol."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[{"strike": "190.0", "call_delta": "0.5"}]):
        uw.get_greeks("AAPL", "2026-10-01")
    assert "stockai:uw:greeks:AAPL:2026-10-01" in fake_redis.store
    assert "stockai:uw:greeks:AAPL:2026-11-01" not in fake_redis.store


def test_greeks_uses_its_own_ttl_constant():
    class _SpyRedis(_FakeRedis):
        def __init__(self):
            super().__init__()
            self.setex_calls = []
        def setex(self, key, ttl, value):
            self.setex_calls.append((key, ttl))
            super().setex(key, ttl, value)
    spy = _SpyRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=spy), \
         patch.object(uw, "_get", return_value=[{"strike": "190.0"}]):
        uw.get_greeks("AAPL", "2026-10-01")
    assert len(spy.setex_calls) == 1
    assert spy.setex_calls[0][1] == uw._GREEKS_TTL


# ── get_flow_alerts() ─────────────────────────────────────────────────────────────────────

def test_flow_alerts_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False):
        assert uw.get_flow_alerts("AAPL") == []


def test_flow_alerts_parses_a_real_response():
    fake_row = {
        "ticker": "MSFT", "option_chain": "MSFT231222C00375000", "type": "call",
        "strike": "375", "expiry": "2023-12-22", "price": "4.05",
        "underlying_price": "372.99", "total_premium": "186705",
        "total_ask_side_prem": "151875", "total_bid_side_prem": "405",
        "total_size": 461, "volume": 2442, "open_interest": 7913,
        "volume_oi_ratio": "0.30860609124226", "has_sweep": True,
        "alert_rule": "RepeatedHits", "created_at": "2023-12-12T16:35:52Z",
    }
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[fake_row]):
        result = uw.get_flow_alerts("MSFT")
    assert len(result) == 1
    a = result[0]
    assert a.ticker == "MSFT"
    assert a.option_chain == "MSFT231222C00375000"
    assert a.option_type == "call"
    assert a.strike == 375.0
    assert a.expiry == "2023-12-22"
    assert a.total_ask_side_prem == 151875.0
    assert a.total_bid_side_prem == 405.0
    assert a.total_size == 461
    assert a.volume == 2442
    assert a.open_interest == 7913
    assert a.has_sweep is True
    assert a.alert_rule == "RepeatedHits"


def test_flow_alerts_returns_empty_list_for_no_alerts():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[]):
        assert uw.get_flow_alerts("XYZ") == []


def test_flow_alerts_returns_empty_list_on_a_non_list_response():
    """A malformed/unexpected response shape must degrade to [], never crash or fabricate
    a partial result."""
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value={"unexpected": "shape"}):
        assert uw.get_flow_alerts("XYZ") == []


def test_flow_alerts_returns_empty_list_on_a_fetch_exception():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        assert uw.get_flow_alerts("AAPL") == []


def test_flow_alerts_one_malformed_row_does_not_drop_the_rest():
    """A single bad row (e.g. a genuinely unparseable total_size) must not take down the
    whole response — every other, well-formed row in the same batch should still come back."""
    good_row = {
        "ticker": "AAPL", "option_chain": "AAPL240101C00200000", "type": "call",
        "strike": "200", "expiry": "2024-01-01", "total_size": 100, "has_sweep": False,
    }
    bad_row = {
        "ticker": "AAPL", "option_chain": "AAPL240101P00190000", "type": "put",
        "total_size": "not-a-number", "has_sweep": False,
    }
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[bad_row, good_row]):
        result = uw.get_flow_alerts("AAPL")
    assert len(result) == 1
    assert result[0].option_chain == "AAPL240101C00200000"


def test_flow_alerts_is_now_cached_short_ttl_aud_uwratelimit():
    """AUD-UWRATELIMIT-FLOWALERTS: reverses the PRIOR "never cached" contract — confirmed live,
    check_options_flow_alerts() calling this once per symbol every 1-minute tick over an
    uncapped symbol set produced 22,031 real UW 429s in 48h. Now cached _FLOW_ALERT_TTL=45s: a
    cache miss must call _get() and then WRITE the result via setex(); a cache hit must return
    without calling _get() at all."""
    class _FakeRedis:
        def __init__(self):
            self.store = {}
        def get(self, key):
            return self.store.get(key)
        def setex(self, key, ttl, value):
            self.store[key] = value

    fake_redis = _FakeRedis()
    good_row = {
        "ticker": "AAPL", "option_chain": "AAPL240101C00200000", "type": "call",
        "strike": "200", "expiry": "2024-01-01", "total_size": 100, "has_sweep": False,
    }
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[good_row]) as mock_get:
        first = uw.get_flow_alerts("AAPL")
        assert len(first) == 1
        assert mock_get.call_count == 1

        # Second call within the TTL window must be a cache hit — no second _get() call.
        second = uw.get_flow_alerts("AAPL")
        assert len(second) == 1
        assert second[0].option_chain == "AAPL240101C00200000"
        assert mock_get.call_count == 1, "second call within TTL must not re-fetch from UW"


def test_flow_alerts_cache_key_includes_filter_params_not_just_symbol():
    """Two callers requesting the SAME symbol with DIFFERENT filter params must not share a
    cached result computed under the other caller's filters — the cache key must encode
    min_premium/min_volume_oi_ratio/is_sweep/max_dte, not just the symbol."""
    class _FakeRedis:
        def __init__(self):
            self.store = {}
        def get(self, key):
            return self.store.get(key)
        def setex(self, key, ttl, value):
            self.store[key] = value

    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[]) as mock_get:
        uw.get_flow_alerts("AAPL", min_premium=50_000)
        uw.get_flow_alerts("AAPL", min_premium=100_000)
        assert mock_get.call_count == 2, "different min_premium must not hit the same cache entry"


def test_flow_alerts_cache_read_failure_fails_open_to_a_real_fetch():
    """A Redis exception on the cache READ must not prevent the real UW call from happening —
    matches every other UW function's own fail-open contract."""
    class _BrokenRedis:
        def get(self, *a, **kw):
            raise ConnectionError("redis unavailable")
        def setex(self, *a, **kw):
            raise ConnectionError("redis unavailable")
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=_BrokenRedis()), \
         patch.object(uw, "_get", return_value=[]):
        result = uw.get_flow_alerts("AAPL")  # must not raise
        assert result == []


# ── get_historical_flow_alerts() — MPE-OPTIONS-FLOW-ALERT backtest ─────────────────────────

def _historical_row(ticker="AAPL", created_at="2026-08-15T12:00:00Z"):
    return {
        "ticker": ticker, "option_chain": f"{ticker}240101C00200000", "type": "call",
        "strike": "200", "expiry": "2024-01-01", "underlying_price": "195.0",
        "total_premium": "300000", "total_ask_side_prem": "300000", "total_bid_side_prem": "0",
        "volume_oi_ratio": "5.0", "has_sweep": True, "created_at": created_at,
    }


def test_historical_flow_alerts_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False):
        assert uw.get_historical_flow_alerts("AAPL", newer_than="2026-08-01", older_than="2026-08-31") == []


def test_historical_flow_alerts_single_page_stops_pagination():
    """Fewer than 200 rows in one page means no more history exists — must NOT make a second
    request just because the loop technically allows up to 5 pages."""
    calls = []
    def _fake_get(path, params=None):
        calls.append(params)
        return [_historical_row()]
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        result = uw.get_historical_flow_alerts("AAPL", newer_than="2026-08-01", older_than="2026-08-31")
    assert len(result) == 1
    assert len(calls) == 1


def test_historical_flow_alerts_paginates_backward_via_older_than():
    """A full 200-row page must trigger a second call, using the OLDEST row's own created_at
    as the new older_than cursor — paging backward in time, never forward or stuck in place."""
    page1 = [_historical_row(created_at=f"2026-08-{20 - i:02d}T12:00:00Z") for i in range(200)]
    page2 = [_historical_row(created_at="2026-08-01T09:00:00Z")]  # < 200 rows -> stop after this
    calls = []
    def _fake_get(path, params=None):
        calls.append(dict(params))
        return page1 if len(calls) == 1 else page2
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        result = uw.get_historical_flow_alerts("AAPL", newer_than="2026-08-01", older_than="2026-08-31")
    assert len(calls) == 2
    assert calls[0]["older_than"] == "2026-08-31"  # first call uses the caller's own older_than
    assert calls[1]["older_than"] == page1[-1]["created_at"]  # second call pages backward
    assert len(result) == 201


def test_historical_flow_alerts_caps_at_max_pages_not_infinite_loop():
    """Even if UW kept returning full 200-row pages forever, this must stop at the real,
    disclosed page cap rather than looping until the caller's window is exhausted."""
    calls = []
    def _fake_get(path, params=None):
        calls.append(params)
        return [_historical_row(created_at=f"2026-0{len(calls)}-01T00:00:00Z") for _ in range(200)]
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_historical_flow_alerts("AAPL", newer_than="2026-01-01", older_than="2026-08-31")
    assert len(calls) == uw._HISTORICAL_FLOW_ALERTS_MAX_PAGES


def test_historical_flow_alerts_is_sweep_none_omits_the_param_entirely():
    """UW's own is_sweep param is a HARD binary filter both ways (confirmed live against
    production) — True returns ONLY sweeps, False returns ONLY non-sweeps. A caller wanting a
    genuine mix (to compare sweep-vs-non-sweep outcomes) must get the key OMITTED, never sent
    as a literal 'false' string, which would silently exclude every real sweep."""
    captured = {}
    def _fake_get(path, params=None):
        captured.update(params)
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_historical_flow_alerts("AAPL", newer_than="2026-08-01", older_than="2026-08-31", is_sweep=None)
    assert "is_sweep" not in captured


def test_historical_flow_alerts_is_sweep_true_still_sends_the_param():
    """The default (is_sweep=True) must keep sending the real filter — only an EXPLICIT
    is_sweep=None omits it."""
    captured = {}
    def _fake_get(path, params=None):
        captured.update(params)
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_historical_flow_alerts("AAPL", newer_than="2026-08-01", older_than="2026-08-31")
    assert captured["is_sweep"] == "true"


def test_historical_flow_alerts_returns_empty_list_on_a_fetch_exception():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        assert uw.get_historical_flow_alerts("AAPL", newer_than="2026-08-01", older_than="2026-08-31") == []


def test_historical_flow_alerts_reuses_the_same_row_parser_as_the_live_endpoint():
    """Both get_flow_alerts() and get_historical_flow_alerts() must translate UW's identical
    response shape through the SAME _parse_flow_alert_rows() helper — never two independently-
    drifting copies of the same parsing logic."""
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[_historical_row(ticker="MSFT")]):
        result = uw.get_historical_flow_alerts("MSFT", newer_than="2026-08-01", older_than="2026-08-31")
    assert len(result) == 1
    assert result[0].ticker == "MSFT"
    assert result[0].underlying_price == 195.0
    assert result[0].total_premium == 300000.0


def test_flow_alerts_passes_real_filter_params_to_get():
    """The whole point of the default thresholds — confirms they actually reach _get()'s own
    params dict, not silently dropped/ignored."""
    captured = {}
    def _fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_flow_alerts("AAPL", min_premium=100_000, min_volume_oi_ratio=2.0, is_sweep=True, max_dte=30)
    assert captured["path"] == "/api/option-trades/flow-alerts"
    assert captured["params"]["ticker_symbol"] == "AAPL"
    assert captured["params"]["min_premium"] == 100_000
    assert captured["params"]["min_volume_oi_ratio"] == 2.0
    assert captured["params"]["is_sweep"] == "true"
    assert captured["params"]["max_dte"] == 30


# ── AUD-OPTIONSFLOW-STALEALERTS: get_flow_alerts() must send a real newer_than ──────────────
# A real user reported receiving an alert whose OWN contract expiry had already passed weeks
# earlier — confirmed live against production that UW's endpoint, with no newer_than sent,
# happily re-serves any row still inside its retention window (found: rows over 1,300 hours/
# 54 days old), and that UW's own newer_than param SILENTLY IGNORES any value with a time
# component (a full ISO datetime, with or without a "Z"/offset) — only a bare unix-epoch
# integer or a bare YYYY-MM-DD date actually filters server-side (confirmed live by testing
# all 4 forms directly: date-only and epoch both correctly narrowed the result; every
# datetime-with-time form returned the identical unfiltered count as omitting the param).

def test_flow_alerts_sends_a_real_newer_than_epoch_value():
    import time
    captured = {}
    def _fake_get(path, params=None):
        captured.update(params)
        return []
    before = int(time.time())
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_flow_alerts("AAPL")
    after = int(time.time())
    assert "newer_than" in captured
    sent = captured["newer_than"]
    assert isinstance(sent, str)
    assert sent.isdigit()  # a bare epoch-seconds integer, never an ISO datetime string
    sent_epoch = int(sent)
    expected_seconds_ago = uw._FLOW_ALERT_MAX_AGE_HOURS * 3600
    # Must be genuinely close to "now minus the max-age window" — a wide, generous tolerance
    # band (not an exact-second match, which would be flaky) rather than asserting no specific
    # value at all.
    assert before - expected_seconds_ago - 5 <= sent_epoch <= after - expected_seconds_ago + 5


def test_flow_alerts_never_sends_an_iso_datetime_string_for_newer_than():
    """The exact class of regression this whole fix closes — reverting to an ISO datetime
    string (even one that LOOKS more precise/correct) would silently un-fix the staleness bug,
    since UW's backend ignores it entirely rather than raising an error."""
    captured = {}
    def _fake_get(path, params=None):
        captured.update(params)
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_flow_alerts("AAPL")
    assert "T" not in captured["newer_than"]
    assert "-" not in captured["newer_than"]
    assert ":" not in captured["newer_than"]


def test_flow_alerts_uppercases_the_symbol():
    captured = {}
    def _fake_get(path, params=None):
        captured["params"] = params
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_flow_alerts("aapl")
    assert captured["params"]["ticker_symbol"] == "AAPL"


# ── get_dark_pool_prints() — T323-DARKPOOL, genuinely new capability ────────────────────────

def test_dark_pool_prints_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_dark_pool_prints("AAPL")
    assert result == []
    mock_get.assert_not_called()


def test_dark_pool_prints_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_row = uw.DarkPoolPrintRow(symbol="AAPL", price=250.0, size=10000, premium=2_500_000.0,
                                       venue="L", executed_at="2026-09-01T14:30:00Z")
    import json
    fake_redis.store["stockai:uw:darkpool:AAPL"] = json.dumps([asdict(cached_row)])
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_dark_pool_prints("AAPL")
    assert result == [cached_row]
    mock_get.assert_not_called()


def test_dark_pool_prints_parses_a_real_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"price": "250.50", "size": 10000, "market_center": "L", "executed_at": "2026-09-01T14:30:00Z"},
         ]):
        result = uw.get_dark_pool_prints("AAPL")
    assert len(result) == 1
    r = result[0]
    assert r.symbol == "AAPL"
    assert r.price == 250.50
    assert r.size == 10000
    assert r.venue == "L"
    assert r.executed_at == "2026-09-01T14:30:00Z"


def test_dark_pool_prints_computes_premium_when_uw_omits_it():
    """UW's own `premium` field is not guaranteed present on every row — price * size must be
    computed as a fallback rather than leaving a real, derivable number as None."""
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"price": "100.0", "size": 5000, "executed_at": "2026-09-01T14:30:00Z"},
         ]):
        result = uw.get_dark_pool_prints("AAPL")
    assert result[0].premium == 500_000.0


def test_dark_pool_prints_uses_uws_own_premium_when_present_not_recomputed():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"price": "100.0", "size": 5000, "premium": "999999", "executed_at": "2026-09-01T14:30:00Z"},
         ]):
        result = uw.get_dark_pool_prints("AAPL")
    assert result[0].premium == 999999.0


def test_dark_pool_prints_returns_empty_list_for_no_prints():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[]):
        assert uw.get_dark_pool_prints("XYZ") == []


def test_dark_pool_prints_returns_empty_list_on_a_non_list_response():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value={"unexpected": "shape"}):
        assert uw.get_dark_pool_prints("XYZ") == []


def test_dark_pool_prints_returns_empty_list_on_a_fetch_exception():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        assert uw.get_dark_pool_prints("XYZ") == []


def test_dark_pool_prints_one_malformed_row_does_not_drop_the_rest():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[
             {"price": "not-a-number-but-still-should-not-crash-the-loop", "size": "also-bad"},
             {"price": "100.0", "size": 5000, "executed_at": "2026-09-01T14:30:00Z"},
         ]):
        result = uw.get_dark_pool_prints("XYZ")
    assert len(result) == 1
    assert result[0].price == 100.0


def test_dark_pool_prints_uses_the_15min_ttl_not_the_6h_congress_ttl():
    fake_redis = _FakeRedis()
    spy_calls = []
    orig_setex = fake_redis.setex
    def _spy_setex(key, ttl, value):
        spy_calls.append((key, ttl, value))
        return orig_setex(key, ttl, value)
    fake_redis.setex = _spy_setex
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"price": "100.0", "size": 5000, "executed_at": "2026-09-01T14:30:00Z"},
         ]):
        uw.get_dark_pool_prints("AAPL")
    assert len(spy_calls) == 1
    assert spy_calls[0][1] == uw._DARK_POOL_TTL
    assert spy_calls[0][1] == 900


# ── get_congress_trades()/CongressTradeRow re-export (T323-DARKPOOL) ─────────────────────

def test_congress_trades_and_congress_trade_row_are_reexported_from_uw_congress():
    """unusual_whales.py deliberately does NOT re-implement congress-trade fetching (see the
    T323-DARKPOOL comment above the import in unusual_whales.py) — it re-exports the real
    implementation from shared/common/uw_congress.py so market-data code can still
    `from services.unusual_whales import get_congress_trades` without knowing about the split.
    Under this test environment's stubs, common.uw_congress is a blanket MagicMock (matching
    common.ai_keys' own stubbing), so this only verifies the NAMES are re-exported and reachable
    — the real behavior is tested directly against shared/common/uw_congress.py in
    test_uw_congress.py, which loads the real file rather than the stub."""
    assert hasattr(uw, "get_congress_trades")
    assert hasattr(uw, "CongressTradeRow")


# ── get_options_screener() — T324-OPTIONSFLOW-TAB ───────────────────────────────────────

def test_options_screener_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        assert uw.get_options_screener() == []
    mock_get.assert_not_called()


def test_options_screener_reads_from_cache_when_present():
    fake_redis = _FakeRedis()
    cached_row = uw.OptionsScreenerRow(ticker="AAPL", option_symbol="AAPL240119C00200000",
                                         option_type="call", strike=200.0, expiry="2024-01-19",
                                         volume=5000, open_interest=1000, premium=500_000.0,
                                         implied_volatility=0.35)
    import json
    cache_key = "stockai:uw:screener:None:250000.0:45:None:None:100"
    fake_redis.store[cache_key] = json.dumps([asdict(cached_row)])
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get") as mock_get:
        result = uw.get_options_screener(min_premium=250_000.0)
    assert result == [cached_row]
    mock_get.assert_not_called()


def test_options_screener_parses_a_real_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"ticker": "aapl", "option_symbol": "AAPL240119C00200000", "type": "Call",
              "strike": "200.0", "expiry": "2024-01-19", "volume": 5000, "open_interest": 1000,
              "premium": "500000.0", "implied_volatility": "0.35"},
         ]):
        result = uw.get_options_screener()
    assert len(result) == 1
    r = result[0]
    assert r.ticker == "AAPL"
    assert r.option_type == "call"
    assert r.strike == 200.0
    assert r.volume == 5000
    assert r.open_interest == 1000
    assert r.premium == 500_000.0
    assert r.implied_volatility == 0.35


def test_options_screener_sends_type_param_only_when_option_type_given():
    captured = {}
    def _fake_get(path, params=None):
        captured["params"] = params
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_options_screener(option_type="Calls")
    assert captured["params"]["type"] == "Calls"

    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_options_screener()
    assert "type" not in captured["params"]


def test_options_screener_drops_rows_with_no_ticker():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[{"ticker": ""}]):
        assert uw.get_options_screener() == []


def test_options_screener_returns_empty_list_on_a_fetch_exception():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        assert uw.get_options_screener() == []


def test_options_screener_one_malformed_row_does_not_drop_the_rest():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[
             {"ticker": "BAD", "volume": "not-a-number-but-caught"},
             {"ticker": "AAPL", "volume": 100},
         ]):
        result = uw.get_options_screener()
    # BAD row: volume int() cast raises ValueError inside the try -> skipped entirely.
    assert len(result) == 1
    assert result[0].ticker == "AAPL"


# ── get_option_trades() — T324-OPTIONSFLOW-TAB ───────────────────────────────────────────

def test_option_trades_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        assert uw.get_option_trades() == []
    mock_get.assert_not_called()


def test_option_trades_parses_a_real_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"ticker": "msft", "option_symbol": "MSFT240119C00400000", "type": "call",
              "strike": "400.0", "expiry": "2024-01-19", "price": "5.25", "size": 10,
              "premium": "5250.0", "is_multi_leg": False, "volume": 200, "open_interest": 50,
              "executed_at": "2026-09-02T14:30:00Z"},
         ]):
        result = uw.get_option_trades()
    assert len(result) == 1
    r = result[0]
    assert r.ticker == "MSFT"
    assert r.is_multi_leg is False
    assert r.price == 5.25
    assert r.size == 10


def test_option_trades_sends_max_dte_zero_for_0dte_filter():
    """max_dte=0 must actually be sent as a real query param — 0 is falsy in Python, a naive
    `if max_dte:` guard would silently drop it and turn a 0DTE-only scan into an unfiltered one."""
    captured = {}
    def _fake_get(path, params=None):
        captured["params"] = params
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_option_trades(max_dte=0)
    assert captured["params"]["max_dte"] == 0


def test_option_trades_sends_is_multi_leg_true_for_multileg_filter():
    captured = {}
    def _fake_get(path, params=None):
        captured["params"] = params
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_option_trades(is_multi_leg=True)
    assert captured["params"]["is_multi_leg"] == "true"


def test_option_trades_omits_max_dte_and_is_multi_leg_when_not_given():
    """The Interval Flow view (no extra filter) must not accidentally send a stale/default
    max_dte or is_multi_leg param."""
    captured = {}
    def _fake_get(path, params=None):
        captured["params"] = params
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_option_trades()
    assert "max_dte" not in captured["params"]
    assert "is_multi_leg" not in captured["params"]


def test_option_trades_returns_empty_list_on_a_fetch_exception():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        assert uw.get_option_trades() == []


def test_option_trades_drops_rows_with_no_ticker():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", return_value=[{"ticker": ""}]):
        assert uw.get_option_trades() == []


# ── get_market_tide() — T324-OPTIONSFLOW-TAB ─────────────────────────────────────────────

def test_market_tide_returns_empty_list_when_not_available():
    with patch.object(uw, "is_available", return_value=False), \
         patch.object(uw, "_get") as mock_get:
        assert uw.get_market_tide() == []
    mock_get.assert_not_called()


def test_market_tide_parses_a_real_response():
    fake_redis = _FakeRedis()
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=fake_redis), \
         patch.object(uw, "_get", return_value=[
             {"timestamp": "2026-09-02T14:00:00Z", "net_call_premium": "1500000.0", "net_put_premium": "-800000.0"},
         ]):
        result = uw.get_market_tide()
    assert len(result) == 1
    r = result[0]
    assert r.timestamp == "2026-09-02T14:00:00Z"
    assert r.net_call_premium == 1_500_000.0
    assert r.net_put_premium == -800_000.0


def test_market_tide_sends_interval_5m_param_correctly():
    captured = {}
    def _fake_get(path, params=None):
        captured["params"] = params
        return []
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=_fake_get):
        uw.get_market_tide(interval_5m=True)
    assert captured["params"]["interval_5m"] == "true"


def test_market_tide_returns_empty_list_on_a_fetch_exception():
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get", side_effect=RuntimeError("boom")):
        assert uw.get_market_tide() == []


# ── _get() real retry/error-classification behavior ─────────────────────────────────────

class TestGetFunctionRealHttpBehavior:
    """Pops httpx/tenacity off the stub list (both real, standalone-installable packages with
    no parent-package involved, unlike common.ai_keys — which stays mocked here and is patched
    at the call boundary instead, avoiding the documented "a fresh import against a
    MagicMock-stubbed parent auto-vivifies a DIFFERENT child mock" gotcha this repo has hit
    before whenever `common` itself needs to resolve as a real package for a submodule import
    to work) and imports a fresh copy of the module against the real httpx/tenacity, so
    _get()'s own @retry decorator and status-code classification run for real (not as a
    MagicMock passthrough). Restores the stubs immediately after import so later-collected
    test files in the same pytest session are unaffected, matching test_risk_snapshots.py's
    established stub-pop-and-restore technique.
    """

    @classmethod
    def setup_class(cls):
        _stubbed = ("httpx", "tenacity")
        saved = {m: sys.modules.pop(m, None) for m in _stubbed}
        # Force a fresh re-import of the module-under-test against the REAL httpx/tenacity —
        # the already-imported `uw` module above is still bound to the stubbed versions.
        sys.modules.pop("src.services.unusual_whales", None)
        import importlib
        cls.real_uw = importlib.import_module("src.services.unusual_whales")
        for m, stub in saved.items():
            if stub is not None:
                sys.modules[m] = stub
            else:
                sys.modules.pop(m, None)
        # Re-import the stubbed version back into sys.modules for every OTHER test in this
        # file (collected before/after this class) to keep working against the mocked version.
        sys.modules.pop("src.services.unusual_whales", None)
        importlib.import_module("src.services.unusual_whales")

    def test_returns_none_with_no_key_configured(self):
        with patch.object(self.real_uw, "get_unusual_whales_key", return_value=""):
            assert self.real_uw._get("/api/stock/AAPL/gex-levels") is None

    def test_a_real_200_response_returns_the_data_field(self):
        class _FakeResp:
            status_code = 200
            def json(self):
                return {"data": {"call_wall": 250.0}}
            def raise_for_status(self):
                pass

        class _FakeClient:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, *a, **kw):
                return _FakeResp()

        with patch.object(self.real_uw, "get_unusual_whales_key", return_value="real-token"), \
             patch.object(self.real_uw.httpx, "Client", return_value=_FakeClient()):
            result = self.real_uw._get("/api/stock/AAPL/gex-levels")
        assert result == {"call_wall": 250.0}

    def test_a_404_returns_none_not_an_exception(self):
        """A real, expected 'no data' response — must never be treated as a retryable/loggable
        error the way a genuine failure would be."""
        class _FakeResp:
            status_code = 404
        class _FakeClient:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, *a, **kw):
                return _FakeResp()

        with patch.object(self.real_uw, "get_unusual_whales_key", return_value="real-token"), \
             patch.object(self.real_uw.httpx, "Client", return_value=_FakeClient()):
            result = self.real_uw._get("/api/stock/ZZZZ/gex-levels")
        assert result is None

    def test_a_429_raises_the_dedicated_rate_limit_exception(self):
        class _FakeResp:
            status_code = 429
        class _FakeClient:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, *a, **kw):
                return _FakeResp()

        with patch.object(self.real_uw, "get_unusual_whales_key", return_value="real-token"), \
             patch.object(self.real_uw.httpx, "Client", return_value=_FakeClient()):
            try:
                self.real_uw._get("/api/stock/AAPL/gex-levels")
                assert False, "expected UnusualWhalesRateLimitError"
            except self.real_uw.UnusualWhalesRateLimitError:
                pass

    def test_a_429_increments_the_dq_check_rate_limit_counter(self):
        """AUD-DQCHECKS-VISIBILITY: a 429 must also increment the rolling counter the new
        uw_rate_limit_events_48h DQ gauge reads — before this fix, a rate-limit event was only
        ever visible in logs (unusual_whales.rate_limit), with no admin-visible rollup at all."""
        class _FakeResp:
            status_code = 429
        class _FakeClient:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, *a, **kw):
                return _FakeResp()

        with patch.object(self.real_uw, "get_unusual_whales_key", return_value="real-token"), \
             patch.object(self.real_uw.httpx, "Client", return_value=_FakeClient()), \
             patch.object(self.real_uw, "_incr_rate_limit_counter") as mock_incr:
            try:
                self.real_uw._get("/api/stock/AAPL/gex-levels")
            except self.real_uw.UnusualWhalesRateLimitError:
                pass
            mock_incr.assert_called_once()

    def test_incr_rate_limit_counter_uses_the_real_redis_client_and_fails_open(self):
        """_incr_rate_limit_counter() itself: must INCR the real counter key, set a TTL only on
        first write (matching scheduler.py's own _incr_rolling_counter idiom exactly), and never
        raise even if Redis itself is unavailable — a metrics-counter failure must never be able
        to break a real UW call path."""
        class _FakeRedis:
            def __init__(self):
                self.incr_calls = []
                self.expire_calls = []
                self._ttl = -1
            def incr(self, key):
                self.incr_calls.append(key)
            def ttl(self, key):
                return self._ttl
            def expire(self, key, seconds):
                self.expire_calls.append((key, seconds))
                self._ttl = seconds

        fake_redis = _FakeRedis()
        with patch.object(self.real_uw, "_get_redis", return_value=fake_redis):
            self.real_uw._incr_rate_limit_counter()
            assert fake_redis.incr_calls == [self.real_uw._RATE_LIMIT_COUNTER_KEY]
            assert fake_redis.expire_calls == [(self.real_uw._RATE_LIMIT_COUNTER_KEY, self.real_uw._RATE_LIMIT_COUNTER_TTL_S)]

            # A second call must NOT reset the TTL (ttl() no longer returns -1) — matches the
            # "expire only on first write" idiom this counter is explicitly modeled on.
            self.real_uw._incr_rate_limit_counter()
            assert fake_redis.incr_calls == [self.real_uw._RATE_LIMIT_COUNTER_KEY] * 2
            assert len(fake_redis.expire_calls) == 1

    def test_incr_rate_limit_counter_fails_open_on_a_redis_exception(self):
        class _BrokenRedis:
            def incr(self, key):
                raise ConnectionError("redis unavailable")

        with patch.object(self.real_uw, "_get_redis", return_value=_BrokenRedis()):
            self.real_uw._incr_rate_limit_counter()  # must not raise

    def test_a_401_raises_the_dedicated_auth_error_and_is_never_retried(self):
        """The auth-error path must be excluded from tenacity's own retry — retrying a bad key
        wastes the request budget on an error that can never self-resolve. Verified by
        counting real .get() calls: if the retry decorator wrongly included this exception
        type, we'd see 3 calls (tenacity's stop_after_attempt(3)), not 1."""
        call_count = {"n": 0}
        class _FakeResp:
            status_code = 401
        class _FakeClient:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, *a, **kw):
                call_count["n"] += 1
                return _FakeResp()

        with patch.object(self.real_uw, "get_unusual_whales_key", return_value="real-token"), \
             patch.object(self.real_uw.httpx, "Client", return_value=_FakeClient()):
            try:
                self.real_uw._get("/api/stock/AAPL/gex-levels")
                assert False, "expected UnusualWhalesAuthError"
            except self.real_uw.UnusualWhalesAuthError:
                pass
        assert call_count["n"] == 1

    def test_a_generic_500_is_retried_up_to_3_times(self):
        """A genuine transient failure (5xx, timeout, connection error) IS covered by the
        retry_if_not_exception_type((RateLimit, Auth)) exclusion — everything else retries."""
        call_count = {"n": 0}
        class _FakeResp:
            status_code = 500
            def raise_for_status(self):
                raise RuntimeError("500 server error")
        class _FakeClient:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, *a, **kw):
                call_count["n"] += 1
                return _FakeResp()

        with patch.object(self.real_uw, "get_unusual_whales_key", return_value="real-token"), \
             patch.object(self.real_uw.httpx, "Client", return_value=_FakeClient()):
            try:
                self.real_uw._get("/api/stock/AAPL/gex-levels")
                assert False, "expected the underlying RuntimeError to propagate after retries"
            except RuntimeError:
                pass
        assert call_count["n"] == 3
