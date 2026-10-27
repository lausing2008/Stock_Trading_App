"""F04 impact probe — READ ONLY. Loads every model bundle under /data/models with joblib, reads
its stored metrics, and re-applies the purged threshold/report bounds to its stored n_test.
Writes nothing. The row-count gate is a necessary condition only: class support of the new
reporting rows cannot be known without retraining."""
import collections
import glob
import json
import os
import joblib

STYLE_H = {"SHORT": 5, "SWING": 10, "LONG": 20, "GROWTH": 15}
MIN_REPORT = 10


def bounds(n, h):
    t = max(1, (n - h) // 2)
    return t, min(n, t + h)


rows, errors = [], 0
for path in sorted(glob.glob("/data/models/**/*.joblib", recursive=True)):
    try:
        b = joblib.load(path)
    except Exception:
        errors += 1
        continue
    m = b.get("metrics") or {}
    style = (b.get("style") or "SWING").upper()
    h = m.get("embargo_target_bars") or STYLE_H.get(style)
    n_test = m.get("n_test")
    slice_rows = m.get("slice_rows") or {}
    emb = m.get("embargo_bars") or {}
    split_rows = (sum(slice_rows.values()) + sum(v or 0 for v in emb.values())) if slice_rows else None
    new_holdout = None
    if n_test is not None and h:
        _, rs = bounds(int(n_test), int(h))
        new_holdout = (int(n_test) - rs) >= MIN_REPORT
    rows.append({
        "model": os.path.basename(os.path.dirname(path)), "style": style, "h": h,
        "n_test": n_test, "split_rows": split_rows,
        "mode": m.get("threshold_evaluation_mode"), "valid": m.get("evaluation_valid"),
        "suppressed": bool(b.get("oos_suppressed")), "new_holdout_rowgate": new_holdout,
        "trained_at": (b.get("trained_at") or "")[:10],
    })

def bucket(r):
    s = r["split_rows"]
    if s is None:
        return "unknown"
    return "<400" if s < 400 else "400-599" if s < 600 else "600-799" if s < 800 else "800+"

summary = collections.defaultdict(lambda: collections.Counter())
for r in rows:
    k = f'{r["style"]}|{bucket(r)}'
    c = summary[k]
    c["total"] += 1
    c["holdout_now"] += r["mode"] == "holdout"
    c["valid_now"] += bool(r["valid"])
    c["unsuppressed_now"] += not r["suppressed"]
    flips = r["mode"] == "holdout" and r["new_holdout_rowgate"] is False
    c["holdout_loses_rowgate"] += flips
    c["unsuppressed_and_flips"] += flips and not r["suppressed"]
    c["valid_and_flips"] += flips and bool(r["valid"])
    c["mode_unknown"] += r["mode"] is None

tot = collections.Counter()
for c in summary.values():
    tot.update(c)
print(json.dumps({"artifacts": len(rows), "read_errors": errors,
                  "totals": dict(tot),
                  "by_style_and_split_rows": {k: dict(v) for k, v in sorted(summary.items())},
                  "unsuppressed_that_flip": [r for r in rows if r["mode"] == "holdout"
                                             and r["new_holdout_rowgate"] is False
                                             and not r["suppressed"]]}, indent=1, default=str))
