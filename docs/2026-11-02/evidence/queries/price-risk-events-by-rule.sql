begin transaction read only;
\echo per US session (ET date of detection), events by rule
select date(detected_at - interval '5 hours') d, rule, count(*) n, count(distinct subject) subj, round(avg(change_pct)::numeric,2) avg_chg
from risk_events where detected_at >= '2026-10-23' group by 1,2 order by 1,2;
\echo Nov 2 top subjects
select subject, rule, step, change_pct, to_char(detected_at,'HH24:MI') utc from risk_events where detected_at >= '2026-11-02 14:00' and detected_at < '2026-11-03 06:00' order by subject, detected_at;
\echo SPY/QQQ day move Nov 2 vs Oct 30 close
select s.symbol, p.ts::date, p.close from prices p join stocks s on s.id=p.stock_id where s.symbol in ('SPY','QQQ','SMH') and p.timeframe='D1' and p.ts >= '2026-10-29' order by 1,2;
rollback;
