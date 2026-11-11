"""7-day hold only: finder minus preset by month, against the stock move, plus the structural
differences (days to expiry, delta proxy via moneyness, spread). Read-only."""
import json, sys, statistics
from collections import defaultdict
pair = defaultdict(dict)
for l in open(sys.argv[1]):
    t = json.loads(l)
    if t["exit_type"] == "holding" and t["strategy"] == "long_call" and t["horizon"] == "SHORT" and t["roc"] is not None \
            and t["arm"] in ("finder", "preset"):
        pair[(t["symbol"], t["entry"])][t["arm"]] = t
m = defaultdict(list); dte = defaultdict(list); mny = defaultdict(list); spr = defaultdict(list)
for k, a in pair.items():
    if len(a) < 2: continue
    f, p = a["finder"], a["preset"]
    m[k[1][:7]].append((f["roc"] - p["roc"], f.get("stock_return")))
    for arm, t in a.items():
        dte[arm].append((json.loads('"%s"' % t["expiry"]) and (int(t["expiry"][:4]) * 372 + int(t["expiry"][5:7]) * 31 + int(t["expiry"][8:10])) - (int(t["entry"][:4]) * 372 + int(t["entry"][5:7]) * 31 + int(t["entry"][8:10]))))
        mny[arm].append(t["strike"] / t["stock_ref"] - 1)
        if t.get("spread_pct") is not None: spr[arm].append(t["spread_pct"])
rows = [(mo, statistics.fmean(d for d, _ in v), statistics.fmean(s for _, s in v if s is not None), len(v)) for mo, v in sorted(m.items()) if len(v) >= 20]
up = [r for r in rows if r[2] > 0.01]
out = {"months": len(rows), "finder_ahead_months": sum(r[1] > 0 for r in rows),
       "ahead_in_rising_months": f"{sum(r[1] > 0 for r in up)}/{len(up)}",
       "mean_diff_rising_months_pct": round(statistics.fmean(r[1] for r in up) * 100, 2),
       "corr_with_stock_move": round(statistics.correlation([r[2] for r in rows], [r[1] for r in rows]), 2),
       "median_days_to_expiry": {a: statistics.median(v) for a, v in dte.items()},
       "median_strike_vs_spot_pct": {a: round(statistics.median(v) * 100, 1) for a, v in mny.items()},
       "median_spread_pct": {a: round(statistics.median(v), 1) for a, v in spr.items()}}
print(json.dumps(out, indent=1))
json.dump(out, open(sys.argv[2], "w"), indent=1)
