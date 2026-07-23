"""Tests for T233-SELFIMPROVE-PHASE4's EV backtest gate (ev_gate.py).

ev_gate.py has zero DB/network/env dependency — pure numpy functions — but `src.training`'s
own __init__.py re-exports from trainer.py, which pulls in the full model registry (including
lightgbm, not installed in this local dev environment) — the same "package __init__ drags in
an unrelated heavy dependency" constraint already documented for meta_trainer.py's tests in
this same directory. Loaded via a direct file-spec import (bypassing src.training.__init__)
rather than a normal `from src.training.ev_gate import ...`, matching test_promotion_history.py's
established pattern for this exact class of import problem.
"""
import importlib.util as _ilu
import pathlib as _pathlib

import numpy as np
import pytest

_ev_gate_path = _pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "ev_gate.py"
_spec = _ilu.spec_from_file_location("ev_gate_test", _ev_gate_path)
_ev_gate_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_ev_gate_mod)

MIN_HOLDOUT_SIGNALED_ROWS = _ev_gate_mod.MIN_HOLDOUT_SIGNALED_ROWS
REFERENCE_PROB_THRESHOLD = _ev_gate_mod.REFERENCE_PROB_THRESHOLD
compute_holdout_ev = _ev_gate_mod.compute_holdout_ev
evaluate_candidate_ev = _ev_gate_mod.evaluate_candidate_ev


def _rows(n_signaled_positive, n_signaled_negative, n_unsignaled, pos_return=0.05, neg_return=-0.03):
    """Build (probs, y_ret) arrays: n_signaled_positive rows cross the threshold with a real
    positive forward return, n_signaled_negative cross with a negative return, n_unsignaled
    stay below the threshold (return value irrelevant, never read for those rows)."""
    probs = np.concatenate([
        np.full(n_signaled_positive, 0.90),
        np.full(n_signaled_negative, 0.90),
        np.full(n_unsignaled, 0.10),
    ])
    y_ret = np.concatenate([
        np.full(n_signaled_positive, pos_return),
        np.full(n_signaled_negative, neg_return),
        np.full(n_unsignaled, 0.0),
    ])
    return probs, y_ret


class TestComputeHoldoutEv:
    def test_ev_pct_is_mean_return_among_signaled_rows_only(self):
        probs, y_ret = _rows(n_signaled_positive=8, n_signaled_negative=7, n_unsignaled=50, pos_return=0.10, neg_return=-0.02)
        result = compute_holdout_ev(probs, y_ret)
        expected = (8 * 0.10 + 7 * -0.02) / 15 * 100.0
        assert result["n"] == 15
        assert result["ev_pct"] == pytest.approx(expected, abs=1e-6)

    def test_unsignaled_rows_never_affect_ev(self):
        """Rows below threshold must not leak into the EV computation even if they'd move
        the mean dramatically — proves the threshold mask is actually applied to y_ret too,
        not just used to count n."""
        probs, y_ret = _rows(n_signaled_positive=12, n_signaled_negative=0, n_unsignaled=3, pos_return=0.05)
        # Poison the "unsignaled" returns with an extreme value that would wreck the mean
        # if it leaked in.
        y_ret[-3:] = 999.0
        result = compute_holdout_ev(probs, y_ret)
        assert result["ev_pct"] == pytest.approx(5.0, abs=1e-6)

    def test_below_min_signaled_rows_returns_none_not_zero(self):
        probs, y_ret = _rows(n_signaled_positive=3, n_signaled_negative=2, n_unsignaled=50)
        result = compute_holdout_ev(probs, y_ret)
        assert result["n"] == 5
        assert result["n"] < MIN_HOLDOUT_SIGNALED_ROWS
        assert result["ev_pct"] is None

    def test_exactly_at_min_signaled_rows_is_measurable(self):
        n = MIN_HOLDOUT_SIGNALED_ROWS
        probs, y_ret = _rows(n_signaled_positive=n, n_signaled_negative=0, n_unsignaled=20, pos_return=0.02)
        result = compute_holdout_ev(probs, y_ret)
        assert result["n"] == n
        assert result["ev_pct"] is not None

    def test_custom_threshold_is_respected(self):
        probs = np.array([0.55, 0.65, 0.75]).repeat(15)
        y_ret = np.array([0.01, 0.02, 0.03]).repeat(15)
        # threshold 0.70 should only pick up the 0.75 rows
        result = compute_holdout_ev(probs, y_ret, threshold=0.70)
        assert result["n"] == 15
        assert result["ev_pct"] == pytest.approx(3.0, abs=1e-6)

    def test_default_threshold_matches_module_constant(self):
        assert REFERENCE_PROB_THRESHOLD == 0.60


class TestEvaluateCandidateEv:
    def test_no_baseline_params_promotes_automatically(self):
        """First-ever tune for a symbol — nothing to beat, matches tune_symbol()'s pre-existing
        behavior of always persisting on a first tune."""
        candidate_probs, y_ret = _rows(n_signaled_positive=15, n_signaled_negative=0, n_unsignaled=30, pos_return=0.01)
        result = evaluate_candidate_ev(candidate_probs, None, y_ret)
        assert result["promoted"] is True
        assert result["baseline_ev"] is None
        assert "no_baseline_params:first_tune_for_symbol" in result["gate_failures"]

    def test_candidate_beats_baseline_is_promoted(self):
        # Both candidate and baseline are scored on the SAME holdout rows (apples-to-apples) —
        # they differ only in WHICH rows each one's own predicted probability crosses the
        # threshold on, exactly as two differently-trained models would in practice.
        y_ret = np.concatenate([np.full(20, 0.08), np.full(20, -0.05), np.full(20, 0.0)])
        cand_probs = np.concatenate([np.full(20, 0.90), np.full(20, 0.10), np.full(20, 0.10)])  # signals only the +0.08 rows
        base_probs = np.concatenate([np.full(20, 0.10), np.full(20, 0.90), np.full(20, 0.10)])  # signals only the -0.05 rows
        result = evaluate_candidate_ev(cand_probs, base_probs, y_ret)
        assert result["candidate_ev"]["ev_pct"] == pytest.approx(8.0, abs=1e-6)
        assert result["baseline_ev"]["ev_pct"] == pytest.approx(-5.0, abs=1e-6)
        assert result["promoted"] is True
        assert result["gate_failures"] == []

    def test_candidate_loses_to_baseline_is_rejected(self):
        y_ret = np.concatenate([np.full(20, -0.05), np.full(20, 0.08), np.full(20, 0.0)])
        cand_probs = np.concatenate([np.full(20, 0.90), np.full(20, 0.10), np.full(20, 0.10)])  # signals the -0.05 rows
        base_probs = np.concatenate([np.full(20, 0.10), np.full(20, 0.90), np.full(20, 0.10)])  # signals the +0.08 rows
        result = evaluate_candidate_ev(cand_probs, base_probs, y_ret)
        assert result["promoted"] is False
        assert any("ev_lift_not_positive" in f for f in result["gate_failures"])

    def test_exactly_equal_ev_is_rejected_not_promoted(self):
        """A zero lift must not be treated as an improvement — matches this codebase's
        established 'unconditional rejection of non-positive EV lift' convention
        (T255-STRATEGY-TUNER-PER-HORIZON hit and fixed this exact tie case)."""
        probs, y_ret = _rows(n_signaled_positive=15, n_signaled_negative=0, n_unsignaled=20, pos_return=0.03)
        result = evaluate_candidate_ev(probs, probs.copy(), y_ret)
        assert result["promoted"] is False
        assert any("ev_lift_not_positive:0.0000pp" in f for f in result["gate_failures"])

    def test_candidate_unmeasurable_is_rejected(self):
        y_ret = np.zeros(60)
        cand_probs = np.full(60, 0.10)  # never crosses threshold
        base_probs = np.full(60, 0.90)
        result = evaluate_candidate_ev(cand_probs, base_probs, y_ret)
        assert result["promoted"] is False
        assert any("candidate_ev_unmeasurable" in f for f in result["gate_failures"])

    def test_baseline_unmeasurable_but_candidate_measurable_promotes(self):
        y_ret = np.concatenate([np.full(15, 0.02), np.full(45, 0.0)])
        cand_probs = np.concatenate([np.full(15, 0.90), np.full(45, 0.10)])
        base_probs = np.full(60, 0.10)  # baseline never crosses threshold
        result = evaluate_candidate_ev(cand_probs, base_probs, y_ret)
        assert result["promoted"] is True
        assert any("baseline_ev_unmeasurable" in f for f in result["gate_failures"])

    def test_returned_dict_always_has_candidate_ev_key(self):
        """Every branch must populate candidate_ev — a caller (tuner.py) reads it unconditionally
        for logging."""
        probs, y_ret = _rows(n_signaled_positive=2, n_signaled_negative=0, n_unsignaled=5)
        result = evaluate_candidate_ev(probs, None, y_ret)
        assert "candidate_ev" in result
        result2 = evaluate_candidate_ev(probs, probs.copy(), y_ret)
        assert "candidate_ev" in result2
