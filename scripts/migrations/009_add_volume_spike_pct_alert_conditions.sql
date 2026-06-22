-- Migration: add volume_spike and pct_below_52wk_high to alertcondition enum
-- Introduced by SA-11 (commit b6d3ce6); was not run on EC2 at the time.
-- Run once: psql -U stockai -d stockai -f 009_add_volume_spike_pct_alert_conditions.sql
-- ALTER TYPE ... ADD VALUE is non-transactional in PostgreSQL — run outside a transaction block.

ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'volume_spike';
ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'pct_below_52wk_high';
