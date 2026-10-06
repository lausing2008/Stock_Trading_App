"""The budget against REAL PostgreSQL and REAL Redis.

WHY THIS FILE EXISTS. The unit tests drive threads against a fake Redis and a fake session.
They cannot establish the two properties that matter most: that the database UPSERT is actually
atomic under concurrent transactions, and that the Lua script actually rolls back every key it
charged. A test double that mirrors the intended behaviour proves the mirror, not the code.

SKIPS unless both URLs are set, so the ordinary suite stays runnable:

    docker run -d --name stockai-budget-pg -e POSTGRES_PASSWORD=probe \\
        -e POSTGRES_DB=budgetprobe -p 55433:5432 postgres:15
    docker run -d --name stockai-budget-redis -p 56379:6379 redis:7
    BUDGET_PG_URL=postgresql+psycopg2://postgres:probe@localhost:55433/budgetprobe \\
    BUDGET_REDIS_URL=redis://localhost:56379/0 \\
        python -m pytest shared/tests/test_llm_budget_integration.py
"""
import os
import sys
import threading
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_PG = os.environ.get("BUDGET_PG_URL")
_REDIS = os.environ.get("BUDGET_REDIS_URL")

pytestmark = pytest.mark.skipif(
    not (_PG and _REDIS),
    reason="set BUDGET_PG_URL and BUDGET_REDIS_URL to run against real stores")

if _PG and _REDIS:
    import redis as redis_lib
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from common import llm_budget as B

    _ENGINE = create_engine(_PG, pool_size=20, max_overflow=20)
    _Session = sessionmaker(bind=_ENGINE)

    DDL = """
    CREATE TABLE IF NOT EXISTS llm_budget_day (
        scope VARCHAR(64) NOT NULL, day DATE NOT NULL,
        reserved BIGINT NOT NULL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT now(),
        PRIMARY KEY (scope, day));
    CREATE TABLE IF NOT EXISTS llm_reservations (
        id VARCHAR(40) PRIMARY KEY, scope VARCHAR(64) NOT NULL, day DATE NOT NULL,
        reserved INTEGER NOT NULL, settled_tokens INTEGER, outcome VARCHAR(16),
        created_at TIMESTAMP DEFAULT now(), settled_at TIMESTAMP);
    """


@pytest.fixture
def stores(monkeypatch):
    """Fresh counters, a real session factory, and a real Redis."""
    with _ENGINE.begin() as c:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.execute(text("TRUNCATE llm_budget_day, llm_reservations"))
    r = redis_lib.from_url(_REDIS)
    r.flushdb()

    import types
    fake_db = types.ModuleType("db")
    fake_db.SessionLocal = _Session
    monkeypatch.setitem(sys.modules, "db", fake_db)
    monkeypatch.setattr(B, "_redis", lambda: r)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY_RESOLVER_FALLBACK", "200")
    return r


def _reserve(tokens, scope=B.SCOPE_NEWS_CLASSIFY):
    from unittest import mock
    with mock.patch.object(B, "upper_bound_tokens", lambda *a, **k: tokens):
        return B.reserve(0, 0, scope=scope)


def _run_concurrently(fn, n):
    out, lock = [], threading.Lock()
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()          # release them together, so the race is real
        v = fn()
        with lock:
            out.append(v)

    ts = [threading.Thread(target=worker) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


# ============================================================ real Redis, real Lua

def test_the_lua_script_admits_exactly_the_capacity_that_fits(stores):
    allowed = _run_concurrently(lambda: _reserve(300).allowed, 12)
    assert sum(allowed) == 3, f"exactly 3 x 300 fit under 1000, got {sum(allowed)}"
    assert int(stores.get(B.utc_day_key(B.SCOPE_NEWS_CLASSIFY))) == 900


def test_a_child_refused_by_its_sublimit_leaves_the_parent_untouched_in_redis(stores):
    """The rollback loop, executed by Redis rather than asserted as text."""
    r = _reserve(250, scope=B.SCOPE_RESOLVER_FALLBACK)      # sublimit is 200
    assert not r.allowed
    parent = stores.get(B.utc_day_key(B.SCOPE_NEWS_CLASSIFY))
    assert int(parent or 0) == 0, "the parent was charged by a call that was refused"


def test_a_nested_reservation_charges_both_ceilings_in_redis(stores):
    assert _reserve(150, scope=B.SCOPE_RESOLVER_FALLBACK).allowed
    assert int(stores.get(B.utc_day_key(B.SCOPE_RESOLVER_FALLBACK))) == 150
    assert int(stores.get(B.utc_day_key(B.SCOPE_NEWS_CLASSIFY))) == 150, \
        "independent allowances would permit the sum of both ceilings"


def test_a_parent_at_its_ceiling_refuses_the_child_in_redis(stores):
    assert _reserve(1000).allowed
    assert not _reserve(50, scope=B.SCOPE_RESOLVER_FALLBACK).allowed


# ============================================================ real PostgreSQL

def test_the_database_upsert_is_atomic_under_concurrent_transactions(stores, monkeypatch):
    """Twelve real transactions against one row. A read-then-write would over-admit."""
    monkeypatch.setattr(B, "_redis", lambda: None)
    allowed = _run_concurrently(lambda: _reserve(300).allowed, 12)
    assert sum(allowed) == 3, f"the database admitted {sum(allowed)} x 300 under 1000"
    with _Session() as s:
        total = s.execute(text(
            "SELECT reserved FROM llm_budget_day WHERE scope = :sc"),
            {"sc": B.SCOPE_NEWS_CLASSIFY}).scalar()
    assert total == 900


def test_the_database_rolls_back_the_parent_when_the_child_refuses(stores, monkeypatch):
    monkeypatch.setattr(B, "_redis", lambda: None)
    assert not _reserve(250, scope=B.SCOPE_RESOLVER_FALLBACK).allowed
    with _Session() as s:
        parent = s.execute(text(
            "SELECT COALESCE(reserved,0) FROM llm_budget_day WHERE scope = :sc"),
            {"sc": B.SCOPE_NEWS_CLASSIFY}).scalar() or 0
    assert parent == 0


def test_duplicate_settlement_against_real_rows_refunds_once(stores):
    r = _reserve(800)
    B.reconcile(r, 100, outcome=B.OUTCOME_OK)
    B.reconcile(r, 100, outcome=B.OUTCOME_OK)
    assert int(stores.get(B.utc_day_key(B.SCOPE_NEWS_CLASSIFY))) == 100, \
        "the second settlement refunded capacity that was spent"
    with _Session() as s:
        n = s.execute(text(
            "SELECT count(*) FROM llm_reservations WHERE settled_at IS NOT NULL")).scalar()
    assert n == 1


def test_concurrent_settlement_of_one_reservation_refunds_once(stores):
    """Two workers settling the same id — the `settled_at IS NULL` guard is the whole defence."""
    r = _reserve(800)
    _run_concurrently(lambda: B.reconcile(r, 100, outcome=B.OUTCOME_OK), 8)
    assert int(stores.get(B.utc_day_key(B.SCOPE_NEWS_CLASSIFY))) == 100


# ============================================================ partial outage

def test_redis_and_database_callers_cannot_spend_the_same_capacity(stores, monkeypatch):
    """THE PARTIAL-OUTAGE CASE. One caller still sees Redis while another has fallen back to
    the database. If the two counters were independent, each could admit a full ceiling."""
    assert _reserve(600).allowed                     # via Redis
    monkeypatch.setattr(B, "_redis", lambda: None)   # this caller lost Redis
    second = _reserve(600)                           # via the database
    assert not second.allowed, (
        "the database admitted capacity Redis had already reserved — the two counters are "
        "spending the same ceiling twice")


def test_a_redis_reset_is_reseeded_from_the_durable_counter(stores):
    """A flushed counter would otherwise admit a fresh ceiling's worth of calls."""
    assert _reserve(900).allowed
    stores.flushdb()                                  # Redis loses everything
    assert not _reserve(200).allowed, (
        "after a Redis reset the durable ledger must restore what was already reserved")


def test_a_child_that_fits_its_sublimit_but_breaches_the_parent_rolls_back_both(stores):
    """THE CASE THAT DISTINGUISHES the rollback loop from rolling back one key.

    When the refusal happens on the FIRST key, undoing one and undoing all are the same thing —
    a sabotage of the loop passed because every existing test refused there. Here the child is
    charged successfully and the PARENT then refuses, so the child must be given back too.
    """
    assert _reserve(950).allowed                       # parent at 950 of 1000
    before_child = int(stores.get(B.utc_day_key(B.SCOPE_RESOLVER_FALLBACK)) or 0)
    r = _reserve(100, scope=B.SCOPE_RESOLVER_FALLBACK)  # 100 <= 200 sublimit, but 1050 > 1000
    assert not r.allowed, "the parent has no room for this"
    after_child = int(stores.get(B.utc_day_key(B.SCOPE_RESOLVER_FALLBACK)) or 0)
    assert after_child == before_child, (
        "the child ceiling kept capacity for a call that was refused by its parent")


def test_the_same_rollback_holds_in_the_database_path(stores, monkeypatch):
    monkeypatch.setattr(B, "_redis", lambda: None)
    assert _reserve(950).allowed
    assert not _reserve(100, scope=B.SCOPE_RESOLVER_FALLBACK).allowed
    with _Session() as s:
        child = s.execute(text(
            "SELECT COALESCE(reserved,0) FROM llm_budget_day WHERE scope = :sc"),
            {"sc": B.SCOPE_RESOLVER_FALLBACK}).scalar() or 0
    assert child == 0, "the child row kept capacity for a refused call"
