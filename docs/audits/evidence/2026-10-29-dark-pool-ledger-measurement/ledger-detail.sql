SET default_transaction_read_only = on;
SET statement_timeout = '120s';
BEGIN READ ONLY;
\echo === per executed day (ET), full days only: legacy vs ledger
WITH l AS (SELECT (executed_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York')::date d, count(*) n FROM dark_pool_prints
           WHERE executed_at >= '2026-10-26 04:00' AND executed_at < '2026-10-30 04:00' GROUP BY 1),
     v AS (SELECT (executed_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York')::date d, count(*) n FROM dark_pool_events_v2
           WHERE executed_at >= '2026-10-26 04:00' AND executed_at < '2026-10-30 04:00' GROUP BY 1)
SELECT l.d, l.n AS legacy, v.n AS ledger, l.n - v.n AS diff FROM l FULL JOIN v USING (d) ORDER BY 1;
\echo === which payload fields differ between versions of an ambiguous identity
WITH amb AS (SELECT symbol, tracking_id FROM dark_pool_events_v2 WHERE ambiguous),
 kv AS (SELECT v.symbol, v.tracking_id, k.key, k.value
        FROM dark_pool_event_versions_v2 v JOIN amb USING (symbol, tracking_id)
        CROSS JOIN LATERAL jsonb_each(v.payload::jsonb) k)
SELECT key, count(*) AS identities_where_it_differs
FROM (SELECT symbol, tracking_id, key FROM kv GROUP BY symbol, tracking_id, key HAVING count(DISTINCT value) > 1) d
GROUP BY key ORDER BY 2 DESC;
\echo === versions per ambiguous identity, and same observed_at?
SELECT count(*) AS ambiguous_ids, sum(CASE WHEN n_obs = 1 THEN 1 ELSE 0 END) AS all_versions_same_observed_at, max(nv) AS max_versions
FROM (SELECT v.symbol, v.tracking_id, count(*) nv, count(DISTINCT v.observed_at) n_obs
      FROM dark_pool_event_versions_v2 v JOIN dark_pool_events_v2 e USING (symbol, tracking_id)
      WHERE e.ambiguous GROUP BY 1,2) x;
\echo === multi-version identities that are NOT ambiguous (true later corrections)
SELECT count(*) FROM (SELECT v.symbol, v.tracking_id FROM dark_pool_event_versions_v2 v JOIN dark_pool_events_v2 e USING (symbol, tracking_id)
                      WHERE NOT e.ambiguous GROUP BY 1,2 HAVING count(*) > 1) x;
ROLLBACK;
