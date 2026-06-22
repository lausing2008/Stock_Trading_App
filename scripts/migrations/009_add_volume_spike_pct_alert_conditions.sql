-- Migration: add volume_spike and pct_below_52wk_high to alertcondition enum
-- Introduced by SA-11 (commit b6d3ce6); was not run on EC2 at the time.
-- Run once: psql -U stockai -d stockai -f 009_add_volume_spike_pct_alert_conditions.sql
-- ALTER TYPE ... ADD VALUE is non-transactional in PostgreSQL — run outside a transaction block.
--
-- NOTE: SQLAlchemy SAEnum stores Python enum .name (uppercase) not .value (lowercase).
-- The DB must have both forms to support existing data and SQLAlchemy ORM writes.
ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'volume_spike';
ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'pct_below_52wk_high';
ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'VOLUME_SPIKE';
ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'PCT_BELOW_52WK_HIGH';
