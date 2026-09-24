"""DA-07: the options-income step lock could delete another run's lease, and failed open.

THE TWO DEFECTS, both already fixed once in this codebase for the paper-trading lock (T232-PT5)
and both still present here — while the comment claimed the opposite, that it was "matching
every other locked job in this codebase":

  1. THE LEASE WAS NOT OWNED. The value was the literal "1" and release was an unconditional
     DELETE. If run A exceeds the 1800s TTL and run B acquires a fresh lease, A's `finally`
     deletes B's lock — and a third run can then start while B still believes it is exclusive.
  2. IT FAILED OPEN. A Redis error during acquire logged a warning and proceeded, then still
     executed the unconditional delete — so one transient outage could both start a concurrent
     run AND destroy the other run's lease.

This function mutates portfolio cash, collateral and positions. That is the same class of state
the paper-trading lock deliberately fails CLOSED to protect: losing one evening's run is
recoverable on the next tick, double-opening positions against the same cash is not.
"""
from types import SimpleNamespace
from unittest.mock import patch

from src.services import options_income_engine as OIE


class _FakeRedis:
    """A Redis good enough to exercise ownership: SET NX, and an EVAL that implements the
    compare-and-delete script's actual semantics rather than trusting it blindly."""

    def __init__(self, fail_on_set=False):
        self.store = {}
        self.fail_on_set = fail_on_set
        self.evals = 0
        self.deletes = []

    def set(self, key, value, nx=False, ex=None):
        if self.fail_on_set:
            raise RuntimeError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def eval(self, _script, _numkeys, key, arg):
        self.evals += 1
        if self.store.get(key) == arg:
            del self.store[key]
            self.deletes.append(key)
            return 1
        return 0

    def delete(self, key):
        self.deletes.append(key)
        self.store.pop(key, None)
        return 1


def _run(redis, work=lambda: {"ok": True}):
    with patch.object(OIE, "_get_income_redis", lambda: redis), \
         patch.object(OIE, "_run_options_income_step_locked", work):
        return OIE.run_options_income_step()


# ── Ownership ────────────────────────────────────────────────────────────────

def test_a_run_does_not_delete_a_lease_it_no_longer_owns():
    """The exact reproduction: this run's lease expires mid-work and another run acquires a new
    one. Releasing must be a no-op, not a deletion of somebody else's lock."""
    r = _FakeRedis()

    def _work_that_overruns():
        # Simulate the TTL lapsing and a second worker taking the lease.
        r.store[OIE._INCOME_STEP_LOCK_KEY] = "some-other-workers-token"
        return {"ok": True}

    _run(r, _work_that_overruns)
    assert r.store.get(OIE._INCOME_STEP_LOCK_KEY) == "some-other-workers-token"
    assert r.deletes == []


def test_a_run_does_release_its_own_lease():
    r = _FakeRedis()
    _run(r)
    assert OIE._INCOME_STEP_LOCK_KEY not in r.store


def test_the_lock_value_is_a_unique_token_not_a_constant():
    """Two runs must never write the same value, or ownership cannot be established at all."""
    seen = set()
    for _ in range(3):
        r = _FakeRedis()
        captured = {}

        def _capture():
            captured["tok"] = r.store.get(OIE._INCOME_STEP_LOCK_KEY)
            return {"ok": True}

        _run(r, _capture)
        seen.add(captured["tok"])
    assert len(seen) == 3
    assert "1" not in seen


def test_release_goes_through_the_atomic_script_not_a_bare_delete():
    """GET-then-DEL is two round trips and the lease can be re-acquired between them — which is
    the race an ownership check exists to close."""
    r = _FakeRedis()
    _run(r)
    assert r.evals == 1


# ── Failing closed ───────────────────────────────────────────────────────────

def test_a_redis_outage_skips_the_run_rather_than_proceeding_unprotected():
    """Failing open here would start a concurrent run against the same portfolio cash. One
    missed evening is recoverable on the next tick; a double-open is not."""
    r = _FakeRedis(fail_on_set=True)
    ran = {"called": False}

    def _work():
        ran["called"] = True
        return {"ok": True}

    out = _run(r, _work)
    assert ran["called"] is False
    assert out["ok"] is False
    assert out["skipped"] == "lock_unavailable"


def test_a_redis_outage_does_not_delete_anyone_elses_lease():
    """The old code failed open AND still ran the unconditional delete, so one outage could
    both start a concurrent run and destroy the other run's lock."""
    r = _FakeRedis(fail_on_set=True)
    r.store[OIE._INCOME_STEP_LOCK_KEY] = "another-workers-token"
    _run(r)
    assert r.store[OIE._INCOME_STEP_LOCK_KEY] == "another-workers-token"
    assert r.deletes == []


# ── Ordinary contention ──────────────────────────────────────────────────────

def test_a_second_concurrent_run_is_refused_while_the_first_holds_the_lease():
    r = _FakeRedis()
    r.store[OIE._INCOME_STEP_LOCK_KEY] = "first-run-token"
    ran = {"called": False}

    def _work():
        ran["called"] = True
        return {"ok": True}

    out = _run(r, _work)
    assert ran["called"] is False
    assert out["skipped"] == "already_running"
    assert r.store[OIE._INCOME_STEP_LOCK_KEY] == "first-run-token"


def test_the_lease_is_released_even_when_the_work_raises():
    r = _FakeRedis()

    def _boom():
        raise RuntimeError("work failed")

    try:
        _run(r, _boom)
    except RuntimeError:
        pass
    assert OIE._INCOME_STEP_LOCK_KEY not in r.store


# ── The Lua script itself ────────────────────────────────────────────────────
#
# _FakeRedis.eval implements compare-and-delete in Python, which is what makes the ownership
# tests above readable — but it also means the actual script text is never executed, so
# rewriting the Lua to `if true then` left every test above green. There is no Lua interpreter
# here, so the script is pinned structurally instead: it must compare the stored value against
# the caller's token, and the DEL must be inside that comparison.

def test_the_release_script_compares_ownership_before_deleting():
    lua = OIE._INCOME_LOCK_RELEASE_LUA
    assert 'redis.call("GET", KEYS[1]) == ARGV[1]' in lua
    assert 'redis.call("DEL", KEYS[1])' in lua
    # The DEL must come AFTER the comparison — an unconditional delete placed first would
    # satisfy both assertions above on their own.
    assert lua.index("GET") < lua.index("DEL")


def test_the_release_script_has_a_non_deleting_branch():
    """Without an else-return the script would fall through, and a non-owner would get no
    signal that its lease had already been taken."""
    lua = OIE._INCOME_LOCK_RELEASE_LUA
    assert "else" in lua
    assert "return 0" in lua


def test_the_release_script_matches_the_schedulers_own_proven_one():
    """T232-PT5 solved this for the paper-trading lock. Divergence between the two is how one
    of them silently regresses — the audit's own advice was to fix the pattern, not one call."""
    import re

    from src.services import scheduler as SCH

    def _norm(x):
        return re.sub(r"\s+", " ", x).strip()

    assert _norm(OIE._INCOME_LOCK_RELEASE_LUA) == _norm(SCH._LOCK_RELEASE_LUA)
