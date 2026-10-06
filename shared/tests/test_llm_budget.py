"""An ENFORCED ceiling: upper-bound, hierarchical, atomic in both backends, settled once.

WHAT EACH ROUND CORRECTED, so a regression reproduces the original defect rather than failing
somewhere nearby:

  1. An email at 400,000 does not prevent call 400,001. A budget that cannot refuse is a
     threshold wearing a budget's name.
  2. A sum-plus-margin fallback cannot bound CONCURRENT spending. With usage at 260,000 of
     300,000 every caller reads "below the margin" and every one proceeds, and in-flight calls
     are not logged yet so the real gap is wider than the measured one.
  3. Reserving an ESTIMATE and charging the shortfall afterwards RECORDS an overshoot; it
     cannot prevent one.
  4. Independent allowances of 300k and 25k permit 325k.
  5. A refund applied twice returns capacity that was spent; a timeout refunded in full
     returns capacity that may well have been charged.
"""
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import llm_budget as B  # noqa: E402


class FakeRedis:
    def __init__(self, fail=False):
        self.store, self.fail, self._lock = {}, fail, threading.Lock()

    def _check(self):
        if self.fail:
            raise RuntimeError("redis down")

    def get(self, k):
        self._check()
        return self.store.get(k)

    def set(self, k, v, ex=None):
        self._check()
        self.store[k] = int(v)
        return True

    def incrby(self, k, n):
        self._check()
        with self._lock:
            self.store[k] = self.store.get(k, 0) + n
            return self.store[k]

    def decrby(self, k, n):
        return self.incrby(k, -n)

    def expire(self, k, s):
        return True

    def eval(self, _script, nkeys, *args):
        """Mirrors the Lua script: every ceiling or none, under one lock."""
        self._check()
        keys = list(args[:nkeys])
        n = int(args[nkeys])
        caps = [int(c) for c in args[nkeys + 1:]]
        with self._lock:
            charged = []
            for k, cap in zip(keys, caps):
                self.store[k] = self.store.get(k, 0) + n
                charged.append(k)
                if self.store[k] > cap:
                    over = self.store[k] - n
                    for c in charged:
                        self.store[c] -= n
                    return [0, k, over]
            return [1, "", 0]


class FakeLedger:
    """The durable reservation ledger, enough to test idempotency and day-attribution."""
    def __init__(self):
        self.rows, self.days, self.lock = {}, {}, threading.Lock()

    def record(self, rid, scope, day, n):
        self.rows[rid] = {"scope": scope, "day": day, "reserved": n, "settled": None}

    def settle(self, rid, tokens, outcome):
        with self.lock:
            row = self.rows.get(rid)
            if row is None or row["settled"] is not None:
                return None                     # idempotent: a second attempt is a no-op
            row["settled"] = tokens
            row["outcome"] = outcome
            return row


@pytest.fixture
def ledger(monkeypatch):
    lg = FakeLedger()
    monkeypatch.setattr(B, "_record_reservation",
                        lambda rid, scope, day, n: lg.record(rid, scope, day, n))
    monkeypatch.setattr(B, "_seed_from_db_if_reset", lambda *a, **k: None)
    return lg


@pytest.fixture
def redis(monkeypatch, ledger):
    r = FakeRedis()
    monkeypatch.setattr(B, "_redis", lambda: r)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY_RESOLVER_FALLBACK", "200")
    return r


def _res(tokens, scope=B.SCOPE_NEWS_CLASSIFY):
    """Reserve exactly `tokens` of upper bound, so the arithmetic reads clearly."""
    with mock.patch.object(B, "upper_bound_tokens", lambda *a, **k: tokens):
        return B.reserve(0, 0, scope=scope)


# ============================================================ upper bound, not estimate

def test_the_reservation_is_an_upper_bound_including_the_maximum_output():
    """Charging a shortfall afterwards records an overshoot; it cannot prevent one."""
    n = B.upper_bound_tokens(prompt_chars=1000, max_output_tokens=1600)
    assert n >= 1000 / 4 + 1600, "the configured max output is reserved in full"
    assert n > B.upper_bound_tokens(1000, 0), "output capacity is part of the bound"


def test_the_input_estimate_is_pessimistic_on_purpose():
    """An underestimate here is the exact failure the upper bound exists to remove."""
    assert B.upper_bound_tokens(1000, 0) > 1000 / 4, "more conservative than ~4 chars/token"


# ============================================================ refusal and atomicity

def test_the_ceiling_actually_refuses(redis):
    assert _res(600).allowed
    assert _res(600).allowed is False, "an alert would have let this through"


def test_a_refused_reservation_does_not_consume_capacity(redis):
    _res(600)
    assert not _res(900).allowed
    assert _res(400).allowed, "the refused 900 was returned"


def test_genuinely_concurrent_callers_cannot_all_pass(redis):
    """Threads, not a sequential loop: the race is the thing being tested."""
    results, lock = [], threading.Lock()

    def worker():
        r = _res(300)
        with lock:
            results.append(r.allowed)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 3, f"exactly 3 x 300 fit under 1000, got {sum(results)}"


# ============================================================ hierarchy

def test_a_nested_scope_charges_its_parent_too(redis):
    """Independent allowances of 300k and 25k would permit 325k."""
    assert _res(150, scope=B.SCOPE_RESOLVER_FALLBACK).allowed
    assert B.scope_chain(B.SCOPE_RESOLVER_FALLBACK) == [
        B.SCOPE_RESOLVER_FALLBACK, B.SCOPE_NEWS_CLASSIFY]
    # The parent was charged as well, so only 850 of the parent's 1000 remains.
    assert _res(900, scope=B.SCOPE_NEWS_CLASSIFY).allowed is False


def test_a_parent_at_its_ceiling_refuses_the_child(redis):
    assert _res(1000, scope=B.SCOPE_NEWS_CLASSIFY).allowed
    assert _res(50, scope=B.SCOPE_RESOLVER_FALLBACK).allowed is False, \
        "the sublimit cannot create capacity the parent does not have"


def test_a_child_refused_by_its_sublimit_does_not_leave_the_parent_charged(redis):
    """All ceilings or none — otherwise a refused child silently consumes the parent."""
    assert _res(250, scope=B.SCOPE_RESOLVER_FALLBACK).allowed is False   # sublimit is 200
    assert _res(1000, scope=B.SCOPE_NEWS_CLASSIFY).allowed, \
        "the parent must be untouched by the refused child"


def test_the_fallback_allowance_is_smaller_than_the_parent(monkeypatch):
    monkeypatch.delenv("LLM_BUDGET_NEWS_CLASSIFY", raising=False)
    monkeypatch.delenv("LLM_BUDGET_NEWS_CLASSIFY_RESOLVER_FALLBACK", raising=False)
    assert 0 < B.budget_for(B.SCOPE_RESOLVER_FALLBACK) < B.budget_for(B.SCOPE_NEWS_CLASSIFY)


# ============================================================ settlement

class FakeSession:
    """Just enough of a session for `reconcile` to run its real SQL path."""
    def __init__(self, rows, counters, updates):
        self.rows, self.counters, self.updates = rows, counters, updates
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        self.committed = True

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = params or {}
        if sql.startswith("UPDATE llm_reservations"):
            row = self.rows.get(params["i"])
            # The REAL guard: `AND settled_at IS NULL` is what makes this idempotent.
            if row is None or (row.get("settled") is not None
                               and "settled_at IS NULL" in sql):
                return _NoRow()
            row["settled"] = params["t"]
            row["outcome"] = params["o"]
            return _OneRow((row["reserved"], date.fromisoformat(row["day"])))
        if sql.startswith("UPDATE llm_budget_day"):
            key = (params["sc"], params["day"])
            self.counters[key] = max(0, self.counters.get(key, 0) + params["d"])
            # WHICH scopes were settled, separately from the clamped totals — the real SQL
            # clamps at zero, so a total of 0 cannot show whether the row was touched.
            self.updates.append((params["sc"], params["day"], params["d"]))
            return _NoRow()
        return _NoRow()


class _NoRow:
    def first(self):
        return None


class _OneRow:
    def __init__(self, v):
        self.v = v

    def first(self):
        return self.v


@pytest.fixture
def real_reconcile(monkeypatch):
    """Drive the ACTUAL reconcile(), not a reimplementation of it.

    Three sabotage runs passed against an earlier version of these tests because they called a
    FakeLedger that mirrored the logic instead of the function that implements it — the doubles
    were being tested, not the code.
    """
    rows, counters, updates = {}, {}, []
    redis_obj = FakeRedis()

    def _record(rid, scope, day, n):
        rows[rid] = {"scope": scope, "day": day, "reserved": n, "settled": None}

    monkeypatch.setattr(B, "_record_reservation", _record)
    monkeypatch.setattr(B, "_seed_from_db_if_reset", lambda *a, **k: None)
    monkeypatch.setattr(B, "_redis", lambda: redis_obj)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY_RESOLVER_FALLBACK", "200")

    import types
    fake_db = types.ModuleType("db")
    fake_db.SessionLocal = lambda: FakeSession(rows, counters, updates)
    monkeypatch.setitem(sys.modules, "db", fake_db)
    return types.SimpleNamespace(rows=rows, counters=counters, updates=updates,
                                 redis=redis_obj)


def test_an_overestimate_is_returned_on_settlement(real_reconcile):
    r = _res(800)
    B.reconcile(r, 100, outcome=B.OUTCOME_OK)
    key = (B.SCOPE_NEWS_CLASSIFY, r.day)
    assert real_reconcile.counters[key] == 0, "the 700 difference was returned"
    assert real_reconcile.redis.store[f"llm:budget:{B.SCOPE_NEWS_CLASSIFY}:{r.day}"] == 100


def test_settling_twice_does_not_refund_twice(real_reconcile):
    """A duplicate settlement would return capacity that was spent."""
    r = _res(800)
    B.reconcile(r, 100, outcome=B.OUTCOME_OK)
    before = real_reconcile.redis.store[f"llm:budget:{B.SCOPE_NEWS_CLASSIFY}:{r.day}"]
    B.reconcile(r, 100, outcome=B.OUTCOME_OK)
    after = real_reconcile.redis.store[f"llm:budget:{B.SCOPE_NEWS_CLASSIFY}:{r.day}"]
    assert before == after == 100, "the second settlement is a no-op"


def test_an_ambiguous_outcome_is_not_refunded(real_reconcile):
    """A timeout may have been served and charged; refunding it lets the ceiling pass."""
    r = _res(800)
    B.reconcile(r, None, outcome=B.OUTCOME_AMBIGUOUS)
    assert real_reconcile.redis.store[f"llm:budget:{B.SCOPE_NEWS_CLASSIFY}:{r.day}"] == 800, \
        "the full reservation stands"


def test_a_clean_failure_does_return_its_capacity(real_reconcile):
    r = _res(800)
    B.reconcile(r, 0, outcome=B.OUTCOME_FAILED)
    assert real_reconcile.redis.store[f"llm:budget:{B.SCOPE_NEWS_CLASSIFY}:{r.day}"] == 0


def test_settlement_returns_to_the_day_the_capacity_came_from(real_reconcile, monkeypatch):
    """A call started at 23:59:58 settles after midnight — against YESTERDAY's ceiling.

    The reservation is taken on a PAST day (both the key builder and the day string are moved,
    because reconcile reads the day from the ledger and must not substitute today's). An earlier
    version of this test moved only `_day_str` and so proved nothing: `utc_day_key` reads the
    clock itself, and the sabotage it was meant to catch passed."""
    past, today = "2099-01-01", datetime.now(timezone.utc).date().isoformat()
    clock = {"day": past}
    # A FLIPPABLE CLOCK, not monkeypatch.undo() — undoing would also revert the fixture's own
    # patches, so reconcile would find no ledger row and the test would pass for that reason
    # instead of the one it is about.
    monkeypatch.setattr(B, "_day_str", lambda now=None: clock["day"])
    monkeypatch.setattr(B, "utc_day_key",
                        lambda scope, now=None: f"llm:budget:{scope}:{clock['day']}")
    r = _res(800)
    assert r.day == past
    clock["day"] = today            # midnight passes while the request is in flight
    B.reconcile(r, 100, outcome=B.OUTCOME_OK)
    assert real_reconcile.redis.store[f"llm:budget:{B.SCOPE_NEWS_CLASSIFY}:{past}"] == 100
    assert real_reconcile.redis.store.get(f"llm:budget:{B.SCOPE_NEWS_CLASSIFY}:{today}") is None, \
        "today's ceiling must not be credited with a previous day's refund"
    assert any(u[1] == past for u in real_reconcile.updates), \
        "the durable counter was settled against the day the capacity came from"
    assert not any(u[1] == today for u in real_reconcile.updates)


def test_a_nested_settlement_returns_capacity_to_every_ceiling_it_charged(real_reconcile):
    """BOTH backends. Checking only Redis left the durable counter's own loop uncovered — a
    sabotage that settled just the child scope in the database passed unnoticed."""
    r = _res(150, scope=B.SCOPE_RESOLVER_FALLBACK)
    B.reconcile(r, 50, outcome=B.OUTCOME_OK)
    for sc in (B.SCOPE_RESOLVER_FALLBACK, B.SCOPE_NEWS_CLASSIFY):
        assert real_reconcile.redis.store[f"llm:budget:{sc}:{r.day}"] == 50, \
            f"{sc} kept Redis capacity the call did not use"
        assert (sc, r.day, -100) in real_reconcile.updates, \
            f"{sc} was not settled in the durable counter"


def test_the_reservation_lua_rolls_back_every_ceiling_it_charged():
    """STRUCTURAL, and labelled as such: the script runs inside Redis, so this asserts the
    rollback loop exists rather than executing it. The behavioural counterpart is
    `test_a_child_refused_by_its_sublimit_does_not_leave_the_parent_charged`, which runs
    against a fake that mirrors the same contract."""
    assert "for j = 1, i do redis.call('DECRBY', KEYS[j], n) end" in B._RESERVE_LUA, \
        "rolling back only the failing key leaves earlier ceilings charged"


# ============================================================ degraded infrastructure

def test_without_redis_the_database_reserves_atomically(monkeypatch, ledger):
    """NOT a sum with a margin: with usage at 260,000 of 300,000 every concurrent caller
    reads 'below the margin' and every one proceeds."""
    monkeypatch.setattr(B, "_redis", lambda: None)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    calls = []

    def fake_db_reserve(chain, caps, n, day):
        calls.append((tuple(chain), n))
        return (True, "", 0) if sum(c[1] for c in calls) <= caps[-1] else (False, chain[0], caps[0])

    monkeypatch.setattr(B, "_db_reserve", fake_db_reserve)
    assert _res(600).enforcement == "database"
    assert _res(600).allowed is False, "the database must refuse, not merely measure"


def test_the_database_reservation_is_a_conditional_upsert_not_a_read_then_write():
    """Read-then-write cannot be atomic across callers; the WHERE clause is what makes it so.

    The "no margin" half is checked against CODE, not source text: the function's docstring
    explains the margin it replaced, and an earlier version of this test matched that
    explanation and failed on its own prose — the third time this session that a test matched
    a comment describing the thing it was asserting the absence of."""
    import ast
    src = (Path(__file__).resolve().parents[1] / "common" / "llm_budget.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_db_reserve")
    body = [n for n in fn.body if not (isinstance(n, ast.Expr)
                                       and isinstance(n.value, ast.Constant))]
    code = "\n".join(ast.unparse(n) for n in body)
    assert "ON CONFLICT (scope, day) DO UPDATE" in code
    assert "WHERE llm_budget_day.reserved + :n <= :cap" in code
    assert "RETURNING reserved" in code
    assert "margin" not in code.lower(), "a margin is risk reduction, not a bound"
    assert "_DB_FALLBACK_MARGIN" not in src, "the margin constant is gone entirely"


def test_with_neither_counter_it_defers_rather_than_spending_blind(monkeypatch, ledger):
    monkeypatch.setattr(B, "_redis", lambda: None)
    monkeypatch.setattr(B, "_db_reserve",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    r = _res(100)
    assert not r.allowed and r.enforcement == "none"
    assert "cannot be bounded" in r.reason


def test_a_redis_reset_is_detected_before_new_calls_are_admitted(monkeypatch):
    """A flushed counter would otherwise admit a fresh ceiling's worth of calls."""
    r = FakeRedis()
    monkeypatch.setattr(B, "_redis", lambda: r)
    monkeypatch.setattr(B, "_record_reservation", lambda *a, **k: None)
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "1000")
    seeded = {}
    monkeypatch.setattr(B, "_seed_from_db_if_reset",
                        lambda rr, scope, day: seeded.setdefault(scope, True))
    _res(100)
    assert seeded.get(B.SCOPE_NEWS_CLASSIFY), "the durable ledger is consulted first"


def test_the_reseed_compares_redis_against_the_durable_ledger():
    src = (Path(__file__).resolve().parents[1] / "common" / "llm_budget.py").read_text()
    seg = src[src.index("def _seed_from_db_if_reset"):src.index("def _db_reserve")]
    assert "if durable > cur:" in seg
    assert "redis_reseeded_after_reset" in seg


# ============================================================ stated, not inherited

def test_the_day_is_the_utc_calendar_day_and_says_so():
    k = B.utc_day_key(B.SCOPE_NEWS_CLASSIFY,
                      datetime(2026, 10, 6, 23, 30, tzinfo=timezone.utc))
    assert k.endswith("2026-10-06")
    assert B.status()["day_basis"] == "UTC calendar day"


def test_every_reservation_carries_an_identity_and_its_day(redis):
    r = _res(100)
    assert len(r.reservation_id) >= 16
    assert r.day == datetime.now(timezone.utc).date().isoformat()


def test_a_zero_budget_means_no_ceiling_not_no_capacity(monkeypatch, ledger):
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY", "0")
    monkeypatch.setenv("LLM_BUDGET_NEWS_CLASSIFY_RESOLVER_FALLBACK", "0")
    r = _res(10_000_000)
    assert r.allowed and "no ceiling configured" in r.reason
