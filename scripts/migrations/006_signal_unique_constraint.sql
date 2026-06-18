-- Migration 006: Unique index on signals (stock_id, horizon, day) (AUD-M23)
--
-- Prevents duplicate signal rows when two parallel refresh calls race.
-- Without this, both can INSERT a signal for the same stock+style+day,
-- doubling the row count and corrupting stability streak calculations.
--
-- The index uses date_trunc('day', ts) so only one signal per stock per
-- trading style per calendar day is allowed. Within-day refreshes UPDATE
-- (handled at application layer via upsert in _bulk_persist).
--
-- NOTE: If there are existing duplicate rows, they must be cleaned up first
-- (uncomment the DELETE block below). On fresh instances this is a no-op.
--
-- When to run: all instances
-- Safe to re-run: IF NOT EXISTS guard
--
-- Run on EC2:
--   docker compose exec -T postgres psql -U stockai -d stockai \
--     < /home/ec2-user/Stock_Trading_App/scripts/migrations/006_signal_unique_constraint.sql

-- Optional: clean up existing duplicates (keep newest per group).
-- Uncomment ONLY if you see duplicate rows causing issues.
-- DELETE FROM signals
-- WHERE id NOT IN (
--     SELECT DISTINCT ON (stock_id, horizon, date_trunc('day', ts))
--         id
--     FROM signals
--     ORDER BY stock_id, horizon, date_trunc('day', ts), ts DESC
-- );

CREATE UNIQUE INDEX IF NOT EXISTS uix_signals_stock_horizon_day
  ON signals (stock_id, horizon, date_trunc('day', ts));

SELECT 'Migration 006 complete — signal unique-per-day index created' AS status,
       COUNT(*) AS total_signals
FROM signals;
