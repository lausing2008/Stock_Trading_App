"""Monthly: finder minus {aggressive, atm, preset} (holding exit, paired) against the stock's own
move over the same trades. Read-only analysis of trades.jsonl."""
import json, sys, statistics
from collections import defaultdict
pair = defaultdict(dict); stock = {}
for l in open(sys.argv[1]):
    t = json.loads(l)
    if t["exit_type"] != "holding" or t["strategy"] != "long_call" or t["horizon"] == "GROWTH" or t["roc"] is None:
        continue
    k = (t["symbol"], t["entry"], t["horizon"])
    pair[k][t["arm"]] = t["roc"]
    if t["arm"] == "finder" and t.get("stock_return") is not None:
        stock[k] = t["stock_return"]
res = {}
for other in ("finder_aggressive", "finder_atm", "preset"):
    m = defaultdict(lambda: ([], []))
    for k, a in pair.items():
        if "finder" in a and other in a and k in stock:
            m[k[1][:7]][0].append(a["finder"] - a[other]); m[k[1][:7]][1].append(stock[k])
    rows = [(mo, statistics.fmean(d), statistics.fmean(s), len(d)) for mo, (d, s) in sorted(m.items()) if len(d) >= 20]
    xs, ys = [r[2] for r in rows], [r[1] for r in rows]
    corr = statistics.correlation(xs, ys)
    up = [r[1] for r in rows if r[2] > 0.01]; dn = [r[1] for r in rows if r[2] < 0]
    pos = sum(r[1] > 0 for r in rows)
    res[other] = {"months": len(rows), "finder_ahead_months": pos, "corr_with_stock_move": round(corr, 2),
                  "mean_diff_when_stocks_rose_1pct_plus": round(statistics.fmean(up) * 100, 2) if up else None,
                  "mean_diff_when_stocks_fell": round(statistics.fmean(dn) * 100, 2) if dn else None,
                  "n_up_months": len(up), "n_down_months": len(dn)}
    print(other, res[other])
json.dump(res, open(sys.argv[2], "w"), indent=1)
