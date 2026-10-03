"""AUD-UW-ADAPTER-UNINSTRUMENTED / AUD-UW-BURST — the bar path was invisible, and a throttled
UW cost a failed call on every symbol.

THE QUESTION THAT COULD NOT BE ANSWERED. Asked "what's the failure from UW and why", the
honest answer was: 13,131 ingest failures in 12 hours, of which 2,789 were confirmed HTTP 429
— and the other ~10,342 had no established cause, because the logs had been reset by a rebuild
and there is no intraday traffic after the close. A log grep cannot answer a question about
yesterday.

WHY THE METRICS DID NOT KNOW EITHER. This adapter builds its own `httpx.Client`, so it never
passed through `services/unusual_whales.py`'s counters:

  * usage dashboard read 75,811 calls for 2026-10-02 and listed NO ohlc endpoint, while this
    adapter was making an estimated ~14,768 — ~20% of real usage unaccounted, against a 120k
    budget;
  * the rate-limit gauge read 5-16/hour while this adapter's 429s ran ~232/hour — the gauge
    that exists to show UW throttling was blind to its largest source.

THE BURST. `ingest_universe` walks ~142 US symbols every five minutes with this adapter first
in the registry. Once UW starts refusing, each symbol paid a full round trip to be told 429
before falling through to yfinance: 142 wasted calls per burst, against the exhausted quota.
A short cooldown makes that one wasted call per window.
"""
import pytest
import ast
import pathlib

import pytest

_SRC = (pathlib.Path(__file__).resolve().parents[1]
        / "src" / "adapters" / "unusual_whales_adapter.py").read_text()


def _fetch_body() -> str:
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "fetch_ohlcv")
    return ast.get_source_segment(_SRC, fn) or ""


def _supports_body() -> str:
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "supports")
    return ast.get_source_segment(_SRC, fn) or ""


# ── every failure is classified, so the next "why" is answerable ────────────────────────

@pytest.mark.parametrize("reason", [
    "no_key", "unsupported_timeframe", "rate_limited", "timeout",
])
def test_each_failure_mode_is_counted_under_its_own_reason(reason):
    assert f'"{reason}"' in _fetch_body(), f"{reason} is not recorded"


def test_an_http_error_is_classified_by_its_status_code():
    """'~10,342 failures of unknown cause' is what this exists to prevent happening again."""
    body = _fetch_body()
    assert 'f"http_{r.status_code}"' in body


def test_an_unexpected_exception_is_still_counted_by_type():
    assert 'f"other_{type(exc).__name__}"' in _fetch_body()


def test_counters_outlive_a_container_rebuild():
    """The original question was unanswerable because `docker logs` resets on recreation.
    A Redis counter with a 49-hour TTL does not."""
    tree = ast.parse(_SRC)
    ttl = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "_COUNTER_TTL_S" for t in n.targets))
    # The value is written as `49 * 3600`, which literal_eval refuses — evaluate the
    # expression rather than the literal, so the test reads the NUMBER the code will use
    # rather than requiring the code to be written in a particular style.
    seconds = eval(compile(ast.Expression(ttl.value), "<ttl>", "eval"), {}, {})  # noqa: S307
    assert seconds >= 48 * 3600


# ── the usage dashboard can finally see this path ───────────────────────────────────────

def test_every_call_is_counted_not_just_failures():
    """Usage read 75,811/day with no ohlc endpoint listed while this adapter made ~14,768."""
    body = _fetch_body()
    assert '_note("call", "/api/stock/{symbol}/ohlc/{candle}")' in body


def test_the_call_counter_uses_the_same_key_shape_as_the_service_module():
    """Otherwise the dashboard still cannot add it up."""
    assert '_CALL_COUNTER_PREFIX = "stockai:metric:uw_calls"' in _SRC


def test_one_counter_per_endpoint_not_one_per_ticker():
    body = _fetch_body()
    assert '"/api/stock/{symbol}/ohlc/{candle}"' in body, \
        "the path template must not be interpolated per symbol"


def test_a_429_feeds_the_existing_rate_limit_gauge():
    """The gauge undercounted by ~33x because this path never reached it."""
    body = _fetch_body()
    i = body.index("rate_limited")
    assert "_incr_rate_limit_counter" in body[i:i + 600]


# ── instrumentation must never break an ingest ──────────────────────────────────────────

def test_recording_a_metric_cannot_raise():
    tree = ast.parse(_SRC)
    note = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_note")
    handlers = [h for n in ast.walk(note) if isinstance(n, ast.Try) for h in n.handlers]
    assert handlers, "_note must swallow its own failures"
    assert any(isinstance(h.body[0], ast.Pass) for h in handlers)


def test_the_gauge_call_cannot_break_the_rate_limit_path():
    body = _fetch_body()
    i = body.index("_incr_rate_limit_counter")
    assert "except Exception:" in body[i:i + 200]


# ── the cooldown ────────────────────────────────────────────────────────────────────────

def test_a_throttled_adapter_steps_aside_instead_of_failing_every_symbol():
    assert "_arm_cooldown()" in _fetch_body()
    assert "_COOLDOWN_KEY" in _supports_body()


def test_the_cooldown_is_short_enough_to_let_uw_come_back():
    """A step-aside, not a circuit breaker with a long memory."""
    tree = ast.parse(_SRC)
    cd = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "_COOLDOWN_SECONDS" for t in n.targets))
    assert 30 <= ast.literal_eval(cd.value) <= 600


def test_a_redis_outage_does_not_remove_a_working_data_source():
    """Fail-open: an unavailable metric store must not take UW offline."""
    body = _supports_body()
    i = body.index("_COOLDOWN_KEY")
    tail = body[i:]
    assert "except Exception:" in tail and "pass" in tail
    assert tail.rstrip().endswith("return True")


def test_hk_is_still_never_routed_to_uw():
    """UW has no HK coverage; the registry must keep sending HK to yfinance."""
    assert 'market == "US"' in _supports_body()
    assert 'supported_markets = ("US",)' in _SRC


# ── the usage dashboard can see all of it ───────────────────────────────────────────────

_ADMIN = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py").read_text()


def test_the_usage_endpoint_picks_up_adapter_calls_automatically():
    """It globs `uw_calls:*:{day}`, so writing under the same prefix is enough — no
    endpoint allow-list to forget to update."""
    assert 'r.keys(f"stockai:metric:uw_calls:*:{day}")' in _ADMIN


def test_the_usage_endpoint_reports_failures_by_cause():
    assert '"adapter_failures_24h"' in _ADMIN
    assert '"adapter_failures_24h_total"' in _ADMIN


def test_the_failure_window_is_a_trailing_24h_not_a_calendar_day():
    """The condition is bursty; a calendar-day figure hides a morning incident once the
    afternoon is quiet."""
    i = _ADMIN.index("failures_24h: dict[str, int] = {}")
    block = _ADMIN[i:_ADMIN.index("return {", i)]
    assert "for back in range(24)" in block
    assert "timedelta(hours=back)" in block


def test_reading_the_failure_counters_cannot_break_the_usage_endpoint():
    i = _ADMIN.index("failures_24h: dict[str, int] = {}")
    block = _ADMIN[i:_ADMIN.index("return {", i)]
    assert "except Exception:" in block and "failures_24h = {}" in block


# ── AUD-UW429NOENDPOINT: per-endpoint, per-cause failure counting on the MAIN client ───────
#
# The 2026-10-03 counter reading found 119 rate-limits with no way to tell which endpoint was
# refused: one global hourly bucket covered every endpoint. These exercise the real functions.

class _CountingRedis:
    def __init__(self):
        self.store = {}
        self.expires = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, ttl):
        self.expires[key] = ttl
        return True


_UW_CACHE = []


def _uw_module():
    """The REAL module, loaded once.

    Registered in sys.modules BEFORE exec_module: this module defines dataclasses, and
    dataclasses resolve their own annotations through `sys.modules[cls.__module__]`. Executed
    under a name that is not registered, that lookup returns None and every dataclass in the
    file raises on definition — which is a loader bug in the test, not a defect in the module.
    """
    if _UW_CACHE:
        return _UW_CACHE[0]
    import importlib.util, pathlib, sys
    from unittest.mock import MagicMock
    for m in ["redis", "httpx", "structlog"]:
        sys.modules.setdefault(m, MagicMock())
    root = pathlib.Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "shared"))
    spec = importlib.util.spec_from_file_location(
        "uw_failure_counters_under_test",
        root / "services/market-data/src/services/unusual_whales.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _UW_CACHE.append(mod)
    return mod


def test_failures_are_counted_by_endpoint_template_and_cause(monkeypatch):
    uw = _uw_module()
    fake = _CountingRedis()
    monkeypatch.setattr(uw, "_get_redis", lambda: fake)
    uw._incr_failure_counter("/api/stock/{ticker}/option-chains", "rate_limited")
    uw._incr_failure_counter("/api/stock/{ticker}/option-chains", "rate_limited")
    uw._incr_failure_counter("/api/stock/{symbol}/max-pain", "rate_limited")
    keys = sorted(fake.store)
    assert len(keys) == 2, "two different endpoints must not share one counter"
    chains = [k for k in keys if "option-chains" in k][0]
    assert fake.store[chains] == 2
    assert "rate_limited" in chains


def test_a_resolved_path_is_never_used_as_a_label(monkeypatch):
    """A per-symbol label mints one Redis key per symbol per hour — a metric that leaks memory.
    Only the bounded template is ever a label."""
    uw = _uw_module()
    fake = _CountingRedis()
    monkeypatch.setattr(uw, "_get_redis", lambda: fake)
    for symbol in ("MU", "AAPL", "NVDA", "TSLA"):
        uw._incr_failure_counter("/api/darkpool/{symbol}", "timeout")
    assert len(fake.store) == 1, "one template, one key, regardless of how many symbols failed"
    assert not any(s in k for k in fake.store for s in ("MU", "AAPL", "NVDA", "TSLA"))


def test_a_call_site_with_no_template_is_labelled_not_guessed(monkeypatch):
    uw = _uw_module()
    fake = _CountingRedis()
    monkeypatch.setattr(uw, "_get_redis", lambda: fake)
    uw._incr_failure_counter(None, "transport")
    assert any("unlabelled" in k for k in fake.store)


@pytest.mark.parametrize("exc,expected", [
    (TimeoutError("x"), "timeout"),
    (ConnectionError("x"), "transport"),
    (ValueError("x"), "other"),
    (KeyError("x"), "other"),
])
def test_causes_are_bounded_labels_never_the_message(exc, expected):
    uw = _uw_module()
    assert uw._classify_failure(exc) == expected


def test_the_cause_label_never_contains_the_exception_text():
    """An exception message carries URLs, symbols and sometimes credentials; it must never
    become part of a metric key."""
    uw = _uw_module()
    secret = "https://api.unusualwhales.com/api/x?token=SHOULD_NOT_APPEAR"
    assert secret not in uw._classify_failure(ConnectionError(secret))


def test_a_failing_counter_never_breaks_the_call(monkeypatch):
    uw = _uw_module()

    class Broken:
        def incr(self, key):
            raise RuntimeError("redis down")

    monkeypatch.setattr(uw, "_get_redis", lambda: Broken())
    uw._incr_failure_counter("/api/x", "timeout")       # must not raise


def test_the_retired_rolling_key_is_no_longer_written():
    """It was a sawtooth that read zero during live incidents. Consumers sum hourly buckets;
    the name survives only so an empty key in Redis reads as RETIRED, not as zero failures."""
    uw = _uw_module()
    assert not hasattr(uw, "_RATE_LIMIT_COUNTER_KEY")
    assert hasattr(uw, "_RATE_LIMIT_COUNTER_KEY_RETIRED")
