"""F04 Stage 0 — read-only, hash-bound model artifact and routing inventory.

Runs in a throwaway container from the deployed ml-prediction image, with the model volume
mounted READ-ONLY at /data and no network. It never imports the trainer (no settings, no DB),
never calls an endpoint and never writes under /data.

Each artifact's bytes are read ONCE; the SHA-256 and the loaded metadata come from those same
bytes (joblib loads from memory), so an atomic replacement during the capture cannot pair one
version's hash with another's metadata. Directory listings before and after are diffed and any
replacement is recorded rather than retried away.

Rules are the deployed ones, extracted unchanged by AST:
  - `_compute_oos_suppression` and `_HORIZON_BY_STYLE` from the image's own trainer.py;
  - `_threshold_report_bounds` from the HELD F04 branch's trainer.py (passed in /in).
M14 `build_inventory` is imported from the image unchanged.

The routing section mirrors predict_latest_ensemble_three's member selection from STORED flags
only. It describes which artifacts a request would load and how the fallback would treat them;
it is not observed runtime contribution (feature-build failures, live probabilities and the
signal-engine fusion are not modelled here).

Usage (inside the container):
  python capture_inventory.py /data/models /in /out
"""
import ast
import hashlib
import io
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

APP = os.environ.get("APP_ROOT", "/app")
sys.path[:0] = [f"{APP}/shared", APP]
import importlib.util  # noqa: E402

import joblib  # noqa: E402

# By file path: importing it through `src.training` would run the package __init__, which
# imports the trainer (settings, database, model libraries) -- exactly what this must not do.
_spec = importlib.util.spec_from_file_location("m14_suppression_inventory",
                                               f"{APP}/src/training/suppression_inventory.py")
_m14 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _m14
_spec.loader.exec_module(_m14)
MIN_TEST_ROWS_FOR_EVIDENCE, SUPPRESSION_RULE_VERSION = _m14.MIN_TEST_ROWS_FOR_EVIDENCE, _m14.SUPPRESSION_RULE_VERSION
build_inventory, evidence_sufficiency = _m14.build_inventory, _m14.evidence_sufficiency

FAMILIES = ("xgboost", "lightgbm", "random_forest")
STYLES = ("SHORT", "SWING", "LONG", "GROWTH")
ENSEMBLE_WEIGHTS = {"xgboost": 0.30, "lightgbm": 0.45, "random_forest": 0.25}
REPORT_FLOOR = 10          # trainer _MIN_REPORT_ROWS (rows + two classes)
NUDGE_MIN_AUC = 0.57       # predict_latest_ensemble_three agreement nudge


def now():
    return datetime.now(timezone.utc).isoformat()


def extract(path, names):
    """Exec the named top-level defs/assignments from a source file, unchanged."""
    src = pathlib.Path(path).read_text()
    nodes = []
    for n in ast.parse(src).body:
        if isinstance(n, ast.FunctionDef) and n.name in names:
            nodes.append(n)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id in names for t in targets):
                nodes.append(n)
    env = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    missing = [n for n in names if n not in env]
    if missing:
        raise SystemExit(f"could not extract {missing} from {path}")
    return env, hashlib.sha256(src.encode()).hexdigest()


def listing(root):
    out = {}
    for fam in FAMILIES:
        d = root / fam
        if not d.is_dir():
            continue
        for p in d.iterdir():
            st = p.stat()
            out[f"{fam}/{p.name}"] = (st.st_size, st.st_mtime_ns, st.st_ino)
    return out


def parse_name(fname):
    """{symbol}_{style}.joblib, or legacy {symbol}.joblib (served as SWING only when no
    {symbol}_swing.joblib exists)."""
    stem = fname[:-len(".joblib")]
    for s in STYLES:
        suf = "_" + s.lower()
        if stem.endswith(suf):
            return stem[:-len(suf)], s, False
    return stem, "SWING", True


def quality(m):
    auc = m.get("auc")
    if auc is None:
        auc = m.get("cv_auc_mean")
    return auc


def main(root, inp, out):
    root, inp, out = pathlib.Path(root), pathlib.Path(inp), pathlib.Path(out)
    started = now()
    prod, prod_sha = extract(f"{APP}/src/training/trainer.py", ["_compute_oos_suppression", "_HORIZON_BY_STYLE"])
    f04, f04_sha = extract(inp / "f04_trainer.py", ["_threshold_report_bounds"])
    decide, horizons, bounds = prod["_compute_oos_suppression"], prod["_HORIZON_BY_STYLE"], f04["_threshold_report_bounds"]
    active = json.loads((inp / "active_symbols.json").read_text())

    before = listing(root)
    artifacts, m14_rows = {}, []
    for rel in sorted(k for k in before if k.endswith(".joblib")):
        fam, fname = rel.split("/", 1)
        symbol, style, legacy = parse_name(fname)
        rec = {"artifact": rel, "family": fam, "symbol": symbol, "style_from_name": style,
               "legacy_swing_name": legacy, "read_at": now()}
        try:
            with open(root / rel, "rb") as fh:
                data = fh.read()
            st = os.stat(root / rel)
            rec.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                       mtime_ns_at_read=st.st_mtime_ns, inode_at_read=st.st_ino)
            bundle = joblib.load(io.BytesIO(data))
            del data
        except Exception as exc:
            rec["error"] = type(exc).__name__
            artifacts[rel] = rec
            m14_rows.append((rel, None))
            continue
        m = bundle.get("metrics") or {}
        light = {"metrics": m, "oos_suppressed": bool(bundle.get("oos_suppressed", False))}
        m14_rows.append((rel, light))
        should, reason = decide(m.get("cv_auc_mean"), m.get("recall", -1.0), m.get("precision", -1.0),
                                m.get("overfit_gap"), m.get("evaluation_valid"))
        h = m.get("embargo_target_bars")
        h_source = "embargo_target_bars"
        if h is None:
            h, h_source = horizons.get(style), "style_map"
        n_test = m.get("n_test")
        f04_report = None
        if isinstance(n_test, int) and isinstance(h, int):
            t_end, r_start = bounds(n_test, h)
            f04_report = n_test - r_start
        sufficiency, missing = evidence_sufficiency(m)
        rec.update(
            saved_style=bundle.get("style"), trained_at=bundle.get("trained_at"),
            oos_suppressed=light["oos_suppressed"], rule_would_suppress=should, rule_reason=reason,
            evaluation_valid=m.get("evaluation_valid"),
            protocol_evaluation_validity="unknown_or_legacy",   # no artifact carries a v2 protocol
            threshold_evaluation_mode=m.get("threshold_evaluation_mode"),
            embargo_shortfall=m.get("embargo_shortfall"), published=m.get("published"),
            auc=m.get("auc"), cv_auc_mean=m.get("cv_auc_mean"), overfit_gap=m.get("overfit_gap"),
            precision=m.get("precision"), recall=m.get("recall"),
            n_test=n_test, n_threshold_rows=m.get("n_threshold_rows"), n_report_rows=m.get("n_report_rows"),
            n_metric_rows=m.get("n_metric_rows"), metric_class_support=m.get("metric_class_support"),
            horizon=h, horizon_source=h_source,
            m14_sufficiency=sufficiency, m14_missing=missing,
            f04_report_rows=f04_report,
            f04_report_rows_feasible=None if f04_report is None else f04_report >= REPORT_FLOOR,
            f04_class_support_after_purge="unknown",
        )
        artifacts[rel] = rec
        del bundle
    after = listing(root)
    completed = now()

    changed = sorted(k for k in before if k in after and before[k] != after[k])
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    for k in changed:
        if k in artifacts:
            artifacts[k]["replaced_during_capture"] = True

    inv = build_inventory(m14_rows, decide=decide)

    # Routing: per active (symbol, style), which files predict_latest_ensemble_three would load.
    def resolve(fam, symbol, style):
        if style == "SWING":
            new, old = f"{fam}/{symbol}_swing.joblib", f"{fam}/{symbol}.joblib"
            return old if (old in before and new not in before) else new
        return f"{fam}/{symbol}_{style.lower()}.joblib"

    cells, cell_counts = [], {}
    for sym in active:
        for style in STYLES:
            members = {}
            for fam in FAMILIES:
                rel = resolve(fam, sym, style)
                if rel in artifacts:
                    members[fam] = artifacts[rel]
            xgb = members.get("xgboost")
            if xgb is None:
                cls = "no_xgboost_ta_only"
            elif "error" in xgb:
                cls = "xgboost_unreadable"
            else:
                usable = {f: r for f, r in members.items() if "error" not in r}
                unsup = [f for f, r in usable.items() if not r["oos_suppressed"]]
                if unsup:
                    cls = f"unsuppressed_contributors_{len(unsup)}"
                else:
                    # Every member returns 0.5, `0.5 > 0.5` is False, so the agreement test reads
                    # unanimous_bear and nudges -0.05 when every member's auc/cv_auc > 0.57.
                    aucs = [quality(r) for r in usable.values()]
                    nudge = len(usable) >= 2 and min(a if a is not None else 0.0 for a in aucs) > NUDGE_MIN_AUC
                    if len(usable) == 1:
                        cls = "all_suppressed_single_neutral"
                    else:
                        cls = "all_suppressed_bear_nudge_0.45" if nudge else "all_suppressed_neutral_0.5"
            # The quality signal-engine reads. Single surviving member: that bundle's own metrics
            # (auc, else cv_auc_mean; absent -> signal-engine treats as unavailable). Several:
            # mean over EVERY loaded member with suppressed ones zeroed, restored to their real
            # values if that sums to 0; a missing AUC counts as 0.55 (_model_auc_3).
            reported_quality = None
            if xgb is not None and "error" not in xgb:
                usable = [r for r in members.values() if "error" not in r]
                unsup = [r for r in usable if not r["oos_suppressed"]]
                available = unsup or usable
                if len(available) == 1:
                    reported_quality = quality(available[0])
                else:
                    real = [quality(r) if quality(r) is not None else 0.55 for r in usable]
                    vals = [0.0 if r["oos_suppressed"] else v for r, v in zip(usable, real)]
                    if sum(vals) <= 0:
                        vals = real
                    reported_quality = round(sum(vals) / len(vals), 4)
            cell = {"symbol": sym, "style": style, "class": cls,
                    "members": {f: {"artifact": r["artifact"], "sha256": r.get("sha256"),
                                    "oos_suppressed": r.get("oos_suppressed"),
                                    "legacy_swing_name": r["legacy_swing_name"]} for f, r in members.items()},
                    "reported_quality_from_stored_metrics": reported_quality,
                    "every_member_legacy_protocol": True}
            cells.append(cell)
            cell_counts.setdefault(style, {}).setdefault(cls, 0)
            cell_counts[style][cls] += 1

    arts = [a for a in artifacts.values() if "error" not in a]
    holdout = [a for a in arts if a.get("threshold_evaluation_mode") == "holdout"]
    unsup = [a for a in arts if not a["oos_suppressed"]]
    summary = {
        "capture_started_at": started, "capture_completed_at": completed,
        "image_id": os.environ.get("IMAGE_ID"), "prod_trainer_sha256": prod_sha,
        "f04_branch_trainer_sha256": f04_sha, "f04_branch_commit": os.environ.get("F04_COMMIT"),
        "suppression_rule_version": SUPPRESSION_RULE_VERSION,
        "artifacts_listed": len([k for k in before if k.endswith(".joblib")]),
        "artifacts_read": len(arts), "unreadable": len(artifacts) - len(arts),
        "non_joblib_files": sorted(k for k in before if not k.endswith(".joblib")),
        "listing_changed_during_capture": changed, "added": added, "removed": removed,
        "by_family": {f: sum(1 for a in arts if a["family"] == f) for f in FAMILIES},
        "suppressed": sum(a["oos_suppressed"] for a in arts),
        "unsuppressed": len(unsup),
        "suppression_reason_if_rule_reapplied": {r: sum(1 for a in arts if a["rule_reason"] == r)
                                                 for r in sorted({str(a["rule_reason"]) for a in arts})},
        "evaluation_valid": {str(v): sum(1 for a in arts if a["evaluation_valid"] is v) for v in (True, False, None)},
        "threshold_mode": {str(v): sum(1 for a in arts if a.get("threshold_evaluation_mode") == v)
                           for v in sorted({str(a.get("threshold_evaluation_mode")) for a in arts})},
        "holdout": len(holdout),
        "f04_holdout_below_report_floor": sum(1 for a in holdout if a["f04_report_rows_feasible"] is False),
        "f04_holdout_unknown": sum(1 for a in holdout if a["f04_report_rows_feasible"] is None),
        "f04_unsuppressed_holdout_below_floor": sum(1 for a in holdout if not a["oos_suppressed"] and a["f04_report_rows_feasible"] is False),
        "m14_floor_n_test": MIN_TEST_ROWS_FOR_EVIDENCE, "report_floor_rows": REPORT_FLOOR,
        "m14_sufficiency": {s: sum(1 for a in arts if a["m14_sufficiency"] == s) for s in ("sufficient", "insufficient", "unknown")},
        "protocol_v2_qualified": 0,
        "active_symbols": len(active),
        "routing_cells": len(cells),
        "routing_classes_by_style": cell_counts,
        "m14_inventory": inv.to_dict(),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (out / "artifacts.json").write_text(json.dumps(list(artifacts.values()), default=str))
    (out / "routing_cells.json").write_text(json.dumps(cells, default=str))
    print(json.dumps({k: summary[k] for k in ("capture_started_at", "capture_completed_at", "artifacts_read",
                                               "unreadable", "listing_changed_during_capture")}, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:4])
