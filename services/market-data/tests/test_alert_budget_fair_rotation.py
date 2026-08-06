"""Tests for AUD266-BUDGET-DETERMINISTIC-STARVE (Deep Audit #6, Tier 266).

check_signal_alerts()'s two wall-clock-budgeted loops (signal fetch, fundamentals fetch)
previously iterated a bare Python set/list(set(...)) — nondeterministic iteration order across
process restarts (though fixed within one long-lived process), so the SAME tail-end symbols
were silently starved of alert coverage every single cycle until the next deploy, then a
DIFFERENT tail was starved instead. Empirically confirmed in the original audit: 3 subprocesses
produced 3 different set iteration orders for the identical input.

Fixed via a new _rotate_for_fair_budget() helper: sorts for a deterministic base order, then
rotates by a persisted Redis cursor advancing by 1 each call, so a fixed relative budget-cutoff
position affects a different subset of symbols each cycle.

_rotate_for_fair_budget() is a small, pure(ish) function with only a Redis dependency —
extracted via source-text exec() against a fake in-memory Redis client (no real dependency
needed), matching this repo's established technique for functions with this exact shape.
scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler and other unstubbed modules).
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


class _FakeRedis:
    """Minimal in-memory stand-in for the two Redis calls _rotate_for_fair_budget makes."""

    def __init__(self, initial: dict | None = None):
        self.store: dict[str, str] = dict(initial or {})

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = str(value)


def _build_rotate_for_fair_budget(fake_redis: _FakeRedis):
    """Extracts _rotate_for_fair_budget()'s real source and exec()s it with _get_redis
    monkeypatched to return the given fake client — exercising the actual function under
    test, not a hand-copied reimplementation."""
    start = _scheduler_source.index("def _rotate_for_fair_budget(")
    end = _scheduler_source.index("\n_SIGNAL_ALERT_LOCK_KEY", start)
    func_source = _scheduler_source[start:end]
    namespace = {"_get_redis": lambda: fake_redis}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of the real function's source
    return namespace["_rotate_for_fair_budget"]


def test_empty_input_returns_empty_without_touching_redis():
    rc = _FakeRedis()
    rotate = _build_rotate_for_fair_budget(rc)
    assert rotate([], "k") == []
    assert rc.store == {}


def test_first_call_with_no_cursor_returns_sorted_order():
    rc = _FakeRedis()
    rotate = _build_rotate_for_fair_budget(rc)
    result = rotate(["C", "A", "B"], "k")
    assert result == ["A", "B", "C"]


def test_rotation_genuinely_shifts_the_order_across_repeated_calls():
    """The exact bug caught and fixed during implementation: an earlier draft advanced the
    cursor by len(items) each call, making `cursor % len(items)` ALWAYS 0 — the rotation never
    actually moved, so every cycle silently returned the identical order. This test asserts
    the rotation genuinely differs cycle to cycle, which the buggy version would fail."""
    rc = _FakeRedis()
    rotate = _build_rotate_for_fair_budget(rc)
    items = ["C", "A", "B", "E", "D"]
    cycle1 = rotate(items, "k")
    cycle2 = rotate(items, "k")
    cycle3 = rotate(items, "k")
    assert cycle1 == ["A", "B", "C", "D", "E"]
    assert cycle2 == ["B", "C", "D", "E", "A"]
    assert cycle3 == ["C", "D", "E", "A", "B"]
    # the three cycles must be genuinely distinct orderings, not the same one 3 times
    assert len({tuple(cycle1), tuple(cycle2), tuple(cycle3)}) == 3


def test_rotation_wraps_around_correctly_after_a_full_cycle():
    rc = _FakeRedis()
    rotate = _build_rotate_for_fair_budget(rc)
    items = ["A", "B", "C"]
    results = [rotate(items, "k") for _ in range(4)]
    assert results[0] == ["A", "B", "C"]
    assert results[3] == ["A", "B", "C"]  # wrapped back to the start after 3 rotations


def test_every_symbol_gets_the_truncated_tail_position_over_enough_cycles():
    """The actual fairness property this fix exists for: if a budget always cuts off the last
    N items, over len(items) cycles every item must have spent at least one cycle in that
    truncated tail position."""
    rc = _FakeRedis()
    rotate = _build_rotate_for_fair_budget(rc)
    items = ["A", "B", "C", "D", "E"]
    tail_positions_seen = set()
    for _ in range(len(items)):
        result = rotate(items, "k")
        tail_positions_seen.add(result[-1])  # the item that would be cut if budget allows 4/5
    assert tail_positions_seen == set(items)


def test_two_different_redis_keys_rotate_independently():
    """The signal-fetch loop and the fundamentals-fetch loop use separate cursor keys — a
    rotation on one must not affect the other."""
    rc = _FakeRedis()
    rotate = _build_rotate_for_fair_budget(rc)
    items = ["A", "B", "C"]
    rotate(items, "cursor_1")
    rotate(items, "cursor_1")
    result_2_first_call = rotate(items, "cursor_2")
    assert result_2_first_call == ["A", "B", "C"]  # cursor_2 is on its own first call


def test_redis_failure_falls_back_to_deterministic_sorted_order():
    class _RaisingRedis:
        def get(self, key):
            raise ConnectionError("redis down")

        def set(self, key, value, ex=None):
            raise ConnectionError("redis down")

    rotate = _build_rotate_for_fair_budget(_RaisingRedis())
    assert rotate(["C", "A", "B"], "k") == ["A", "B", "C"]


# ── Wiring: both budgeted loops in check_signal_alerts() actually use the rotation ────────

def _check_signal_alerts_body() -> str:
    start = _scheduler_source.index("\ndef check_signal_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


def test_signal_fetch_loop_uses_the_rotation_helper():
    body = _check_signal_alerts_body()
    assert '_rotate_for_fair_budget(list(style_sym_pairs), "stockai:signal_alert_budget_cursor:signals")' in body


def test_fundamentals_fetch_loop_uses_the_rotation_helper():
    body = _check_signal_alerts_body()
    assert '_rotate_for_fair_budget(symbols, "stockai:signal_alert_budget_cursor:fundamentals")' in body


def test_skipped_symbols_are_now_surfaced_not_just_counted():
    """Per this tracker item's own fix description: 'surface the skipped symbols rather than
    only logging a count.'"""
    body = _check_signal_alerts_body()
    assert "skipped_pairs=_skipped_signal_pairs[:50]" in body
    assert "skipped_symbols=_skipped_fund_symbols[:50]" in body
