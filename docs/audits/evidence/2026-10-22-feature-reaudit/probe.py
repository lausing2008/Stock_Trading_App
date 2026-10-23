"""Offline witnesses using current production function bodies; no network/DB writes.

Run from any directory: python3 docs/audits/evidence/2026-10-22-feature-reaudit/probe.py
Imports inside extracted bodies are removed only when supplied explicitly below.
Fixtures are synthetic. Passing assertions confirm current defects, not desired behaviour.
"""
import ast
import copy
import hashlib
import json
import math
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ROUTES = "services/market-data/src/api/routes.py"
UW = "services/market-data/src/services/unusual_whales.py"
CHAIN = "services/market-data/src/services/uw_option_chain.py"
CAL = "services/signal-engine/src/api/calibration.py"
GEN = "services/signal-engine/src/generators/signals.py"


def extract(file, names, ns, remove_imports=False):
    for node in ast.parse((ROOT / file).read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node = copy.deepcopy(node)
            node.decorator_list = []
            if remove_imports:
                node.body = [n for n in node.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
            exec(compile(ast.Module(body=[node], type_ignores=[]), file, "exec"), ns)


def main():
    out = {}
    ns = {}
    extract(CHAIN, {"_to_chain_row"}, ns)
    row = {"strike": 100, "option_type": "call", "volume": None, "open_interest": None}
    unknown = ns["_to_chain_row"](row, None)
    measured = ns["_to_chain_row"](row, 120)
    assert unknown["itm"] is False and measured["itm"] is True
    assert unknown["volume"] == unknown["oi"] == unknown["iv"] == 0
    out["missing_inputs"] = {"no_spot_itm": unknown["itm"], "spot_120_itm": measured["itm"],
                              "missing_volume": unknown["volume"], "missing_oi": unknown["oi"],
                              "missing_iv": unknown["iv"]}
    ns = {}
    extract(ROUTES, {"_options_chain_rows"}, ns)
    flattened = ns["_options_chain_rows"](pd.DataFrame([{"strike": 100, "bid": 1, "ask": 2,
        "option_symbol": "MU1261120C00100000", "multiplier_status": "nonstandard_root_deliverable_unknown"}]))[0]
    assert "option_symbol" not in flattened and "multiplier_status" not in flattened
    out["adjusted_identity_discarded_by_chain_rows"] = flattened

    ns = {"is_available": lambda: True, "DarkPoolPrintRow": object,
          "_get_redis": lambda: NS(get=lambda _: None),
          "_get": lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic upstream 503")),
          "log": NS(warning=lambda *a, **k: None)}
    extract(UW, {"get_dark_pool_prints"}, ns)
    assert ns["get_dark_pool_prints"]("SYNTH") == []
    out["provider_failure"] = {"exception": "synthetic upstream 503", "returned": []}

    # The route first truncates each side using ONLY volume. A later total order cannot
    # recover tied rows discarded by that first truncation.
    rows = [dict(volume=100, strike=k, vol_oi=1, expiry="2026-11-20", side="call") for k in [100, 101, 102, 103]]
    ns = {}
    extract(ROUTES, {"rank_unusual", "compute_options_pressure_score"}, ns)
    selected = lambda rs: ns["rank_unusual"](pd.DataFrame(rs).sort_values("volume", ascending=False).head(3).to_dict("records"))
    a, b = selected(rows), selected(list(reversed(rows)))
    assert {r["strike"] for r in a} != {r["strike"] for r in b}
    out["tied_selection"] = {"input_order_a_strikes": [r["strike"] for r in a],
                              "input_order_b_strikes": [r["strike"] for r in b]}
    out["pressure_reciprocals"] = {str(r): ns["compute_options_pressure_score"](r, "neutral", None, 5000, 5000)["components"]["cp_ratio_pts"] for r in [2, .5]}
    assert out["pressure_reciprocals"] == {"2": 10.0, "0.5": 25.0}

    # Execute the full pure sweep and actual production weight helper. Policy, database
    # observations and action-coverage evidence are injected; no applying endpoint is called.
    policy = {"SWING": {"floor": .15, "buy_thresholds": {"bull": .7, "unknown": .7}}}
    ns = {"np": np, "math": math, "datetime": datetime, "date": date, "timedelta": timedelta,
          "_ml_weight_policy": lambda: policy, "_OUTCOME_HOLD_DAYS": {"SWING": 14},
          "_ML_WEIGHT_MIN_VAL_SAMPLES": 15,
          "_observation_basis": lambda *a: ({"prices": []}, {"usable": True})}
    extract(GEN, {"_calculate_ml_weight"}, ns)
    extract(CAL, {"_ml_weight_inputs", "_ml_weight_evaluation", "_ml_candidate_score"}, ns, remove_imports=True)
    extract(CAL, {"_ml_weight_sweep"}, ns)
    rows, prices = [], []
    for sid in range(100):
        rows.append((NS(stock_id=sid, ts=datetime(2026, 1, 2), horizon=NS(value="SWING"),
                        reasons={"ml_test_auc": .7, "ml_probability": .8, "ta_score": .8, "market_regime": "bull"}), f"SYNTH{sid}"))
        prices.extend([NS(stock_id=sid, ts=date(2026, 1, 5), close=100),
                       NS(stock_id=sid, ts=date(2026, 1, 16), close=90)])
    sweep = ns["_ml_weight_sweep"](rows, prices, {}, {}, None)
    assert sweep["decision"]["would_apply"] is True
    assert sweep["decision"]["candidate_validation_ev_pct"] == -10
    assert sweep["decision"]["history"]["train_window"] == sweep["decision"]["history"]["validation_window"]
    out["first_tune_negative_returns_same_day_split"] = {"decision": sweep["decision"], "return_basis": sweep["return_basis"], "split": sweep["split"]}
    out["protective_floor_at_zero_cap"] = ns["_calculate_ml_weight"](.8, .8, .7, 0, .15)

    sources = [ROUTES, UW, CHAIN, CAL, GEN, "services/market-data/src/services/options_scope.py",
               "services/market-data/src/services/dark_pool_evidence.py",
               "frontend/src/components/OptionsFlowCard.tsx", "frontend/src/components/MarketPressurePanel.tsx",
               "frontend/src/lib/optionsGainLoss.ts", "frontend/src/lib/optionsFlowStatus.ts"]
    out["head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    out["source_sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources}
    inventory = {}
    # Extend the previous threshold inventory with current static configuration. No Redis
    # snapshot is re-used as if current. Literal assignments only; no module execution.
    config_files = [GEN, CAL, ROUTES, "services/market-data/src/services/scheduler.py",
                    "services/market-data/src/services/paper_trading_engine.py",
                    "services/ml-prediction/src/training/trainer.py",
                    "services/signal-engine/src/api/signals_shared.py"]
    for file in config_files:
        tree = ast.parse((ROOT / file).read_text())
        constants = {}
        for node in tree.body:
            target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if isinstance(node, ast.Assign) else None
            if not isinstance(target, ast.Name) or not target.id.isupper():
                continue
            try:
                value = ast.literal_eval(node.value)
                json.dumps(value)
            except (ValueError, TypeError, SyntaxError):
                continue
            constants[target.id] = {"line": node.lineno, "value": value}
        inventory[file] = {"sha256": hashlib.sha256((ROOT / file).read_bytes()).hexdigest(), "constants": constants}
    (HERE / "threshold-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    (HERE / "probe-results.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k != "source_sha256"}, indent=2, default=str))


if __name__ == "__main__":
    main()
