-- Migration 002: Add signal_at_exit columns to paper_trades (PA-G3)
--
-- Enables walk-forward attribution: which signal type was active at exit?
-- When to run: upgrading an instance deployed before 2026-06-11
-- Fresh instances: skip — create_all() creates these columns automatically
--
-- Run on EC2:
--   docker compose exec -T postgres psql -U stockai -d stockai \
--     < /home/ec2-user/Stock_Trading_App/scripts/migrations/002_add_signal_at_exit_to_paper_trades.sql

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS signal_at_exit_id   INTEGER REFERENCES signals(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS signal_at_exit_type VARCHAR(16);

CREATE INDEX IF NOT EXISTS ix_paper_trades_signal_at_exit
  ON paper_trades (signal_at_exit_id)
  WHERE signal_at_exit_id IS NOT NULL;

SELECT 'Migration 002 complete — signal_at_exit columns added' AS status;
