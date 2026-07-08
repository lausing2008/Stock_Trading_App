-- Migration 004: Add stock_id to paper_trades (PT-H2)
--
-- Enables double-top mid-trade detection in _monitor_positions().
-- Without this column the monitor queries stock_id=NULL and never detects
-- double-top breakdowns, so the 1.2× ATR trail tightening never fires.
--
-- When to run: upgrading an instance deployed before 2026-06-13
-- Fresh instances: skip — create_all() creates this column automatically
--
-- Run on EC2:
--   docker compose exec -T postgres psql -U stockai -d stockai \
--     < /home/ec2-user/Stock_Trading_App/scripts/migrations/004_add_stock_id_to_paper_trades.sql

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS stock_id INTEGER REFERENCES stocks(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_paper_trades_stock_id
  ON paper_trades (stock_id)
  WHERE stock_id IS NOT NULL;

-- Back-fill existing trades from stocks table via symbol lookup
UPDATE paper_trades t
SET stock_id = s.id
FROM stocks s
WHERE t.symbol = s.symbol
  AND t.stock_id IS NULL;

SELECT 'Migration 004 complete — stock_id added and back-filled from stocks table' AS status,
       COUNT(*) FILTER (WHERE stock_id IS NOT NULL) AS trades_with_stock_id,
       COUNT(*) FILTER (WHERE stock_id IS NULL)     AS trades_still_null
FROM paper_trades;
