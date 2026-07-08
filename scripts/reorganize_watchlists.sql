-- =============================================================================
-- Watchlist Reorganisation — user_id = 1
-- =============================================================================
-- Schema facts (confirmed from models.py + session.py migrations):
--   watchlists      : id, user_id, name, trading_style, created_at
--                     UNIQUE(user_id, name)
--   watchlist_items : id, stock_id, user_id, watchlist_id, added_at
--                     partial UNIQUE INDEX idx_uq_wl_item ON (watchlist_id, stock_id)
--                     WHERE watchlist_id IS NOT NULL
--   stocks          : id, symbol, market, exchange, ...
--
-- ON CONFLICT targets the partial unique index, so use
--   ON CONFLICT (watchlist_id, stock_id) WHERE watchlist_id IS NOT NULL DO NOTHING
-- =============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 1 — Rename existing watchlist id=4
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE watchlists
SET    name = 'Quantum & Deep Tech'
WHERE  id = 4
  AND  user_id = 1;

-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 2 — Create the 5 new watchlists (idempotent via ON CONFLICT DO NOTHING)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO watchlists (user_id, name, trading_style)
VALUES
    (1, 'AI & AGI',              NULL),
    (1, 'Semiconductors',        NULL),
    (1, 'Cloud & Cybersecurity', NULL),
    (1, 'HK Tech & Consumer',    NULL),
    (1, 'Fintech & Financial',   NULL)
ON CONFLICT (user_id, name) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 3 — Populate new watchlists
--          Each INSERT looks up the stock_id by symbol; rows where the symbol
--          does not exist in stocks are silently skipped (inner join in the
--          SELECT means no match = no row inserted).
-- ─────────────────────────────────────────────────────────────────────────────

-- 3a. AI & AGI
INSERT INTO watchlist_items (watchlist_id, stock_id, user_id)
SELECT w.id, s.id, 1
FROM   watchlists w
CROSS  JOIN (
    SELECT id FROM stocks WHERE symbol IN (
        'NVDA', 'PLTR', 'AI', 'GOOG', 'IBM', 'AAPL',
        '0100.HK', '6082.HK', '9903.HK', '6651.HK', '9880.HK'
    )
) s
WHERE  w.user_id = 1
  AND  w.name    = 'AI & AGI'
ON CONFLICT (watchlist_id, stock_id) WHERE watchlist_id IS NOT NULL DO NOTHING;

-- 3b. Semiconductors
INSERT INTO watchlist_items (watchlist_id, stock_id, user_id)
SELECT w.id, s.id, 1
FROM   watchlists w
CROSS  JOIN (
    SELECT id FROM stocks WHERE symbol IN (
        'AVGO', 'MU', 'TSM', 'INTC', 'CGNX', 'LITE', 'VICR', 'POET', 'NOK',
        'SSNLF', '0981.HK', '1347.HK', '3986.HK'
    )
) s
WHERE  w.user_id = 1
  AND  w.name    = 'Semiconductors'
ON CONFLICT (watchlist_id, stock_id) WHERE watchlist_id IS NOT NULL DO NOTHING;

-- 3c. Cloud & Cybersecurity
INSERT INTO watchlist_items (watchlist_id, stock_id, user_id)
SELECT w.id, s.id, 1
FROM   watchlists w
CROSS  JOIN (
    SELECT id FROM stocks WHERE symbol IN (
        'CRWD', 'DDOG', 'DT', 'NET', 'ZS', 'AKAM', 'ORCL'
    )
) s
WHERE  w.user_id = 1
  AND  w.name    = 'Cloud & Cybersecurity'
ON CONFLICT (watchlist_id, stock_id) WHERE watchlist_id IS NOT NULL DO NOTHING;

-- 3d. HK Tech & Consumer
INSERT INTO watchlist_items (watchlist_id, stock_id, user_id)
SELECT w.id, s.id, 1
FROM   watchlists w
CROSS  JOIN (
    SELECT id FROM stocks WHERE symbol IN (
        '0700.HK', '0992.HK', '3690.HK', '9961.HK', '3896.HK',
        '6088.HK', '6613.HK', '2513.HK', '6682.HK', '9992.HK',
        '2476.HK', '2171.HK'
    )
) s
WHERE  w.user_id = 1
  AND  w.name    = 'HK Tech & Consumer'
ON CONFLICT (watchlist_id, stock_id) WHERE watchlist_id IS NOT NULL DO NOTHING;

-- 3e. Fintech & Financial
INSERT INTO watchlist_items (watchlist_id, stock_id, user_id)
SELECT w.id, s.id, 1
FROM   watchlists w
CROSS  JOIN (
    SELECT id FROM stocks WHERE symbol IN (
        'JPM', 'NU', 'SOFI', 'V', 'AMADY'
    )
) s
WHERE  w.user_id = 1
  AND  w.name    = 'Fintech & Financial'
ON CONFLICT (watchlist_id, stock_id) WHERE watchlist_id IS NOT NULL DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 4 — Move SMH and SOXX to ETF watchlist (id = 2)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO watchlist_items (watchlist_id, stock_id, user_id)
SELECT 2, s.id, 1
FROM   stocks s
WHERE  s.symbol IN ('SMH', 'SOXX')
ON CONFLICT (watchlist_id, stock_id) WHERE watchlist_id IS NOT NULL DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 5 — Move KGS and GEV to Gas & Energy watchlist (id = 3)
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO watchlist_items (watchlist_id, stock_id, user_id)
SELECT 3, s.id, 1
FROM   stocks s
WHERE  s.symbol IN ('KGS', 'GEV')
ON CONFLICT (watchlist_id, stock_id) WHERE watchlist_id IS NOT NULL DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 6 — Remove all moved stocks from "My Watchlist" (id = 1)
--          Covers: all 5 new-list stocks + SMH/SOXX + KGS/GEV
-- ─────────────────────────────────────────────────────────────────────────────
DELETE FROM watchlist_items
WHERE  watchlist_id = 1
  AND  stock_id IN (
      SELECT id FROM stocks WHERE symbol IN (
          -- AI & AGI
          'NVDA', 'PLTR', 'AI', 'GOOG', 'IBM', 'AAPL',
          '0100.HK', '6082.HK', '9903.HK', '6651.HK', '9880.HK',
          -- Semiconductors
          'AVGO', 'MU', 'TSM', 'INTC', 'CGNX', 'LITE', 'VICR', 'POET', 'NOK',
          'SSNLF', '0981.HK', '1347.HK', '3986.HK',
          -- Cloud & Cybersecurity
          'CRWD', 'DDOG', 'DT', 'NET', 'ZS', 'AKAM', 'ORCL',
          -- HK Tech & Consumer
          '0700.HK', '0992.HK', '3690.HK', '9961.HK', '3896.HK',
          '6088.HK', '6613.HK', '2513.HK', '6682.HK', '9992.HK',
          '2476.HK', '2171.HK',
          -- Fintech & Financial
          'JPM', 'NU', 'SOFI', 'V', 'AMADY',
          -- To ETF
          'SMH', 'SOXX',
          -- To Gas & Energy
          'KGS', 'GEV'
      )
  );

-- ─────────────────────────────────────────────────────────────────────────────
-- STEP 7 — Verification queries (returns 0 rows if everything is correct)
-- ─────────────────────────────────────────────────────────────────────────────

-- 7a. My Watchlist should now be empty
SELECT 'My Watchlist still has items — unexpected!' AS check_label, s.symbol
FROM   watchlist_items wi
JOIN   stocks s ON s.id = wi.stock_id
WHERE  wi.watchlist_id = 1;

-- 7b. Show final item counts per watchlist for user_id = 1
SELECT w.id, w.name, COUNT(wi.id) AS item_count
FROM   watchlists w
LEFT   JOIN watchlist_items wi ON wi.watchlist_id = w.id
WHERE  w.user_id = 1
GROUP  BY w.id, w.name
ORDER  BY w.id;

COMMIT;
