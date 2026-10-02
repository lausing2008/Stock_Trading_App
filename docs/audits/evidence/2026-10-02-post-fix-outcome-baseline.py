"""READ-ONLY frozen baseline at the SR-01..SR-08 deploy boundary.

Post-fix outcome collection needs a line in the sand: counts as they stood when the fixes went
live, so a later read can compare like with like instead of pooling across the change. Records
WHAT WAS TRUE, never a projection.
"""
import json, sys
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/shared")
from sqlalchemy import text
from db import SessionLocal

OUT = {"boundary_commit": "f9b6edb3", "note": "counts as of the SR-01..SR-08 deploy"}
with SessionLocal() as s:
    s.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    OUT["now_utc"] = str(s.execute(text("SELECT NOW()")).scalar())

    OUT["signals_30d_by_market_horizon_signal"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT st.market, sg.horizon, sg.signal, COUNT(*) AS n
        FROM signals sg JOIN stocks st ON st.id = sg.stock_id
        WHERE sg.ts > NOW() - INTERVAL '30 days'
        GROUP BY st.market, sg.horizon, sg.signal ORDER BY 1,2,3
    """))]

    # Resolved signal outcomes to date — the denominator any later "did it improve" rests on.
    OUT["signal_outcomes"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT signal_direction, horizon,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE pct_return IS NOT NULL) AS resolved,
               ROUND(AVG(pct_return)::numeric, 4) AS avg_pct_return
        FROM signal_outcomes
        GROUP BY signal_direction, horizon ORDER BY 1,2
    """))]

    OUT["paper_trades"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT portfolio_id, stage, COUNT(*) AS n,
               ROUND(AVG(pnl)::numeric, 2) AS avg_pnl
        FROM paper_trades GROUP BY portfolio_id, stage ORDER BY 1,2
    """))]

    # The alert families SR-03/SR-04 touched: what the outcome table holds at the boundary.
    OUT["options_flow_outcomes"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT direction, COUNT(*) AS n,
               COUNT(*) FILTER (WHERE is_correct_10d IS NOT NULL) AS resolved_10d,
               ROUND(AVG(return_10d)::numeric, 4) AS avg_return_10d
        FROM options_flow_alert_outcomes GROUP BY direction ORDER BY 1
    """))]

print(json.dumps(OUT, indent=2, default=str))
