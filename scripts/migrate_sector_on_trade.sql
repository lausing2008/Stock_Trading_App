-- H-SECTOR fix: sector column on paper_trades for PA-D1 sector-cap monitor
-- Run once:
--   docker exec -i stockai-postgres-1 psql -U stockai stockai < scripts/migrate_sector_on_trade.sql
--   On EC2: docker exec -i <postgres_container> psql -U stockai stockai < /tmp/migrate_sector_on_trade.sql

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS sector VARCHAR(128);

-- Back-fill existing open trades from stocks table
UPDATE paper_trades t
SET sector = s.sector
FROM stocks s
WHERE t.symbol = s.symbol
  AND t.sector IS NULL;
