"""AUD-CONVGATE-IDENTITY + SR-01: a cached conviction result must be about the signal being
evaluated — and every consumer must agree on that.

THE ORIGINAL DEFECT. `_scan_for_entries` read `conv_gate:{symbol}:{style}` and blocked entry
whenever the record said `signal == "BUY"` and `sent is False` — without binding the record to
the signal now under consideration. The record carries a **24-hour TTL**, so a gate that failed
against the 09:30 refresh went on blocking entries for the rest of the trading day, including
against a NEWER signal the gate had never seen and might well have passed. Second, smaller
defect: the block was inferred from `sent`, a field named for delivery that actually carries the
gate outcome at three of `_store_conviction`'s four call sites.

SR-01 (2026-10-02) — THE FIX WAS ONLY HALF APPLIED. `_scan_for_entries` was repaired; the
AUTHORITATIVE decision engine it calls a few lines later was not, and still read the raw record
with no identity check at all. So a September 30 failed record went on vetoing an October 1
signal through the authoritative path, reporting "old signal failed". A local fix that a
downstream consumer overrides is not a fix.

The interpretation now lives in ONE place — `shared/common/conviction_gate.py` — and both
consumers call it. These tests therefore execute the real contract rather than a snippet lifted
from one caller, and separately assert that neither caller has grown its own copy again.

WHAT THE FIX IS NOT. A stale record is treated exactly as a MISSING one already was: no
information, fall through to the real gates, never permission. Every remaining gate still runs.
"""
import pathlib
import sys
from datetime import datetime, timezone

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "shared"))

PT_SRC = (pathlib.Path(__file__).resolve().parents[1]
          / "src/services/paper_trading_engine.py").read_text()
SCHED_SRC = (pathlib.Path(__file__).resolve().parents[1]
             / "src/services/scheduler.py").read_text()
DE_SRC = (_ROOT / "services/decision-engine/src/api/core/hard_rejects.py").read_text()

from common.conviction_gate import (  # noqa: E402
    ALLOWS, BLOCKS, NO_INFORMATION, evaluate_conviction_record,
)


def _record(ts, *, gate_passed=False, signal="BUY", signal_ts=None, **extra):
    rec = {"signal": signal, "gate_passed": gate_passed, "ts": ts,
           "failed": ["ML probability"], **extra}
    if signal_ts is not None:
        rec["signal_ts"] = signal_ts
    return rec


def _verdict(gate_ts, signal_ts, **kw):
    """Execute the REAL shared contract — never a description of it."""
    return evaluate_conviction_record(_record(gate_ts, **kw), signal_ts=signal_ts).verdict


# ── the staleness floor, executed ───────────────────────────────────────────────────────

def test_a_gate_that_ran_before_this_signal_carries_no_information():
    assert _verdict("2026-09-30T09:30:00+00:00",
                    datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) == NO_INFORMATION


def test_a_gate_that_ran_after_this_signal_still_decides():
    assert _verdict("2026-09-30T11:05:00+00:00",
                    datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) == BLOCKS


def test_a_gate_that_ran_at_the_same_instant_decides():
    """The boundary. Equal timestamps mean the gate saw this signal, so its verdict counts."""
    assert _verdict("2026-09-30T11:00:00+00:00",
                    datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) == BLOCKS


def test_a_naive_signal_timestamp_is_treated_as_utc():
    """`Signal.ts` comes out of the DB NAIVE. Comparing naive against aware raises TypeError,
    which a surrounding try/except swallows into 'not stale' — silently restoring the bug."""
    assert _verdict("2026-09-30T09:30:00+00:00",
                    datetime(2026, 9, 30, 11, 0)) == NO_INFORMATION


def test_an_unparseable_gate_timestamp_keeps_the_blocking_behaviour():
    assert _verdict("not-a-timestamp",
                    datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) == BLOCKS


def test_a_missing_gate_timestamp_keeps_the_blocking_behaviour():
    assert _verdict(None, datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc)) == BLOCKS


# ── SR-01: identity beats the timestamp floor ───────────────────────────────────────────

def test_a_record_naming_a_different_signal_carries_no_information():
    """THE CASE THE TIMESTAMP FLOOR CANNOT CATCH. A gate that ran AFTER this signal was
    published passes the staleness test — but if it judged a different signal, it still says
    nothing about this one."""
    rec = _record("2026-10-01T15:30:00+00:00", signal_ts="2026-09-30T09:30:00+00:00")
    assert evaluate_conviction_record(
        rec, signal_ts="2026-10-01T14:55:00+00:00").verdict == NO_INFORMATION


def test_a_record_naming_this_signal_decides():
    rec = _record("2026-10-01T15:30:00+00:00", signal_ts="2026-10-01T14:55:00+00:00")
    assert evaluate_conviction_record(
        rec, signal_ts="2026-10-01T14:55:00+00:00").verdict == BLOCKS


def test_signal_id_mismatch_beats_every_timestamp():
    rec = _record("2026-10-01T15:30:00+00:00", signal_ts="2026-10-01T14:55:00+00:00",
                  signal_id=41)
    assert evaluate_conviction_record(
        rec, signal_ts="2026-10-01T14:55:00+00:00", signal_id=77).verdict == NO_INFORMATION


def test_the_production_witness_no_longer_vetoes():
    """SR-01's exact reported case: a September 30 failed record against an October 1 signal."""
    rec = _record("2026-09-30T18:00:00+00:00", failed=["old signal failed"])
    assert evaluate_conviction_record(
        rec, signal_ts="2026-10-01T14:55:00+00:00").verdict == NO_INFORMATION


# ── the verdict itself ──────────────────────────────────────────────────────────────────

def test_a_current_failed_record_still_blocks():
    """The gate's real purpose is preserved."""
    v = evaluate_conviction_record(
        _record("2026-09-30T11:00:00+00:00", gate_passed=False, failed=["ADX", "OBV", "MACD"]),
        signal_ts=datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc))
    assert v.is_block and v.failed_layers == ["ADX", "OBV", "MACD"]
    assert "ADX" in v.reason and "MACD" not in v.reason, "only the first two are reported"


def test_a_passing_record_allows_and_is_informative():
    v = evaluate_conviction_record(
        _record("2026-09-30T11:00:00+00:00", gate_passed=True),
        signal_ts=datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc))
    assert v.verdict == ALLOWS and v.informative and not v.is_block


def test_the_reader_prefers_the_explicit_gate_result_over_sent():
    """A DELIVERY failure must never read as a CONVICTION failure."""
    sent_failed_gate_passed = {"signal": "BUY", "gate_passed": True, "sent": False,
                               "ts": "2026-09-30T11:00:00+00:00", "failed": []}
    v = evaluate_conviction_record(sent_failed_gate_passed,
                                   signal_ts="2026-09-30T11:00:00+00:00")
    assert v.verdict == ALLOWS, "the gate passed; the email merely did not go out"
    assert v.detail["outcome_field"] == "gate_passed"


def test_a_legacy_record_without_gate_passed_still_honours_sent():
    legacy = {"signal": "BUY", "sent": False, "ts": "2026-09-30T11:00:00+00:00",
              "failed": ["ADX"]}
    v = evaluate_conviction_record(legacy, signal_ts="2026-09-30T11:00:00+00:00")
    assert v.is_block and v.detail["outcome_field"] == "sent (legacy record)"


@pytest.mark.parametrize("raw", [None, "", b"", "{not json", "[]", '"a string"'])
def test_unusable_records_carry_no_information_and_never_raise(raw):
    assert evaluate_conviction_record(raw, signal_ts="2026-09-30T11:00:00+00:00").verdict \
        == NO_INFORMATION


def test_a_non_buy_record_says_nothing_about_a_buy():
    assert evaluate_conviction_record(
        _record("2026-09-30T11:00:00+00:00", signal="SELL"),
        signal_ts="2026-09-30T11:00:00+00:00").verdict == NO_INFORMATION


# ── parity: ONE implementation, two callers ─────────────────────────────────────────────

def test_both_consumers_call_the_shared_contract():
    """SR-01's actual lesson. The defect was not that the logic was wrong in one place — it was
    that two readers of the same record disagreed."""
    assert "evaluate_conviction_record" in PT_SRC, "paper engine must use the shared contract"
    assert "evaluate_conviction_record" in DE_SRC, "decision engine must use the shared contract"


def test_neither_consumer_reimplements_the_verdict():
    """The copy is what rotted last time. `sent is False` appearing in a caller again means a
    second interpretation has grown back."""
    for name, src in (("paper_trading_engine", PT_SRC), ("hard_rejects", DE_SRC)):
        i = src.index("evaluate_conviction_record")
        window = src[max(0, i - 2000):i + 2000]
        assert '.get("sent") is False' not in window, \
            f"{name} re-derives the verdict instead of asking the contract"


def _de_conviction_block() -> str:
    """The decision engine's conviction block, sliced on REAL boundaries.

    Not a fixed character count: an earlier version of these assertions used one and silently
    cut the block in half, so the test was reading a window that happened to exclude what it
    was checking for.
    """
    i = DE_SRC.index("if symbol and style:", DE_SRC.index("SR-01 (2026-10-02)"))
    return DE_SRC[i:DE_SRC.index("\n    return None", i)]


def _pt_conviction_block() -> str:
    i = PT_SRC.index("from common.conviction_gate import")
    return PT_SRC[i:PT_SRC.index("# Entry qualifier: Decision Engine is authoritative", i)]


def test_the_decision_engine_no_longer_blocks_on_an_unbound_record():
    block = _de_conviction_block()
    assert "signal_ts=" in block, "the record must be bound to the signal being evaluated"
    assert "_verdict.is_block" in block, "only a real block may veto"


def test_a_no_information_verdict_is_counted_in_the_paper_scan():
    """Falling through must stay observable — a silent change here is indistinguishable from
    the bug it replaced."""
    block = _pt_conviction_block()
    assert "conviction_gate_no_information" in block
    assert "paper.entry_gate_no_information" in block
    i = block.index("if _verdict.is_block:")
    assert "continue" in block[i:block.index("if not _verdict.informative:", i)], \
        "a real conviction failure must still block the entry"


# ── the producer ────────────────────────────────────────────────────────────────────────

def test_the_producer_writes_the_explicit_gate_result():
    i = SCHED_SRC.index('"sent": sent,')
    assert '"gate_passed": sent,' in SCHED_SRC[i:i + 800]


def test_the_producer_stamps_the_signal_it_judged():
    """SR-01: evaluation time can only rule a record OUT. Identity is what confirms the record
    judged THIS signal."""
    i = SCHED_SRC.index('"signal": signal,')
    window = SCHED_SRC[i:i + 1400]
    assert '"signal_ts": signal_ts,' in window
    assert '"contract_version"' in window


def test_every_producer_call_site_passes_the_signal_identity():
    import ast
    tree = ast.parse(SCHED_SRC)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_store_conviction"]
    assert len(calls) >= 4, f"expected every call site, found {len(calls)}"
    for call in calls:
        kw = {k.arg for k in call.keywords}
        assert "signal_ts" in kw, f"call at line {call.lineno} stores no signal identity"
