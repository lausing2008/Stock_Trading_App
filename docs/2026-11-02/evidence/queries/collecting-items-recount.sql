begin transaction read only;
\echo M08 prebreakout: fires, names, top-name share (all time; and since Oct 13)
select sum(c) fires, count(*) names, round(100.0*max(c)/sum(c),1) top_pct, (array_agg(symbol order by c desc))[1:3] top3
from (select symbol, count(*) c from prebreakout_alert_outcomes group by 1) x;
select count(*) fires_since_1013, count(distinct symbol) names_since_1013, string_agg(distinct symbol, ',') from prebreakout_alert_outcomes where fired_date > '2026-10-13';
\echo squeeze family by type (all time / since Oct 13)
select alert_type, count(*) total, count(*) filter (where fired_date > '2026-10-13') since_1013, count(distinct symbol) names from squeeze_alert_outcomes group by 1 order by 2 desc;
\echo M09 GEX control arm: uncorroborated, resolved at 5d
select gex_corroborated, count(*) n, count(return_5d) resolved_5d from squeeze_alert_outcomes where alert_type like 'gamma%' group by 1;
rollback;
