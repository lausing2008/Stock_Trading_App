-- Migration: add pattern alert conditions to alertcondition enum
-- Run once: psql -U stockai -d stockai -f add_pattern_alert_conditions.sql
-- ALTER TYPE ... ADD VALUE is non-transactional in PostgreSQL 9.1+; run outside a transaction block.

ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'macd_bullish_cross';
ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'rsi_oversold_bounce';
ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'double_bottom';
ALTER TYPE alertcondition ADD VALUE IF NOT EXISTS 'breakout';
