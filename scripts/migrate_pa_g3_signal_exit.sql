-- PA-G3: Signal lifecycle tracking — record which signal was active at exit
-- Run once against the database:
--   docker exec -i stockai-db-1 psql -U stockai stockai < scripts/migrate_pa_g3_signal_exit.sql
--   OR on EC2:
--   docker exec -i stockai-db-1 psql -U stockai stockai < /home/ec2-user/app/scripts/migrate_pa_g3_signal_exit.sql

ALTER TABLE paper_trades
  ADD COLUMN IF NOT EXISTS signal_at_exit_id   INTEGER REFERENCES signals(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS signal_at_exit_type VARCHAR(16);

CREATE INDEX IF NOT EXISTS ix_paper_trades_signal_at_exit_id
  ON paper_trades (signal_at_exit_id)
  WHERE signal_at_exit_id IS NOT NULL;
