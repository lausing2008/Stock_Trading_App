-- Migration: add realized_pnl + entry_shares to paper_trades (T232-PT6 scale-out P&L fix)
-- Run once: psql -U stockai -d stockai -f 011_add_realized_pnl_to_paper_trades.sql
--
-- Previously, scale-out partial exits computed partial_pnl for logging only — it was never
-- accumulated onto the trade, so trade.pnl at final close only reflected the remaining shares.
-- A trade that scaled out profitably (+7%, +12%) then trailed the remainder to breakeven or a
-- small loss was recorded as a ~$0/negative trade — feeding wrong labels into _recent_win_rate,
-- _consec_loss_streak, the heat brake, daily/weekly loss limits, and RL training.
--
-- realized_pnl accumulates dollar P&L from partial exits as they happen (default 0 for existing
-- open trades and for closed trades before this fix — NOT backfilled, since we can't reliably
-- reconstruct historical partial fills from just entry_decision_notes text).
-- entry_shares snapshots the original position size before any scale-out shrinks `shares`, so
-- pct_return at close can be computed against the true original cost basis.
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC(20, 6) NOT NULL DEFAULT 0.0;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS entry_shares NUMERIC(20, 6);

-- Backfill entry_shares for existing OPEN trades only, using current `shares` as the best
-- available approximation (scale-outs on existing open trades will now slightly underestimate
-- pct_return's cost basis if they already partially scaled out before this migration ran —
-- acceptable one-time transitional imprecision, self-corrects for all trades opened after).
UPDATE paper_trades SET entry_shares = shares WHERE entry_shares IS NULL AND stage = 'open';
