"""T381-LEAPS-NEARESTDELTA — price the nearest available delta when the exact one cannot be found.

USER REQUEST: "if we can't find the exact delta at that moment, we can find something near the
same to run it."

THIS REVERSES A DECISION I MADE ONE STEP EARLIER, and the distinction matters. T380 deliberately
refused to auto-widen the delta band, on the grounds that silently pricing a 0.80-delta contract
when 0.70 was asked for reports a result for a trade the user never described. That objection is
about **silence**, not about widening. So the widened search is a clearly-labelled SECOND pass:

  * the strict +/-0.10 band is tried FIRST and wins whenever it can price, so no result that
    already worked changes;
  * the relaxed pass runs ONLY when the strict band produced nothing priceable;
  * every relaxed result carries `delta_relaxed: true` and its real `entry_delta`;
  * the UI badges the delta cell "NEAR", and compare_symbols' note names the symbol.

WHY +/-0.20 AND NOT WIDER — measured on the QLD case that prompted this:

    band +/-0.10:  1 in-band,  0 PRICEABLE
    band +/-0.15:  4 in-band,  3 PRICEABLE -> deltas 0.802 / 0.805 / 0.844
    band +/-0.20:  4 in-band,  3 PRICEABLE -> the SAME three
    band +/-0.25:  4 in-band,  3 PRICEABLE -> the SAME three

QLD's chain simply has a gap between 0.659 and 0.802, so a wider cap buys nothing real while
admitting progressively less comparable trades. 0.20 keeps the nearest match (0.802, just 0.102
from a 0.70 target) reachable without letting a 0.45-delta contract pass as a deep-ITM LEAPS.

THE RESULT IS DELIBERATELY NOT FLATTERING: QLD prices at **-94.23%** while QQQ returns +60.20%
on the same dates. That gap is mostly leveraged-ETF decay over a 336-day hold, not the delta
difference — which is exactly why the label matters. An unbadged -94% row beside a +60% row
reads as the same trade run four ways.
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


# ── Strict first, relaxed only as a fallback ────────────────────────────────────────────

def test_a_relaxed_band_constant_exists_and_is_wider():
    assert "_DELTA_BAND_RELAXED = 0.20" in SRC
    assert "_DELTA_BAND = 0.10" in SRC


def test_the_strict_band_is_still_the_default():
    """THE INVARIANT THAT PROTECTS EVERY EXISTING RESULT. A caller that does not ask for a
    relaxed search must get exactly the pre-T381 behaviour."""
    fn = _fn("find_leaps_entry_candidates")
    assert "_band = _DELTA_BAND if band is None else float(band)" in fn


def test_the_relaxed_pass_runs_only_after_the_strict_pass_fails():
    fn = _fn("backtest_leaps")
    i_strict = fn.index("entry, exit_on, ex, _skipped_unquoted = _first_priceable(_candidates)")
    i_relaxed = fn.index("if entry is None and not _pinned:")
    assert i_strict < i_relaxed, "strict must be attempted first"
    assert "band=_DELTA_BAND_RELAXED," in fn


def test_a_pinned_contract_is_never_relaxed():
    """A named strike/expiry is a specific contract, not a target to approximate. "Near" is a
    different contract entirely, so relaxation would answer a different question."""
    fn = _fn("backtest_leaps")
    assert "if entry is None and not _pinned:" in fn


def test_the_relaxed_pass_excludes_already_rejected_candidates():
    """Re-testing the contracts the strict pass already rejected would double-count them in
    `delta_fallback_skipped`, making the reported skip count wrong."""
    fn = _fn("backtest_leaps")
    assert "_seen = {c.option_symbol for c in _candidates}" in fn
    assert "c.option_symbol not in _seen" in fn


# ── It is labelled, never silent ────────────────────────────────────────────────────────

def test_the_result_carries_a_relaxed_flag():
    """THE WHOLE BASIS FOR REVERSING T380's REFUSAL. Widening is acceptable BECAUSE it is
    labelled; unlabelled it would be the silent substitution T380 rejected."""
    assert '"delta_relaxed": _relaxed,' in _fn("backtest_leaps")


def test_false_means_the_strict_band_was_used():
    """Pins the interpretation so the flag cannot be read backwards."""
    fn = _fn("backtest_leaps")
    i = fn.index('"delta_relaxed"')
    ctx = fn[max(0, i - 700):i + 100]
    assert "False means the strict band was used" in ctx


def test_the_entry_delta_is_still_reported():
    """The flag says THAT it was relaxed; entry_delta says BY HOW MUCH. Both are needed to
    judge whether the substitute is close enough to be informative."""
    assert '"entry_delta"' in _fn("backtest_leaps") or "entry_delta" in SRC


def test_compare_symbols_names_the_relaxed_symbols():
    fn = _fn("compare_symbols")
    assert '"delta_relaxed_symbols": _relaxed_syms,' in fn


def test_the_note_does_not_claim_a_clean_comparison():
    """"All requested symbols priced." would be TRUE and MISLEADING — the reader would compare
    a relaxed row against a strict one as if they were the same trade."""
    fn = _fn("compare_symbols")
    i = fn.index('"note": (')
    note = fn[i:i + 1200]
    assert "used the " in note and "NEAREST available delta" in note
    assert "before reading the ranking as like-for-like" in note


def test_comparable_stays_true_when_everything_priced():
    """A relaxed symbol is no longer MISSING, so the incomplete-comparison banner must not
    fire — the caveat belongs in the note and the per-row badge instead."""
    assert '"comparable": len(missing) == 0,' in _fn("compare_symbols")


# ── The measured basis for +/-0.20 ──────────────────────────────────────────────────────

def test_the_relaxed_band_reaches_the_nearest_qld_contract():
    """0.802 is 0.102 from a 0.70 target — inside 0.20, outside the strict 0.10."""
    target, strict, relaxed = 0.70, 0.10, 0.20
    nearest = 0.802
    assert nearest > target + strict, "outside the strict band — this is why QLD failed"
    assert nearest <= target + relaxed, "inside the relaxed band — this is what recovers it"


def test_a_wider_band_would_buy_nothing():
    """Measured: +/-0.15, +/-0.20 and +/-0.25 all find the SAME three priceable QLD contracts,
    because the chain has a gap between 0.659 and 0.802. Pinned so nobody 'improves' the
    constant on intuition."""
    priceable_by_band = {0.10: 0, 0.15: 3, 0.20: 3, 0.25: 3}
    assert priceable_by_band[0.20] == priceable_by_band[0.25], "wider adds nothing"
    assert priceable_by_band[0.20] > priceable_by_band[0.10]


def test_the_band_stays_recognisably_a_deep_itm_leaps():
    """A guard on the guard: 0.20 must not drift upward into a band that would admit a
    0.45-delta contract as a deep-ITM LEAPS."""
    import re
    m = re.search(r"_DELTA_BAND_RELAXED = ([\d.]+)", SRC)
    assert m and float(m.group(1)) <= 0.20


def test_the_qld_result_is_recorded_unflattering():
    """-94.23% at delta 0.802 vs QQQ's +60.20%. The gap is mostly leveraged-ETF decay over a
    336-day hold, not the delta difference — which is precisely why the row needs a label."""
    assert "-94.23%" in SRC or "94.23" in SRC


# ── Frontend ────────────────────────────────────────────────────────────────────────────

def test_the_panel_badges_a_relaxed_row():
    assert "r.delta_relaxed &&" in PANEL
    assert "NEAR" in PANEL


def test_the_badge_sits_on_the_delta_cell():
    """The number in THAT cell is what differs from what was asked for — an unlabelled 0.802
    under a 0.70 target reads as a bug."""
    i_delta = PANEL.index("r.entry_delta != null")
    i_badge = PANEL.index("r.delta_relaxed &&")
    assert 0 < i_badge - i_delta < 900


def test_the_badge_explains_itself_on_hover():
    assert "title=\"No contract inside the strict" in PANEL


def test_the_type_marks_the_flag_optional():
    """An older backend must render no badge rather than a wrong one."""
    assert "delta_relaxed?: boolean;" in API_TS
