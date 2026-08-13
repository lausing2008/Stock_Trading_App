"""Tests for _record_de_shadow_comparison()'s agreement logic (T232-DL-DUALSCORER-SHADOW).

Found while investigating why the /paper-portfolio/de-divergences endpoint's backing Redis
lists (de:divergences, de:agreements) were empty in production: not a bug in this function
after all — the candidate-scoring loop that calls it only reaches a real candidate rarely
(dominated by benign "outside_market_hours"/short-TTL causes), confirmed via live log/Redis
inspection, not assumed. But the investigation did surface a real, harmless dead-string bug:
de_agrees checked `de_verdict in ("BUY", "SCALE")` — decision-engine's real verdict vocabulary
is exactly BUY/HOLD/SKIP/BLOCKED (confirmed via grep, "SCALE" never appears anywhere in
services/decision-engine/src/), the same dead-string pattern already found and fixed once this
session in AUD266-TWO-GATES-CONTRADICTORY-BARS's alert-fired gate. Harmless (an always-False
OR-branch changes nothing), fixed for correctness rather than left stale.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models, which the stubbed conftest.py doesn't provide) — the
Redis-write side is source-text-verified; the agreement-logic itself (de_agrees) is exercised
directly via exec()-extraction of the real function body, matching this repo's established
technique for functions with this constraint.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def test_no_dead_scale_string_in_de_agrees_check():
    """Regression guard against the exact dead-string bug this fix closed — decision-engine
    never returns "SCALE" (confirmed via grep against its real source), so comparing against it
    in the real comparison expression is dead code that should not be reintroduced. Checks the
    real executable line specifically (not the whole function body, which legitimately mentions
    "SCALE" in this fix's own explanatory comment) — the same docstring-vs-real-code trap this
    repo's own test-writing history has hit before."""
    start = _pte_source.index("de_agrees = paper_enter == (")
    line_end = _pte_source.index("\n", start)
    line = _pte_source[start:line_end]
    assert '"SCALE"' not in line
    assert 'de_verdict == "BUY"' in line


def _extract_de_agrees(de_verdict: str, paper_enter: bool) -> bool:
    """Exercises the REAL de_agrees expression (extracted from source, not hand-copied) against
    real inputs, so a future edit to this one-line comparison is caught by re-running the exact
    logic in production, not a duplicate that could silently drift from it."""
    start = _pte_source.index("de_agrees = paper_enter == (")
    line_end = _pte_source.index("\n", start)
    line = _pte_source[start:line_end]
    namespace = {"paper_enter": paper_enter, "de_verdict": de_verdict}
    exec(line, namespace)
    return namespace["de_agrees"]


def test_buy_verdict_and_paper_enter_true_agrees():
    assert _extract_de_agrees("BUY", True) is True


def test_buy_verdict_and_paper_enter_false_disagrees():
    assert _extract_de_agrees("BUY", False) is False


def test_hold_verdict_and_paper_enter_false_agrees():
    assert _extract_de_agrees("HOLD", False) is True


def test_hold_verdict_and_paper_enter_true_disagrees():
    assert _extract_de_agrees("HOLD", True) is False


def test_skip_verdict_and_paper_enter_false_agrees():
    assert _extract_de_agrees("SKIP", False) is True


def test_blocked_verdict_and_paper_enter_false_agrees():
    assert _extract_de_agrees("BLOCKED", False) is True


def test_a_verdict_of_scale_never_counts_as_agreement_with_a_real_entry():
    """The exact case the dead-string bug would have mishandled differently: decision-engine
    can never actually return "SCALE" in production, but if it somehow did (a malformed/future
    response), this must be treated as NOT a BUY — de_agrees requires paper_enter to be False
    for this verdict, not True."""
    assert _extract_de_agrees("SCALE", True) is False
    assert _extract_de_agrees("SCALE", False) is True
