-- Migration 001: Add sector column to paper_trades (H-SECTOR fix, PA-D1)
--
-- When to run: upgrading an instance deployed before 2026-06-09
-- Fresh instances: skip — create_all() creates this column automatically
--
-- Run on EC2:
--   docker compose exec -T postgres psql -U stockai -d stockai \
--     < /home/ec2-user/Stock_Trading_App/scripts/migrations/001_add_sector_to_paper_trades.sql

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS sector VARCHAR(128);

-- Back-fill existing open trades from stocks table
UPDATE paper_trades t
SET sector = s.sector
FROM stocks s
WHERE t.symbol = s.symbol
  AND t.sector IS NULL;

SELECT 'Migration 001 complete — sector column added and back-filled' AS status;
