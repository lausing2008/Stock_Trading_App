"""Regression tests for the lock-leak found in the 2026-09-06 deep audit (priority item #11):
check_conditional_orders() acquired _CONDITIONAL_ORDER_LOCK_KEY but never explicitly released
it — the lock always held its full 55s TTL against a 60s job interval, leaving only a 5s
acquisition window; a slightly-late fire lands inside the still-held TTL and the tick is
silently skipped.

Fixed with the same token-based compare-and-delete pattern already hardened for
_PAPER_TRADING_LOCK_KEY (scheduler.py, T232-PT5): each acquirer writes a unique token as the
lock value, and release only deletes the key if its current value still matches the token that
acquired it — this is what makes it safe for a run that outlives its own TTL to never delete a
DIFFERENT run's lock.
"""
import pathlib

import src.services.conditional_orders as co

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "conditional_orders.py"
_MODULE_SOURCE = _MODULE_PATH.read_text()


class _FakeRedisEval:
    """Minimal fake supporting exactly what the Lua release script needs: GET and DEL,
    driven through .eval() the same way the real redis-py client dispatches EVAL."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def eval(self, script, numkeys, *args):
        key, token = args[0], args[1]
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


def test_lock_release_lua_deletes_the_key_when_the_token_matches():
    """The core release path: a run that finishes within its own TTL must actually remove the
    lock key, not just let it expire — this is what closes the acquisition-window gap."""
    fake = _FakeRedisEval()
    token = "abc-123"
    fake.set(co._CONDITIONAL_ORDER_LOCK_KEY, token, nx=True, ex=55)
    assert fake.get(co._CONDITIONAL_ORDER_LOCK_KEY) == token

    released = fake.eval(co._LOCK_RELEASE_LUA, 1, co._CONDITIONAL_ORDER_LOCK_KEY, token)
    assert released == 1
    assert fake.get(co._CONDITIONAL_ORDER_LOCK_KEY) is None


def test_lock_release_lua_refuses_to_delete_a_different_runs_lock():
    """The T232-PT5 safety property this pattern exists for: if run A's TTL expired and run B
    already acquired a NEW lock (different token) before run A finally releases, run A's
    release must be a no-op — it must NEVER delete run B's still-active lock."""
    fake = _FakeRedisEval()
    run_a_token = "run-a-token"
    run_b_token = "run-b-token"

    # Run A acquires, then (simulated) its TTL expires and Run B acquires a fresh lock.
    fake.store[co._CONDITIONAL_ORDER_LOCK_KEY] = run_b_token

    # Run A, unaware its TTL already expired, now tries to release using its OWN (stale) token.
    released = fake.eval(co._LOCK_RELEASE_LUA, 1, co._CONDITIONAL_ORDER_LOCK_KEY, run_a_token)
    assert released == 0
    # Run B's lock must still be intact — this is the exact cascading race being guarded against.
    assert fake.get(co._CONDITIONAL_ORDER_LOCK_KEY) == run_b_token


def test_check_conditional_orders_acquires_the_lock_with_a_unique_token_not_a_fixed_literal():
    """Source-level guard: the acquire call must write a unique per-run token as the lock
    VALUE (matching _PAPER_TRADING_LOCK_KEY's own established pattern), not the old fixed
    literal "1" — a fixed value can't support the compare-and-delete release."""
    start = _MODULE_SOURCE.index("def check_conditional_orders(")
    body = _MODULE_SOURCE[start:start + 3000]
    assert '_CONDITIONAL_ORDER_LOCK_KEY, "1", nx=True' not in body, (
        "found the old fixed-literal lock value — this cannot support a safe "
        "compare-and-delete release."
    )
    assert "_CONDITIONAL_ORDER_LOCK_KEY, _lock_token, nx=True" in body


def test_check_conditional_orders_releases_the_lock_in_a_finally_block():
    """Source-level guard: the function must actually call the release Lua script in a
    finally block — this is what closes the acquisition-window gap. Before the fix, no
    release call existed anywhere in this function at all."""
    start = _MODULE_SOURCE.index("def check_conditional_orders(")
    try:
        next_def = _MODULE_SOURCE.index("\ndef ", start + 10)
        body = _MODULE_SOURCE[start:next_def]
    except ValueError:
        body = _MODULE_SOURCE[start:]  # this is the last function in the file

    assert "finally:" in body
    finally_idx = body.rindex("finally:")
    finally_block = body[finally_idx:]
    assert "_get_redis().eval(_LOCK_RELEASE_LUA, 1, _CONDITIONAL_ORDER_LOCK_KEY, _lock_token)" in finally_block
