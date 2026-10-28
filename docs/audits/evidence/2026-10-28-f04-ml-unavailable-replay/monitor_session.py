"""Post-release monitor for the ML-unavailable fix: one market session, read-only.

Runs inside the signal-engine container (its DB and Redis access, nothing written):
  docker exec -i -e MON_START=2026-10-28T13:30:00 -e MON_END=2026-10-28T20:00:00 -e MON_MARKET=US \
      stockai-signal-engine-1 python - < monitor_session.py > out.json

Window bounds are naive UTC (the database stores naive UTC). MON_MARKET filters symbols by suffix
(HK = ".HK"). Compare a post-release session with the same session before the release.

Reports:
  - the session DATE's signal rows (one per stock/horizon/day; every refresh overwrites `ts`, so a
    time window would drift), by horizon: ML unavailable, suppressed rows still carrying ML
    weight (must be 0 after the release), ML probability exactly 0.45 (the removed nudge), BUY/SELL;
  - signal transitions as F03 occurrences (BUY/SELL runs) opened in the window, by direction, and
    their close reasons -- the signals table keeps one row per stock/horizon/day, so intraday
    transitions are only visible there;
  - conviction tiers now in Redis (conv_gate:*, one-day TTL: a snapshot at run time), and how many
    record "no eligible model";
  - alert volume: notification_outbox rows created in the window, by alert type and state;
  - exits: paper trades closed in the window, by exit reason.
"""
import json
import os
import sys
from collections import Counter

sys.path[:0] = ["/app", "/app/shared"]
from sqlalchemy import text  # noqa: E402

from common.redis_client import get_redis  # noqa: E402
from db import SessionLocal  # noqa: E402

START, END, MARKET = os.environ["MON_START"], os.environ["MON_END"], os.environ.get("MON_MARKET", "US")
HK = MARKET.upper() == "HK"


def in_market(symbol: str) -> bool:
    return symbol.upper().endswith(".HK") == HK


out = {"window": [START, END], "market": MARKET}
with SessionLocal() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    rows = s.execute(text("""
        SELECT st.symbol, sg.horizon::text, sg.signal::text, sg.reasons
        FROM signals sg JOIN stocks st ON st.id = sg.stock_id
        WHERE sg.source = 'signal-engine' AND sg.ts >= CAST(:a AS date)
          AND sg.ts < CAST(:a AS date) + 1"""), {"a": START}).all()
    by = {}
    for sym, hor, sig, r in rows:
        if not in_market(sym):
            continue
        r = r or {}
        d = by.setdefault(hor, Counter())
        d["signals"] += 1
        d["ml_unavailable"] += r.get("ml_unavailable_reason") == "no_eligible_model"
        d["suppressed_with_ml_weight"] += bool(r.get("ml_oos_suppressed")) and (r.get("ml_weight") or 0) > 0
        d["ml_probability_0.45"] += r.get("ml_probability") == 0.45
        d["ml_weighted"] += (r.get("ml_weight") or 0) > 0
        d[sig] += 1
    out["signals_by_horizon"] = {h: dict(c) for h, c in sorted(by.items())}

    occ = s.execute(text("""
        SELECT st.symbol, o.horizon, o.direction, coalesce(o.close_reason, 'open')
        FROM signal_occurrences o JOIN stocks st ON st.id = o.stock_id
        WHERE o.opened_at >= :a AND o.opened_at < :b"""), {"a": START, "b": END}).all()
    oc = Counter((h, d) for sym, h, d, _ in occ if in_market(sym))
    cr = Counter(c for sym, _, _, c in occ if in_market(sym))
    out["occurrences_opened"] = {f"{h}:{d}": n for (h, d), n in sorted(oc.items())}
    out["occurrence_close_reasons"] = dict(cr)

    alerts = s.execute(text("""
        SELECT coalesce(alert_type, 'none'), state, count(*) FROM notification_outbox
        WHERE created_at >= :a AND created_at < :b GROUP BY 1, 2 ORDER BY 1, 2"""), {"a": START, "b": END}).all()
    out["alerts_created"] = {f"{t}:{st}": n for t, st, n in alerts}

    exits = s.execute(text("""
        SELECT symbol, coalesce(exit_reason, 'none') FROM paper_trades
        WHERE exit_time >= :a AND exit_time < :b"""), {"a": START, "b": END}).all()
    out["paper_exits"] = dict(Counter(r for sym, r in exits if in_market(sym)))
    s.rollback()

r = get_redis()
tiers, no_eligible = Counter(), 0
for k in r.scan_iter("conv_gate:*", count=500):
    k = k.decode() if isinstance(k, bytes) else k
    sym = k.split(":")[1]
    if not in_market(sym):
        continue
    raw = r.get(k)
    try:
        v = json.loads(raw)
    except Exception:
        continue
    tiers[str(v.get("conviction_tier"))] += 1
    no_eligible += any("no eligible model" in f for f in (v.get("failed") or []))
out["conviction_tiers_snapshot"] = dict(tiers)
out["conviction_no_eligible_model"] = no_eligible
print(json.dumps(out, indent=1, default=str))
