-- Migration 005: Partial unique index on paper_portfolios (AUD-M19)
--
-- Prevents duplicate active portfolios with the same name.
-- A race condition or bug could create two portfolios with the same name and
-- both is_active=TRUE, causing double-counting in the paper trading engine.
--
-- The index is PARTIAL (WHERE is_active = TRUE) so deactivated/archived
-- portfolios with the same name are allowed — only one active at a time.
--
-- When to run: all instances (fresh and upgraded)
-- Safe to re-run: IF NOT EXISTS guard on the index name
--
-- Run on EC2:
--   docker compose exec -T postgres psql -U stockai -d stockai \
--     < /home/ec2-user/Stock_Trading_App/scripts/migrations/005_paper_portfolio_unique_index.sql

CREATE UNIQUE INDEX IF NOT EXISTS uix_paper_portfolios_active_name
  ON paper_portfolios (name)
  WHERE is_active = TRUE;

SELECT 'Migration 005 complete — unique active portfolio name index created' AS status;
