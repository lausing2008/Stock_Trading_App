-- Backfill signal_alerts for all existing watchlist items.
-- Creates one subscription per (user, stock, watchlist_horizon).
-- Skips stocks already subscribed for that horizon.
-- Run once on EC2: docker exec -i stockai-postgres-1 psql -U postgres stockai < backfill_signal_alerts.sql

INSERT INTO signal_alerts (user_id, symbol, email, horizon, alert_mode, require_consensus)
SELECT DISTINCT
    w.user_id,
    s.symbol,
    u.email,
    COALESCE(w.trading_style, 'SWING') AS horizon,
    'all'  AS alert_mode,
    false  AS require_consensus
FROM watchlist_items wi
JOIN watchlists w ON wi.watchlist_id = w.id
JOIN stocks s    ON wi.stock_id = s.id
JOIN users u     ON w.user_id = u.id
ON CONFLICT (user_id, symbol, horizon) DO NOTHING;

-- Show how many rows were affected
SELECT 'Backfill complete. Signal alert subscriptions now:', COUNT(*) FROM signal_alerts;
