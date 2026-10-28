"""F04 Stage 0: suppressed models contribute neither direction nor quality weight.

docs/audits/2026-10-28-f04-stage0-inventory-and-no-model-trace.md found that when every member
was oos_suppressed the ensembles fell back to those members: each returned 0.5, `0.5 > 0.5`
read as unanimous_bear and nudged the blend to 0.45, and their stored AUC was reported as
quality, so rejected models carried live fusion weight (435 signals in 3 days).

The REAL functions run here: trainer.py's patched ensembles, `_no_eligible_model` and
`predict_single_served`, extracted by AST (trainer.py cannot be imported locally -- lightgbm),
with only each member's `predict_latest` result supplied as a fixture, shaped as the real one
returns a suppressed bundle (0.5 plus its stored metrics). The pre-patch functions, frozen in
fixtures/pre_stage0_ensembles.py, are the golden reference for the fully eligible case.
"""
import ast
import pathlib
import types

import pytest

HERE = pathlib.Path(__file__).resolve().parent
TRAINER = HERE.parents[0] / "src" / "training" / "trainer.py"
GOLDEN = HERE / "fixtures" / "pre_stage0_ensembles.py"
NEW_KEYS = {"ml_available", "eligible_contributors", "suppressed_members"}


class _MetaDisabled(Exception):
    pass


def _load(path, names, members):
    """Exec the named top-level functions from `path` against stubbed member predictions."""
    exists = {n: n in members for n in ("xgboost", "lightgbm", "random_forest")}

    def predict_latest(symbol, model, horizon, style="SWING"):
        if model not in members:
            raise FileNotFoundError(model)
        return dict(members[model])

    env = {
        "predict_latest": predict_latest,
        "_artifact_path": lambda symbol, model, style="SWING": types.SimpleNamespace(exists=lambda m=model: exists[m]),
        "_serving_artifact_path": lambda symbol, model, style="SWING": types.SimpleNamespace(exists=lambda m=model: exists[m]),
        "log": types.SimpleNamespace(warning=lambda *a, **k: None),
        "_meta_blend_enabled": lambda: False, "_meta_shadow_enabled": lambda: False,
        "_MetaDisabled": _MetaDisabled, "_load_sector_and_market_cap": lambda s: (None, None),
    }
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    return env


def _new(members):
    return _load(TRAINER, {"_no_eligible_model", "predict_single_served", "predict_latest_ensemble",
                           "predict_latest_ensemble_three"}, members)


def _old(members):
    return _load(GOLDEN, {"predict_latest_ensemble", "predict_latest_ensemble_three"}, members)


def m(prob, auc, suppressed=False, cv=None, thr=0.55):
    return {"symbol": "FIX", "bullish_probability": prob, "confidence": round(abs(prob - .5) * 200, 2),
            "metrics": {"auc": auc, "cv_auc_mean": cv if cv is not None else auc, "buy_threshold": thr},
            "oos_suppressed": suppressed}


def three(members):
    return _new(members)["predict_latest_ensemble_three"]("FIX", 10, style="SWING")


def two(members):
    return _new(members)["predict_latest_ensemble"]("FIX", 10, style="SWING")


def _assert_unavailable(res, suppressed):
    assert res["ml_available"] is False
    assert res["ml_unavailable_reason"] == "no_eligible_model"
    assert res["eligible_contributors"] == 0
    assert sorted(res["suppressed_members"]) == sorted(suppressed)
    assert res["metrics"] == {}, "no quality metric a fusion consumer could read"
    assert res["bullish_probability"] == 0.5 and res["ensemble_agreement"] == "no_eligible_model"
    assert res["model_probabilities"] == {} and res["applied_weights"] == {}
    assert res["oos_suppressed"] is True


# ── All suppressed ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("members", [
    {"xgboost": m(.5, .83, True), "random_forest": m(.5, .86, True)},
    {"xgboost": m(.5, .83, True), "lightgbm": m(.5, .80, True), "random_forest": m(.5, .86, True)},
    {"xgboost": m(.5, .80, True)},
])
def test_all_suppressed_three_model_path_is_unavailable_with_no_nudge(members):
    """Before: 0.45 / unanimous_bear / stored AUC 0.845 reported as quality."""
    _assert_unavailable(three(members), list(members))


def test_the_old_path_really_did_nudge_and_report_quality():
    """Negative control: the frozen pre-patch function shows the defect this file removes."""
    old = _old({"xgboost": m(.5, .83, True), "random_forest": m(.5, .86, True)})["predict_latest_ensemble_three"]("FIX", 10)
    assert old["bullish_probability"] == pytest.approx(0.45)
    assert old["ensemble_agreement"] == "unanimous_bear"
    assert old["metrics"]["mean_model_test_auc"] == pytest.approx(0.845)


@pytest.mark.parametrize("members", [
    {"xgboost": m(.5, .48, True), "random_forest": m(.5, .51, True)},
    {"xgboost": m(.5, .80, True)},
])
def test_all_suppressed_two_model_path_is_unavailable(members):
    _assert_unavailable(two(members), list(members))


def test_single_model_endpoint_answers_unavailable_for_a_suppressed_model():
    res = _new({"xgboost": m(.5, .80, True)})["predict_single_served"]("FIX", "xgboost", 10)
    _assert_unavailable(res, ["xgboost"])


# ── Mixed: suppressed members excluded from probability, agreement and quality ─────────────

def test_mixed_three_excludes_the_suppressed_member_everywhere():
    res = three({"xgboost": m(.95, .50, True), "lightgbm": m(.45, .70), "random_forest": m(.45, .70)})
    assert res["ml_available"] is True and res["eligible_contributors"] == 2
    assert res["suppressed_members"] == ["xgboost"]
    assert set(res["model_probabilities"]) == {"lightgbm", "random_forest"}
    assert set(res["applied_weights"]) == {"lightgbm", "random_forest"}
    assert res["model"] == "ensemble_lgb_rf"
    # Both contributors below 0.5 -> unanimous_bear among CONTRIBUTORS; min auc 0.70 > 0.57.
    assert res["ensemble_agreement"] == "unanimous_bear"
    assert res["bullish_probability"] == pytest.approx(0.45 - 0.05)
    # Quality is the contributors' own mean, not (0.0 + 0.70 + 0.70) / 3 = 0.4667.
    assert res["metrics"]["mean_model_test_auc"] == pytest.approx(0.70)
    assert res["oos_suppressed"] is False


def test_mixed_three_with_one_survivor_returns_that_model_alone():
    res = three({"xgboost": m(.5, .80, True), "lightgbm": m(.5, .79, True), "random_forest": m(.61, .62)})
    assert res["model"] == "random_forest" and res["ensemble_agreement"] == "single_model"
    assert res["bullish_probability"] == .61 and res["metrics"]["auc"] == .62
    assert res["eligible_contributors"] == 1 and sorted(res["suppressed_members"]) == ["lightgbm", "xgboost"]


def test_mixed_two_model_uses_only_the_eligible_member_and_keeps_the_tracked_quality_issue():
    res = two({"xgboost": m(.9, .50, True), "random_forest": m(.4, .60)})
    assert res["bullish_probability"] == pytest.approx(0.4)
    assert res["weights"] == {"xgboost": 0.0, "random_forest": 1.0}
    assert res["oos_suppressed"] is False and res["suppressed_members"] == ["xgboost"]
    # S0-3, tracked separately and deliberately unchanged: mean over both with one zeroed.
    assert res["metrics"]["mean_model_test_auc"] == pytest.approx(0.30)


def test_two_model_eligible_member_with_real_zero_auc_never_hands_weight_back_to_the_suppressed_one():
    res = two({"xgboost": m(.9, .70, True), "random_forest": m(.4, 0.0)})
    assert res["weights"] == {"xgboost": 0.0, "random_forest": 1.0}
    assert res["bullish_probability"] == pytest.approx(0.4)


# ── Missing members ────────────────────────────────────────────────────────────────────────

def test_missing_xgboost_is_still_a_404_not_a_borrowed_model():
    with pytest.raises(FileNotFoundError):
        three({"random_forest": m(.6, .62)})


@pytest.mark.parametrize("members", [
    {"xgboost": m(.62, .61)},
    {"xgboost": m(.62, .61), "random_forest": m(.58, .60)},
    {"xgboost": m(.62, .61), "lightgbm": m(.59, .63)},
])
def test_missing_lgb_or_rf_with_eligible_members_matches_the_old_path(members):
    _assert_matches_golden(members)


# ── Fully eligible: unchanged ──────────────────────────────────────────────────────────────

def _assert_matches_golden(members):
    for fn in ("predict_latest_ensemble_three", "predict_latest_ensemble"):
        new = _new(members)[fn]("FIX", 10, style="SWING")
        old = _old(members)[fn]("FIX", 10, style="SWING")
        assert new["ml_available"] is True and new["suppressed_members"] == []
        # The 2-model function reads xgboost and random_forest only; lightgbm is not its member.
        expected = len(members) if fn.endswith("three") else len({"xgboost", "random_forest"} & set(members))
        assert new["eligible_contributors"] == expected
        assert {k: v for k, v in new.items() if k not in NEW_KEYS} == old, fn


@pytest.mark.parametrize("members", [
    {"xgboost": m(.70, .64), "lightgbm": m(.66, .62), "random_forest": m(.61, .60)},   # unanimous bull, nudge
    {"xgboost": m(.40, .58), "lightgbm": m(.35, .59), "random_forest": m(.42, .61)},   # unanimous bear, nudge
    {"xgboost": m(.70, .64), "lightgbm": m(.40, .62), "random_forest": m(.55, .56)},   # split
    {"xgboost": m(.70, .55), "lightgbm": m(.66, .56), "random_forest": m(.61, .52)},   # agree, min auc <= 0.57
    {"xgboost": m(.70, None, cv=.60), "lightgbm": m(.66, .0), "random_forest": m(.61, .60, thr=0.0)},
])
def test_fully_eligible_output_is_identical_to_the_pre_patch_function(members):
    _assert_matches_golden(members)
