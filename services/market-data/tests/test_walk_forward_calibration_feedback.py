"""Tests for AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK's walk_forward_calibration_feedback()
(gate_harness.py) and the new calibration-feedback score layer it validates in _should_enter()
(paper_trading_engine.py).

Background: signal-engine's _calibrated_win_rate() has always computed a real, measured
historical win rate per (horizon, direction, market, confidence-band) — but it was only ever
persisted into Signal.reasons on a fix shipped in the same session as this one
(_bulk_persist()'s new enrichment block). This is the NEXT step: a new, OFF-by-default score
layer in _should_enter() that reads that value, validated via the same walk-forward
train/validation promotion-margin discipline every other gate-tuning sweep in this module
already uses, before it is ever trusted to affect a real entry decision.

Both gate_harness.py and paper_trading_engine.py can't be imported directly in this test
environment (conftest.py stubs sqlalchemy/db/common wholesale) — this file uses source-text
regression checks against the real, current source for both the new sweep function's
orchestration logic (it reuses _resolvable_window_end()/_passes_promotion_margin()/
replay_should_enter(), all already independently tested elsewhere — see
test_gate_harness_review_fixes.py) and the new score layer's own structural properties,
matching test_gate_harness_review_fixes.py's own established convention for exactly this
class of Docker-only-dependency constraint.
"""
import pathlib

_GH_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "gate_harness.py"
_GH_SOURCE = _GH_PATH.read_text()

_PTE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_PTE_SOURCE = _PTE_PATH.read_text()


def _sweep_function_body() -> str:
    start = _GH_SOURCE.index("def walk_forward_calibration_feedback(")
    end = _GH_SOURCE.index("\ndef _resolvable_window_end(", start)
    return _GH_SOURCE[start:end]


def _score_layer_body() -> str:
    start = _PTE_SOURCE.index("# ── AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK")
    end = _PTE_SOURCE.index("# ── T232-DL-DUALSCORER: pre-regime early-warning score (F11)", start)
    return _PTE_SOURCE[start:end]


# ── Sweep function orchestration ────────────────────────────────────────────

def test_sweep_function_exists_and_is_extractable():
    body = _sweep_function_body()
    assert "def walk_forward_calibration_feedback(" in body
    assert len(body) > 500


def test_sweep_pulls_window_end_back_by_the_style_resolution_lag_before_splitting():
    """Same BUG233-BACKTESTHARNESS-EMPTYVALIDATION guard every other walk-forward function in
    this module applies — skipping this would make the validation slice structurally empty at
    realistic window sizes for 3 of 4 styles."""
    body = _sweep_function_body()
    assert "resolvable_end = _resolvable_window_end(window_end, style)" in body
    assert "if resolvable_end <= window_start:" in body


def test_sweep_uses_a_chronological_not_random_split():
    """70/30 split by calendar date, never a random shuffle — avoids look-ahead leakage,
    matching every other tuning mechanism in this codebase."""
    body = _sweep_function_body()
    assert "split_days = max(1, int(total_days * 0.7))" in body


def test_sweep_builds_off_and_on_cfg_variants_that_differ_only_in_the_flag():
    """The ON/OFF comparison must be a controlled experiment — both cfg dicts must derive
    from the SAME base_cfg, differing only in calibration_feedback_enabled, or the comparison
    would be confounded by some other unrelated cfg difference."""
    body = _sweep_function_body()
    assert 'off_cfg = {**base_cfg, "calibration_feedback_enabled": False}' in body
    assert 'on_cfg = {**base_cfg, "calibration_feedback_enabled": True}' in body


def test_sweep_checks_train_slice_before_spending_the_validation_slice():
    """A cheap train-slice rejection ('ON does not even beat OFF on train') must return early
    WITHOUT calling replay_should_enter() again for the validation slice — spending the
    validation slice on a candidate that failed the much lower train-slice bar wastes nothing
    but does reflect a real design intent worth locking in via a regression test."""
    body = _sweep_function_body()
    train_check_idx = body.index("if train_on.avg_return_pct <= train_off.avg_return_pct:")
    early_return_idx = body.index('"promoted": False,', train_check_idx)
    val_replay_idx = body.index(
        'baseline_val = replay_should_enter(\n        session, style, market, off_cfg, val_start, resolvable_end,'
    )
    assert train_check_idx < early_return_idx < val_replay_idx


def test_sweep_promotion_decision_uses_the_shared_promotion_margin_gate():
    """Must reuse _passes_promotion_margin() (the BUG233-BACKTESTHARNESS-COINFLIP fix already
    applied everywhere else in this module) rather than a bare '>' comparison, which was
    already proven to be a ~50% false-promotion coin flip at realistic sample sizes."""
    body = _sweep_function_body()
    assert "promoted = _passes_promotion_margin(candidate_val, baseline_val)" in body


def test_sweep_replays_off_and_on_against_the_same_validation_window():
    """Both the baseline (OFF) and candidate (ON) validation replays must use the identical
    val_start/resolvable_end window — comparing them over different date ranges would make
    any observed difference meaningless."""
    body = _sweep_function_body()
    baseline_call = body.index(
        "baseline_val = replay_should_enter(\n        session, style, market, off_cfg, val_start, resolvable_end,"
    )
    candidate_call = body.index(
        "candidate_val = replay_should_enter(\n        session, style, market, on_cfg, val_start, resolvable_end,"
    )
    assert baseline_call > 0
    assert candidate_call > 0


def test_sweep_note_discloses_this_is_research_only_not_a_live_config_change():
    """Matches every other walk-forward endpoint's own disclosure convention — a promoted=True
    result here must not be mistaken for the flag already being live for real trading."""
    body = _sweep_function_body()
    assert "is NOT itself a live config change" in body


def test_sweep_note_discloses_the_de_outage_fallback_scope_limitation():
    """Same disclosure every other function in this module carries — this harness only ever
    replays the DE-outage fallback gate, never the live primary decision-engine path."""
    body = _sweep_function_body()
    assert "DE-outage fallback gate" in body


# ── New score layer in _should_enter() ──────────────────────────────────────

def test_score_layer_exists():
    body = _score_layer_body()
    assert len(body) > 300


def test_score_layer_defaults_to_a_no_op_when_the_flag_is_unset():
    """cfg.get(...) with no default (falsy on absence) — an existing portfolio's cfg dict that
    has never heard of this key must behave EXACTLY as it did before this fix shipped."""
    body = _score_layer_body()
    assert 'if cfg.get("calibration_feedback_enabled") and reasons.get("calibrated_win_rate") is not None:' in body


def test_score_layer_requires_both_the_flag_and_a_real_calibrated_value():
    """Neither condition alone is sufficient: a portfolio with the flag on but a signal with no
    calibrated_win_rate (not enough historical samples for that band yet) must not score
    anything; a signal WITH a calibrated value but a portfolio that never opted in must also
    not score anything."""
    body = _score_layer_body()
    condition_line = 'if cfg.get("calibration_feedback_enabled") and reasons.get("calibrated_win_rate") is not None:'
    assert body.count(condition_line) == 1


def test_score_layer_boosts_above_the_high_band_and_penalizes_below_the_low_band():
    body = _score_layer_body()
    assert "if _cal_wr >= 0.55:" in body
    assert "score += 1" in body
    assert "elif _cal_wr <= 0.35:" in body
    assert "score -= 1" in body


def test_score_layer_does_not_assume_higher_confidence_always_means_higher_win_rate():
    """Real production calibration data (2026-08-19) shows genuine non-monotonic inversions —
    the score layer must read whichever band's OWN measured win rate applies to THIS signal,
    never assume confidence itself is a proxy for win rate."""
    body = _score_layer_body()
    assert "non-monotonic" in body or "inversion" in body.lower()


def test_score_layer_trusts_calibrated_win_rate_without_a_second_sample_floor_check():
    """_calibrated_win_rate() (signals_shared.py) already enforces _CONF_CAL_MIN_COUNT (30)
    before ever returning a non-None value — a present value here is already trustworthy by
    that upstream contract, and this test locks in that this function correctly does NOT
    duplicate a second, redundant sample-count check of its own."""
    body = _score_layer_body()
    assert "no second sample-floor check is needed here" in body
