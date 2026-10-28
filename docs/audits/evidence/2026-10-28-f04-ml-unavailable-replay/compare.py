"""Compare the replay passes: old (record) vs corrected (replay), with the control as a harness check.

Usage (repository root):
  python compare.py <dir with record/control/replay .jsonl + -meta.json> <out json> <active_symbols.json>

Conviction-gate eligibility is computed with the REAL `_is_conviction_buy`, extracted from
scheduler.py at HEAD (old) and from the working tree (corrected), on each pass's own reasons.
Calibrated soft-layer edges are not loaded (`_load_conviction_edges` -> {}), identically for
both, so tiers use the base soft layers; kscore comes from each row's reasons.
"""
import json
import pathlib
import subprocess
import sys
from collections import Counter, defaultdict
from statistics import mean, median

ROOT = pathlib.Path(__file__).resolve().parents[4]
D, OUT = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
SCHED = "services/market-data/src/services/scheduler.py"


def gate_from(source):
    def fn_src(name):
        start = source.index(f"def {name}")
        return source[start:source.index("\n\n\n", start)]

    def const(name, close):
        start = source.index(f"{name}")
        return source[start:source.index(close, start) + len(close)]

    ns = {"_load_conviction_edges": lambda: {}}
    exec(const("_REGIME_THRESHOLDS: dict", "\n}"), ns)
    exec(const("_CONVICTION_LAYER_FLAG = {", "\n}"), ns)
    for line in source.splitlines():
        if line.startswith(("_CONVICTION_EDGE_NOISE_THRESHOLD_PCT = ", "_MAX_ROC10_FOR_ENTRY = ", "_SOFT_LAYER_BASE = ")):
            exec(line, ns)
    exec(fn_src("_soft_layer_keywords"), ns)
    exec(fn_src("_is_conviction_buy"), ns)
    return ns["_is_conviction_buy"]


old_gate = gate_from(subprocess.check_output(["git", "-C", str(ROOT), "show", f"HEAD:{SCHED}"], text=True))
new_gate = gate_from((ROOT / SCHED).read_text())


def load(name):
    return {(r["symbol"], r["style"]): r for r in map(json.loads, open(D / f"{name}.jsonl"))}


def clean(r):
    r = {k: v for k, v in r.items() if k != "seconds"}
    rs = dict(r["reasons"])
    ic = dict(rs.get("input_contract") or {})
    ic.pop("generated_at", None)
    rs["input_contract"] = ic
    r["reasons"] = rs
    return r


rec, ctl, new = load("record"), load("control"), load("replay")

# ── Accounting: every expected cell is completed, failed or missing in each pass ──────────
STYLES = ("SHORT", "SWING", "LONG", "GROWTH")
expected = {(s, st) for s in json.loads(pathlib.Path(sys.argv[3]).read_text()) for st in STYLES}
accounting = {}
for name, rows in (("record", rec), ("control", ctl), ("replay", new)):
    meta = json.loads((D / f"{name}-meta.json").read_text())
    failed_syms = {e["symbol"] for e in meta["errors"]}
    failed = {c for c in expected if c[0] in failed_syms}
    completed = expected & set(rows)
    accounting[name] = {"expected": len(expected), "completed": len(completed), "failed": len(failed),
                        "missing": len(expected - completed - failed), "unexpected": len(set(rows) - expected),
                        "errors": meta["errors"], "misses": meta["misses"], "miss_sample": meta["miss_sample"]}
compared = expected & set(rec) & set(ctl) & set(new)
rec = {k: rec[k] for k in compared}
ctl = {k: ctl[k] for k in compared}
new = {k: new[k] for k in compared}
control_mismatch = [list(k) for k in rec if clean(rec[k]) != clean(ctl[k])]
contract_diff = [list(k) for k in rec
                 if clean(rec[k])["reasons"]["input_contract"] != clean(new[k])["reasons"]["input_contract"]]


def tier(gate, row):
    if row["signal"] != "BUY":
        return None
    ok, t, _, failed = gate({"horizon": row["style"], "reasons": row["reasons"]},
                            kscore=row["reasons"].get("kscore"), regime=row["reasons"].get("market_regime"))
    return t


per = defaultdict(lambda: {"cells": 0, "ml_unavailable": 0, "eligible_ml": 0, "no_model": 0,
                           "changed_signal": 0, "changed_confidence": 0, "transitions": Counter(),
                           "abs_conf_delta": [], "signed_conf_delta": [], "gate": Counter(),
                           "newly_actionable": [], "no_longer_actionable": [], "new_buys": [], "lost_buys": [],
                           "new_sells": [], "lost_sells": []})
eligible_changed = []
for k in sorted(rec):
    o, n = rec[k], new[k]
    p = per[k[1]]
    p["cells"] += 1
    reason = n["reasons"].get("ml_unavailable_reason")
    if reason == "no_eligible_model":
        p["ml_unavailable"] += 1
    elif n["reasons"].get("ml_probability") is None:
        p["no_model"] += 1
    else:
        p["eligible_ml"] += 1
        if clean(o) != {**clean(n), "reasons": {kk: vv for kk, vv in clean(n)["reasons"].items()}} and \
                (o["signal"], o["confidence"], o["bullish_probability"]) != (n["signal"], n["confidence"], n["bullish_probability"]):
            eligible_changed.append(list(k))
    if o["signal"] != n["signal"]:
        p["changed_signal"] += 1
        p["transitions"][f"{o['signal']}->{n['signal']}"] += 1
    if round(o["confidence"], 4) != round(n["confidence"], 4):
        p["changed_confidence"] += 1
        p["abs_conf_delta"].append(abs(n["confidence"] - o["confidence"]))
        p["signed_conf_delta"].append(n["confidence"] - o["confidence"])
    to, tn = tier(old_gate, o), tier(new_gate, n)
    if to is not None or tn is not None:
        p["gate"][f"{to}->{tn}"] += 1
    was = to in ("full", "near")
    now = tn in ("full", "near")
    item = {"symbol": k[0], "old": [o["signal"], round(o["confidence"], 1), to],
            "new": [n["signal"], round(n["confidence"], 1), tn], "ml_unavailable": reason == "no_eligible_model",
            "old_ml": [o["reasons"].get("ml_probability"), o["reasons"].get("ml_weight")]}
    if now and not was:
        p["newly_actionable"].append(item)
    if was and not now:
        p["no_longer_actionable"].append(item)
    # End-to-end BUY transitions, independent of the gate: removing a bearish ML pull can turn
    # a signal into BUY even though the gate's own logic for it is unchanged.
    if n["signal"] == "BUY" and o["signal"] != "BUY":
        p["new_buys"].append(item)
    if o["signal"] == "BUY" and n["signal"] != "BUY":
        p["lost_buys"].append(item)
    if n["signal"] == "SELL" and o["signal"] != "SELL":
        p["new_sells"].append(item)
    if o["signal"] == "SELL" and n["signal"] != "SELL":
        p["lost_sells"].append(item)


def summarise(p):
    a = p.pop("abs_conf_delta")
    s = p.pop("signed_conf_delta")
    p["transitions"] = dict(p["transitions"])
    p["gate"] = dict(p["gate"])
    p["confidence_delta"] = {"n": len(a), "mean_abs": round(mean(a), 2) if a else None,
                             "median_abs": round(median(a), 2) if a else None,
                             "mean_signed": round(mean(s), 2) if s else None, "max_abs": round(max(a), 2) if a else None}
    for key in ("newly_actionable", "no_longer_actionable", "new_buys", "lost_buys", "new_sells", "lost_sells"):
        p[f"{key}_n"] = len(p[key])
    return p


old_dist = {st: dict(Counter(r["signal"] for (s, sty), r in rec.items() if sty == st)) for st in ("SHORT", "SWING", "LONG", "GROWTH")}
new_dist = {st: dict(Counter(r["signal"] for (s, sty), r in new.items() if sty == st)) for st in ("SHORT", "SWING", "LONG", "GROWTH")}
result = {
    "accounting": accounting, "compared_cells": len(compared), "rows": len(rec), "control_matches_record": not control_mismatch, "control_mismatch": control_mismatch[:20],
    "input_contract_differs_record_vs_replay": contract_diff[:20], "input_contract_differs_n": len(contract_diff),
    "eligible_ml_cells_changed": eligible_changed,
    "signal_distribution": {"old": old_dist, "new": new_dist},
    "by_horizon": {st: summarise(per[st]) for st in ("SHORT", "SWING", "LONG", "GROWTH")},
}
OUT.write_text(json.dumps(result, indent=1, default=str))
for st, p in result["by_horizon"].items():
    print(st, {k: p[k] for k in ("cells", "ml_unavailable", "eligible_ml", "no_model", "changed_signal", "transitions",
                                 "new_buys_n", "lost_buys_n", "newly_actionable_n", "no_longer_actionable_n",
                                 "new_sells_n", "lost_sells_n", "gate")},
          p["confidence_delta"])
for name, a in accounting.items():
    print(name, {k: a[k] for k in ("expected", "completed", "failed", "missing", "unexpected", "misses")})
print("control==record:", result["control_matches_record"], "| contract diffs:", len(contract_diff),
      "| eligible cells changed:", len(eligible_changed))
