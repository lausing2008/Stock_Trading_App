SET default_transaction_read_only = on;
SET statement_timeout = '120s';
BEGIN READ ONLY;
\echo === ledger totals
SELECT count(*) AS events, count(DISTINCT symbol) AS symbols,
       count(*) FILTER (WHERE canceled) AS canceled, count(*) FILTER (WHERE ambiguous) AS ambiguous,
       count(*) FILTER (WHERE premium IS NULL) AS no_premium,
       min(observed_at) AS first_observed, max(observed_at) AS last_observed,
       min(executed_at) AS first_executed, max(executed_at) AS last_executed
FROM dark_pool_events_v2;
\echo === versions per identity
SELECT count(*) AS identities, count(*) FILTER (WHERE n > 1) AS with_multiple_versions, max(n) AS max_versions, sum(n) AS version_rows
FROM (SELECT symbol, tracking_id, count(*) AS n FROM dark_pool_event_versions_v2 GROUP BY symbol, tracking_id) v;
\echo === baseline readiness (uncanceled, unambiguous, premium, executed in last 14 days; needs >= 20)
WITH b AS (SELECT symbol, count(*) AS n FROM dark_pool_events_v2
           WHERE NOT canceled AND NOT ambiguous AND premium IS NOT NULL AND executed_at >= now() - interval '14 days'
           GROUP BY symbol)
SELECT count(*) FILTER (WHERE n >= 20) AS symbols_ready, count(*) FILTER (WHERE n < 20) AS symbols_short,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY n) AS median_prints, min(n), max(n) FROM b;
\echo === symbols in ledger with zero baseline-eligible prints
SELECT count(*) FROM (SELECT DISTINCT symbol FROM dark_pool_events_v2) s
WHERE NOT EXISTS (SELECT 1 FROM dark_pool_events_v2 e WHERE e.symbol = s.symbol AND NOT canceled AND NOT ambiguous
                  AND premium IS NOT NULL AND executed_at >= now() - interval '14 days');
\echo === daily intake by observation day (ET)
SELECT (observed_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York')::date AS day, count(*) AS events,
       count(*) FILTER (WHERE canceled) AS canceled, count(*) FILTER (WHERE ambiguous) AS ambiguous
FROM dark_pool_events_v2 GROUP BY 1 ORDER BY 1;
\echo === legacy table since the ledger began
SELECT count(*) AS legacy_rows, count(DISTINCT symbol) AS symbols
FROM dark_pool_prints WHERE executed_at >= (SELECT min(executed_at) FROM dark_pool_events_v2);
ROLLBACK;
