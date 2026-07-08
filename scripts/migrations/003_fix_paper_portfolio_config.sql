-- Migration 003: Fix paper portfolio config scale values (PT-C1)
--
-- Problem: The /configure UI accepted percentage values (e.g. "1" meaning 1%)
-- but the engine expects decimal fractions (0.01). This migration resets all
-- affected config keys to their correct values and adds missing safety params.
--
-- Run on EC2 (safe to run multiple times — idempotent):
--   docker compose exec -T postgres psql -U stockai -d stockai \
--     < /home/ec2-user/Stock_Trading_App/scripts/migrations/003_fix_paper_portfolio_config.sql

-- Step 1: Show current state before fix
SELECT name,
       config->>'risk_per_trade_pct'         AS before_risk_pct,
       config->>'max_position_pct'            AS before_max_pos,
       config->>'max_hold_days'               AS before_hold_days
FROM paper_portfolios;

-- Step 2: Fix all portfolios with trading_style=GROWTH (the only style in use)
--   risk_per_trade_pct : 1% of equity as risk basis per trade (decimal fraction)
--   max_position_pct   : hard cap at 10% of equity per position
--   max_hold_days      : 60 trading days for GROWTH style (momentum plays need time)
--   max_loss_per_trade_pct : backstop cap — no trade loses more than 2% of equity
--   max_portfolio_drawdown_pct : pause entries if equity drops 20% from peak
--   max_daily_loss_pct : pause entries if realized loss today > 4% of equity
--   max_entries_per_day : never open more than 5 new positions in one session
--   max_open_risk_pct  : aggregate open risk capped at 12% of equity
--   hold_stall_days    : exit a HOLD that hasn't moved after 30 days
--   hold_stall_max_gain : consider "stalled" if gain < 5% after hold_stall_days
UPDATE paper_portfolios
SET config = config::jsonb
  || jsonb_build_object(
    'risk_per_trade_pct',         0.01,
    'max_position_pct',           0.10,
    'max_hold_days',              60,
    'max_loss_per_trade_pct',     0.02,
    'max_portfolio_drawdown_pct', 0.20,
    'max_daily_loss_pct',         0.04,
    'max_entries_per_day',        5,
    'max_open_risk_pct',          0.12,
    'hold_stall_days',            30,
    'hold_stall_max_gain',        0.05
  )
WHERE config->>'trading_style' = 'GROWTH'
   OR config->>'trading_style' IS NULL;

-- Step 3: Confirm result
SELECT name,
       config->>'risk_per_trade_pct'         AS risk_pct,
       config->>'max_position_pct'            AS max_pos_pct,
       config->>'max_hold_days'               AS hold_days,
       config->>'max_loss_per_trade_pct'      AS max_loss_pct,
       config->>'max_open_risk_pct'           AS open_risk_pct,
       config->>'trading_style'               AS style
FROM paper_portfolios;

SELECT 'Migration 003 complete — GROWTH portfolio config corrected' AS status;
