-- T377-DARKPOOL-SIDE: NBBO quote at execution + buy/sell side + the two prices kept apart.
--
-- REQUIRED: SQLAlchemy's create_all() only creates MISSING TABLES; it never adds a column to
-- an existing one. Both tables already exist, so without this the new model attributes raise
-- UndefinedColumn on the first query.
--
-- WHY NBBO RATHER THAN "compare against the current price": the intuitive version was MEASURED
-- against NBBO ground truth on 364 real comparable prints and agreed only 66.8% of the time —
-- wrong on one print in three. The spread is 10-30 cents wide while the live price drifts
-- dollars over a session, so the drift dominates the signal.
--
-- All columns are NULLABLE with no backfill, deliberately. Existing rows genuinely do not have
-- this data, and inventing a side for them would be the AUD-RANK-RSPLACEHOLDER error (a
-- fabricated neutral value that a downstream consumer then learned from).

ALTER TABLE dark_pool_prints
  ADD COLUMN IF NOT EXISTS nbbo_bid DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS nbbo_ask DOUBLE PRECISION;

ALTER TABLE dark_pool_alert_outcomes
  ADD COLUMN IF NOT EXISTS exec_price DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS live_price DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS side VARCHAR(4);

-- Verification (expect 2 rows, then 3 rows):
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='dark_pool_prints' AND column_name LIKE 'nbbo%';
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='dark_pool_alert_outcomes'
--      AND column_name IN ('exec_price','live_price','side');
