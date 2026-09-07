"""AUD-MLCV-SINGLECLASSFOLD — a single-class slice must not kill the whole training run.

Source-text extraction rather than importing trainer.py, matching this service's own existing
convention (see test_feature_ablation.py): trainer.py imports xgboost/lightgbm/torch at module
level, far too heavy for a focused unit test.

Context — this is a real production failure, not a hypothetical. On 2026-09-07, 5 of 86
retrains died on it:

    WMT / GEV / EE / AAON  random_forest -> IndexError: index 1 is out of bounds for axis 1 with size 1
    GEV                    xgboost       -> Invalid classes inferred from unique values of `y`

Both trace to the same cause. sklearn will happily fit a model on a single-class training
slice, but its predict_proba then returns shape (n, 1) instead of (n, 2), so the `[:, 1]`
index raises — killing the ENTIRE train_model() call, not just the offending fold. XGBoost
rejects the same input up front with a different message. The validation slice was already
guarded; the training slice never was.
"""
import re
import numpy as np
import pytest

SRC = open(__file__.rsplit("/tests/", 1)[0] + "/src/training/trainer.py").read()


# ── The CV-fold guard ────────────────────────────────────────────────────────────────────

def test_cv_loop_checks_the_training_slice_not_only_the_validation_slice():
    """The guard must inspect y_cv_TR specifically.

    Guarding y_cv_val instead reintroduces the exact bug — the validation slice was ALREADY
    checked further down, and it is the TRAINING slice whose single class produces the
    (n, 1) predict_proba. An earlier version of this test only asserted that the substring
    'np.unique(y_cv_tr)' appeared somewhere in the loop, which a y_cv_tr -> y_cv_val swap
    survived because the skip-guard is not the only place that name occurs. Assert on the
    skip-guard statement itself.
    """
    loop = SRC[SRC.index("for tr_idx, val_idx in tscv.split"):SRC.index("preds_proba = cv_model.predict_proba")]
    assert "if len(np.unique(y_cv_tr)) < 2:" in loop, \
        "the skip-guard must test the TRAINING slice (y_cv_tr), not the validation slice"
    assert "if len(np.unique(y_cv_val)) < 2:" not in loop, \
        "guarding y_cv_val here would skip the wrong folds and leave the crash in place"
    assert "continue" in loop, "a degenerate fold must be skipped, not fitted"


def test_cv_guard_precedes_the_fit_and_the_proba_index():
    """Ordering is the whole point: guarding after the fit would still raise."""
    guard = SRC.index("np.unique(y_cv_tr)")
    fit = SRC.index("cv_model.fit(X_cv_tr_s")
    proba = SRC.index("cv_model.predict_proba(X_cv_val_s)")
    assert guard < fit < proba


def test_validation_slice_guard_is_retained():
    """The pre-existing y_cv_val guard protects roc_auc_score and must not be lost."""
    assert "len(np.unique(y_cv_val)) > 1" in SRC


def test_skipped_fold_is_logged_with_the_symbol():
    """A silently-skipped fold would hide a real data problem — it must be attributable."""
    assert "train.cv_fold_single_class" in SRC
    blk = SRC[SRC.index("train.cv_fold_single_class"):][:400]
    assert "symbol=symbol" in blk


# ── The final-fit guard ──────────────────────────────────────────────────────────────────

def test_final_training_slice_is_guarded_before_any_model_is_built():
    guard = SRC.index("training slice has a single class")
    build = SRC.index('model = get_model(model_name, early_stopping_rounds=50')
    assert guard < build, "must fail before spending time constructing/fitting the model"


def test_final_guard_error_names_the_symbol_and_the_cause():
    """The raw sklearn/xgboost messages name neither, which is what made this hard to triage."""
    blk = SRC[SRC.index("training slice has a single class") - 300:][:700]
    assert "{symbol}" in blk
    assert "single class" in blk


# ── Behavioral: reproduce the exact sklearn shape that caused the IndexError ──────────────

def test_single_class_predict_proba_really_is_shape_n_by_1():
    """Pin the underlying sklearn behavior this guard exists for. If a future sklearn returned
    (n, 2) for single-class fits, this test failing would tell us the guard's rationale changed."""
    from sklearn.ensemble import RandomForestClassifier
    X = np.random.RandomState(0).rand(40, 4)
    y = np.ones(40, dtype=int)          # every label identical — the degenerate case
    m = RandomForestClassifier(n_estimators=5, random_state=0).fit(X, y)
    proba = m.predict_proba(X)
    assert proba.shape[1] == 1, "single-class fit should yield one probability column"
    with pytest.raises(IndexError):
        _ = proba[:, 1]                 # exactly the line that killed 4 retrains


def test_two_class_fit_is_unaffected():
    """The guard must not perturb the normal path."""
    from sklearn.ensemble import RandomForestClassifier
    rng = np.random.RandomState(0)
    X = rng.rand(40, 4)
    y = (rng.rand(40) > 0.5).astype(int)
    m = RandomForestClassifier(n_estimators=5, random_state=0).fit(X, y)
    assert m.predict_proba(X)[:, 1].shape == (40,)


def test_guard_condition_matches_the_degenerate_cases_only():
    """len(np.unique(y)) < 2 — true for all-0 and all-1, false for a mix, including a 1-of-40
    minority (which is legitimately trainable and must NOT be skipped)."""
    assert len(np.unique(np.zeros(10, dtype=int))) < 2
    assert len(np.unique(np.ones(10, dtype=int))) < 2
    mostly_zero = np.zeros(40, dtype=int); mostly_zero[7] = 1
    assert not len(np.unique(mostly_zero)) < 2
