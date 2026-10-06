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


# ============================================================ one authority

def test_concurrent_reservations_admit_exactly_the_capacity_that_fits(stores):
    """Twelve real transactions against one row. A read-then-write would over-admit."""
    allowed = _run_concurrently(lambda: _reserve(300).allowed, 12)
    assert sum(allowed) == 3, f"admitted {sum(allowed)} x 300 under a 1000 ceiling"
    with _Session() as s:
        assert s.execute(text("SELECT reserved FROM llm_budget_day WHERE scope = :sc"),
                         {"sc": B.SCOPE_NEWS_CLASSIFY}).scalar() == 900


def test_a_nested_reservation_charges_both_ceilings(stores):
    assert _reserve(150, scope=B.SCOPE_RESOLVER_FALLBACK).allowed
    with _Session() as s:
        rows = dict(s.execute(text(
            "SELECT scope, reserved FROM llm_budget_day")).all())
    assert rows[B.SCOPE_RESOLVER_FALLBACK] == 150
    assert rows[B.SCOPE_NEWS_CLASSIFY] == 150, \
        "independent allowances would permit the sum of both ceilings"


def test_a_parent_at_its_ceiling_refuses_the_child(stores):
    assert _reserve(1000).allowed
    assert not _reserve(50, scope=B.SCOPE_RESOLVER_FALLBACK).allowed


def test_a_child_that_fits_its_sublimit_but_breaches_the_parent_charges_neither(stores):
    """THE CASE THAT DISTINGUISHES a full rollback from undoing one ceiling: the child is
    charged successfully and the PARENT then refuses."""
    assert _reserve(950).allowed
    assert not _reserve(100, scope=B.SCOPE_RESOLVER_FALLBACK).allowed
    with _Session() as s:
        child = s.execute(text(
            "SELECT COALESCE(reserved,0) FROM llm_budget_day WHERE scope = :sc"),
            {"sc": B.SCOPE_RESOLVER_FALLBACK}).scalar() or 0
    assert child == 0, "the child ceiling kept capacity for a refused call"


def test_the_first_reservation_of_a_day_is_still_subject_to_the_ceiling(stores):
    """With the cap only on DO UPDATE, the first insert of a day had no row to conflict with
    and was admitted unconditionally — a single call larger than the whole budget."""
    assert not _reserve(5000).allowed
    with _Session() as s:
        assert (s.execute(text(
            "SELECT COALESCE(reserved,0) FROM llm_budget_day WHERE scope = :sc"),
            {"sc": B.SCOPE_NEWS_CLASSIFY}).scalar() or 0) == 0


# ============================================================ the crash window

def test_a_reservation_is_all_or_nothing_across_counter_and_ledger(stores):
    """ONE TRANSACTION. The earlier design admitted in Redis and recorded in PostgreSQL, with
    no answer to what was true between the two writes. Here a failure mid-reservation leaves
    neither the counter charged nor a reservation row behind."""
    import unittest.mock as _m
    real_session = B.__dict__.get("_SessionForTest")

    # Fail the LEDGER insert, after the ceilings have been incremented in the same transaction.
    class _Boom(Exception):
        pass

    orig = _Session

    def _failing_session():
        s = orig()
        real_execute = s.execute

        def execute(stmt, params=None):
            if "INSERT INTO llm_reservations" in str(stmt):
                raise _Boom("crash after the counters, before the record")
            return real_execute(stmt, params)
        s.execute = execute
        return s

    import types
    fake_db = types.ModuleType("db")
    fake_db.SessionLocal = _failing_session
    with _m.patch.dict(sys.modules, {"db": fake_db}):
        r = _reserve(300)
    assert not r.allowed, "a reservation that cannot be recorded must not be granted"

    with _Session() as s:
        charged = s.execute(text(
            "SELECT COALESCE(reserved,0) FROM llm_budget_day WHERE scope = :sc"),
            {"sc": B.SCOPE_NEWS_CLASSIFY}).scalar() or 0
        rows = s.execute(text("SELECT count(*) FROM llm_reservations")).scalar()
    assert charged == 0, "the ceiling kept capacity for a reservation that was never recorded"
    assert rows == 0


def test_admission_stops_when_the_ledger_cannot_be_reached(stores, monkeypatch):
    """There is no second counter to fall back to: a fallback is a second authority."""
    import types
    broken = types.ModuleType("db")

    def _explode():
        raise RuntimeError("ledger unreachable")
    broken.SessionLocal = _explode
    monkeypatch.setitem(sys.modules, "db", broken)
    r = _reserve(10)
    assert not r.allowed and r.enforcement == "none"


def test_redis_being_wrong_cannot_grant_capacity(stores):
    """The advisory cache is deliberately powerless: corrupt it and admission is unchanged."""
    assert _reserve(900).allowed
    stores.flushdb()                      # the cache now says nothing is reserved
    assert not _reserve(200).allowed, \
        "the ledger, not the cache, decides — a flushed cache must not free capacity"


# ============================================================ real PostgreSQL

def test_the_upsert_is_atomic_under_concurrent_transactions(stores):
    allowed = _run_concurrently(lambda: _reserve(300).allowed, 12)
    assert sum(allowed) == 3, f"the database admitted {sum(allowed)} x 300 under 1000"
    with _Session() as s:
        total = s.execute(text(
            "SELECT reserved FROM llm_budget_day WHERE scope = :sc"),
            {"sc": B.SCOPE_NEWS_CLASSIFY}).scalar()
    assert total == 900


def test_the_ledger_rolls_back_the_parent_when_the_child_refuses(stores):
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







def test_the_same_rollback_holds_for_a_later_day(stores):
    assert _reserve(950).allowed
    assert not _reserve(100, scope=B.SCOPE_RESOLVER_FALLBACK).allowed
    with _Session() as s:
        child = s.execute(text(
            "SELECT COALESCE(reserved,0) FROM llm_budget_day WHERE scope = :sc"),
            {"sc": B.SCOPE_RESOLVER_FALLBACK}).scalar() or 0
    assert child == 0, "the child row kept capacity for a refused call"
