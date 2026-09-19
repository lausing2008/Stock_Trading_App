-- Migration: add broker_exit_order_id + broker_exit_fill_confirmed to paper_trades (AUD-B02-EXITIDPERSISTED)
-- Run once: psql -U stockai -d stockai -f 015_add_broker_exit_order_id_to_paper_trades.sql
--
-- _place_broker_exit() held a broker-returned SELL order's ID in a local variable long enough
-- to log it twice and then discarded it — no column existed to persist it to, so
-- poll_broker_order_fills() (entry-only, filtering on broker_order_id/broker_fill_confirmed)
-- had no equivalent exit poller to exist for. If the immediate-fill check inside
-- _place_broker_exit() didn't resolve the fill right away (after-hours, partial fill, a slow
-- response), the exit order was orphaned: real and live at the broker, with no durable link
-- back to the paper trade the UI had already marked closed. See
-- docs/audits/2026-09-18-a01-a03-broker-lifecycle-scoping.md (A02/B02) for the full incident.
--
-- Mirrors broker_order_id/broker_fill_confirmed's own shape exactly (see migration 012), for
-- the exit leg. Defaults NULL/false for all existing rows (safe: no existing closed trade can
-- retroactively acquire an exit order ID it never had; broker_exit_fill_confirmed defaulting
-- false on a row with no broker_exit_order_id is inert since poll_broker_exit_fills() also
-- filters on broker_exit_order_id IS NOT NULL).
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS broker_exit_order_id VARCHAR(64);
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS broker_exit_fill_confirmed BOOLEAN NOT NULL DEFAULT false;
