SET default_transaction_read_only=on;
SET statement_timeout='12s';
SELECT CURRENT_TIMESTAMP AS audit_time;
SELECT direction,count(*) FILTER (WHERE expiry<fired_date) AS expired_before_alert,count(*) FILTER (WHERE expiry=fired_date) AS expires_alert_day,count(*) FILTER (WHERE expiry<entry_date) AS expired_before_entry,count(DISTINCT fired_date) AS alert_dates,count(DISTINCT symbol) AS symbols FROM options_flow_alert_outcomes GROUP BY direction;
SELECT stage,strategy,count(*),min(entry_date),max(entry_date),count(pnl),round(sum(pnl)::numeric,2) AS stored_pnl FROM options_income_positions GROUP BY stage,strategy;
SELECT relname,n_live_tup AS estimated_rows FROM pg_stat_user_tables WHERE relname IN ('option_chain_history','dark_pool_prints');
SELECT symbol,max(as_of) AS latest,count(*) AS rows_latest_7d,count(*) FILTER (WHERE nbbo_bid>nbbo_ask AND nbbo_ask>0) AS crossed FROM option_chain_history WHERE as_of>=CURRENT_DATE-7 GROUP BY symbol ORDER BY latest,symbol;
SELECT date_trunc('week',fired_date)::date AS week, count(*) AS alerts,count(DISTINCT symbol) AS symbols FROM dark_pool_alert_outcomes GROUP BY 1 ORDER BY 1;
