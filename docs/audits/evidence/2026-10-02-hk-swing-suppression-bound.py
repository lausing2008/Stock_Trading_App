"""Bound the suppression hypothesis from the cap's own arithmetic, READ-ONLY.

The compression cap restores an over-compressed signal to `0.5 + orig_dist * max_ratio`.
So a signal whose PRE-compression score was at or above the BUY bar cannot finish below that
floor. Turning it round: a FINAL score below the floor implied by the bar is proof the signal
was never at the bar to begin with — no stored pre-score needed.

    bar = 0.74 (SWING, choppy)   orig_dist at the bar = 0.24
    cap floor for such a signal  = 0.5 + 0.24 * 0.55 = 0.632

Any final score below 0.632 therefore CANNOT be a suppressed bar-clearing signal.
"""
import json, sys
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/shared")
from sqlalchemy import text
from db import SessionLocal
try:
    from src.generators.signals import _STYLE_PROFILES
except Exception:
    from generators.signals import _STYLE_PROFILES

SWING = _STYLE_PROFILES["SWING"]
BAR = SWING["buy_threshold"]["choppy"]
RATIO = SWING["max_compress_ratio"]
FLOOR = 0.5 + (BAR - 0.5) * RATIO

with SessionLocal() as s:
    s.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    rows = s.execute(text("""
        SELECT st.symbol, sg.bullish_probability AS bp, sg.reasons
        FROM signals sg JOIN stocks st ON st.id = sg.stock_id
        WHERE st.market = 'HK' AND sg.horizon = 'SWING'
          AND sg.ts > NOW() - INTERVAL '7 days' AND sg.bullish_probability IS NOT NULL
    """)).all()

n = len(rows)
could = [r for r in rows if r.bp >= FLOOR]
cap_fired = sum(1 for r in rows if (r.reasons or {}).get("compression_cap_applied"))
print(json.dumps({
    "bar_choppy": BAR, "max_compress_ratio": RATIO,
    "cap_floor_for_a_bar_clearing_signal": round(FLOOR, 4),
    "n_signals": n,
    "provably_never_at_the_bar": n - len(could),
    "could_conceivably_have_been_suppressed": len(could),
    "candidates": sorted([{"symbol": r.symbol, "final": round(r.bp, 4),
                           "cap_applied": (r.reasons or {}).get("compression_cap_applied")}
                          for r in could], key=lambda d: -d["final"]),
    "cap_fired_on": cap_fired,
}, indent=2))
