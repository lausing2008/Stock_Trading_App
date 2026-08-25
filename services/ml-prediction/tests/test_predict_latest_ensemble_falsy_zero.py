"""Tests for AUD301-ML1B: `predict_latest_ensemble()`'s falsy-zero AUC coercion and its
missing oos_suppressed exclusion — the 2-model (XGBoost + RandomForest) sibling of
`predict_latest_ensemble_three()`, which already received the equivalent fix under T237-ML1.

Two real bugs, both fixed in the same pass:
  1. `xgb.get("metrics", {}).get("auc") or ... or 0.55` treats a real, legitimate auc=0.0
     (a perfectly rank-inverted model) as falsy and silently substitutes 0.55 — giving a
     degenerate model near-normal ensemble weight instead of the ~zero weight it deserves.
  2. A model already flagged `oos_suppressed=True` by `predict_latest()` (CV-AUC < 0.52,
     coin-flip territory) still had its own real held-out AUC feed the weighting formula at
     full strength — the top-level `oos_suppressed` flag on the RETURNED dict only informed
     signal-engine's downstream compression AFTER the blend already happened.

`trainer.py` can't be imported directly in this local test environment (its import chain
pulls in `lightgbm`, not installed locally — the identical constraint already documented for
`meta_trainer.py`'s own tests). `predict_latest_ensemble()`'s real source is extracted via
`exec()` with `predict_latest`/`Path.exists` faked, matching this repo's established
source-text-extraction technique for functions with heavy import-chain dependencies.
"""
import pathlib

_TRAINER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "trainer.py"
)
_TRAINER_SOURCE = _TRAINER_PATH.read_text()


def _extract_predict_latest_ensemble():
    start = _TRAINER_SOURCE.index("def predict_latest_ensemble(symbol:")
    end = _TRAINER_SOURCE.index("\n\n\ndef predict_latest_ensemble_three(", start)
    func_source = _TRAINER_SOURCE[start:end]

    class _FakePath:
        """Stands in for `_artifact_path(...)`'s return value — only `.exists()` is used."""

        def __init__(self, exists: bool):
            self._exists = exists

        def exists(self) -> bool:
            return self._exists

    namespace: dict = {
        "predict_latest": None,       # set per-test
        "_artifact_path": None,       # set per-test
        "_FakePath": _FakePath,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one real function's own source
    return namespace["predict_latest_ensemble"], _FakePath


def _make_ensemble(xgb_result: dict, rf_result: dict):
    """Wires up fakes for one call and returns the extracted function ready to invoke."""
    fn, FakePath = _extract_predict_latest_ensemble()

    def _fake_predict_latest(symbol, model_name, horizon=5, style="SWING"):
        return xgb_result if model_name == "xgboost" else rf_result

    def _fake_artifact_path(symbol, model_name, style="SWING"):
        return FakePath(exists=True)  # RF artifact always "exists" — the ensemble path

    fn.__globals__["predict_latest"] = _fake_predict_latest
    fn.__globals__["_artifact_path"] = _fake_artifact_path
    return fn


def _model(auc=None, cv_auc_mean=None, buy_threshold=None, bullish_probability=0.5,
           oos_suppressed=False):
    metrics = {}
    if auc is not None:
        metrics["auc"] = auc
    if cv_auc_mean is not None:
        metrics["cv_auc_mean"] = cv_auc_mean
    if buy_threshold is not None:
        metrics["buy_threshold"] = buy_threshold
    return {
        "bullish_probability": bullish_probability,
        "metrics": metrics,
        "oos_suppressed": oos_suppressed,
    }


# ── Bug 1: a real auc=0.0 must NOT be coerced to the 0.55 "absent" fallback ────────────────

def test_a_real_zero_auc_gives_that_model_near_zero_weight_not_the_absent_fallback():
    """xgb has a genuinely terrible (rank-inverted) model: auc=0.0. rf is healthy: auc=0.65.
    Pre-fix, `0.0 or 0.55` substituted 0.55 for xgb — pulling it up to near-equal weight with
    rf's 0.65. Post-fix, xgb's real 0.0 must survive, giving it (near-)zero weight."""
    xgb = _model(auc=0.0, bullish_probability=0.9)  # a wrong-direction model would have a
    rf = _model(auc=0.65, bullish_probability=0.3)  # confidently WRONG probability too
    fn = _make_ensemble(xgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    # With the bug: total = 0.55 + 0.65 = 1.2, w_xgb ≈ 0.458 — xgb's bad 0.9 pulls prob way up.
    # Fixed: xgb_auc stays 0.0, total = 0.65, w_xgb = 0.0, w_rf = 1.0 — prob == rf's own 0.3.
    assert result["weights"]["xgboost"] == 0.0
    assert result["weights"]["random_forest"] == 1.0
    assert result["bullish_probability"] == 0.3


def test_both_models_with_real_zero_auc_falls_back_to_an_even_split_not_a_crash():
    """An edge case the fix must not crash on: total AUC weight is 0.0 for BOTH models
    (neither oos_suppressed, both genuinely report auc=0.0 — a degenerate-but-real state
    with no meaningful AUC signal to weight by). Must not divide by zero, and must not
    fabricate a preference between two equally-uninformative models — split evenly."""
    xgb = _model(auc=0.0, bullish_probability=0.4)
    rf = _model(auc=0.0, bullish_probability=0.6)
    fn = _make_ensemble(xgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    assert result["weights"]["xgboost"] == 0.5
    assert result["weights"]["random_forest"] == 0.5
    assert result["bullish_probability"] == 0.5


def test_real_nonzero_low_auc_is_not_confused_with_absent():
    """A genuinely low-but-nonzero AUC (0.52, just at the oos_suppressed boundary but not
    flagged suppressed here) must be used as-is, not treated as 'missing'."""
    xgb = _model(auc=0.52, bullish_probability=0.7)
    rf = _model(auc=0.60, bullish_probability=0.3)
    fn = _make_ensemble(xgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    total = 0.52 + 0.60
    expected_w_xgb = round(0.52 / total, 2)
    assert result["weights"]["xgboost"] == expected_w_xgb


def test_absent_auc_falls_back_to_cv_auc_mean_then_055_correctly():
    """The genuine 'metric is missing entirely' case must still work — this is the ONLY
    scenario the 0.55 fallback should ever fire for."""
    xgb = _model(bullish_probability=0.6)  # no auc, no cv_auc_mean at all -> 0.55
    rf = _model(cv_auc_mean=0.58, bullish_probability=0.4)  # no auc -> falls back to cv_auc_mean
    fn = _make_ensemble(xgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    total = 0.55 + 0.58
    expected_w_xgb = round(0.55 / total, 2)
    assert result["weights"]["xgboost"] == expected_w_xgb


# ── Bug 2: oos_suppressed must zero out a model's weighting influence ──────────────────────

def test_oos_suppressed_model_gets_zero_weight_even_with_a_nonzero_reported_auc():
    """A model can be oos_suppressed=True (CV-AUC < 0.52) while its point-estimate `auc`
    metric is still e.g. 0.50 — a real, nonzero, non-degenerate-looking number that would
    otherwise pass the falsy-zero guard fine and still get real weight. The oos_suppressed
    flag itself must independently zero out its weight, on top of the falsy-zero fix."""
    xgb = _model(auc=0.50, bullish_probability=0.95, oos_suppressed=True)
    rf = _model(auc=0.60, bullish_probability=0.45, oos_suppressed=False)
    fn = _make_ensemble(xgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    assert result["weights"]["xgboost"] == 0.0
    assert result["weights"]["random_forest"] == 1.0
    assert result["bullish_probability"] == 0.45


def test_both_models_suppressed_falls_back_to_using_both_rather_than_a_zero_total():
    """If BOTH models are oos_suppressed, zeroing both out would divide-by-zero — the
    function must fall back to using both models' own real AUCs rather than crashing or
    silently returning a degenerate all-zero-weight result."""
    xgb = _model(auc=0.48, bullish_probability=0.5, oos_suppressed=True)
    rf = _model(auc=0.51, bullish_probability=0.5, oos_suppressed=True)
    fn = _make_ensemble(xgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    # Falls back: total = 0.48 + 0.51, real weights derived from real AUCs — not a crash,
    # not a 0.55/0.55 midpoint fallback that would hide the suppression from the weighting.
    total = 0.48 + 0.51
    assert result["weights"]["xgboost"] == round(0.48 / total, 2)
    assert result["weights"]["random_forest"] == round(0.51 / total, 2)
    # Both being suppressed must still be visible in the top-level flag for downstream
    # (signal-engine SA-27) compression to discount this result appropriately.
    assert result["oos_suppressed"] is True


def test_only_one_model_suppressed_the_top_level_oos_suppressed_flag_still_propagates():
    """T237-ML-OOS1's pre-existing propagation (`bool(xgb.get(...) or rf.get(...))`) must
    still correctly flag the WHOLE ensemble result as suppressed even though only one of the
    two models triggered it and that model's weight was independently zeroed."""
    xgb = _model(auc=0.50, bullish_probability=0.9, oos_suppressed=True)
    rf = _model(auc=0.60, bullish_probability=0.4, oos_suppressed=False)
    fn = _make_ensemble(xgb, rf)
    result = fn("TEST", horizon=5, style="SWING")
    assert result["oos_suppressed"] is True


# ── cv_auc_mean reporting must reflect each model's OWN real metric, not the weighting value ──

def test_reported_cv_auc_mean_is_not_corrupted_by_a_suppressed_models_zeroed_weight():
    """xgb_auc/rf_auc (post-fix) are the WEIGHTING values — zeroed for a suppressed model.
    The `metrics.cv_auc_mean` field on the RETURNED dict is a separate, reported diagnostic
    and must show the suppressed model's own real cv_auc_mean (e.g. 0.48), never the zeroed
    weighting value — a caller reading this field to understand model quality must not see a
    misleading 0.0 for a model that actually had a real (if sub-threshold) CV AUC."""
    xgb = _model(auc=0.50, cv_auc_mean=0.48, bullish_probability=0.9, oos_suppressed=True)
    rf = _model(auc=0.60, cv_auc_mean=0.65, bullish_probability=0.4, oos_suppressed=False)
    fn = _make_ensemble(xgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    reported_cv_mean = result["metrics"]["cv_auc_mean"]
    expected = round((0.48 + 0.65) / 2, 4)
    assert reported_cv_mean == expected
    # The zeroed-for-weighting value would have produced (0.0 + 0.65)/2 = 0.325 instead.
    assert reported_cv_mean != round((0.0 + 0.65) / 2, 4)


# ── buy_threshold falsy-zero guard (the same class of bug, a smaller blast radius) ─────────

def test_a_real_zero_buy_threshold_is_not_coerced_to_the_05_absent_fallback():
    """A precision-optimised buy_threshold of exactly 0.0 is an unusual but real value a
    precision_recall_curve sweep could in principle land on — must not be silently replaced
    with the 'absent' fallback of 0.5."""
    xgb = _model(auc=0.6, buy_threshold=0.0, bullish_probability=0.5)
    rf = _model(auc=0.6, buy_threshold=0.5, bullish_probability=0.5)
    fn = _make_ensemble(xgb, rf)
    result = fn("TEST", horizon=5, style="SWING")
    # Equal AUC weights (0.5/0.5) -> buy_threshold = 0.5*0.0 + 0.5*0.5 = 0.25, not 0.5.
    assert result["metrics"]["buy_threshold"] == 0.25
