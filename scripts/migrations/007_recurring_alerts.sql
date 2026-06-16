-- Migration 007: add recurring support to price_alerts
-- Run: psql -U stockai -d stockai -f 007_recurring_alerts.sql

ALTER TABLE price_alerts
  ADD COLUMN IF NOT EXISTS recurring BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMPTZ;
