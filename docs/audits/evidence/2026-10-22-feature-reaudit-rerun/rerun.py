"""Rerun of the 2026-10-22 re-audit witnesses against the CURRENT working tree.

The original probe (../2026-10-22-feature-reaudit/probe.py) ASSERTS each defect, so it stops at
the first fixed one, and its R07 witness re-implements the old truncation itself rather than
calling the route. This script records what each witness now observes, without asserting, and
exercises R07 through the real get_options_flow body. It writes only rerun-results.json here.
The original evidence directory is not modified.

Run from the repository root with the market-data test environment available:
    python3 docs/audits/evidence/2026-10-22-feature-reaudit-rerun/rerun.py
"""
import ast
import copy
import hashlib
import json
import random
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MD = ROOT / "services/market-data"
ROUTES = "services/market-data/src/api/routes.py"
UW = "services/market-data/src/services/unusual_whales.py"
CHAIN = "services/market-data/src/services/uw_option_chain.py"


def extract(file, names, ns):
    for node in ast.parse((ROOT / file).read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node = copy.deepcopy(node)
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), file, "exec"), ns)


def main():
    out = {}
    # The suite's own stubs for config/db modules, as under pytest, so real modules import.
    sys.path.insert(0, str(MD))
    sys.path.insert(0, str(MD / "tests"))
    import conftest  # noqa: F401
    from unittest.mock import patch
    from src.api import routes
    from src.services import unusual_whales as uw

    # R03/R04 — adapter, same inputs as the original witness.
    ns = {}
    extract(CHAIN, {"_to_chain_row", "_measured_count", "_measured_iv", "moneyness_for"}, ns)
    row = {"strike": 100, "option_type": "call", "volume": None, "open_interest": None}
    unknown, measured = ns["_to_chain_row"](row, None), ns["_to_chain_row"](row, 120)
    out["R03_R04_missing_inputs"] = {
        "no_reference_itm": unknown["itm"], "no_reference_moneyness": unknown["moneyness"],
        "reference_120_itm": measured["itm"], "missing_volume": unknown["volume"],
        "missing_oi": unknown["oi"], "missing_iv": unknown["iv"],
        "reproduced": unknown["itm"] is False or unknown["volume"] == 0,
    }

    # R05 — the original witness's adjusted-root row, through the real chain-row builder, then
    # into the real game plan with a usable quote.
    flat = routes._options_chain_rows(pd.DataFrame([{"strike": 100, "bid": 1, "ask": 2,
        "option_symbol": "MU1261120C00100000"}]), "MU")[0]
    plan = routes.compute_options_game_plan(current_price=110.0, stop_loss=None, take_profit=100.0, signal="BUY",
        put_expiries=[], put_rows=[], call_expiries=["2026-11-20"], call_rows=[flat], today=date(2026, 10, 22))
    out["R05_identity_in_chain_rows"] = {
        "option_symbol_kept": flat.get("option_symbol"), "terms_status": (flat.get("contract_terms") or {}).get("status"),
        "game_plan_covered_call": plan.get("covered_call"), "refused": plan.get("contract_terms_refused"),
        "reproduced": "option_symbol" not in flat or plan.get("covered_call") is not None,
    }

    # R01 — the original witness's upstream exception, through the real provider and route.
    class _Redis:
        def get(self, k): return None
        def setex(self, *a): pass
    with patch.object(uw, "is_available", lambda: True), patch.object(uw, "_get_redis", lambda: _Redis()), \
         patch.object(uw, "_get", side_effect=RuntimeError("synthetic upstream 503")):
        route_out = routes.get_dark_pool_prints_route("SYNTH", session=None)
        scan = uw.get_dark_pool_prints("SYNTH")
    out["R01_provider_failure"] = {"route_status": route_out.get("status"), "route_reason": route_out.get("reason"),
                                   "scanner_wrapper_returned": scan,
                                   "reproduced": route_out.get("reason") == "no_data"}

    # R07 — through the real route body, forward vs reversed vs shuffled.
    from test_options_flow_selection_order import _run, _row  # the maintained regression harness
    exp = "2099-01-16"
    rows = [_row(s, 200) for s in (100, 101, 102, 103)]
    ids = lambda r: [(u["side"], u["strike"]) for u in r["unusual"]]
    fwd = _run({exp: rows}, {exp: [_row(90, 150)]})
    rev = _run({exp: list(reversed(rows))}, {exp: [_row(90, 150)]})
    rng = random.Random(1)
    shuffles = [ids(_run({exp: rng.sample(rows, 4)}, {exp: [_row(90, 150)]})) for _ in range(20)]
    out["R07_tied_selection_via_route"] = {
        "forward": ids(fwd), "reversed": ids(rev),
        "distinct_selections_over_20_shuffles": len({tuple(s) for s in shuffles}),
        "reproduced": ids(fwd) != ids(rev) or len({tuple(s) for s in shuffles}) != 1,
    }

    # R08 — pressure reciprocals (open; not in this work package).
    ns = {}
    extract(ROUTES, {"compute_options_pressure_score"}, ns)
    rec = {str(r): ns["compute_options_pressure_score"](r, "neutral", None, 5000, 5000)["components"]["cp_ratio_pts"] for r in [2, .5]}
    out["R08_pressure_reciprocals"] = {"points": rec, "reproduced": rec["2"] != rec["0.5"]}

    out["head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    out["working_tree_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    out["source_sha256"] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (ROUTES, UW, CHAIN)}
    (HERE / "rerun-results.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps({k: v.get("reproduced") for k, v in out.items() if isinstance(v, dict) and "reproduced" in v}))


if __name__ == "__main__":
    main()
