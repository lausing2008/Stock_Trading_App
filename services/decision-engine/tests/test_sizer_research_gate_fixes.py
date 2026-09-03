"""Regression tests for AUD-DECIDE2 (Decision-Making deep audit, 2026-09-03):
- AUD-DECIDE2-SIZERFALSYZERO: sizer.py's research-gate chain used `research_score_val or 0`,
  the same falsy-zero pattern already fixed once in the exact sibling variable at scorer.py's
  own T247-DECISIONENGINE-RESEARCHSCORE-FALSY, never swept to sizer.py.
- AUD-DECIDE2-INSUFFICIENTDATA: research-engine's real "INSUFFICIENT DATA" verdict (emitted
  when a report's own quality is "fallback") fell through every branch in both sizer.py's
  research_mult chain and scorer.py's _RESEARCH_SCORE table, silently scoring/sizing it
  identically to "no research was attempted" rather than "research explicitly failed."
"""
from src.api.core.sizer import compute_position
from src.api.core.scorer import _RESEARCH_SCORE


def _make_game_plan(live_price=100.0):
    return {
        "stop": live_price * 0.90,
        "take_profit": live_price * 1.30,
    }


def _cfg():
    return {}


def _compute(research_rec, research_score_val, confidence=60.0):
    _, mults = compute_position(
        equity=100_000.0, live_price=100.0, game_plan=_make_game_plan(),
        confidence=confidence, research_rec=research_rec, research_score_val=research_score_val,
        regime_state="bull", cross_style_buys=0, days_to_earnings=None, cfg=_cfg(),
    )
    return mults.research


# ── AUD-DECIDE2-SIZERFALSYZERO ──────────────────────────────────────────────────────────

def test_genuine_zero_research_score_still_fails_the_75_gate_for_strong_buy():
    """A genuine overall_score of 0.0 (the worst real score) must correctly fail the >=75
    gate — same practical outcome as before the fix, but now via an honest is-not-None check
    rather than a falsy-zero coercion that happened to produce the same answer by accident."""
    assert _compute("STRONG BUY", 0.0) == 0.60


def test_genuine_zero_research_score_still_fails_the_65_gate_for_buy():
    assert _compute("BUY", 0.0) == 0.60


def test_none_research_score_also_fails_every_gate():
    assert _compute("STRONG BUY", None) == 0.60
    assert _compute("BUY", None) == 0.60
    assert _compute("WATCH", None) == 0.60


def test_a_real_score_that_clears_the_gate_still_gets_the_boosted_multiplier():
    assert _compute("STRONG BUY", 80.0) == 1.20
    assert _compute("BUY", 70.0) == 1.00
    assert _compute("WATCH", 65.0) == 0.80


def test_a_real_score_just_below_the_gate_gets_the_de_weighted_multiplier():
    assert _compute("STRONG BUY", 74.9) == 0.60
    assert _compute("BUY", 64.9) == 0.60


# ── AUD-DECIDE2-INSUFFICIENTDATA ────────────────────────────────────────────────────────

def test_insufficient_data_is_de_weighted_not_treated_as_no_research():
    """The core fix: INSUFFICIENT DATA must land in the same 0.60 de-weighted bucket as a
    recommendation that exists but doesn't clear its own confidence bar — NOT the neutral
    1.00 multiplier a symbol with zero research coverage gets."""
    assert _compute("INSUFFICIENT DATA", None) == 0.60


def test_insufficient_data_with_a_real_score_value_is_still_de_weighted():
    """Even if research-engine happens to attach some numeric score alongside an
    INSUFFICIENT DATA verdict, it must not accidentally clear a boost gate meant for a
    genuine STRONG BUY/BUY/WATCH recommendation."""
    assert _compute("INSUFFICIENT DATA", 90.0) == 0.60


def test_no_research_rec_at_all_is_still_the_true_neutral_case():
    """Distinguishes 'no research object was ever attached' (still neutral 1.00) from
    'research ran and explicitly reported INSUFFICIENT DATA' (0.60) — these must not collapse
    to the same multiplier."""
    assert _compute(None, None) == 1.00


def test_insufficient_data_scores_negative_one_not_zero_in_the_scorer_table():
    """scorer.py's own _RESEARCH_SCORE table must not silently treat INSUFFICIENT DATA the
    same as a real, neutral WATCH recommendation (both would otherwise score 0)."""
    assert _RESEARCH_SCORE["INSUFFICIENT DATA"] == -1
    assert _RESEARCH_SCORE["WATCH"] == 0
    assert _RESEARCH_SCORE["INSUFFICIENT DATA"] != _RESEARCH_SCORE["WATCH"]


def test_case_and_underscore_insensitive_matching_still_works_for_insufficient_data():
    """rec_upper normalizes via .upper().replace('_', ' ') before comparison — confirm the
    real research-engine value (whichever underscore/casing form it emits) still matches."""
    assert _compute("insufficient_data", None) == 0.60
    assert _compute("Insufficient Data", None) == 0.60
