"""F04 Stage 0, end to end: a suppressed model gives a signal neither direction nor weight.

Each case builds the ml-prediction response with the REAL ensemble function -- the patched one
in trainer.py, or the pre-patch one frozen in ml-prediction/tests/fixtures -- serves it through
a mocked httpx to the REAL `_fetch_ml_data`, and fuses it with the REAL `_apply_style_signal`.
`base_reasons` is merged exactly as generate_all_signals' `_make_signal` does.
"""
import ast
import json
import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.generators import signals  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[3]
TRAINER = ROOT / "services/ml-prediction/src/training/trainer.py"
GOLDEN = ROOT / "services/ml-prediction/tests/fixtures/pre_stage0_ensembles.py"


class _MetaDisabled(Exception):
    pass


def _ensemble(path, members):
    exists = {n: n in members for n in ("xgboost", "lightgbm", "random_forest")}

    def predict_latest(symbol, model, horizon, style="SWING"):
        if model not in members:
            raise FileNotFoundError(model)
        return dict(members[model])

    env = {"predict_latest": predict_latest,
           "_artifact_path": lambda s, model, style="SWING": types.SimpleNamespace(exists=lambda m=model: exists[m]),
           "_serving_artifact_path": lambda s, model, style="SWING": types.SimpleNamespace(exists=lambda m=model: exists[m]),
           "log": types.SimpleNamespace(warning=lambda *a, **k: None),
           "_meta_blend_enabled": lambda: False, "_meta_shadow_enabled": lambda: False,
           "_MetaDisabled": _MetaDisabled, "_load_sector_and_market_cap": lambda s: (None, None)}
    nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)
             and n.name in ("_no_eligible_model", "predict_latest_ensemble_three")]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    try:
        return 200, env["predict_latest_ensemble_three"]("FIX", 10, style="SWING")
    except FileNotFoundError:
        return 404, {"detail": "No trained model"}


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, json.loads(json.dumps(body))   # wire round trip

    def json(self):
        return self._body


def _fetch(status, body):
    client = MagicMock()
    client.__enter__.return_value.post.return_value = _Resp(status, body)
    with patch.object(signals.httpx, "Client", return_value=client), \
         patch.object(signals, "_ml_service_token", return_value="t"), \
         patch.object(signals, "_settings", MagicMock(ml_prediction_url="http://ml")):
        return signals._fetch_ml_data("FIX", "SWING")


def _fuse(fetched, ta_prob=0.62, style="SWING"):
    ml_prob, ml_auc, ml_m = fetched
    base = {"ta_score": ta_prob,
            **{k: ml_m.get(k) for k in ("ml_model", "ml_agreement", "ml_model_probs", "ml_quality_status",
                                        "ml_unavailable_reason")},
            "ml_test_auc": ml_auc if ml_m.get("ml_quality_status") == "measured" else None}
    return signals._apply_style_signal(
        ta_prob=ta_prob, ml_prob=ml_prob, ml_test_auc=ml_auc, style_key=style, market_regime="bull",
        adx_val=30.0, weekly_tech={}, pattern_adj=0.0, days_to_earnings=None, news_sentiment=None,
        rs_rank=None, options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
        base_reasons=base, ml_oos_suppressed=ml_m.get("ml_oos_suppressed", False))


def m(prob, auc, suppressed=False):
    return {"symbol": "FIX", "bullish_probability": prob, "confidence": 0.0, "oos_suppressed": suppressed,
            "metrics": {"auc": auc, "cv_auc_mean": auc, "buy_threshold": 0.55}}


ALL_SUPPRESSED = {"xgboost": m(.5, .83, True), "random_forest": m(.5, .86, True)}


def test_all_suppressed_gives_no_ml_direction_or_weight_and_keeps_sa27():
    res = _fuse(_fetch(*_ensemble(TRAINER, ALL_SUPPRESSED)))
    r = res.reasons
    assert r["ml_probability"] is None and r["ml_weight"] == 0.0
    assert r["ml_unavailable_reason"] == "no_eligible_model"
    assert r["ml_test_auc"] is None and r["ml_quality_status"] == "no_eligible_model"
    assert r["ml_oos_suppressed"] is True and r["low_oos_accuracy"] is True   # SA-27 unchanged


def test_negative_control_the_old_response_pulled_a_bullish_ta_below_neutral_side():
    """The pre-patch ensemble's 0.45 + stored quality, through today's fetch/fusion code
    with a legacy response (no ml_available key): now also read as unavailable."""
    status, legacy = _ensemble(GOLDEN, ALL_SUPPRESSED)
    assert legacy["bullish_probability"] == pytest.approx(0.45) and "ml_available" not in legacy
    res = _fuse(_fetch(status, legacy))
    assert res.reasons["ml_probability"] is None and res.reasons["ml_weight"] == 0.0


def test_a_legacy_response_was_weighted_before_the_signal_engine_change():
    """Proves what the fetch change removes: the pre-patch fetch logic (oos_suppressed ignored)
    on the legacy response hands the fusion a 0.45 with measured quality 0.845."""
    status, legacy = _ensemble(GOLDEN, ALL_SUPPRESSED)
    ml_prob = legacy["bullish_probability"]
    weight = signals._calculate_ml_weight(ml_prob, 0.62, legacy["metrics"]["mean_model_test_auc"], 0.75, 0.0)
    assert ml_prob == pytest.approx(0.45) and weight > 0


def test_mixed_uses_only_the_eligible_members():
    members = {"xgboost": m(.95, .50, True), "lightgbm": m(.66, .70), "random_forest": m(.64, .70)}
    status, body = _ensemble(TRAINER, members)
    res = _fuse(_fetch(status, body))
    r = res.reasons
    assert r["ml_unavailable_reason"] is None and r["ml_oos_suppressed"] is False
    assert r["ml_weight"] > 0
    assert set(r["ml_model_probs"]) == {"lightgbm", "random_forest"}
    assert r["ml_test_auc"] == pytest.approx(0.70)


def test_missing_models_are_ta_only_and_soft_pass_semantics_are_untouched():
    res = _fuse(_fetch(*_ensemble(TRAINER, {})))
    r = res.reasons
    assert r["ml_probability"] is None and r["ml_weight"] == 0.0
    assert r.get("ml_unavailable_reason") is None, "no model is not the same as no eligible model"
    assert r["ml_oos_suppressed"] is False


@pytest.mark.parametrize("members,ta", [
    ({"xgboost": m(.70, .64), "lightgbm": m(.66, .62), "random_forest": m(.61, .60)}, .62),
    ({"xgboost": m(.40, .58), "lightgbm": m(.35, .59), "random_forest": m(.42, .61)}, .45),
    ({"xgboost": m(.70, .64), "random_forest": m(.55, .56)}, .70),
    ({"xgboost": m(.58, .61)}, .30),
])
def test_fully_eligible_signal_is_identical_to_the_pre_patch_path(members, ta):
    new = _fuse(_fetch(*_ensemble(TRAINER, members)), ta_prob=ta)
    old = _fuse(_fetch(*_ensemble(GOLDEN, members)), ta_prob=ta)
    assert (new.signal, new.confidence, new.bullish_probability) == (old.signal, old.confidence, old.bullish_probability)
    assert {k: v for k, v in new.reasons.items()} == {k: v for k, v in old.reasons.items()}


@pytest.mark.parametrize("ta", [0.30, 0.62, 0.80])
def test_sa27_on_unavailable_ml_is_the_explicit_flag_not_any_auc(ta):
    """SA-27 policy for ML-unavailable: compress iff the explicit `ml_oos_suppressed` flag is
    set -- which the unavailable path sets itself -- whatever AUC value is passed alongside.
    A retained or absent AUC must not change the outcome."""
    def run(auc, flag):
        return signals._apply_style_signal(
            ta_prob=ta, ml_prob=None, ml_test_auc=auc, style_key="SWING", market_regime="bull",
            adx_val=30.0, weekly_tech={}, pattern_adj=0.0, days_to_earnings=None, news_sentiment=None,
            rs_rank=None, options_sentiment=None, cp_ratio=None, kscore=None, is_stale=False,
            base_reasons={"ta_score": ta, "ml_unavailable_reason": "no_eligible_model"}, ml_oos_suppressed=flag)
    outs = [run(auc, True) for auc in (0.0, 0.55, 0.85, float("nan"))]
    assert len({(o.signal, o.confidence, o.bullish_probability) for o in outs}) == 1
    assert all(o.reasons["low_oos_accuracy"] is True and o.reasons["ml_weight"] == 0.0 for o in outs)
    # And the flag is what does it: the same inputs without it are the plain TA-only result.
    plain = run(0.85, False)
    assert plain.reasons["low_oos_accuracy"] is False
    if ta != 0.5:
        assert abs(outs[0].bullish_probability - 0.5) <= abs(plain.bullish_probability - 0.5)


def test_fetch_sets_the_sa27_flag_explicitly_for_unavailable_and_reports_no_quality():
    status, body = _ensemble(TRAINER, ALL_SUPPRESSED)
    ml_prob, quality, meta = _fetch(status, body)
    assert ml_prob is None and quality == 0.0
    assert meta["ml_oos_suppressed"] is True and meta["ml_quality_status"] == "no_eligible_model"


def _serving_unavailable_envelope(reason):
    """The REAL ml-prediction `_serving_unavailable` response (F04 fencing, RF-01)."""
    node = next(n for n in ast.parse(TRAINER.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "_serving_unavailable")
    env = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(TRAINER), "exec"), env)
    return env["_serving_unavailable"]("FIX", 10, reason)


@pytest.mark.parametrize("reason", ["publication_policy_unavailable:PermissionError",
                                    "publication_policy_unavailable:schema:selection:FIX|SWING|xgboost",
                                    "selected_artifact_hash_mismatch:FIX|SWING|xgboost"])
def test_a_serving_refusal_reaches_the_signal_as_zero_weight_unavailable_ml(reason):
    """Publication-policy failures answer 200 + ml_available False; the fetch then records the
    same unavailable metadata as all-suppressed (zero weight, SA-27 flag, the label the
    conviction gate fails on) and stops the endpoint cascade."""
    res = _fuse(_fetch(200, _serving_unavailable_envelope(reason)))
    r = res.reasons
    assert r["ml_probability"] is None and r["ml_weight"] == 0.0
    assert r["ml_unavailable_reason"] == "no_eligible_model"
    assert r["ml_oos_suppressed"] is True and r["low_oos_accuracy"] is True


def test_negative_control_a_500_would_lose_the_unavailable_metadata():
    """Why the routes must not 500 on a policy error: the fetch then reads it as 'no model',
    which the conviction gate soft-PASSES."""
    ml_prob, quality, meta = _fetch(500, {"detail": "Internal Server Error"})
    assert ml_prob is None and meta == {}
