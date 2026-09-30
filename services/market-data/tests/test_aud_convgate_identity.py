"""AUD-CONVGATE-IDENTITY: a cached conviction result must be about the signal being evaluated.

THE DEFECT. `_scan_for_entries` read `conv_gate:{symbol}:{style}` and blocked entry whenever the
record said `signal == "BUY"` and `sent is False` — without binding the record to the signal now
under consideration. The record carries a **24-hour TTL**, so a gate that failed against the 09:30
refresh went on blocking entries for the rest of the trading day, including against a NEWER signal
the gate had never seen and might well have passed.

Two consequences, both measured as real paths (5,333 conviction-gate checks in September):
trading eligibility depended on whether the alert path happened to have evaluated the symbol
earlier, and a failed OLD evaluation could veto a newer plan.

Second, smaller defect: the block was inferred from `sent`, a field named for delivery that
actually carries the gate outcome at three of `_store_conviction`'s four call sites. The producer
now writes `gate_passed` explicitly and the reader prefers it.

THE FIX IS NOT "IGNORE THE CACHE". A stale record is treated exactly as a MISSING one already was
— no information, fall through to the real gates — never as permission. The decision engine,
`_should_enter()` and every remaining gate still run, so removing a stale veto does not bypass a
genuine risk check.
"""
import json
import pathlib
import re

PT_SRC = (pathlib.Path(__file__).resolve().parents[1]
          / "src/services/paper_trading_engine.py").read_text()
SCHED_SRC = (pathlib.Path(__file__).resolve().parents[1]
             / "src/services/scheduler.py").read_text()


def _gate_block() -> str:
    i = PT_SRC.index("Conviction gate hard-block")
    return PT_SRC[i:PT_SRC.index("# Entry qualifier: Decision Engine is authoritative", i)]


# ── the staleness comparison, EXECUTED from production source ───────────────────────────
#
# An earlier version of this file reimplemented the comparison in the test and asserted against
# the copy. Two sabotages walked straight through it — deleting the comparison, and deleting the
# naive-timestamp normalisation — because the copy still behaved correctly. The snippet below is
# lifted verbatim out of the engine and executed, so the test cannot pass a broken engine.

def _staleness_snippet() -> str:
    i = PT_SRC.index("_cg_stale = False\n", PT_SRC.index("Conviction gate hard-block"))
    j = PT_SRC.index("if _cg_stale:", i)
    block = PT_SRC[i:j]
    return "\n".join(line[16:] if line.startswith(" " * 16) else line.lstrip()
                     for line in block.splitlines())


def _run_staleness(gate_ts, signal_ts):
    """Execute the REAL extracted code and return what it decided."""
    from datetime import datetime, timezone
    env = {"datetime": datetime, "timezone": timezone,
           "_cgdata": {"ts": gate_ts},
           "sig": type("S", (), {"ts": signal_ts})()}
    exec(compile(_staleness_snippet(), "<engine-snippet>", "exec"), env)
    return env["_cg_stale"]


def test_a_gate_that_ran_before_this_signal_is_stale():
    from datetime import datetime, timezone
    assert _run_staleness("2026-09-30T09:30:00+00:00",
                          datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) is True


def test_a_gate_that_ran_after_this_signal_is_current():
    from datetime import datetime, timezone
    assert _run_staleness("2026-09-30T11:05:00+00:00",
                          datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) is False


def test_a_gate_that_ran_at_the_same_instant_is_current():
    """The boundary. Equal timestamps mean the gate saw this signal, so its verdict counts."""
    from datetime import datetime, timezone
    assert _run_staleness("2026-09-30T11:00:00+00:00",
                          datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) is False


def test_a_naive_signal_timestamp_is_treated_as_utc():
    """`Signal.ts` comes out of the DB NAIVE. Comparing naive against aware raises TypeError,
    which the surrounding try/except swallows into 'not stale' — silently restoring the bug. This
    is why the snippet is executed rather than described."""
    from datetime import datetime
    assert _run_staleness("2026-09-30T09:30:00+00:00", datetime(2026, 9, 30, 11, 0)) is True


def test_an_unparseable_gate_timestamp_keeps_the_blocking_behaviour():
    from datetime import datetime, timezone
    assert _run_staleness("not-a-timestamp",
                          datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) is False


def test_a_missing_gate_timestamp_keeps_the_blocking_behaviour():
    from datetime import datetime, timezone
    assert _run_staleness(None, datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) is False


# ── the production block ────────────────────────────────────────────────────────────────

def test_the_reader_compares_the_record_against_the_signal():
    block = _gate_block()
    assert "sig.ts" in block, "the record must be bound to the signal being evaluated"
    assert "_cg_stale" in block


def test_a_stale_record_does_not_block_and_is_counted_separately():
    """It must fall through to the real gates, and be observable when it does — a silent change
    of behaviour here would be indistinguishable from the bug."""
    block = _gate_block()
    i = block.index("if _cg_stale:")
    stale_arm = block[i:block.index("else:", i)]
    assert "continue" not in stale_arm, "a stale record must NOT veto the entry"
    assert "conviction_gate_stale_ignored" in stale_arm, "must be countable"
    assert "paper.entry_gate_stale" in stale_arm


def test_a_current_failed_record_still_blocks():
    """The gate's real purpose is preserved: paper trading must still agree with a conviction
    failure the alert system evaluated against THIS signal."""
    block = _gate_block()
    i = block.index("else:", block.index("if _cg_stale:"))
    fresh_arm = block[i:]
    assert 'conviction_gate' in fresh_arm
    assert "continue" in fresh_arm, "a current failure must still block"


def test_the_reader_prefers_the_explicit_gate_result_over_sent():
    block = _gate_block()
    assert "gate_passed" in block
    assert '_cgdata.get("sent") is False' in block, \
        "records cached before gate_passed existed must still be honoured"


def test_the_producer_writes_the_explicit_gate_result():
    i = SCHED_SRC.index('"sent": sent,')
    assert '"gate_passed": sent,' in SCHED_SRC[i:i + 800]


def test_a_missing_record_still_allows_entry():
    """Unchanged, and the reason the stale path is safe: 'no gate key = gate not yet run' was
    already defined as allow-and-let-the-real-gates-decide."""
    block = _gate_block()
    assert "No gate key = gate not yet run" in block


def test_redis_failure_still_fails_open():
    block = _gate_block()
    assert "fail-open" in block
