"""Tier 384 remainder — AUD-C04 (stale-price entry gate), AUD-A07 (income-step concurrency),
AUD-A10 (exit replay cutoff).

paper_trading_engine imports cleanly here but its query bodies are SQLAlchemy expressions that
cannot be evaluated without a real DB, so the structural properties are pinned by source
extraction (the file's established convention) and the genuinely behavioural piece — the A10
cutoff seam — is exercised directly.
"""
import pathlib
from datetime import datetime, timedelta, timezone

_PTE_PATH = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py")
_PTE = _PTE_PATH.read_text()
_ENGINE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "options_income_engine.py").read_text()


def _fn(src: str, name: str) -> str:
    start = src.index(f"def {name}(")
    nxt = src.find("\ndef ", start + 1)
    return src[start:nxt if nxt != -1 else len(src)]


# ── AUD-C04: stale price data must not authorise a NEW entry ─────────────────────────────

def test_entry_scan_requires_a_recent_daily_bar():
    """SSNLF sat `active`, `not delisted` and tradeable with a latest daily bar from
    2025-11-07 — ten months stale — because the entry query checked administrative flags and
    never the data itself."""
    body = _fn(_PTE, "_scan_for_entries")
    assert "cutoff_fresh" in body
    assert "func.max(Price.ts) >= cutoff_fresh" in body
    assert "Price.timeframe == TimeFrame.D1" in body


def test_the_freshness_window_is_a_named_constant_not_a_literal():
    assert "_MAX_ENTRY_PRICE_STALENESS_DAYS = 5" in _PTE
    assert "cutoff_fresh = _entry_price_cutoff(now)" in _PTE


# The threshold is a NUMBER, so it is tested as one. The first version of these tests asserted
# only that `func.max(Price.ts) >= cutoff_fresh` appeared in the query — and passed happily when
# that expression was sabotaged to `>= cutoff_fresh - timedelta(days=99999)`, because the
# substring survived. A source-text assertion cannot detect a semantic defeat of the thing it
# names (AUD-T401-SOURCETEXTTESTS).

def test_ssnlf_shaped_staleness_is_rejected():
    """The actual case: active, not delisted, newest daily bar 2025-11-07, asked about on
    2026-09-18 — ten months stale."""
    import src.services.paper_trading_engine as pte
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    assert pte.is_price_fresh_enough_to_enter(datetime(2025, 11, 7, tzinfo=timezone.utc), now) is False


def test_a_long_weekend_plus_a_holiday_still_passes():
    """The window must not be so tight that a normal market closure blocks every entry."""
    import src.services.paper_trading_engine as pte
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    assert pte.is_price_fresh_enough_to_enter(now - timedelta(days=4), now) is True


def test_the_boundary_is_inclusive_at_exactly_the_window():
    import src.services.paper_trading_engine as pte
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    assert pte.is_price_fresh_enough_to_enter(pte._entry_price_cutoff(now), now) is True
    assert pte.is_price_fresh_enough_to_enter(
        pte._entry_price_cutoff(now) - timedelta(seconds=1), now) is False


def test_no_price_history_at_all_is_not_fresh():
    """Returning True on None would make the ABSENCE of data look like the absence of a
    problem — the exact shape of the bug this gate exists to close."""
    import src.services.paper_trading_engine as pte
    assert pte.is_price_fresh_enough_to_enter(None, datetime(2026, 9, 18, tzinfo=timezone.utc)) is False


def test_a_naive_timestamp_is_treated_as_utc_not_crashed_on():
    import src.services.paper_trading_engine as pte
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)
    assert pte.is_price_fresh_enough_to_enter(datetime(2026, 9, 17), now) is True


def test_the_gate_is_on_entries_only_never_on_exits():
    """A HELD position with a stale quote needs more scrutiny, not less. Blocking its exit
    would trap capital in exactly the position you most want out of — the opposite of the
    intent. The exit path has its own staleness escalation."""
    exit_fns = [f for f in ("_monitor_positions", "_check_exits") if f"def {f}(" in _PTE]
    for fn in exit_fns:
        assert "cutoff_fresh" not in _fn(_PTE, fn), f"{fn} must not inherit the entry freshness gate"


# ── AUD-A07: the income step must not run twice at once ──────────────────────────────────

def test_income_step_takes_a_lock():
    body = _fn(_ENGINE, "run_options_income_step")
    assert "_INCOME_STEP_LOCK_KEY" in body
    assert "nx=True" in body


def test_the_lock_guards_the_shared_function_not_just_the_scheduler():
    """TWO entry points reach this — the 19:00 ET job and the admin POST /run-step. Locking
    only the scheduler leaves the route a human triggers impatiently, most likely WHILE the
    scheduled run is going, entirely unprotected."""
    assert "def run_options_income_step() -> dict:" in _ENGINE
    body = _fn(_ENGINE, "run_options_income_step")
    assert "_run_options_income_step_locked()" in body
    api = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "options_income.py").read_text()
    assert "return run_options_income_step()" in api, (
        "the admin route must surface the engine's verdict — reporting a SKIPPED run as "
        '{"ok": True} tells an admin their trigger did something it did not'
    )


def test_lock_ttl_exceeds_the_measured_runtime():
    """A TTL shorter than the work it guards is not a lock, it is a delay: the second caller
    takes it mid-run. The last measured income step took 344s."""
    assert "_INCOME_STEP_LOCK_TTL = 1800" in _ENGINE


def test_lock_failure_fails_CLOSED():
    """REVERSED by DA-07 (2026-09-24). This test previously asserted the opposite, on the
    reasoning that "losing the evening run to a Redis blip is worse than the small overlap
    risk — and overlap additionally requires a human triggering at that exact moment."

    That reasoning does not survive contact with what the function does: it mutates portfolio
    cash, collateral and positions. Two runs can each read the same `current_cash`, each decide
    the same candidate is affordable, and each open it — the concentration and daily-entry caps
    are enforced in Python against a snapshot, so neither notices. The paper-trading lock
    already fails CLOSED for exactly this reason (T232-PT5). A missed evening is recoverable on
    the next tick; double-opening against the same cash is not.

    Recorded rather than quietly rewritten, because a test asserting the defective behaviour is
    a large part of why the defect survived a previous review."""
    body = _fn(_ENGINE, "run_options_income_step")
    assert "lock_unavailable" in body
    assert "lock_unavailable_proceeding" not in body


def test_the_lock_is_released_ONLY_BY_ITS_OWNER():
    """Also reversed by DA-07. The previous assertion required an unconditional
    `delete(_INCOME_STEP_LOCK_KEY)` — which is the bug: if this run overruns the TTL and another
    acquires a fresh lease, that delete removes the OTHER run's lock."""
    body = _fn(_ENGINE, "run_options_income_step")
    assert "finally:" in body
    assert "_release_income_lock(token)" in body
    assert "delete(_INCOME_STEP_LOCK_KEY)" not in body


# ── AUD-A10: exit reads must respect a cutoff ────────────────────────────────────────────

def test_rsi_exit_read_is_bounded_by_an_as_of():
    """Unbounded, this reads the LATEST signal — correct live, and a look-ahead leak in any
    replay of exits."""
    assert "AND sig.ts <= :as_of " in _PTE
    assert '"as_of": _exit_as_of()' in _PTE


def test_exit_as_of_defaults_to_now_so_live_behaviour_is_unchanged():
    import src.services.paper_trading_engine as pte
    before = datetime.now(timezone.utc)
    got = pte._exit_as_of()
    assert before - timedelta(seconds=5) <= got <= datetime.now(timezone.utc) + timedelta(seconds=5)


def test_a_replay_can_pin_the_exit_clock():
    """The point of the seam: ONE place to set the simulated clock, rather than finding and
    bounding each historical query individually — which is how the unbounded read arose."""
    import src.services.paper_trading_engine as pte
    pinned = datetime(2026, 3, 1, tzinfo=timezone.utc)
    token = pte._EXIT_AS_OF_OVERRIDE.set(pinned)
    try:
        assert pte._exit_as_of() == pinned
    finally:
        pte._EXIT_AS_OF_OVERRIDE.reset(token)
    assert pte._exit_as_of() != pinned, "the override must not leak past its context"
