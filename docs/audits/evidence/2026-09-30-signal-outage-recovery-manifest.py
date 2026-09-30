"""READ-ONLY recovery manifest for the AI-signal email outage (EA-01). SELECTs only.

Dates are converted to AMERICA/NEW_YORK before grouping. `signals.ts` is stored naive UTC, so a
UTC `date()` produces a phantom extra day for every evening run — measured here: 176 rows dated
2026-09-30 that are really 2026-09-29 evening ET. Grouping in UTC also splits a trading day's
signal across two buckets, which manufactures transitions that never happened.
See docs/incidents/utc-vs-et-date-boundary.md.
"""
import sys, json
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/src")
from shared.db.session import SessionLocal
from sqlalchemy import text

s = SessionLocal()
q = lambda sql, **kw: s.execute(text(sql), kw).all()
ET = "(sg.ts AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York')::date"
out = {}

# One signal per (symbol, horizon, ET trading date) — the LAST of that day, which is the state
# the alert job would have compared against on the following run.
hist = q(f"""
    SELECT st.symbol, sg.horizon, {ET} AS d, sg.signal, sg.ts
    FROM signals sg JOIN stocks st ON st.id = sg.stock_id
    WHERE sg.ts >= :a
    ORDER BY st.symbol, sg.horizon, d, sg.ts
""", a="2026-09-18")

daily = {}
for sym, hz, d, sig, ts in hist:
    daily[(sym, hz, str(d))] = sig          # later ts overwrites: last of the day wins

days = sorted({k[2] for k in daily})
out["et_days"] = days
out["rows_per_et_day"] = {d: sum(1 for k in daily if k[2] == d) for d in days}

series = {}
for (sym, hz, d), sig in daily.items():
    series.setdefault((sym, hz), []).append((d, sig))
for v in series.values():
    v.sort()

# Trading days only — a weekend row is a scheduler artifact, not a market event.
import datetime as _dt
def _is_trading(d):
    return _dt.date.fromisoformat(d).weekday() < 5
out["weekend_days_present"] = [d for d in days if not _is_trading(d)]

subs = q("""
    SELECT sa.id, sa.user_id, sa.email, sa.symbol, sa.horizon, sa.last_signal, sa.last_sent_at,
           sa.alert_mode, sa.require_consensus, u.is_active
    FROM signal_alerts sa LEFT JOIN users u ON u.id = sa.user_id
    ORDER BY sa.symbol, sa.horizon
""")

# Outage window: last successful send day before the gap, through the fix. Derived below.
OUTAGE = {"2026-09-25", "2026-09-28"}          # trading days with zero sends
out["outage_trading_days"] = sorted(OUTAGE)

rows = []
for (sid, uid, email, sym, hz, last_sig, last_sent, mode, consensus, active) in subs:
    seq = [(d, g) for d, g in series.get((sym, hz), []) if _is_trading(d)]
    missed = []
    for i in range(1, len(seq)):
        pd, ps = seq[i - 1]
        cd, cs = seq[i]
        if cd in OUTAGE and ps != cs:
            missed.append({"date": cd, "from": ps, "to": cs})
    cur = seq[-1] if seq else None
    rows.append({
        "sub_id": sid, "user_id": uid, "email": email, "symbol": sym, "horizon": hz,
        "last_signal": last_sig, "last_sent_at": str(last_sent) if last_sent else None,
        "alert_mode": mode, "require_consensus": consensus, "user_active": active,
        "missed": missed,
        "current_signal": cur[1] if cur else None, "current_date": cur[0] if cur else None,
        "sent_since_fix": bool(last_sent and str(last_sent)[:10] >= "2026-09-29"),
    })

aff = [r for r in rows if r["missed"]]
out["totals"] = {
    "subscriptions_total": len(rows),
    "affected_subscriptions": len(aff),
    "missed_events": sum(len(r["missed"]) for r in aff),
    "affected_symbols": len({r["symbol"] for r in aff}),
    "affected_recipients": len({r["email"] for r in aff if r["email"]}),
}
kinds = {}
for r in aff:
    for m in r["missed"]:
        k = f'{m["from"]}->{m["to"]}'
        kinds[k] = kinds.get(k, 0) + 1
out["transition_kinds"] = dict(sorted(kinds.items(), key=lambda kv: -kv[1]))

# ACTIONABLE vs INFORMATIONAL. A missed BUY is the one a reader could have acted on; a
# HOLD<->WAIT flip is diagnostic noise that no alert would have been worth sending for.
actionable = [r for r in aff if any(m["to"] in ("BUY", "SELL") for m in r["missed"])]
out["actionable_subscriptions"] = len(actionable)
out["actionable_events"] = sum(1 for r in aff for m in r["missed"] if m["to"] in ("BUY", "SELL"))
out["buy_events"] = sum(1 for r in aff for m in r["missed"] if m["to"] == "BUY")
out["sell_events"] = sum(1 for r in aff for m in r["missed"] if m["to"] == "SELL")

# Does the missed state STILL hold? That is what separates a historical miss from something
# worth telling someone about today.
still = [r for r in actionable if r["current_signal"] == r["missed"][-1]["to"]]
out["actionable_still_current"] = len(still)
out["actionable_moved_on"] = len(actionable) - len(still)
out["already_notified_since_fix"] = sum(1 for r in actionable if r["sent_since_fix"])

out["actionable_detail"] = [
    {"symbol": r["symbol"], "horizon": r["horizon"], "email": r["email"],
     "missed": r["missed"], "current": r["current_signal"], "as_of": r["current_date"],
     "last_signal_field": r["last_signal"], "last_sent_at": r["last_sent_at"],
     "still_current": r["current_signal"] == r["missed"][-1]["to"],
     "sent_since_fix": r["sent_since_fix"], "user_active": r["user_active"]}
    for r in actionable
]
print(json.dumps(out, indent=2, default=str))
