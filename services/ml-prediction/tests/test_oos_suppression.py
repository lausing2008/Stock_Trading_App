"""Tests for _compute_oos_suppression() in trainer.py (AUD-ML2-DEADRECALLNOTSUPPRESSED,
AUD-ML2-ASYMMETRICOVERFITGAP — Model Training deep audit, 2026-09-03).

Confirmed live in production: 9961.HK's random_forest model had auc=1.0, recall=0.0,
precision=0.0, cv_auc_mean=0.716 — clearing the pre-existing SA-9 suppression gate
(cv_auc_mean < 0.52) despite having never once correctly predicted a true positive on its own
held-out test slice. Its xgboost sibling for the same symbol showed the identical pathology
(auc=0.875, recall=0.0, cv_auc_mean=0.683). Separately, 40 of 249 production model artifacts
(16%) showed overfit_gap < -0.2 (test-AUC dramatically HIGHER than CV-AUC) — the OPPOSITE
direction from the pre-existing ML-FIX-4 check, which only ever fires when CV-AUC > test-AUC.

_compute_oos_suppression() is loaded via exec() of its own real source slice (matching
test_feature_ablation.py's established convention for a pure function in a module — trainer.py
— whose OTHER module-level imports (xgboost, torch, lightgbm) make a real import too heavy for
a focused unit test of one small decision function).
"""
import pathlib

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "trainer.py"
_SOURCE = _MODULE_PATH.read_text()

_start = _SOURCE.index("def _compute_oos_suppression(")
_end = _SOURCE.index("\n\ndef _load_outcome_features(", _start)
_namespace = {}
exec(_SOURCE[_start:_end], _namespace)  # noqa: S102 — isolated eval of one pure function's real source
_compute_oos_suppression = _namespace["_compute_oos_suppression"]


def test_low_cv_auc_alone_suppresses():
    suppressed, reason = _compute_oos_suppression(0.45, recall=0.6, precision=0.6, overfit_gap_val=0.02)
    assert suppressed is True
    assert reason == "cv_auc_below_0.52"


def test_dead_recall_suppresses_even_with_high_cv_auc():
    """The exact confirmed production bug: 9961.HK's random_forest model, cv_auc_mean=0.716
    (comfortably clears the 0.52 bar) but recall==0 and precision==0 — must still suppress."""
    suppressed, reason = _compute_oos_suppression(0.716, recall=0.0, precision=0.0, overfit_gap_val=-0.284)
    assert suppressed is True
    assert reason == "dead_recall"


def test_xgboost_sibling_dead_recall_also_suppresses():
    """9961.HK's xgboost sibling: cv_auc_mean=0.683, recall=0.0, precision=0.0."""
    suppressed, reason = _compute_oos_suppression(0.683, recall=0.0, precision=0.0, overfit_gap_val=-0.192)
    assert suppressed is True
    assert reason == "dead_recall"


def test_nonzero_recall_or_precision_does_not_trigger_dead_recall_condition():
    """A model with SOME true positives (even a low but nonzero recall) should not be
    suppressed by the dead-recall condition specifically."""
    suppressed, reason = _compute_oos_suppression(0.60, recall=0.1, precision=0.2, overfit_gap_val=0.02)
    assert suppressed is False
    assert reason is None


def test_large_negative_overfit_gap_suppresses():
    """The confirmed 40/249-model population: test-AUC far above CV-AUC, recall/precision
    otherwise unremarkable — must still suppress via the new symmetric magnitude check."""
    suppressed, reason = _compute_oos_suppression(0.60, recall=0.5, precision=0.5, overfit_gap_val=-0.30)
    assert suppressed is True
    assert reason == "overfit_gap_magnitude"


def test_large_positive_overfit_gap_still_suppresses():
    """Regression guard: the new symmetric check must not accidentally narrow to only the
    negative direction — the pre-existing ML-FIX-4 log-only check's own positive direction
    must also now suppress via this function."""
    suppressed, reason = _compute_oos_suppression(0.60, recall=0.5, precision=0.5, overfit_gap_val=0.30)
    assert suppressed is True
    assert reason == "overfit_gap_magnitude"


def test_small_overfit_gap_within_tolerance_does_not_suppress():
    suppressed, reason = _compute_oos_suppression(0.60, recall=0.5, precision=0.5, overfit_gap_val=0.05)
    assert suppressed is False
    assert reason is None


def test_none_cv_auc_and_none_overfit_gap_does_not_crash():
    """Both cv_auc_mean and overfit_gap_val can legitimately be None (e.g. insufficient CV
    folds, or a single-class y_test) — must not raise, must fall through to the recall check."""
    suppressed, reason = _compute_oos_suppression(None, recall=0.0, precision=0.0, overfit_gap_val=None)
    assert suppressed is True
    assert reason == "dead_recall"

    suppressed, reason = _compute_oos_suppression(None, recall=0.5, precision=0.5, overfit_gap_val=None)
    assert suppressed is False
    assert reason is None


def test_conditions_checked_in_documented_order():
    """When multiple conditions are simultaneously true, the first one checked (cv_auc, then
    dead_recall, then overfit_gap) is the one reported as the reason."""
    suppressed, reason = _compute_oos_suppression(0.30, recall=0.0, precision=0.0, overfit_gap_val=0.5)
    assert suppressed is True
    assert reason == "cv_auc_below_0.52"
