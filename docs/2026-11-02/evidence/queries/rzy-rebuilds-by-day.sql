begin transaction read only;
select date(rebuilt_at - interval '5 hours') us_day, count(*) rebuilds,
  count(*) filter (where reason='stored_completed_bar_changed') bar_changed,
  count(*) filter (where symbol not like '%.HK') us_rebuilds
from little_rzy_rebuilds where rebuilt_at >= '2026-10-26 04:00' and rebuilt_at < '2026-11-03 05:00'
group by 1 order by 1;
rollback;
