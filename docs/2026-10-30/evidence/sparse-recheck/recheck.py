"""Read-only: 5m grid coverage now for the four symbols classed ingestion_failure on Oct 21/22."""
import sys; sys.path[:0] = ["/app", "/app/shared"]
from sqlalchemy import text
from db import SessionLocal
Q = """
with days(d) as (values (date '2026-10-21'), (date '2026-10-22')),
grid as (select d as day, generate_series(d + time '13:30', d + time '19:55', interval '5 minutes') as ts from days)
select s.symbol, g.day, count(p.ts) present, count(*) - count(p.ts) missing,
       string_agg(case when p.ts is null then to_char(g.ts,'HH24:MI') end, ',' order by g.ts) missing_utc
from stocks s cross join grid g
left join prices p on p.stock_id=s.id and p.timeframe='M5' and p.ts=g.ts
where s.symbol in ('XSD','BRK-A','AMADY','EE')
group by 1,2 order by 1,2"""
with SessionLocal() as s:
    s.execute(text("SET TRANSACTION READ ONLY"))
    for r in s.execute(text(Q)).all():
        print(f"{r.symbol:6} {r.day} present={r.present:2} missing={r.missing:2} {r.missing_utc or ''}")
