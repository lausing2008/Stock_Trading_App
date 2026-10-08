SET default_transaction_read_only=on;
SET statement_timeout='12s';
SELECT fired_date,count(*) AS candidates,count(*) FILTER(WHERE expiry<fired_date) AS expired_before_alert,count(*) FILTER(WHERE expiry=fired_date) AS same_day FROM options_flow_alert_outcomes WHERE fired_date>='2026-10-01' GROUP BY fired_date ORDER BY fired_date;
SELECT max(as_of),count(*) AS rows,count(*) FILTER (WHERE gamma_flip IS NULL) AS missing_flip,count(*) FILTER (WHERE call_wall IS NULL AND put_wall IS NULL AND gamma_flip IS NULL) AS all_main_levels_missing FROM gex_snapshots;
WITH s AS(SELECT symbol,max(as_of) AS last_day FROM option_chain_history WHERE as_of>=CURRENT_DATE-7 GROUP BY symbol) SELECT last_day,count(*) AS symbols FROM s GROUP BY last_day ORDER BY last_day;
WITH x AS(SELECT symbol,as_of,count(*) AS n FROM option_chain_history WHERE as_of>=CURRENT_DATE-7 GROUP BY symbol,as_of) SELECT count(*) AS symbol_sessions,count(*) FILTER(WHERE n=500) AS exactly_500,min(n),max(n) FROM x;
SELECT stage,count(*),count(*) FILTER(WHERE expiry<CURRENT_DATE) AS past_expiry,min(expiry),max(expiry) FROM options_income_positions GROUP BY stage;
