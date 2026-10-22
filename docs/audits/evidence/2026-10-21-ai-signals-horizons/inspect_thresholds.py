"""Offline inventory and executable witnesses for the horizon settings review.

Reads source and an optional previously captured runtime.json. Never imports the
application, calls providers, mutates configuration or runs calibration endpoints.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
GEN = "services/signal-engine/src/generators/signals.py"
FILES = {
    GEN: ["_STYLE_PROFILES", "_TA_WEIGHTS_DEFAULT", "_DYNAMIC_BUY_THRESHOLD_BOUNDS",
          "_DYNAMIC_SELL_THRESHOLD_BOUNDS", "_SELL_THRESHOLD_FALLBACK", "_EARNINGS_COMPRESSION_ENABLED",
          "_HOT_NEWS_DELAYED_COMPRESS"],
    "services/signal-engine/src/api/signals_shared.py": ["_OUTCOME_HOLD_DAYS", "_SELL_OUTCOME_HOLD_DAYS",
        "_OUTCOME_WIN_HURDLE_PCT", "_CONF_BANDS", "_CONF_CAL_MIN_COUNT"],
    "services/signal-engine/src/api/calibration.py": ["_TUNE_STRATEGY_MIN_SAMPLES",
        "_TUNE_STRATEGY_BUY_BOUNDS", "_TUNE_STRATEGY_ML_CAP_BOUNDS"],
    "services/market-data/src/services/scheduler.py": ["_REGIME_THRESHOLDS", "_STYLE_PARAMS",
        "_MAX_ROC10_FOR_ENTRY", "_MIN_CONFIDENCE", "_MIN_CONFLUENCE", "_SOFT_LAYER_BASE",
        "_CONVICTION_EDGE_NOISE_THRESHOLD_PCT"],
    "services/market-data/src/services/paper_trading_engine.py": ["_DEFAULT_CONFIG", "_STYLE_PARAMS",
        "_STYLE_OVERRIDES", "_HK_MARKET_OVERRIDES"],
    "services/ml-prediction/src/training/trainer.py": ["_HORIZON_BY_STYLE", "_PRECISION_BY_STYLE"],
    "services/ml-prediction/src/features/builder.py": [],
    "services/market-data/src/services/ai_signal_alert_contract.py": [],
    "services/signal-engine/src/api/routes.py": [],
    "services/signal-engine/src/api/outcomes.py": [],
    "shared/db/models.py": [],
}


def assignment_name(node):
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id


def main():
    inventory = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                 "files": {}, "configuration": {}, "generator_conditions": [],
                 "generator_numeric_expressions": [], "calibration_parameters": {}}
    for file, names in FILES.items():
        source = (ROOT / file).read_text()
        tree = ast.parse(source)
        inventory["files"][file] = hashlib.sha256(source.encode()).hexdigest()
        inventory["configuration"][file] = {}
        for node in tree.body:
            name = assignment_name(node)
            if name in names:
                inventory["configuration"][file][name] = {"line": node.lineno, "value": ast.literal_eval(node.value)}
            if isinstance(node, ast.FunctionDef) and file.endswith("api/calibration.py"):
                inventory["calibration_parameters"][node.name] = ast.unparse(node.args)
            if isinstance(node, ast.FunctionDef) and file == GEN:
                for item in ast.walk(node):
                    if isinstance(item, ast.If) and any(isinstance(x, ast.Constant) and isinstance(x.value, (int, float))
                                                        for x in ast.walk(item.test)):
                        inventory["generator_conditions"].append({"function": node.name, "line": item.lineno,
                                                                  "condition": ast.unparse(item.test)})
                    if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Compare, ast.IfExp)):
                        if any(isinstance(x, ast.Constant) and type(x.value) in (int, float) for x in ast.walk(item)):
                            inventory["generator_numeric_expressions"].append({
                                "function": node.name, "line": item.lineno, "expression": ast.unparse(item)})
    (HERE / "source-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    profiles = inventory["configuration"][GEN]["_STYLE_PROFILES"]["value"]
    runtime = json.loads((HERE / "runtime.json").read_text())
    keys = runtime["redis_allowlisted_values"]
    namespace = {"np": np, "_STYLE_PROFILES": profiles,
                 "_DYNAMIC_BUY_THRESHOLD_BOUNDS": (.55, .85),
                 "_DYNAMIC_SELL_THRESHOLD_BOUNDS": (.15, .45), "_SELL_THRESHOLD_FALLBACK": .35,
                 "_redis_get_float": lambda key: float(keys[key]) if keys.get(key) is not None else None}
    tree = ast.parse((ROOT / GEN).read_text())
    wanted = {"_get_dynamic_buy_threshold", "_get_dynamic_sell_threshold", "_decide_style"}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.Module(body=[node], type_ignores=[]), GEN, "exec"), namespace)
    effective = {}
    for style in profiles:
        effective[style] = {reg: namespace["_get_dynamic_buy_threshold"](style, reg)
                            for reg in ("bull", "neutral", "choppy", "risk_off", "bear", "unknown")}
        effective[style]["sell"] = namespace["_get_dynamic_sell_threshold"](style) or .35
    # Execute the original fusion block, stopping before downstream filters. Inject a
    # measured model with no conflict and an explicit zero cap, to isolate cap/floor order.
    node = copy.deepcopy(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_apply_style_signal"))
    stop = next(i for i, n in enumerate(node.body) if assignment_name(n) == "fused_before_filters")
    node.body = node.body[:stop] + ast.parse("return reasons").body
    node.name = "fusion_probe"
    node.args = ast.parse("def f(ta_prob, ml_prob, ml_test_auc, style_key, base_reasons): pass").body[0].args
    node.returns = None
    ast.fix_missing_locations(node)
    namespace.update({"_ml_weight_global_cap": 0., "_get_style_tuned_param": lambda s,p,d: d})
    exec(compile(ast.Module(body=[node], type_ignores=[]), GEN, "exec"), namespace)
    witness = namespace["fusion_probe"](.7, .8, .65, "SWING", {})
    assert witness["ml_weight"] == .15
    # Exact boundary against captured runtime policy, not a reimplemented decision function.
    boundary = effective["SHORT"]["bull"]
    exact = namespace["_decide_style"](boundary, "SHORT", "bull")[0]
    above = namespace["_decide_style"](boundary + .000001, "SHORT", "bull")[0]
    assert exact == "HOLD" and above == "BUY"
    results = {"runtime_capture": runtime["captured_at_utc"], "code_derived_effective_thresholds": effective,
               "synthetic_zero_global_cap_swing": witness,
               "synthetic_buy_boundary": {"value": boundary, "at_boundary": exact, "just_above": above},
               "scope": "Offline source execution with captured keys and synthetic fusion inputs; not a profitability backtest."}
    (HERE / "probe-results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
