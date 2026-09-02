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


def test_flow_alerts_is_never_cached_unlike_gex_and_short_interest():
    """Flow alerts are inherently a fast-moving, minute-to-minute feed — unlike GEX/short-
    interest, _get_redis() must never be touched by this function at all."""
    class _ExplodingRedis:
        def get(self, *a, **kw):
            raise AssertionError("get_flow_alerts must never read from Redis")
        def setex(self, *a, **kw):
            raise AssertionError("get_flow_alerts must never write to Redis")
    with patch.object(uw, "is_available", return_value=True), \
         patch.object(uw, "_get_redis", return_value=_ExplodingRedis()), \
         patch.object(uw, "_get", return_value=[]):
        uw.get_flow_alerts("AAPL")  # must not raise


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
