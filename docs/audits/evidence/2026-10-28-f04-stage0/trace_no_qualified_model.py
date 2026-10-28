"""F04 Stage 0 — what live fusion does when no ML model is qualified (all suppressed).

Executes the REAL functions, extracted unchanged by AST from the repository:
  - ml-prediction  predict_latest_ensemble_three  (training/trainer.py)
  - signal-engine  _calculate_ml_weight           (generators/signals.py)
Only each member's predict_latest() result is supplied as a fixture, shaped exactly as the real
predict_latest returns for a suppressed bundle: bullish_probability 0.5 plus the bundle's stored
metrics. The signal-engine quality read (`mean_model_test_auc` -> `auc` -> `cv_auc_mean`) and
the SA-27 0.6x compression are reproduced as written in signals.py and named here.

No network, database, model files or services. Prints one JSON object per scenario.
Usage: python trace_no_qualified_model.py  (from the repository root)
"""
import ast
import json
import pathlib
import types

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[4]


def extract(path, name, env):
    src = (ROOT / path).read_text()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), env)
    return env[name]


class _MetaDisabled(Exception):
    pass


def member(prob, auc, suppressed, cv=None):
    m = {"auc": auc, "cv_auc_mean": cv if cv is not None else auc, "buy_threshold": 0.55}
    return {"symbol": "FIX", "bullish_probability": prob, "confidence": round(abs(prob - .5) * 200, 2),
            "metrics": m, "oos_suppressed": suppressed}


def run(members, ta_prob, cap=0.75, floor=0.0):
    """cap 0.75 is the profile maximum; production per-style caps are tuned lower (observed
    average applied weight on the 0.45 rows: SWING 0.14, GROWTH 0.61)."""
    exists = {name: name in members for name in ("xgboost", "lightgbm", "random_forest")}
    env = {
        "predict_latest": lambda symbol, model, horizon, style="SWING": members[model],
        "_artifact_path": lambda symbol, model, style="SWING": types.SimpleNamespace(exists=lambda m=model: exists[m]),
        "log": types.SimpleNamespace(warning=lambda *a, **k: None),
        "_meta_blend_enabled": lambda: False, "_meta_shadow_enabled": lambda: False,
        "_MetaDisabled": _MetaDisabled, "_load_sector_and_market_cap": lambda s: (None, None),
    }
    ens = extract("services/ml-prediction/src/training/trainer.py", "predict_latest_ensemble_three", env)
    weight = extract("services/signal-engine/src/generators/signals.py", "_calculate_ml_weight", {"np": np})
    out = ens("FIX", 10, style="SWING")
    # signal-engine _fetch_ml_data quality read, as written:
    m, q, status = out.get("metrics") or {}, 0.0, "unavailable"
    for key in ("mean_model_test_auc", "auc", "cv_auc_mean"):
        if m.get(key) is not None:
            q, status = float(m[key]), "measured"
            break
    prob = float(out.get("bullish_probability", 0.5))
    w = weight(prob, ta_prob, q, cap, floor)
    fused = w * float(np.clip(prob, .05, .95)) + (1 - w) * ta_prob
    sa27 = 0.5 + (fused - 0.5) * 0.6 if out.get("oos_suppressed") else fused
    return {"ml_probability": round(prob, 4), "agreement": out.get("ensemble_agreement"), "model": out.get("model"),
            "reported_quality": q, "quality_status": status, "oos_suppressed": out.get("oos_suppressed"),
            "ml_weight": round(w, 4), "ta_prob": ta_prob, "fused": round(fused, 4),
            "after_sa27": round(sa27, 4), "cap": cap, "ta_only_fused": ta_prob}


SCENARIOS = {
    "all_suppressed_xgb_rf_high_auc": ({"xgboost": member(.5, .83, True), "random_forest": member(.5, .86, True)}, .62),
    "all_suppressed_three_high_auc": ({"xgboost": member(.5, .83, True), "lightgbm": member(.5, .80, True),
                                       "random_forest": member(.5, .86, True)}, .62),
    "all_suppressed_xgb_rf_low_auc": ({"xgboost": member(.5, .55, True), "random_forest": member(.5, .56, True)}, .62),
    "all_suppressed_single_xgb": ({"xgboost": member(.5, .80, True)}, .62),
    "one_unsuppressed_rf": ({"xgboost": member(.5, .83, True), "random_forest": member(.58, .62, False)}, .62),
    "control_none_suppressed": ({"xgboost": member(.6, .62, False), "random_forest": member(.58, .60, False)}, .62),
}

if __name__ == "__main__":
    for name, (members, ta) in SCENARIOS.items():
        print(json.dumps({"scenario": name, **run(members, ta)}))
    members, ta = SCENARIOS["all_suppressed_xgb_rf_high_auc"]
    print(json.dumps({"scenario": "all_suppressed_xgb_rf_high_auc_cap_0.15", **run(members, ta, cap=0.15)}))
