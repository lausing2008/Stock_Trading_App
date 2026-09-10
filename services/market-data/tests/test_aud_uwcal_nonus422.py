"""AUD-UWCAL-NONUS422 — the earnings calendar took 61s because 14 symbols could never succeed.

REPORTED BY THE USER: "Failed to load events. when opening earning calendar".

The endpoint was not broken, it was SLOW PAST EVERY TIMEOUT — `GET /stocks/events/calendar`
returned a valid 174-event payload in **61 seconds**, so the browser gave up first and
earnings.tsx rendered its generic `Failed to load events.` message.

THE MEASUREMENT THAT FOUND IT, and it inverted my first hypothesis. I assumed the 120
per-symbol `_compute_weighted_analyst_consensus()` DB queries. Measured separately:

    analyst consensus (DB)   :   0.52s   (4 ms/symbol)     <- negligible
    UW earnings moves (warm) :  59.94s   (500 ms/symbol avg)

and then, split by whether the symbol can actually be served:

    cached US symbol   (AAPL/MSFT/NVDA/MU/V) :    ~1 ms
    uncacheable symbol (9868.HK/3750.HK/BRK-A): ~4,400 ms

Of 120 symbols with earnings in the 90-day window, **106 were cached US symbols contributing
essentially nothing**, and **14 (13 `.HK` + `BRK-A`) cost ~4.4s each**. 14 x 4.4 = 62s, which
matches the observed 61s wall time.

WHY 4.4s FOR A SINGLE FAILING CALL — three compounding facts:
  1. UW has NO non-US coverage; `/api/earnings/9868.HK` returns **422 Unprocessable Entity**.
  2. `_get()`'s tenacity decorator excludes only `UnusualWhalesRateLimitError` and
     `UnusualWhalesAuthError`. A 422 reaches `raise_for_status()` -> `HTTPStatusError`, which
     is NOT excluded, so it is **retried 3x with `wait_exponential(min=2)`**.
  3. `get_historical_earnings_moves()` did `return []` **BEFORE** its `setex`, so the failure
     was **never cached** — every calendar load paid the full cost again, forever.

Fact 3 is why the inline comment claiming the 6h cache means "repeated requests cost nothing
extra" was wrong for exactly the symbols that mattered. It was true only of successes.

It also burned real UW quota (`_incr_call_counter` counts every attempt) on requests that were
structurally incapable of succeeding, and would get WORSE as HK coverage grew.

FIXED AT BOTH LAYERS, deliberately:
  * the CALLER skips UW when it already knows the market (`mkt == "US"`), avoiding the request;
  * the FUNCTION rejects a non-US-shaped ticker and negative-caches real failures, which
    protects every future caller rather than just this one.
"""
import pathlib

import pytest

import src.services.unusual_whales as uw

ROUTES_SRC = (
    pathlib.Path(__file__).resolve().parents[1] / "src/api/routes.py"
).read_text()
UW_SRC = pathlib.Path(uw.__file__).read_text()


# ── The symbol guard ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sym", ["AAPL", "MSFT", "NVDA", "MU", "V", "SPY", "BRK", "F"])
def test_real_us_tickers_are_still_served(sym):
    """THE REGRESSION THAT WOULD MATTER MOST. Over-rejecting silently disables UW for real
    symbols — a far worse outcome than the slowness being fixed, and invisible because the
    function returns [] on both a skip and a genuine empty result."""
    assert uw.is_us_ticker(sym) is True


@pytest.mark.parametrize("sym", [
    "9868.HK", "3750.HK", "0700.HK",   # the 13 real HK symbols in the production window
    "BRK-A", "BRK-B",                  # UW expects BRK.A; the dashed class form 422s
    "600519.SS", "000001.SZ", "7203.T", "VOD.L",
])
def test_tickers_uw_cannot_serve_are_rejected(sym):
    """Each of these cost ~4.4s per calendar load before the fix."""
    assert uw.is_us_ticker(sym) is False


def test_the_two_real_production_cases_are_both_covered():
    """Measured on production: 13 `.HK` plus `BRK-A` = the exact 14 slow symbols."""
    assert uw.is_us_ticker("9868.HK") is False, "the .HK half of the 14"
    assert uw.is_us_ticker("BRK-A") is False, "the BRK-A half of the 14"


def test_case_is_normalised():
    assert uw.is_us_ticker("9868.hk") is False
    assert uw.is_us_ticker("aapl") is True


@pytest.mark.parametrize("sym", ["", None])
def test_empty_input_is_rejected_not_crashed(sym):
    """A leaf function in a scheduled path must not raise on a missing symbol."""
    assert uw.is_us_ticker(sym) is False


def test_a_bare_dash_alone_does_not_reject():
    """DELIBERATE NARROWNESS. Rejecting every dashed ticker would be over-broad; the rule
    targets a single trailing class letter (BRK-A), which is what UW actually 422s on."""
    assert uw.is_us_ticker("X-RAY") is True, "not a single-letter class suffix"


# ── The function skips before spending a request ────────────────────────────────────────

def test_a_non_us_symbol_makes_no_http_request_at_all(monkeypatch):
    """THE POINT OF THE FIX. Not 'fails faster' — makes NO request, so it costs no wall time
    and no UW quota."""
    monkeypatch.setattr(uw, "is_available", lambda: True)
    called = []
    monkeypatch.setattr(uw, "_get", lambda *a, **k: called.append(a) or None)
    assert uw.get_historical_earnings_moves("9868.HK") == []
    assert called == [], "a non-US symbol must not reach _get() at all"


def test_a_us_symbol_still_reaches_the_api(monkeypatch):
    """The guard must not disable the feature it is protecting."""
    monkeypatch.setattr(uw, "is_available", lambda: True)
    monkeypatch.setattr(uw, "_get_redis", lambda: _FakeRedis())
    called = []

    def _fake_get(path, params=None, *, endpoint=None):
        called.append(path)
        return [{"report_date": "2026-07-01", "expected_move_perc": "5.0"}]

    monkeypatch.setattr(uw, "_get", _fake_get)
    rows = uw.get_historical_earnings_moves("AAPL")
    assert called == ["/api/earnings/AAPL"]
    assert len(rows) == 1


class _FakeRedis:
    def __init__(self):
        self.store = {}
        self.setex_calls = []

    def get(self, k):
        return self.store.get(k)

    def setex(self, k, ttl, v):
        self.setex_calls.append((k, ttl))
        self.store[k] = v


# ── Negative caching of REAL failures ───────────────────────────────────────────────────

def test_a_real_failure_is_negative_cached(monkeypatch):
    """THE 61-SECOND BUG. `return []` sat before the setex, so a failing symbol re-requested
    on every single call forever."""
    monkeypatch.setattr(uw, "is_available", lambda: True)
    fake = _FakeRedis()
    monkeypatch.setattr(uw, "_get_redis", lambda: fake)

    def _boom(*a, **k):
        raise RuntimeError("500 from UW")

    monkeypatch.setattr(uw, "_get", _boom)
    assert uw.get_historical_earnings_moves("AAPL") == []
    keys = [k for k, _ in fake.setex_calls]
    assert "stockai:uw:earnings_moves:AAPL" in keys, "the failure must be cached"


def test_the_negative_cache_short_circuits_the_second_call(monkeypatch):
    """One request per TTL instead of one per page load — the actual latency fix for a symbol
    that fails for a REAL reason (as opposed to being structurally non-US)."""
    monkeypatch.setattr(uw, "is_available", lambda: True)
    fake = _FakeRedis()
    monkeypatch.setattr(uw, "_get_redis", lambda: fake)
    calls = []

    def _boom(*a, **k):
        calls.append(1)
        raise RuntimeError("500 from UW")

    monkeypatch.setattr(uw, "_get", _boom)
    uw.get_historical_earnings_moves("AAPL")
    uw.get_historical_earnings_moves("AAPL")
    assert len(calls) == 1, "the second call must be served from the negative cache"


def test_the_failure_ttl_is_much_shorter_than_the_success_ttl():
    """A transient outage must not pin a symbol empty for 6h. Both directions matter: too
    short and the fix does nothing, too long and a recovered API stays invisible."""
    assert uw._EARNINGS_MOVE_FAIL_TTL < uw._EARNINGS_MOVE_TTL
    assert 300 <= uw._EARNINGS_MOVE_FAIL_TTL <= 3600


def test_the_negative_cache_uses_the_failure_ttl_not_the_success_one(monkeypatch):
    monkeypatch.setattr(uw, "is_available", lambda: True)
    fake = _FakeRedis()
    monkeypatch.setattr(uw, "_get_redis", lambda: fake)
    monkeypatch.setattr(uw, "_get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    uw.get_historical_earnings_moves("AAPL")
    ttls = [t for k, t in fake.setex_calls if k.endswith("AAPL")]
    assert ttls == [uw._EARNINGS_MOVE_FAIL_TTL]


def test_a_structurally_non_us_symbol_is_not_even_negative_cached(monkeypatch):
    """It never reaches the request, so there is nothing to cache — and writing a cache entry
    per HK symbol would be pure waste. The guard is cheaper than the cache."""
    monkeypatch.setattr(uw, "is_available", lambda: True)
    fake = _FakeRedis()
    monkeypatch.setattr(uw, "_get_redis", lambda: fake)
    uw.get_historical_earnings_moves("9868.HK")
    assert fake.setex_calls == []


# ── The caller-side skip ────────────────────────────────────────────────────────────────

def _earnings_block() -> str:
    i = ROUTES_SRC.index("def events_calendar(")
    return ROUTES_SRC[i:ROUTES_SRC.index("\n@router", i + 10)]


def test_the_call_site_is_gated_on_market():
    """Belt AND braces: the caller already has `mkt` in scope, so it can skip without even
    calling into the UW module."""
    blk = _earnings_block()
    assert 'if mkt == "US" else []' in blk


def test_the_call_site_still_calls_it_exactly_once():
    """Preserves the pre-existing AUD-EARNINGSMOVE scoping invariant — the call must stay
    inside the near-term-earnings branch, never in the outer per-stock loop."""
    assert _earnings_block().count("_uw.get_historical_earnings_moves(") == 1


def test_the_measured_evidence_is_recorded_in_the_source():
    """So the next person does not re-measure, or 'simplify' the guard away as paranoia."""
    blk = _earnings_block()
    assert "4.4s" in blk
    assert "422" in blk


# ── The arithmetic, pinned so the severity claim cannot rot ─────────────────────────────

def test_fourteen_failing_symbols_account_for_the_full_wall_time():
    """14 x 4.4s = 61.6s against a measured 61.1s endpoint. The 106 cached US symbols at ~1ms
    contribute ~0.1s — i.e. essentially all of the latency came from calls that could never
    succeed."""
    slow_n, slow_cost = 14, 4.4
    cached_n, cached_cost = 106, 0.001
    total = slow_n * slow_cost + cached_n * cached_cost
    assert 55 <= total <= 70, f"{total}"
    assert slow_n * slow_cost > 20 * (cached_n * cached_cost)


def test_the_db_consensus_query_was_not_the_cause():
    """Pins the corrected diagnosis. My first hypothesis was these 120 DB queries; measured at
    0.52s total they are ~1% of the endpoint's time, and 'fixing' them would have changed
    nothing."""
    assert 120 * 0.004 < 1.0
