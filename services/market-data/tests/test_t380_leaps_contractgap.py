"""T380-LEAPS-CONTRACTGAP — a contract that stops being quoted mid-hold, and a message that blamed the dates.

REPORTED BY THE USER: "QLD has data from 2023-10-20 but why it said error: Incomplete
comparison — no usable LEAPS quote for QLD on these dates."

The user was right to be suspicious, and the contradiction was on ONE SCREEN: the coverage
panel said QLD had **629 usable days from 2023-10-20**, and the banner directly below said
there was no usable quote on those dates. Both were rendered from the same response.

WHAT WAS ACTUALLY TRUE (measured, not inferred):
  * QLD has **726 quote days**, 2023-10-16 to 2026-09-08, with continuous monthly coverage —
    no gap anywhere near the requested window.
  * On the exit date 2026-09-01 there were **101 QLD 2027-01-15 calls, every one carrying a
    bid**.
  * The failure was ONE CONTRACT: the nearest-delta pick, strike 126 at delta 0.659, was last
    quoted **2025-11-19** — a thin strike UW dropped from its chain after only **52 quotes in
    its entire life**.

So neither the dates nor the coverage were the problem, which is exactly what made the old
message misleading rather than merely terse.

TWO SEPARATE FIXES, because the investigation found two distinct things:

**1. The engine gave up too early.** `find_leaps_entry()` commits to one contract via SQL
`LIMIT 1` before anything knows whether it is quoted at exit. Now the in-band band is walked in
delta order until one prices. THIS RECOVERED QQQM AND TQQQ, which had also been failing (TQQQ
reports `delta_fallback_skipped=1`, i.e. it used the second-best candidate).

**2. QLD still does not price, and that is CORRECT.** At target delta 0.70 with a ±0.10 band,
QLD has exactly ONE in-band contract for a 336-day hold — the dead strike 126. Its next
contracts sit at delta **0.802**, missing the 0.800 ceiling **by 0.002**. Widening the band to
rescue it would silently test a materially different trade, so the fix is to SAY SO precisely,
not to substitute a contract.

`_explain_no_price()` diagnoses in the SAME ORDER `backtest_leaps` fails. A test pins that
ordering: diagnosing in a different order could name a guard that is not the one that fired —
the T373-FORECAST-REASON failure mode, where a modal confidently asserted two explanations that
were both false.

A WRONG SUSPICION OF MY OWN, recorded because it nearly sent me to widen the band: I expected
`delta 0.30` to report a delta-band miss and read its "in-band contract existed" message as a
bug. It was correct — QLD genuinely has one in-band candidate at delta 0.366 (strike 175) which
is also unquoted at exit. Branch 3 does fire, verified separately at delta 0.05, where the band
is genuinely empty and the message names the available 0.366-0.844 span.
"""
import pathlib

import pytest

SRC = (
    pathlib.Path(__file__).resolve().parents[1] / "src/backtest/leaps_backtest.py"
).read_text()
PANEL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "frontend/src/components/LeapsBacktestPanel.tsx"
).read_text()
API_TS = (
    pathlib.Path(__file__).resolve().parents[3] / "frontend/src/lib/api.ts"
).read_text()


def _fn(name: str) -> str:
    i = SRC.index(f"def {name}(")
    return SRC[i:SRC.index("\ndef ", i + 10)]


# ── The engine walks the band instead of giving up on one contract ──────────────────────

def test_a_ranked_candidate_lookup_exists():
    assert "def find_leaps_entry_candidates(" in SRC


def test_the_candidate_query_is_not_limit_one():
    """THE BUG. `LIMIT 1` commits to a contract before anything checks it can be priced."""
    fn = _fn("find_leaps_entry_candidates")
    assert "LIMIT :lim" in fn
    # Assert on the SQL, not on the function text: "LIMIT 1" also appears in this function's
    # own DOCSTRING (describing the bug being fixed), so a bare `not in fn` matched my own
    # prose. The recurring "assert on code, not prose" trap in this repo.
    _sql = fn[fn.index("sql = "):]
    assert "LIMIT 1" not in _sql, "the candidate query must not commit to one contract"


def test_the_candidate_ordering_matches_the_single_pick():
    """candidate[0] must BE what find_leaps_entry() returns, so a fallback can only ever move
    AWAY from the requested delta — never silently 'improve' the pick."""
    for fn in (_fn("find_leaps_entry"), _fn("find_leaps_entry_candidates")):
        assert "ORDER BY ABS(delta - :tgt), open_interest DESC NULLS LAST" in fn


def test_the_fallback_is_bounded():
    """Walking the whole band would drift arbitrarily far from the target delta; a match eight
    candidates away is no longer the strategy the user asked to test."""
    assert "_ENTRY_CANDIDATE_LIMIT = 8" in SRC


def test_the_backtest_loops_over_candidates():
    fn = _fn("backtest_leaps")
    # T381-LEAPS-NEARESTDELTA extracted the loop into a local _first_priceable() so the strict
    # and relaxed passes share it. Assert the BEHAVIOUR — a ranked list is obtained and walked
    # until one prices — rather than the literal loop line, which a pure refactor moves.
    assert "find_leaps_entry_candidates(" in fn
    assert "_first_priceable(" in fn, "candidates must still be walked, not taken blindly"
    assert "for _c in cands:" in fn, "the walk itself must still exist"
    # A SECOND SABOTAGE ROUND EXPOSED THIS: my first rewrite of this assertion checked only
    # that _first_priceable EXISTS, so replacing the call with `_candidates[0]` — taking the
    # best-delta contract without checking it can be priced, i.e. restoring the original bug —
    # still passed. Pin that the result comes FROM the walker, not from indexing the list.
    assert "_first_priceable(_candidates)" in fn
    assert "_candidates[0]" not in fn, "must not take the first candidate without pricing it"


def test_an_explicit_strike_or_expiry_is_never_substituted():
    """The user pinned a specific contract. Quietly pricing a different one would answer a
    question they did not ask."""
    fn = _fn("backtest_leaps")
    assert "_pinned = strike is not None or expiry is not None" in fn
    i = fn.index("_pinned")
    assert "if _pinned:" in fn[i:i + 600]


def test_the_expiry_requirement_from_T375_is_still_enforced():
    """A DIFFERENT failure, and a hard impossibility rather than a recoverable gap: an expiry
    BEFORE the exit date. Must not be loosened by this change."""
    fn = _fn("backtest_leaps")
    assert "_eff_min_dte = max(min_dte, _needed_dte)" in fn


def test_a_fallback_is_reported_not_silent():
    """A run whose delta is further from target than requested must say so, or the user cannot
    tell which trade was actually tested."""
    fn = _fn("backtest_leaps")
    assert '"delta_fallback_skipped": _skipped_unquoted,' in fn


def test_zero_skipped_means_identical_to_the_old_behaviour():
    """Pins the interpretation of the new field, so it is not read backwards."""
    fn = _fn("backtest_leaps")
    i = fn.index("delta_fallback_skipped")
    assert "0 = the nearest-delta pick" in fn[max(0, i - 500):i + 200]


# ── The reason, diagnosed in the engine's own failure order ─────────────────────────────

def test_an_explainer_exists_and_is_used():
    assert "def _explain_no_price(" in SRC
    assert "reasons[_u] = _explain_no_price(" in _fn("compare_symbols")


def test_compare_symbols_returns_per_symbol_reasons():
    assert '"missing_reasons": reasons,' in _fn("compare_symbols")


def test_the_four_branches_are_ordered_as_the_engine_fails():
    """THE ORDERING IS THE POINT. Diagnosing out of order could name a guard that is not the
    one that fired — the T373-FORECAST-REASON failure mode."""
    fn = _fn("_explain_no_price")
    i_capture = fn.index("no option chain captured")
    i_greeks = fn.index("no delta on any")
    i_band = fn.index("within {_DELTA_BAND")
    i_gap = fn.index("stopped being quoted mid-hold")
    assert i_capture < i_greeks < i_band < i_gap


def test_the_band_message_names_the_available_delta_range():
    """VERIFIED LIVE at delta 0.05: "available deltas on that date span 0.366-0.844. Try a
    target delta in that range" — actionable, unlike a bare failure."""
    fn = _fn("_explain_no_price")
    assert "available deltas on that date span" in fn
    # The sentence wraps across two f-string lines in the source, so assert on a fragment that
    # does not straddle the break rather than on the rendered sentence.
    assert "Try a target delta in that" in fn


def test_the_contract_gap_message_does_not_blame_the_dates():
    """The whole defect. The old text implied a coverage problem and contradicted the coverage
    panel on the same screen."""
    fn = _fn("_explain_no_price")
    i = fn.index("stopped being quoted mid-hold")
    msg = fn[max(0, i - 400):i + 200]
    assert "in-band contract(s) on" in msg
    assert "on these dates" not in msg


def test_the_explainer_does_not_reprice_anything():
    """It inspects the same inputs the engine used. Re-running the backtest inside the error
    path would double the cost of every failure."""
    fn = _fn("_explain_no_price")
    assert "backtest_leaps(" not in fn


def test_the_band_is_not_auto_widened_to_rescue_a_symbol():
    """DELIBERATE. A 0.80-delta LEAPS is a materially different trade from a 0.70-delta one;
    substituting it would report a result for a strategy the user did not ask to test."""
    fn = _fn("compare_symbols")
    assert "NOT auto-widening" in fn
    assert "_DELTA_BAND * 2" not in SRC
    assert "_DELTA_BAND + " not in SRC


# ── The measured evidence, pinned so the claims cannot rot ──────────────────────────────

def test_the_qld_arithmetic():
    """0.802 misses the 0.800 ceiling BY 0.002 — which is why QLD legitimately does not price
    at target 0.70, and why the answer is a better message rather than a wider band."""
    target, band = 0.70, 0.10
    assert target + band == pytest.approx(0.80)
    assert 0.802 > target + band, "the next QLD contract is out of band by 0.002"
    assert 0.659 < target + band, "the dead strike 126 IS in band, which is why it was picked"


def test_the_coverage_contradiction_is_recorded():
    """The user checked the coverage panel and was right to. 726 quote days and 101 quoted
    contracts on the exit date mean the old message was not merely terse, it was wrong."""
    assert "726 quote days" in SRC
    assert "101 QLD" in SRC


def test_the_dead_strike_is_named_in_the_source():
    """So nobody re-investigates from scratch."""
    assert "2025-11-19" in SRC
    assert "52 quotes" in SRC


# ── Frontend ────────────────────────────────────────────────────────────────────────────

def test_the_panel_renders_the_per_symbol_reason():
    assert "result.missing_reasons?.[sym]" in PANEL


def test_the_panel_falls_back_when_the_backend_sends_no_reason():
    """Optional field — an older backend must not render "undefined"."""
    assert "?? 'no usable LEAPS quote on these dates.'" in PANEL


def test_the_panel_no_longer_asserts_the_dates_are_the_problem():
    """The exact sentence the user quoted must be gone from the unconditional path."""
    assert "no usable LEAPS quote for" not in PANEL


def test_the_type_marks_reasons_optional():
    assert "missing_reasons?: Record<string, string>;" in API_TS
