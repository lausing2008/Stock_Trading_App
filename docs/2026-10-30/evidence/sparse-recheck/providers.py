"""Read-only: for the four symbols classed ingestion_failure on Oct 22, which DB-missing 5m grid
intervals does each provider serve NOW? One request per symbol per provider; nothing is written."""
import sys; sys.path[:0] = ["/app", "/app/shared"]
from datetime import datetime, timezone
import yfinance as yf
from sqlalchemy import text
from db import SessionLocal
from src.services import unusual_whales as U

SYMS = ("XSD", "BRK-A", "AMADY", "EE")
DAY = "2026-10-22"
Q = """
with grid as (select generate_series(timestamp '2026-10-22 13:30', timestamp '2026-10-22 19:55', interval '5 minutes') ts)
select to_char(g.ts,'HH24:MI') from stocks s cross join grid g
left join prices p on p.stock_id=s.id and p.timeframe='M5' and p.ts=g.ts
where s.symbol=:sym and p.ts is null order by g.ts"""
with SessionLocal() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    missing = {sym: set(s.execute(text(Q), {"sym": sym}).scalars().all()) for sym in SYMS}
total_recoverable = 0
for sym in SYMS:
    yf_times, uw_times, notes = set(), set(), []
    try:
        df = yf.Ticker(sym).history(start="2026-10-22", end="2026-10-23", interval="5m", prepost=False)
        yf_times = {ts.tz_convert("UTC").strftime("%H:%M") for ts in df.index} if not df.empty else set()
    except Exception as e:
        notes.append(f"yf error {type(e).__name__}")
    try:
        rows = U._get(f"/api/stock/{sym}/ohlc/5m", params={"date": DAY, "limit": 200},
                      endpoint="/api/stock/{symbol}/ohlc/{candle}") or []
        for r in rows if isinstance(rows, list) else []:
            st = r.get("start_time") or r.get("date")
            if st and str(st).startswith(DAY) and r.get("market_time") in (None, "r"):
                uw_times.add(datetime.fromisoformat(str(st).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%H:%M"))
    except Exception as e:
        notes.append(f"uw error {type(e).__name__}")
    m = missing[sym]
    rec = sorted(m & (yf_times | uw_times))
    total_recoverable += len(rec)
    print(f"{sym:6} db_missing={len(m):2} yf_serves={len(yf_times):2} uw_serves={len(uw_times):2} "
          f"provider_has_but_db_missing={len(rec)} {','.join(rec)} {' '.join(notes)}")
print("total still missing that a provider serves:", total_recoverable)
