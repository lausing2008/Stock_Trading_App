"""Audit R01 (2026-10-22): dark-pool failures must not reach the page as "no data".

Before: get_dark_pool_prints() caught every exception and returned [], and the route mapped []
to `available: false, reason: no_data`. A 503, a 429, an expired key and a quiet symbol were the
same answer. These tests drive the REAL provider function (only `_get` and Redis are faked)
through the REAL route, so the whole provider -> route boundary is covered.
"""
import json
from dataclasses import asdict
from unittest.mock import patch

import pytest

import src.services.scheduler as sched
from src.api import routes
from src.services import unusual_whales as UW


class _Redis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def setex(self, k, ttl, v):
        self.store[k] = v


class TimeoutException(Exception):      # named like httpx's, which the classifier reads
    pass


class HTTPStatusError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.response = type("R", (), {"status_code": status})()


GOOD = [{"price": "100.0", "size": 5000, "market_center": "L", "executed_at": "2026-10-22T14:30:00Z"}]
BASE = {"status": "ok", "median": 1.0, "prints": 30, "window_days": 14, "min_prints": 20,
        "first_captured": None, "last_captured": None, "population": "captured"}


def _route(get_effect, redis=None, available=True):
    redis = redis if redis is not None else _Redis()
    kw = {"side_effect": get_effect} if isinstance(get_effect, BaseException) or callable(get_effect) else {"return_value": get_effect}
    with patch.object(UW, "is_available", lambda: available), patch.object(UW, "_get_redis", lambda: redis), \
         patch.object(UW, "_get", **kw), patch.object(sched, "_dark_pool_baseline", lambda session, sym: BASE):
        return routes.get_dark_pool_prints_route("SYNTH", session=None), redis


CASES = {
    "timeout": TimeoutException("read timed out"),
    "rate_limited": UW.UnusualWhalesRateLimitError("429"),
    "upstream_error": HTTPStatusError(503),
    "unauthorized": UW.UnusualWhalesAuthError("403"),
    "malformed": {"unexpected": "shape"},
    "empty": [],
}


@pytest.mark.parametrize("expected,effect", CASES.items())
def test_each_outcome_reaches_the_page_as_its_own_state(expected, effect):
    out, _ = _route(effect)
    assert out["available"] is False and out["prints"] == []
    assert out["status"] == expected
    # Only a genuine empty answer is "no_data"; nothing else may masquerade as it.
    assert (out["reason"] == "no_data") is (expected == "empty")


def test_disabled_is_distinct_and_never_fetches():
    with patch.object(UW, "is_available", lambda: False), patch.object(UW, "_get") as g:
        out = routes.get_dark_pool_prints_route("SYNTH", session=None)
    assert out["status"] == "disabled" and out["reason"] == "unusual_whales_disabled"
    g.assert_not_called()


def test_the_six_outcomes_are_pairwise_distinct():
    states = {_route(e)[0]["status"] for e in CASES.values()}
    assert len(states) == len(CASES)


def test_503_keeps_its_http_status_and_a_429_is_rate_limited():
    assert _route(HTTPStatusError(503))[0]["http_status"] == 503
    assert _route(UW.UnusualWhalesRateLimitError("x"))[0]["http_status"] == 429


def test_all_unparseable_rows_are_malformed_not_empty():
    out, _ = _route([{"price": "x", "size": "y"}, {"price": "z", "size": "w"}])
    assert out["status"] == "malformed" and out["malformed_rows"] == 2


def test_a_404_is_an_empty_answer():
    out, _ = _route(None)
    assert out["status"] == "empty" and out["reason"] == "no_data"


def test_success_reports_when_the_provider_answered_and_the_newest_print():
    out, _ = _route(GOOD)
    assert out["available"] is True and out["status"] == "ok" and out["stale"] is False
    assert out["fetched_at"] and out["source_as_of"] == "2026-10-22T14:30:00Z"
    assert out["from_cache"] is False


def test_a_cached_answer_keeps_its_original_fetch_time():
    _, redis = _route(GOOD)
    first = json.loads(redis.store["stockai:uw:darkpool:v2:SYNTH"])["fetched_at"]
    out, _ = _route(AssertionError("must not fetch"), redis=redis)
    assert out["from_cache"] is True and out["fetched_at"] == first


def test_a_failure_is_never_cached_so_the_next_view_retries():
    _, redis = _route(HTTPStatusError(503))
    assert "stockai:uw:darkpool:v2:SYNTH" not in redis.store
    out, _ = _route(GOOD, redis=redis)
    assert out["status"] == "ok"


def test_a_last_good_sample_is_shown_only_beside_the_failure():
    _, redis = _route(GOOD)
    del redis.store["stockai:uw:darkpool:v2:SYNTH"]          # the 15-min answer cache expired
    out, _ = _route(UW.UnusualWhalesRateLimitError("429"), redis=redis)
    assert out["status"] == "rate_limited"                    # the failure is the answer...
    assert out["available"] is True and out["stale"] is True  # ...with the old sample beside it
    assert out["stale_fetched_at"] and len(out["prints"]) == 1
    assert out["fetched_at"] is None                          # nothing new was fetched


def test_the_scanning_wrapper_keeps_its_empty_list_contract():
    with patch.object(UW, "is_available", lambda: True), patch.object(UW, "_get_redis", lambda: _Redis()), \
         patch.object(UW, "_get", side_effect=HTTPStatusError(503)):
        assert UW.get_dark_pool_prints("SYNTH") == []
