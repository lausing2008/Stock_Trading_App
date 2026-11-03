begin transaction read only;
with b as (
  select date(p.ts - interval '5 hours') d, extract(epoch from p.first_written_at - (p.ts + interval '5 minutes'))/60.0 late_min
  from bar_provenance p join stocks s on s.id=p.stock_id
  where s.market='US' and p.timeframe::text in ('5m','M5') and p.ts >= '2026-10-27' and p.ts < '2026-11-03'
    and (p.ts::time >= '13:30' and p.ts::time < '21:00') and not coalesce(p.off_grid,false)
)
select d, count(*) bars,
  round(percentile_cont(0.5) within group (order by late_min)::numeric,2) med_min,
  round(percentile_cont(0.9) within group (order by late_min)::numeric,2) p90_min,
  round(100.0*avg((late_min>3)::int),1) pct_over3,
  round(100.0*avg((late_min>8)::int),1) pct_over8,
  round(100.0*avg((late_min>15)::int),1) pct_over15
from b group by d order by d;
rollback;
