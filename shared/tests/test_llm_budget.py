"""An ENFORCED ceiling, not an alert threshold.

THE CORRECTION THIS PINS. The first "daily token budget" sent an email when the day's total
passed 400,000. An email does not prevent call 400,001 — the ceiling could be exceeded without
limit while reporting that it had been. These tests drive the decision that actually refuses.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import llm_budget as B  # noqa: E402


class FakeRedis:
    """Atomic enough for the property under test: INCRBY returns the post-increment value."""
    def __init__(self, fail=False):
        self.store, self.fail = {}, fail

    def incrby(self, k, n):
        if self.fail:
            raise RuntimeError("redis down")
        self.store[k] = self.store.get(k, 0) + n
        return self.store[k]

    def decrby(self, k, n):
        if self.fail:
            raise RuntimeError("redis down")
        self.store[k] = self.store.get(k, 0) - n
        return self.store[k]

    def get(self, k):
        if self.fail:
            raise RuntimeError("redis down")
        return self.store.get(k)

    def expire(self, k, s):
        return True


@pytest.fixture
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(B, "_redis", lambda: r)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    return r


def test_capacity_is_reserved_before_the_call_not_counted_after():
    assert hasattr(B, "reserve") and hasattr(B, "reconcile")


def test_a_reservation_inside_the_ceiling_is_allowed(redis):
    r = B.reserve(400)
    assert r.allowed and r.reserved == 400 and r.enforcement == "redis"
    assert r.remaining_before == 1000


def test_the_ceiling_actually_refuses(redis):
    assert B.reserve(600).allowed
    assert B.reserve(600).allowed is False, "an alert would have let this through"


def test_a_refused_reservation_does_not_consume_capacity(redis):
    """Otherwise one oversized request permanently shrinks the day."""
    B.reserve(600)
    refused = B.reserve(900)
    assert not refused.allowed
    assert B.reserve(400).allowed, "the refused 900 was returned, leaving room for 400"


def test_concurrent_callers_cannot_all_pass_the_same_check(redis):
    """The failure a post-hoc total cannot prevent: N callers each read 'under budget'."""
    allowed = [B.reserve(300).allowed for _ in range(5)]
    assert allowed == [True, True, True, False, False]


def test_an_overestimate_is_returned_on_reconciliation(redis):
    r = B.reserve(800)
    B.reconcile(r, 100)
    assert B.reserve(800).allowed, "unreturned over-estimates would shrink the real capacity"


def test_an_underestimate_is_charged_on_reconciliation(redis):
    r = B.reserve(100)
    B.reconcile(r, 900)
    assert B.reserve(200).allowed is False, "the ceiling must not be passed silently"


# ---------------------------------------------------------------- the resolver fallback

def test_the_resolver_fallback_has_its_own_smaller_allowance(monkeypatch):
    monkeypatch.delenv("LLM_BUDGET_NEWS_CLASSIFY", raising=False)
    monkeypatch.delenv("LLM_BUDGET_NEWS_CLASSIFY_RESOLVER_FALLBACK", raising=False)
    main = B.budget_for(B.SCOPE_NEWS_CLASSIFY)
    fallback = B.budget_for(B.SCOPE_RESOLVER_FALLBACK)
    assert 0 < fallback < main, "'classify everything loudly' must be bounded, not just visible"


def test_the_two_scopes_do_not_share_a_counter(redis, monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY_RESOLVER_FALLBACK", "500")
    B.reserve(900, scope=B.SCOPE_NEWS_CLASSIFY)
    assert B.reserve(400, scope=B.SCOPE_RESOLVER_FALLBACK).allowed, \
        "one runaway caller must not consume another scope's room"


# ---------------------------------------------------------------- degraded infrastructure

def test_without_redis_the_database_bounds_spending_with_a_margin(monkeypatch):
    monkeypatch.setattr(B, "_redis", lambda: None)
    monkeypatch.setattr(B, "_db_used_today", lambda scope: 880)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    r = B.reserve(100)
    assert not r.allowed and r.enforcement == "database"
    assert "margin" in r.reason, "the margin covers races the database cannot prevent"


def test_the_database_fallback_still_allows_work_below_its_margin(monkeypatch):
    monkeypatch.setattr(B, "_redis", lambda: None)
    monkeypatch.setattr(B, "_db_used_today", lambda scope: 100)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    assert B.reserve(100).allowed


def test_with_neither_counter_it_defers_rather_than_spending_blind(monkeypatch):
    """An explicit policy. Alerting alone would not bound anything."""
    monkeypatch.setattr(B, "_redis", lambda: None)
    monkeypatch.setattr(B, "_db_used_today", lambda scope: None)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    r = B.reserve(100)
    assert not r.allowed
    assert r.enforcement == "none"
    assert "cannot be bounded" in r.reason


def test_a_redis_failure_falls_through_to_the_database(monkeypatch):
    monkeypatch.setattr(B, "_redis", lambda: FakeRedis(fail=True))
    monkeypatch.setattr(B, "_db_used_today", lambda scope: 0)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    assert B.reserve(100).enforcement == "database"


# ---------------------------------------------------------------- stated, not inherited

def test_the_day_is_the_utc_calendar_day_and_says_so():
    from datetime import datetime, timezone
    k = B.utc_day_key(B.SCOPE_NEWS_CLASSIFY,
                      datetime(2026, 10, 6, 23, 30, tzinfo=timezone.utc))
    assert k.endswith("2026-10-06")
    assert B.status()["day_basis"] == "UTC calendar day"


def test_status_says_which_mechanism_is_enforcing(redis):
    B.reserve(300)
    st = B.status()
    assert st["enforcement"] == "redis"
    assert st["budget"] == 1000 and st["reserved_or_used"] == 300 and st["remaining"] == 700


def test_a_zero_budget_means_no_ceiling_not_no_capacity(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "0")
    r = B.reserve(10_000_000)
    assert r.allowed and "no ceiling configured" in r.reason
