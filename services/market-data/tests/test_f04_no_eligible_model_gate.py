"""F04 Stage 0: "no eligible model" must not soft-pass the conviction gate's ML layer.

signal-engine now records an all-suppressed horizon as ML-unavailable (`ml_probability` None,
weight 0, `ml_unavailable_reason: no_eligible_model`). Before, the same signal carried a
weighted 0.45/0.5, below every regime's ML threshold (>= 0.65), so the layer failed. Without
this check the None would fall into the "no model trained yet" soft pass and make the entry
newly eligible. Runs the REAL _is_conviction_buy through test_conviction_buy_overextension_
guards.py's harness (real _REGIME_THRESHOLDS, real helpers).
"""
import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "conviction_harness", pathlib.Path(__file__).with_name("test_conviction_buy_overextension_guards.py"))
_h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_h)


def _gate(sig):
    ok, tier, passed, failed = _h._is_conviction_buy(sig, kscore=70.0, regime="bull")
    return ok, tier, [p for p in passed if p.startswith("ML")], [f for f in failed if f.startswith("ML")]


NO_ELIGIBLE = dict(ml_probability=None, ml_weight=0.0, ml_unavailable_reason="no_eligible_model")
PRE_PATCH_SUPPRESSED = dict(ml_probability=0.45, ml_weight=0.14)   # what the same row used to carry


def test_no_eligible_model_fails_the_ml_layer_rather_than_soft_passing_it():
    ok, tier, passed, failed = _gate(_h._signal(**NO_ELIGIBLE))
    assert passed == [] and len(failed) == 1 and "no eligible model" in failed[0]
    assert tier == "near", "the single soft ML failure, exactly as before"


def test_it_gets_the_same_tier_as_the_pre_patch_suppressed_row_with_and_without_another_failure():
    for extra in ({}, {"obv_trend_bullish": False}, {"adx_trending": False}):
        new = _gate(_h._signal(**NO_ELIGIBLE, **extra))
        old = _gate(_h._signal(**PRE_PATCH_SUPPRESSED, **extra))
        assert (new[0], new[1]) == (old[0], old[1]), extra


def test_never_trained_still_soft_passes_unchanged():
    ok, tier, passed, failed = _gate(_h._signal(ml_probability=None, ml_weight=0.0))
    assert ok is True and tier == "full" and failed == [] and "no model trained yet" in passed[0]


def test_eligible_model_layer_is_unchanged():
    ok, tier, passed, failed = _gate(_h._signal(ml_probability=0.90, ml_weight=0.5, ml_unavailable_reason=None))
    assert tier == "full" and failed == [] and "bullish probability" in passed[0]
