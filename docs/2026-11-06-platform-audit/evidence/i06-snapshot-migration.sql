-- Run on a disposable PostgreSQL database; everything rolls back.
\set ON_ERROR_STOP on
BEGIN;
CREATE SCHEMA i06_snapshot_probe;
SET LOCAL search_path = i06_snapshot_probe;
CREATE TABLE portfolio_risk_metrics (id integer PRIMARY KEY, symbols_json text);
CREATE TABLE stress_test_results (id integer PRIMARY KEY, symbols_json text);
INSERT INTO portfolio_risk_metrics VALUES (1, '["A","B"]');
INSERT INTO stress_test_results VALUES (1, '["A","B"]');
ALTER TABLE IF EXISTS portfolio_risk_metrics ADD COLUMN IF NOT EXISTS coverage JSON;
ALTER TABLE IF EXISTS stress_test_results ADD COLUMN IF NOT EXISTS coverage JSON;
ALTER TABLE IF EXISTS portfolio_risk_metrics ADD COLUMN IF NOT EXISTS coverage JSON;
ALTER TABLE IF EXISTS stress_test_results ADD COLUMN IF NOT EXISTS coverage JSON;
DO $$ BEGIN
  ASSERT (SELECT coverage IS NULL FROM portfolio_risk_metrics WHERE id=1);
  ASSERT (SELECT coverage IS NULL FROM stress_test_results WHERE id=1);
END $$;
UPDATE portfolio_risk_metrics SET coverage='{"selected_weight_pct":90,"covered_weight_pct":72}';
UPDATE stress_test_results SET coverage='{"excluded_positions":[{"symbol":"SMALL","weight_pct":10}]}';
DO $$ BEGIN
  ASSERT (SELECT (coverage->>'covered_weight_pct')::numeric = 72 FROM portfolio_risk_metrics WHERE id=1);
  ASSERT (SELECT coverage->'excluded_positions'->0->>'symbol' = 'SMALL' FROM stress_test_results WHERE id=1);
  ASSERT (SELECT symbols_json='["A","B"]' FROM portfolio_risk_metrics WHERE id=1);
END $$;
ROLLBACK;
