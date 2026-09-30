"""AUD-RECOVERY-LIFECYCLE: a recovery grant is spent by an ENTRY, not by an attempt.

THE DEFECT. `_mark_recovery_grant()` was called at the top of the consecutive-loss deadlock
branch — before the per-day cap, before candidate selection, before every remaining gate and
before `_open_paper_trade()`. So:

    losing streak -> no open trades -> MARK USED -> zero candidates -> no entry

burned the grant for SEVEN DAYS without a trade ever opening. The code recorded *permission to
attempt* as *consumption of an entry*, and the next scan then refused to try because the marker
said the recovery had been used.

Measured on production 2026-09-30: portfolios 2 and 5 both held markers (streak 4 and 10) with
584,763s and 23,432s remaining while sitting at ZERO open positions; portfolio 2 had a
zero-candidate scan that same day and no September trades at all.

Reserve -> consume. These tests exercise the real functions against a fake Redis that enforces
SET NX semantics, because the whole correctness of the fix is in that atomicity.
"""
import pathlib
import sys
import types
from unittest.mock import MagicMock

import pytest

PT_SRC = (pathlib.Path(__file__).resolve().parents[1]
          / "src/services/paper_trading_engine.py").read_text()


def _fn_src(name: str) -> str:
    i = PT_SRC.index(f"def {name}")
    nxt = PT_SRC.find("\ndef ", i + 10)
    return PT_SRC[i:nxt if nxt != -1 else len(PT_SRC)]


class FakeRedis:
    """Enough Redis to be honest about SET NX and TTLs — the whole correctness of the fix is in
    that atomicity, so a mock that ignores NX would prove nothing."""

    def __init__(self):
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = (value, ex)
        return True

    def setex(self, key, ttl, value):
        self.store[key] = (value, ttl)
        return True

    def get(self, key):
        v = self.store.get(key)
        return None if v is None else v[0]

    def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def eng(monkeypatch):
    """Execute the REAL grant helpers, extracted from the engine source, over a fake Redis.

    The module cannot be imported whole (relative imports), so this follows the established
    extraction pattern used by test_aud_consec_loss_recovery_grant.py.
    """
    fake = FakeRedis()
    mod = types.ModuleType("common.redis_client")
    mod.get_redis = lambda: fake
    monkeypatch.setitem(sys.modules, "common.redis_client", mod)

    env = {
        "_RECOVERY_GRANT_TTL": 7 * 86400,
        "_RECOVERY_RESERVE_TTL": 900,
        "_RECOVERY_RESERVED_PREFIX": "reserved:",
    }
    for fn in ("_recovery_grant_key", "_recovery_grant_used", "_reserve_recovery_grant",
               "_consume_recovery_grant", "_clear_recovery_grant"):
        exec(compile(_fn_src(fn), "<engine>", "exec"), env)
    return types.SimpleNamespace(**env), fake


def test_a_reservation_is_not_a_consumption(eng):
    """THE REGRESSION TEST. Reserving must not cost a week. Before the fix this path set the
    seven-day marker outright."""
    m, fake = eng
    assert m._reserve_recovery_grant(7, 3) is True
    _value, ttl = fake.store[m._recovery_grant_key(7)]
    assert ttl == m._RECOVERY_RESERVE_TTL
    assert ttl < m._RECOVERY_GRANT_TTL
    assert ttl <= 900, "a reservation must self-heal in minutes, not days"


def test_a_scan_that_opens_nothing_leaves_only_a_short_reservation(eng):
    """The reported failure path: streak tripped, grant claimed, then zero candidates. The next
    scan must be able to try again once the reservation lapses — not be locked out for a week."""
    m, fake = eng
    m._reserve_recovery_grant(7, 3)
    _v, ttl = fake.store[m._recovery_grant_key(7)]
    assert ttl == m._RECOVERY_RESERVE_TTL
    # Nothing consumed it, so simulate expiry and confirm the next scan may reserve again.
    fake.delete(m._recovery_grant_key(7))
    assert m._reserve_recovery_grant(7, 3) is True


def test_opening_a_trade_consumes_the_grant_for_a_week(eng):
    m, fake = eng
    m._reserve_recovery_grant(7, 3)
    m._consume_recovery_grant(7, 3)
    value, ttl = fake.store[m._recovery_grant_key(7)]
    assert ttl == m._RECOVERY_GRANT_TTL
    assert value == "3"
    assert m._recovery_grant_used(7, 3) is True


def test_two_concurrent_scans_cannot_both_take_the_attempt(eng):
    """`SET NX` is the whole point: the allowance is ONE entry, and duplicate or restarted
    workers must not exceed it."""
    m, _ = eng
    assert m._reserve_recovery_grant(7, 3) is True
    assert m._reserve_recovery_grant(7, 3) is False


def test_a_held_reservation_blocks_the_used_check(eng):
    """A reservation held by another scan must read as 'used' — otherwise the second scan falls
    through into the deadlock branch and enters alongside the first."""
    m, _ = eng
    m._reserve_recovery_grant(7, 3)
    assert m._recovery_grant_used(7, 3) is True


def test_a_worse_streak_still_earns_a_fresh_attempt(eng):
    """Behaviour deliberately preserved from the original fix: the key is the streak length, so a
    recovery entry that also loses (3 -> 4) gets one more attempt at the new level rather than
    locking the portfolio out permanently."""
    m, _ = eng
    m._reserve_recovery_grant(7, 3)
    m._consume_recovery_grant(7, 3)
    assert m._recovery_grant_used(7, 3) is True
    assert m._recovery_grant_used(7, 4) is False


def test_a_positive_close_still_clears_everything(eng):
    m, fake = eng
    m._reserve_recovery_grant(7, 3)
    m._consume_recovery_grant(7, 3)
    m._clear_recovery_grant(7)
    assert m._recovery_grant_used(7, 3) is False
    assert m._recovery_grant_key(7) not in fake.store


def test_redis_failure_fails_open_in_both_directions(eng, monkeypatch):
    """A Redis outage must not permanently freeze a portfolio. Reserve returns True (proceed) and
    the used-check returns False (not yet used) — together that degrades to the pre-fix
    behaviour rather than to a new failure mode."""
    m, _ = eng
    broken = types.ModuleType("common.redis_client")
    broken.get_redis = MagicMock(side_effect=RuntimeError("controlled redis outage"))
    monkeypatch.setitem(sys.modules, "common.redis_client", broken)
    assert m._reserve_recovery_grant(7, 3) is True
    assert m._recovery_grant_used(7, 3) is False


def test_bytes_from_redis_are_handled(eng):
    """A real client may return bytes rather than str; comparing bytes to str silently reads as
    'not used' and would hand out unlimited recovery entries."""
    m, fake = eng
    fake.store[m._recovery_grant_key(7)] = (b"3", m._RECOVERY_GRANT_TTL)
    assert m._recovery_grant_used(7, 3) is True
