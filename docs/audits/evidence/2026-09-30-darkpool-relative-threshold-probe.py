"""READ-ONLY: how often does the RELATIVE dark-pool bar reject a print that already cleared the
ABSOLUTE bar? SELECTs only. Reproduces the production constants and baseline definition exactly."""
import sys, json
sys.path.insert(0,"/app"); sys.path.insert(0,"/app/src")
from shared.db.session import SessionLocal
from sqlalchemy import text
s=SessionLocal(); q=lambda sql,**kw: s.execute(text(sql),kw).all()
ABS=1_000_000; REL=5.0; DAYS=14; MIN_PRINTS=20
o={"constants":{"abs_premium":ABS,"rel_multiple":REL,"baseline_days":DAYS,"min_prints":MIN_PRINTS}}

# Per-symbol baseline exactly as _dark_pool_premium_baseline computes it (trailing 14d median,
# only once a symbol has >= 20 prints in that window).
base=q("""
  SELECT symbol, count(*) n, percentile_cont(0.5) WITHIN GROUP (ORDER BY premium) med
  FROM dark_pool_prints
  WHERE executed_at >= now() - interval '14 days' AND premium IS NOT NULL
  GROUP BY 1""")
baseline={r[0]: (float(r[2]) if r[1] and r[1]>=MIN_PRINTS and r[2] is not None else None) for r in base}
o["symbols_in_window"]=len(baseline)
o["symbols_with_baseline"]=sum(1 for v in baseline.values() if v)
o["symbols_without_baseline"]=sum(1 for v in baseline.values() if not v)

# Every print in the window that clears the ABSOLUTE bar.
rows=q("""SELECT symbol, premium FROM dark_pool_prints
          WHERE executed_at >= now() - interval '14 days'
            AND premium IS NOT NULL AND premium >= :a""", a=ABS)
o["prints_clearing_absolute"]=len(rows)

blocked=passed=no_baseline=0
per={}
for sym,prem in rows:
    b=baseline.get(sym)
    if not b:
        no_baseline+=1; continue
    d=per.setdefault(sym,{"abs_ok":0,"rel_blocked":0,"baseline":round(b),"rel_floor":round(b*REL)})
    d["abs_ok"]+=1
    if float(prem) < b*REL:
        blocked+=1; d["rel_blocked"]+=1
    else:
        passed+=1
o["of_those"]={"also_cleared_relative":passed,"REJECTED_BY_RELATIVE":blocked,
               "no_baseline_yet_so_absolute_only":no_baseline}
den=passed+blocked
o["relative_rejection_rate_pct"]=round(100*blocked/den,1) if den else None
o["top_symbols"]=sorted(({"symbol":k,**v} for k,v in per.items()),
                        key=lambda d:-d["abs_ok"])[:12]
# Baseline magnitude vs the absolute floor: if 5x median is below $1M the relative bar can never bind.
o["symbols_where_rel_floor_below_abs"]=sum(1 for v in baseline.values() if v and v*REL < ABS)
o["symbols_where_rel_floor_above_abs"]=sum(1 for v in baseline.values() if v and v*REL >= ABS)
print(json.dumps(o,indent=1,default=str))
