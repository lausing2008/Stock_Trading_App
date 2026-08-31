"""Tests for AUD-ML1B-3MODEL: `predict_latest_ensemble_three()`'s falsy-zero AUC coercion in
its own separate `mean_model_test_auc`/`cv_auc_mean` reporting block — the sibling bug to
AUD301-ML1B, which fixed the SAME class of issue in the 2-model `predict_latest_ensemble()`
but never ported the fix into this 3-model function's own metrics block.

The bug: `xgb_auc = float((xgb.get("metrics") or {}).get("auc") or ... or 0.55)` treats a
real, legitimate `auc=0.0` (a perfectly rank-inverted model) as falsy and silently substitutes
0.55 — giving a degenerate model a near-normal REPORTED `mean_model_test_auc`. This is
distinct from the probability-BLEND weights computed earlier in the same function (those
already correctly exclude `oos_suppressed` models via T237-ML1's `available` list) — this bug
lives only in the separately-computed, separately-reported AUC metric. That reported metric is
what signal-engine's `_fetch_ml_data()` (`signals.py`) reads to set the model's ML/TA fusion
weight — landing exactly on the `else`-branch boundary of that formula (`ml_test_auc >= 0.55`)
gives a known-bad model 20% weight instead of ~0%.

`trainer.py` can't be imported directly in this local test environment (its import chain pulls
in `lightgbm`, not installed locally — the same constraint already documented for
`meta_trainer.py`'s and `predict_latest_ensemble()`'s own tests). `predict_latest_ensemble_
three()`'s real source is extracted via `exec()`, matching this repo's established source-text-
extraction technique. The function's own lazy `from .meta_trainer import predict_meta`
import is faked to fail (raising ImportError, caught by the function's own real try/except) so
the meta-model blend step is cleanly skipped — this test is scoped to the 3-real-model AUC
reporting bug only, not the meta-model blend, which is a separate, already-covered concern.
"""
import pathlib

_TRAINER_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "trainer.py"
)
_TRAINER_SOURCE = _TRAINER_PATH.read_text()


def _extract_predict_latest_ensemble_three():
    start = _TRAINER_SOURCE.index("def predict_latest_ensemble_three(symbol:")
    end = _TRAINER_SOURCE.index("\n\n\ndef validate_walkforward(", start)
    func_source = _TRAINER_SOURCE[start:end]

    class _FakePath:
        """Stands in for `_artifact_path(...)`'s return value — only `.exists()` is used."""

        def __init__(self, exists: bool):
            self._exists = exists

        def exists(self) -> bool:
            return self._exists

    namespace: dict = {
        "predict_latest": None,        # set per-test
        "_artifact_path": None,        # set per-test
        "_FakePath": _FakePath,
        "_load_sector_and_market_cap": lambda symbol: (None, None),
        "log": _FakeLog(),
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one real function's own source
    return namespace["predict_latest_ensemble_three"], _FakePath


class _FakeLog:
    """A no-op stand-in for structlog's module-level `log` — the real one isn't in this
    extracted namespace, and `predict_latest_ensemble_three()`'s own meta-predict except block
    calls `log.warning(...)`."""

    def warning(self, *a, **kw):
        pass


def _make_ensemble_three(xgb_result: dict, lgb_result: dict | None, rf_result: dict | None):
    """Wires up fakes for one call. lgb_result/rf_result=None means that artifact 'doesn't
    exist' (the ensemble degrades to fewer models), matching predict_latest_ensemble_three()'s
    own real _artifact_path(...).exists() gating."""
    fn, FakePath = _extract_predict_latest_ensemble_three()

    def _fake_predict_latest(symbol, model_name, horizon=5, style="SWING"):
        if model_name == "xgboost":
            return xgb_result
        if model_name == "lightgbm":
            return lgb_result
        return rf_result

    def _fake_artifact_path(symbol, model_name, style="SWING"):
        if model_name == "lightgbm":
            return FakePath(exists=lgb_result is not None)
        if model_name == "random_forest":
            return FakePath(exists=rf_result is not None)
        return FakePath(exists=True)

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


# ── Bug: a real auc=0.0 must NOT be coerced to the 0.55 "absent" fallback in the reported
#    mean_model_test_auc / cv_auc_mean metrics ───────────────────────────────────────────────

def test_a_real_zero_auc_reports_near_zero_not_the_absent_fallback():
    """xgb has a genuinely terrible (rank-inverted) model: auc=0.0. lgb and rf are both
    healthy: auc=0.65 each. Pre-fix, `0.0 or 0.55` substituted 0.55 into the reported mean,
    landing squarely on the >=0.55 boundary of signal-engine's fusion-weight formula. Post-fix,
    xgb's real 0.0 survives, pulling the reported mean down meaningfully below that boundary."""
    xgb = _model(auc=0.0, bullish_probability=0.9)
    lgb = _model(auc=0.65, bullish_probability=0.3)
    rf = _model(auc=0.65, bullish_probability=0.3)
    fn = _make_ensemble_three(xgb, lgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    # Pre-fix: (0.55 + 0.65 + 0.65) / 3 = 0.6167 -- comfortably clears the 0.55 fusion-weight
    # boundary, giving a genuinely rank-inverted model real influence.
    # Post-fix: (0.0 + 0.65 + 0.65) / 3 = 0.4333 -- correctly reflects the real drag a
    # rank-inverted model puts on the ensemble's own trustworthiness.
    expected = round((0.0 + 0.65 + 0.65) / 3, 4)
    assert result["metrics"]["mean_model_test_auc"] == expected
    assert result["metrics"]["cv_auc_mean"] == expected
    # Confirm this is genuinely a different number than the pre-fix buggy value would be.
    buggy_value = round((0.55 + 0.65 + 0.65) / 3, 4)
    assert result["metrics"]["mean_model_test_auc"] != buggy_value


def test_all_three_models_with_real_zero_auc_falls_back_to_their_real_values_not_a_crash():
    """An edge case the fix must not crash on: every model genuinely reports auc=0.0 (none
    oos_suppressed) -- there's no meaningful AUC signal to zero out further; the mean should
    just correctly be 0.0, not silently replaced with the absent-fallback 0.55."""
    xgb = _model(auc=0.0, bullish_probability=0.4)
    lgb = _model(auc=0.0, bullish_probability=0.5)
    rf = _model(auc=0.0, bullish_probability=0.6)
    fn = _make_ensemble_three(xgb, lgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    assert result["metrics"]["mean_model_test_auc"] == 0.0


def test_real_nonzero_low_auc_is_not_confused_with_absent():
    """A genuinely low-but-nonzero AUC (0.52) must be used as-is in the reported mean, not
    treated as 'missing' and replaced with 0.55."""
    xgb = _model(auc=0.52, bullish_probability=0.7)
    rf = _model(auc=0.60, bullish_probability=0.3)
    fn = _make_ensemble_three(xgb, None, rf)
    result = fn("TEST", horizon=5, style="SWING")

    expected = round((0.52 + 0.60) / 2, 4)
    assert result["metrics"]["mean_model_test_auc"] == expected


def test_absent_auc_falls_back_to_cv_auc_mean_then_055_correctly():
    """The genuine 'metric is missing entirely' case must still work -- this is the ONLY
    scenario the 0.55 fallback should ever fire for."""
    xgb = _model(bullish_probability=0.6)  # no auc, no cv_auc_mean at all -> 0.55
    rf = _model(cv_auc_mean=0.58, bullish_probability=0.4)  # no auc -> falls back to cv_auc_mean
    fn = _make_ensemble_three(xgb, None, rf)
    result = fn("TEST", horizon=5, style="SWING")

    expected = round((0.55 + 0.58) / 2, 4)
    assert result["metrics"]["mean_model_test_auc"] == expected


# ── oos_suppressed must zero out a model's contribution to the REPORTED mean AUC too ───────

def test_oos_suppressed_model_is_excluded_from_the_reported_mean_even_with_a_real_auc():
    """A model can be oos_suppressed=True (CV-AUC < 0.52) while its point-estimate `auc`
    metric still looks like a real, non-degenerate number (e.g. 0.50). The reported mean must
    not be inflated by a model already known to be coin-flip-quality."""
    xgb = _model(auc=0.50, bullish_probability=0.95, oos_suppressed=True)
    lgb = _model(auc=0.70, bullish_probability=0.45, oos_suppressed=False)
    rf = _model(auc=0.70, bullish_probability=0.45, oos_suppressed=False)
    fn = _make_ensemble_three(xgb, lgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    # Suppressed xgb zeroed out -> (0.0 + 0.70 + 0.70) / 3, not (0.50 + 0.70 + 0.70) / 3.
    expected = round((0.0 + 0.70 + 0.70) / 3, 4)
    assert result["metrics"]["mean_model_test_auc"] == expected


def test_every_model_suppressed_restores_real_aucs_rather_than_reporting_a_misleading_zero():
    """If ALL contributing models are oos_suppressed, zeroing every one out would report a
    misleading, uniformly-zeroed mean_model_test_auc that hides real (if sub-threshold)
    quality differences between the models -- must restore their real AUCs in this case."""
    xgb = _model(auc=0.48, bullish_probability=0.5, oos_suppressed=True)
    lgb = _model(auc=0.50, bullish_probability=0.5, oos_suppressed=True)
    rf = _model(auc=0.51, bullish_probability=0.5, oos_suppressed=True)
    fn = _make_ensemble_three(xgb, lgb, rf)
    result = fn("TEST", horizon=5, style="SWING")

    expected = round((0.48 + 0.50 + 0.51) / 3, 4)
    assert result["metrics"]["mean_model_test_auc"] == expected
    # Top-level oos_suppressed flag must still correctly propagate for downstream (SA-27)
    # compression to discount this result, independent of the reported mean AUC fix.
    assert result["oos_suppressed"] is True


def test_two_model_ensemble_lgb_absent_still_applies_the_falsy_zero_fix():
    """The fix must apply correctly when the ensemble degrades to 2 real models (lgb artifact
    absent) -- not just the full 3-model case."""
    xgb = _model(auc=0.0, bullish_probability=0.8)
    rf = _model(auc=0.60, bullish_probability=0.4)
    fn = _make_ensemble_three(xgb, None, rf)
    result = fn("TEST", horizon=5, style="SWING")

    expected = round((0.0 + 0.60) / 2, 4)
    assert result["metrics"]["mean_model_test_auc"] == expected
