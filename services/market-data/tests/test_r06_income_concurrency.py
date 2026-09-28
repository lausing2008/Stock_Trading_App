"""R06 (2026-09-24 follow-up audit): correct lease RELEASE does not prevent concurrent WORK.

DA-07 gave the options-income step a unique token and a compare-and-delete release, so run A
can no longer delete run B's lock. That was correct and it is not what this finding is about.

THE REMAINING DEFECT. The TTL is a fixed 1,800 seconds with no renewal and no fencing. A run
that is merely SLOW — a stalled provider call, an over-long ranking pass, a paused container —
loses its lease while still executing. B then legitimately acquires it and starts, and both are
inside the same read/check/write on portfolio cash, collateral and positions. Every limit in
open_income_positions() (available cash, max_positions, the per-symbol cap, the daily entry
budget) is evaluated in Python against a snapshot read at the top, so two workers holding the
same snapshot both pass all of them and both open. The audit's probe shows B entering its work
callback while A is still active; it does not claim a production run has exceeded 30 minutes.

THREE GUARDS, and the Redis one is the weakest.

  RENEWAL — a slow-but-alive run keeps its lease instead of silently losing it.
  ABORT ON LOSS — a run that HAS lost it stops before committing.
  THE DATABASE — a FOR UPDATE row lock on the portfolio plus a unique intent key on
    (portfolio_id, option_symbol, entry_date). This is the only guard that still holds when a
    worker is paused mid-transaction, killed outright, or loses its network, because none of
    those states can be observed from Redis. The audit is explicit: "The database must enforce
    the economic invariant even if a worker pauses or dies", and "Test database results, not
    just whether another worker's Redis key survives."

So the tests below run against a REAL database with the REAL models and assert on ROWS AND
CASH, not on lock keys. SQLite has no row-level locking, so `_lock_portfolio_row` degrades to a
warning here and the INTENT KEY is what does the work — which is the point: they are
independent guards, and the one being exercised is the one that survives a dead worker.
"""
import json
import pathlib
import re
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from src.services import options_income_engine as E

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_PROBE = pathlib.Path(__file__).resolve().parent / "_r06_db_probe.py"


def _probe(scenario: str) -> dict:
    """Run one scenario in a clean interpreter and return the resulting DATABASE STATE.

    Why a subprocess: this service's conftest stubs `sqlalchemy` itself (along with `db` and
    `psycopg2`), because almost nothing here imports locally otherwise. Inside the suite
    `select()` is a MagicMock, so open_income_positions() cannot reach a real session however
    the test is written. R06's acceptance is explicit that database results are what must be
    checked, so the probe runs with the REAL sqlalchemy, the REAL shared/db/models.py and a
    real SQLite database, and reports rows and cash back as JSON.

    See _r06_db_probe.py for what SQLite can and cannot model here.
    """
    proc = subprocess.run([sys.executable, str(_PROBE), scenario],
                          capture_output=True, text=True, timeout=120)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("R06_JSON ")), None)
    assert line, (f"probe {scenario} produced no result\n"
                  f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
    return json.loads(line[len("R06_JSON "):])


# ── The acceptance: two workers, database results ────────────────────────────

def test_two_workers_cannot_open_the_same_intent_twice():
    """THE CORE R06 CASE. Worker B decided on this candidate against a snapshot taken before A
    committed. Every Python check in open_income_positions() passes for B — ample cash, the
    position count under its cap, room in the daily budget — so nothing but the schema stops
    it."""
    r = _probe("two_workers_same_intent")
    assert r["first"] == 1
    assert r["second"] == 0
    assert r["rows"] == ["OPT-A"], f"the same intent was opened {len(r['rows'])} times"


def test_the_second_worker_does_not_double_reserve_cash():
    """The audit's phrasing exactly: "must neither double-reserve cash nor duplicate the same
    intent". Cash is the half a row count alone would not catch."""
    r = _probe("two_workers_same_intent")
    # 50,000 - 9,000 collateral + 150 premium, debited exactly once.
    assert r["cash"] == pytest.approx(41_150.0)


def test_a_rejected_intent_does_not_lose_the_positions_opened_alongside_it():
    """The SAVEPOINT. Without one the unique-key rejection aborts the whole transaction, and a
    single duplicate turns into a lost batch — a worse failure than the one being fixed."""
    r = _probe("savepoint_keeps_the_batch")
    assert r["opened"] == 2
    assert r["rows"] == ["DUP", "FRESH-1", "FRESH-2"]


def test_cash_is_moved_only_for_intents_that_actually_landed():
    """Debiting before the insert would charge the portfolio for a position the intent key then
    refused — money gone, nothing held. Two fresh positions at 9,000 collateral / 150 premium
    on top of the first, and nothing at all for the rejected duplicate."""
    r = _probe("savepoint_keeps_the_batch")
    assert r["cash"] == pytest.approx(50_000.0 - 3 * 9_000.0 + 3 * 150.0)


def test_the_same_contract_on_a_different_day_is_a_different_intent():
    """The key is per-DAY on purpose: rolling into the same contract next session is legitimate
    work, not a duplicate. A key without the date would silently block it forever."""
    r = _probe("same_contract_next_day")
    assert r["opened"] == 1
    assert r["rows"] == ["SAME", "SAME"]


def test_two_portfolios_may_hold_the_same_contract():
    """The key is per-PORTFOLIO. Two books independently selling the same strike is normal, and
    a key that blocked it would be a bug dressed as a guard."""
    r = _probe("two_portfolios_same_contract")
    assert (r["one"], r["two"]) == (1, 1)
    assert r["p1"]["rows"] == ["SHARED"] and r["p2"]["rows"] == ["SHARED"]


# ── A stale worker attempting to commit ──────────────────────────────────────

def test_a_worker_that_lost_its_lease_commits_nothing():
    """The audit's third scenario, checked against the DATABASE. The work was decided against a
    snapshot another worker has since changed; committing now would write cash and positions on
    someone else's authority."""
    r = _probe("lease_lost_before_commit")
    assert r["opened"] == 0
    assert r["rows"] == [], "a worker that lost its lease still wrote positions"
    assert r["cash"] == pytest.approx(50_000.0), "a worker that lost its lease still moved cash"


def test_a_worker_that_never_held_the_lease_does_no_work_at_all():
    r = _probe("lease_never_held")
    assert r["opened"] == 0
    assert r["rows"] == []


def test_a_held_lease_does_not_interfere():
    """The guard must not be so tight that the normal path stops working."""
    r = _probe("lease_held_throughout")
    assert r["opened"] == 1
    assert r["rows"] == ["OPT-A"]
    assert r["cash"] == pytest.approx(41_150.0)


# ── Recognising the duplicate, and ONLY the duplicate ────────────────────────

class IntegrityError(Exception):
    """Stands in for sqlalchemy.exc.IntegrityError, which cannot be imported here — this
    service's conftest stubs the whole `sqlalchemy` package.

    Named EXACTLY as the real class is, because _is_duplicate_intent() matches on the type name
    precisely so that it needs no import. A stand-in called `_IntegrityError` was not
    recognised, which is the check doing its job: the name is the contract."""


class UniqueViolation(Exception):
    """psycopg2's own class name, which appears as the __cause__ under SQLAlchemy's wrapper."""


def test_a_unique_violation_is_recognised_on_postgres_and_sqlite_wording():
    assert E._is_duplicate_intent(IntegrityError(
        'duplicate key value violates unique constraint "uq_options_income_intent"'))
    assert E._is_duplicate_intent(IntegrityError(
        "UNIQUE constraint failed: options_income_positions.portfolio_id"))


def test_it_looks_through_the_drivers_chained_cause():
    """SQLAlchemy wraps the driver's error; the recognisable one is often the __cause__."""
    driver = UniqueViolation("duplicate key value violates unique constraint")
    wrapper = IntegrityError("(psycopg2.errors.UniqueViolation) ...")
    wrapper.__cause__ = driver
    assert E._is_duplicate_intent(wrapper)


def test_another_integrity_violation_is_NOT_treated_as_a_duplicate():
    """A NOT NULL or foreign-key violation is a real defect. Filing it as "someone else got
    there first" would skip the candidate silently and leave the cause undiagnosed — the
    swallowed-exception failure mode this repo has been bitten by repeatedly."""
    assert not E._is_duplicate_intent(IntegrityError(
        'null value in column "strike" violates not-null constraint'))
    assert not E._is_duplicate_intent(IntegrityError(
        'insert or update on table "x" violates foreign key constraint'))


def test_an_unrelated_exception_is_not_a_duplicate():
    for exc in (ValueError("unique"), RuntimeError("database is locked"), KeyError("symbol")):
        assert not E._is_duplicate_intent(exc)


def test_a_self_referential_exception_chain_terminates():
    """`raise ... from ...` cycles are constructible, and an unbounded walk would spin forever
    inside the entry loop.

    Run on a worker thread with a join timeout rather than called directly. Removing the
    visited-set guard makes this loop NEVER RETURN, and a test that hangs is a worse signal
    than one that fails: it blocks the suite instead of reporting. Verified by sabotage — the
    direct-call version of this test hung the whole run.
    """
    a, b = IntegrityError("a"), IntegrityError("b")
    a.__cause__ = b
    b.__cause__ = a

    result = {}
    worker = threading.Thread(target=lambda: result.update(v=E._is_duplicate_intent(a)),
                              daemon=True)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "the exception-chain walk did not terminate"
    assert result["v"] is False


def test_a_real_database_error_is_re_raised_rather_than_skipped():
    """The call site must not swallow what _is_duplicate_intent() declines to recognise."""
    import inspect

    src = inspect.getsource(E.open_income_positions)
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "if not _is_duplicate_intent(exc):" in code
    assert "raise" in code[code.index("_is_duplicate_intent(exc)"):][:200]


# ── The lease itself ─────────────────────────────────────────────────────────

class _FakeRedis:
    """Real GET/SET/EXPIRE semantics for the renew and release scripts."""

    def __init__(self):
        self.store, self.ttls, self.fail = {}, {}, False

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key], self.ttls[key] = value, ex
        return True

    def eval(self, script, _n, key, *args):
        if self.fail:
            raise RuntimeError("redis down")
        cur = self.store.get(key)
        if script == E._INCOME_LOCK_RENEW_LUA:
            if cur == args[0]:
                self.ttls[key] = int(args[1])
                return 1
            return 0
        if script == E._INCOME_LOCK_RELEASE_LUA:
            if cur == args[0]:
                del self.store[key]
                return 1
            return 0
        raise AssertionError("unknown script")


def test_renewal_extends_only_the_holders_own_lease():
    r = _FakeRedis()
    with patch.object(E, "_get_income_redis", lambda: r):
        r.set(E._INCOME_STEP_LOCK_KEY, "mine", nx=True, ex=10)
        assert E._renew_income_lock("mine") is True
        assert r.ttls[E._INCOME_STEP_LOCK_KEY] == E._INCOME_STEP_LOCK_TTL
        assert E._renew_income_lock("someone-else") is False


def test_renewal_of_an_expired_lease_fails_rather_than_recreating_it():
    """EXPIRE on a missing key is a no-op in Redis, so this must not resurrect a lease another
    worker may already have taken."""
    r = _FakeRedis()
    with patch.object(E, "_get_income_redis", lambda: r):
        assert E._renew_income_lock("mine") is False
        assert E._INCOME_STEP_LOCK_KEY not in r.store


def test_the_lease_marks_itself_lost_when_renewal_is_refused():
    r = _FakeRedis()
    with patch.object(E, "_get_income_redis", lambda: r), \
         patch.object(E, "_INCOME_LOCK_RENEW_INTERVAL", 0.01):
        r.set(E._INCOME_STEP_LOCK_KEY, "theirs", nx=True, ex=10)   # someone else owns it
        lease = E.IncomeLease("mine").start()
        try:
            deadline = time.time() + 2
            while lease.is_held() and time.time() < deadline:
                time.sleep(0.01)
            assert not lease.is_held(), "a refused renewal must mark the lease lost"
        finally:
            lease.stop()


def test_a_transient_redis_error_does_not_abort_a_healthy_run():
    """A blip is not proof the lease was lost, and treating it as such would abort a good run
    every time Redis hiccups. The TTL is the backstop."""
    r = _FakeRedis()
    r.fail = True
    with patch.object(E, "_get_income_redis", lambda: r), \
         patch.object(E, "_INCOME_LOCK_RENEW_INTERVAL", 0.01):
        lease = E.IncomeLease("mine").start()
        try:
            time.sleep(0.1)
            assert lease.is_held()
        finally:
            lease.stop()


def test_a_lost_lease_never_returns_to_held():
    """Re-acquiring the key would not make the snapshot the work was decided against correct
    again."""
    r = _FakeRedis()
    with patch.object(E, "_get_income_redis", lambda: r), \
         patch.object(E, "_INCOME_LOCK_RENEW_INTERVAL", 0.01):
        r.set(E._INCOME_STEP_LOCK_KEY, "theirs", nx=True, ex=10)
        lease = E.IncomeLease("mine").start()
        try:
            deadline = time.time() + 2
            while lease.is_held() and time.time() < deadline:
                time.sleep(0.01)
            assert not lease.is_held()
            r.store[E._INCOME_STEP_LOCK_KEY] = "mine"     # we somehow own it again
            time.sleep(0.05)
            assert not lease.is_held()
        finally:
            lease.stop()


def test_the_renewal_interval_leaves_room_for_failures():
    """Renewing at the TTL would mean one missed renewal loses the lease. A third leaves room
    for two consecutive failures."""
    assert E._INCOME_LOCK_RENEW_INTERVAL <= E._INCOME_STEP_LOCK_TTL / 3
    assert E._INCOME_LOCK_RENEW_INTERVAL > 0


def test_the_renewal_thread_is_a_daemon_and_is_stopped():
    """A non-daemon renewal thread would hold the process open after the step finished."""
    lease = E.IncomeLease("t")
    with patch.object(E, "_INCOME_LOCK_RENEW_INTERVAL", 60):
        lease.start()
        assert lease._thread.daemon
        lease.stop()
        assert not lease._thread.is_alive()


# ── Wiring ───────────────────────────────────────────────────────────────────

def test_the_step_starts_a_lease_and_stops_it():
    import inspect

    src = inspect.getsource(E.run_options_income_step)
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "IncomeLease(token).start()" in code
    assert "lease.stop()" in code
    assert "_run_options_income_step_locked(lease=lease)" in code
    assert code.index("lease.stop()") < code.index("_release_income_lock(token)"), \
        "stop renewing before releasing, or the renewal can outlive the lock"


def test_the_settlement_path_is_locked_too():
    """FOUND 2026-09-28 (pre-deployment audit). R06 locked `open_income_positions` and left
    `settle_expired_positions` — which mutates the SAME `current_cash` — unguarded. It is a
    read/modify/write like any other: it reads cash, adds released collateral and P&L, and
    writes the total back. The audit's probe interleaves an entry's debit between that read and
    that write, and the debit is lost.

    Asserted structurally, and the reason is worth stating rather than hiding: SQLite has no
    row-level locking, so the probe database cannot stage two writers serialising. The REFRESH
    half — what repairs the arithmetic once the lock is held — is covered on the helper itself
    by test_the_row_lock_refreshes_the_stale_orm_snapshot. What is left for this test is that
    the settlement path calls it at all, before it reads anything.

    This gap survived the first sabotage pass: removing the lock from settle_expired_positions
    left every test green.
    """
    import inspect

    code = "\n".join(ln.split("#", 1)[0]
                     for ln in inspect.getsource(E.settle_expired_positions).splitlines())
    assert "_lock_portfolio_row(session, portfolio)" in code, \
        "the settlement path mutates portfolio cash without taking the row lock"
    assert code.index("_lock_portfolio_row(") < code.index("open_positions = session.execute("), \
        "the lock must be held before the positions and the cash are read"


def test_both_cash_mutating_paths_take_the_same_lock():
    """One helper, both writers. A second locking idiom in either place is how the two paths
    drift into disagreeing about what is protected."""
    import inspect

    for fn in (E.open_income_positions, E.settle_expired_positions):
        code = "\n".join(ln.split("#", 1)[0] for ln in inspect.getsource(fn).splitlines())
        assert "_lock_portfolio_row(session, portfolio)" in code, fn.__name__


def test_the_portfolio_row_is_locked_before_anything_is_read_from_it():
    import inspect

    src = inspect.getsource(E.open_income_positions)
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "_lock_portfolio_row(session, portfolio)" in code
    assert code.index("_lock_portfolio_row(") < code.index("portfolio.config"), \
        "the config/cash snapshot must be read AFTER the lock, not before"


def test_the_row_lock_refreshes_the_stale_orm_snapshot():
    """Serializing correctly and then spending a number read before waiting is the same bug in
    slower motion."""
    import inspect

    code = inspect.getsource(E._lock_portfolio_row)
    assert "with_for_update()" in code
    assert "session.refresh(portfolio)" in code


def test_the_intent_key_is_declared_on_the_model_and_created_on_existing_tables():
    """create_all() only creates MISSING TABLES, so a declaration alone never reaches a
    deployed database — a past incident in this repo, not a hypothetical."""
    models_src = (_ROOT / "shared" / "db" / "models.py").read_text()
    assert 'Index("uq_options_income_intent", "portfolio_id", "option_symbol", "entry_date",' \
        in models_src
    assert "unique=True" in models_src
    session_src = (_ROOT / "shared" / "db" / "session.py").read_text()
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_options_income_intent" in session_src


def test_a_failed_index_creation_does_not_block_startup():
    """A platform that refuses to boot because a concurrency guard could not be added is a
    worse outcome than one that boots with the other two guards in force — but it must be
    visible.

    REWRITTEN 2026-09-28 (pre-deployment audit). The original searched for `try:` /
    `except Exception` near the CREATE INDEX and passed while the handler called
    `conn.rollback()` INSIDE a `with engine.begin()` block — which closes the transaction, so
    every later statement in it raises InvalidRequestError and the admin seeding already done
    in that same transaction is discarded. A guard that takes down the thing it guards. Worse,
    the whole block lived in `_seed_admin()`, which RETURNS EARLY when admin_password is unset
    — and it is unset in production, so neither statement would have run at all.

    Asserting on structure now: each statement in its OWN transaction, reached unconditionally
    from init_db, with no rollback inside a begin block."""
    src = (_ROOT / "shared" / "db" / "session.py").read_text()
    # Comments AND docstrings stripped: the corrected function's own docstring explains what
    # `conn.rollback()` did wrong, which a raw search reads as the defect still being present.
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    code = re.sub(r'"""(?:.|\n)*?"""', "", code)

    # Reached on every startup, not only when a password happens to be configured.
    assert "_apply_isolated_ddl()" in code[code.index("def init_db("):code.index("_seed_admin()")]

    body = code[code.index("def _apply_isolated_ddl("):code.index("def _seed_admin(")]
    assert "uq_options_income_intent" in body and "mark_evidence" in body
    assert "with engine.begin() as conn:" in body, "each statement needs its own transaction"
    assert "conn.rollback()" not in body, \
        "a rollback inside a begin block closes the transaction and kills everything after it"
    assert "except Exception" in body and "WARNING" in body

    # And they must NOT be back inside the early-returning seeder.
    seeder = code[code.index("def _seed_admin("):]
    assert "uq_options_income_intent" not in seeder
    assert "mark_evidence" not in seeder


def test_the_isolated_ddl_survives_one_statement_failing():
    """Behavioural, against a real database: a duplicate row makes the unique index fail, and
    the statements around it must still be applied. Run in the probe subprocess because this
    service's conftest stubs sqlalchemy wholesale."""
    r = _probe("isolated_ddl_one_failure")
    assert r["applied"] == ["good"], "a failing statement must not prevent the ones after it"
    assert r["later_table_exists"] is True


# ── The lease itself ─────────────────────────────────────────────────────────

class _FakeRedis:
    """Real GET/SET/EXPIRE semantics for the renew and release scripts."""

    def __init__(self):
        self.store, self.ttls, self.fail = {}, {}, False

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key], self.ttls[key] = value, ex
        return True

    def eval(self, script, _n, key, *args):
        if self.fail:
            raise RuntimeError("redis down")
        cur = self.store.get(key)
        if script == E._INCOME_LOCK_RENEW_LUA:
            if cur == args[0]:
                self.ttls[key] = int(args[1])
                return 1
            return 0
        if script == E._INCOME_LOCK_RELEASE_LUA:
            if cur == args[0]:
                del self.store[key]
                return 1
            return 0
        raise AssertionError("unknown script")


def test_renewal_extends_only_the_holders_own_lease():
    r = _FakeRedis()
    with patch.object(E, "_get_income_redis", lambda: r):
        r.set(E._INCOME_STEP_LOCK_KEY, "mine", nx=True, ex=10)
        assert E._renew_income_lock("mine") is True
        assert r.ttls[E._INCOME_STEP_LOCK_KEY] == E._INCOME_STEP_LOCK_TTL
        assert E._renew_income_lock("someone-else") is False


def test_renewal_of_an_expired_lease_fails_rather_than_recreating_it():
    """EXPIRE on a missing key is a no-op in Redis, so this must not resurrect a lease another
    worker may already have taken."""
    r = _FakeRedis()
    with patch.object(E, "_get_income_redis", lambda: r):
        assert E._renew_income_lock("mine") is False
        assert E._INCOME_STEP_LOCK_KEY not in r.store


def test_the_lease_marks_itself_lost_when_renewal_is_refused():
    r = _FakeRedis()
    with patch.object(E, "_get_income_redis", lambda: r), \
         patch.object(E, "_INCOME_LOCK_RENEW_INTERVAL", 0.01):
        r.set(E._INCOME_STEP_LOCK_KEY, "theirs", nx=True, ex=10)   # someone else owns it
        lease = E.IncomeLease("mine").start()
        try:
            deadline = time.time() + 2
            while lease.is_held() and time.time() < deadline:
                time.sleep(0.01)
            assert not lease.is_held(), "a refused renewal must mark the lease lost"
        finally:
            lease.stop()


def test_a_transient_redis_error_does_not_abort_a_healthy_run():
    """A blip is not proof the lease was lost, and treating it as such would abort a good run
    every time Redis hiccups. The TTL is the backstop."""
    r = _FakeRedis()
    r.fail = True
    with patch.object(E, "_get_income_redis", lambda: r), \
         patch.object(E, "_INCOME_LOCK_RENEW_INTERVAL", 0.01):
        lease = E.IncomeLease("mine").start()
        try:
            time.sleep(0.1)
            assert lease.is_held()
        finally:
            lease.stop()


def test_a_lost_lease_never_returns_to_held():
    """Re-acquiring the key would not make the snapshot the work was decided against correct
    again."""
    r = _FakeRedis()
    with patch.object(E, "_get_income_redis", lambda: r), \
         patch.object(E, "_INCOME_LOCK_RENEW_INTERVAL", 0.01):
        r.set(E._INCOME_STEP_LOCK_KEY, "theirs", nx=True, ex=10)
        lease = E.IncomeLease("mine").start()
        try:
            deadline = time.time() + 2
            while lease.is_held() and time.time() < deadline:
                time.sleep(0.01)
            assert not lease.is_held()
            r.store[E._INCOME_STEP_LOCK_KEY] = "mine"     # we somehow own it again
            time.sleep(0.05)
            assert not lease.is_held()
        finally:
            lease.stop()


def test_the_renewal_interval_leaves_room_for_failures():
    """Renewing at the TTL would mean one missed renewal loses the lease. A third leaves room
    for two consecutive failures."""
    assert E._INCOME_LOCK_RENEW_INTERVAL <= E._INCOME_STEP_LOCK_TTL / 3
    assert E._INCOME_LOCK_RENEW_INTERVAL > 0


def test_the_renewal_thread_is_a_daemon_and_is_stopped():
    """A non-daemon renewal thread would hold the process open after the step finished."""
    lease = E.IncomeLease("t")
    with patch.object(E, "_INCOME_LOCK_RENEW_INTERVAL", 60):
        lease.start()
        assert lease._thread.daemon
        lease.stop()
        assert not lease._thread.is_alive()
