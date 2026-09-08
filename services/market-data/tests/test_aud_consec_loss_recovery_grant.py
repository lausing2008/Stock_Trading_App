"""AUD-ENTRY-CONSECLOSS-DEADLOCKLOOP — the circuit breaker's escape hatch swallowed the breaker.

The consecutive-loss breaker suspends new entries after N losing trades. With OPEN positions it
works correctly. With ZERO open positions there is a genuine deadlock — no trade can close
positive to reset the streak — so the code granted "one recovery entry".

    _consec_losses = 0        # a LOCAL variable
    _clear_gate_block(...)    # falls through, no return

**Nothing persisted that the grant had been used.** `_consec_loss_streak()` recomputes from the
DB every cycle, so the branch re-fired on EVERY 5-minute scan, indefinitely. The comment said
"allow one recovery entry"; nothing enforced "one".

PRODUCTION EVIDENCE at the time of the fix — three portfolios sat at zero open positions with
the streak tripped, and portfolio 5 (ETrade Sandbox) had taken 7 trades under this branch since
2026-08-17:

    DELL   -6.66%  -$325.63      BRK-A  -1.20%   -$58.59
    TMDX   -0.38%   -$18.64      IGV    -0.32%   -$15.56
    BRK-A  -0.26%   -$12.50      BRK-A  -0.25%   -$12.21
    NATL   -0.20%    -$9.79
                                 TOTAL          -$452.92

**ALL SEVEN LOST.** The control that exists precisely to stop trading during a losing streak had
inverted into a licence to keep trading — bounded only by max_entries_per_day (3), which bounds
per day but never across days.

Found INDEPENDENTLY by two separate audit agents, which is itself worth noting: the entry-gate
audit and the exit/risk audit reached it from opposite directions.
"""
import pathlib

import pytest

PT_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/paper_trading_engine.py"
).read_text()


def _fn_src(name: str) -> str:
    i = PT_SRC.index(f"def {name}")
    nxt = PT_SRC.find("\ndef ", i + 10)
    return PT_SRC[i:nxt if nxt != -1 else len(PT_SRC)]


# ── The core fix: the grant must be PERSISTED, not a local ──────────────────────────────

def test_the_recovery_branch_now_marks_the_grant():
    """THE FIX. Without a persisted marker the branch re-fires every scan cycle."""
    i = PT_SRC.index("no open trades — allowing one recovery entry to break deadlock")
    block = PT_SRC[i:i + 500]
    assert "_mark_recovery_grant(portfolio.id, _consec_losses)" in block


def test_an_already_used_grant_blocks_instead_of_re_granting():
    """The arm that did not exist. It must RETURN (suspend entries), not fall through."""
    i = PT_SRC.index("elif _recovery_grant_used(portfolio.id, _consec_losses):")
    block = PT_SRC[i:PT_SRC.index("else:", i)]
    assert "_write_gate_block(" in block, "the UI must show why entries are suspended"
    assert "return" in block, "must SUSPEND entries, not fall through to trading"


def test_the_local_zeroing_alone_is_no_longer_the_whole_mechanism():
    """`_consec_losses = 0` is still needed (it stops DE's own T187 gate double-blocking) but it
    must no longer be the ONLY thing standing between a freefalling portfolio and more trades."""
    i = PT_SRC.index("no open trades — allowing one recovery entry to break deadlock")
    block = PT_SRC[i:i + 500]
    assert "_consec_losses = 0" in block, "still needed for the DE call"
    mark = block.index("_mark_recovery_grant")
    zero = block.index("_consec_losses = 0")
    assert mark < zero, "the grant must be recorded BEFORE the local is zeroed"


# ── Keyed on the streak, so a worse streak earns a fresh (single) attempt ────────────────

def test_the_marker_is_keyed_on_the_streak_length():
    """If the recovery entry ALSO loses (streak 4 -> 5), the portfolio gets one more attempt at
    the new level rather than being locked out permanently. That preserves the deadlock escape
    the branch exists for while keeping it strictly finite."""
    fn = _fn_src("_recovery_grant_used")
    assert "str(streak)" in fn, "must compare against the streak that earned the grant"
    fn_mark = _fn_src("_mark_recovery_grant")
    assert "str(streak)" in fn_mark


def _grant_used(stored: str | None, streak: int) -> bool:
    """Mirrors the real comparison; the source assertions above pin the real implementation."""
    return stored == str(streak)


def test_same_streak_is_blocked_a_second_time():
    assert _grant_used("4", 4) is True, "a repeat at the same streak must be refused"


def test_a_worse_streak_earns_one_more_attempt():
    assert _grant_used("4", 5) is False, "streak 4 -> 5 is a new level, one fresh grant"


def test_no_marker_means_not_yet_used():
    assert _grant_used(None, 4) is False


# ── The marker must be released when the portfolio genuinely recovers ───────────────────

def test_a_winning_close_clears_the_marker():
    """Otherwise a portfolio that recovers stays locked out for the marker's full TTL — trading
    a breaker that never stops for a breaker that never restarts."""
    assert "_clear_recovery_grant(trade.portfolio_id)" in PT_SRC
    i = PT_SRC.index("_clear_recovery_grant(trade.portfolio_id)")
    guard = PT_SRC[max(0, i - 300):i]
    assert "total_pnl_dollar >= 0" in guard, "must clear on a NON-NEGATIVE close"


def test_the_clear_matches_how_the_streak_itself_is_counted():
    """_consec_loss_streak stops counting at the first non-negative pnl (`if pnl < 0 ... else
    break`), so the clear threshold must be >= 0, not > 0. A breakeven close ends the streak."""
    fn = _fn_src("_consec_loss_streak")
    assert "if pnl < 0:" in fn and "break" in fn
    i = PT_SRC.index("_clear_recovery_grant(trade.portfolio_id)")
    assert "total_pnl_dollar >= 0" in PT_SRC[max(0, i - 300):i]


def test_a_losing_close_does_NOT_clear_the_marker():
    """The inverse — a loss must leave the block in place."""
    i = PT_SRC.index("_clear_recovery_grant(trade.portfolio_id)")
    guard_line = PT_SRC[max(0, i - 300):i]
    assert "if total_pnl_dollar >= 0:" in guard_line, "must be conditional, not unconditional"


# ── Failure direction ───────────────────────────────────────────────────────────────────

def test_redis_failure_fails_OPEN_not_closed():
    """A Redis outage must not permanently freeze a portfolio. Failing open degrades to today's
    (buggy) behaviour rather than inventing a new failure mode — the safer direction when the
    alternative is an un-clearable lockout."""
    fn = _fn_src("_recovery_grant_used")
    assert "except Exception" in fn
    assert "return False" in fn, "on error, report 'not yet used' so the portfolio can trade"


def test_all_three_helpers_are_fail_silent():
    """Consistent with _write_gate_block/_clear_gate_block, which this follows."""
    for name in ("_recovery_grant_used", "_mark_recovery_grant", "_clear_recovery_grant"):
        assert "except Exception" in _fn_src(name), f"{name} must not raise into the scan loop"


def test_the_marker_has_a_finite_ttl():
    """A marker that never expires could strand a portfolio if the clear path were ever missed.
    7 days ~= one trading week: long enough that it cannot be re-granted on the next 5-minute
    scan (the actual defect), short enough to self-heal."""
    import re
    m = re.search(r"_RECOVERY_GRANT_TTL = ([^\n#]+)", PT_SRC)
    assert m, "TTL constant must exist"
    ttl = eval(m.group(1).strip())  # noqa: S307 — a literal arithmetic expression in our own source
    assert ttl >= 86400, "must outlast many scan cycles, or it re-grants like the bug did"
    assert ttl <= 30 * 86400, "must self-heal rather than stranding a portfolio indefinitely"


# ── The deadlock the branch exists for must still be escapable ──────────────────────────

def test_the_deadlock_escape_still_exists():
    """The branch is NOT being deleted. With zero open positions and no prior grant, one entry
    is still allowed — otherwise a portfolio whose last N trades lost could never trade again,
    which is the deadlock the branch was written to solve."""
    i = PT_SRC.index("no open trades — allowing one recovery entry to break deadlock")
    block = PT_SRC[i:i + 500]
    assert "return" not in block.split("_consec_losses = 0")[0], (
        "the first grant must still fall through to trading"
    )


def test_open_positions_still_take_the_original_blocking_path():
    """The `open_count > 0` arm was always correct and must be untouched."""
    i = PT_SRC.index("max_consec_losses and max_consec_losses > 0")
    block = PT_SRC[i:i + 700]
    assert "if open_count > 0:" in block
    assert "new entries suspended until a trade closes positive" in block
