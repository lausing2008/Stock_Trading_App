-- Dark pool direction test — per-symbol price-position vs forward return.
-- Scoped 2026-09-08, intended to be RUN 2026-12-08 once ~60 trading days have accumulated.
-- See docs/2026-09-08/DARK_POOL_DIRECTION_SCOPING.md for why each choice is what it is.
--
-- READ-ONLY. Safe to run against production.
--
-- The three design constraints, all measured rather than assumed:
--   * size >= 10000  — median print is 1,000 shares (retail-scale noise); signal lives in the tail
--   * per (symbol, day) — aggregate skew is 0.5099, dead center, so pooling reads as zero
--   * p.high > p.low — a flat 5m bar makes pos_in_bar a division by zero, common in illiquid names

\set MIN_BLOCK_SIZE 10000
\set MIN_PRINTS_PER_DAY 5

\echo
\echo ===== 0. SAMPLE GUARD — read this before trusting anything below =====
SELECT COUNT(*)                                  AS total_prints,
       COUNT(*) FILTER (WHERE size >= 10000)     AS block_prints,
       COUNT(DISTINCT symbol)                    AS symbols,
       COUNT(DISTINCT executed_at::date)         AS trading_days,
       MIN(executed_at)::date                    AS oldest,
       MAX(executed_at)::date                    AS newest
FROM dark_pool_prints;
\echo '   >> Fewer than ~40 trading days or ~5k block prints: STOP. The result will not survive'
\echo '   >> a widened sample. Three prior findings on this data reversed under exactly that test.'

\echo
\echo ===== 1. Per-symbol-per-day skew, block-filtered =====
DROP TABLE IF EXISTS _dp_daily;
CREATE TEMP TABLE _dp_daily AS
WITH pos AS (
  SELECT d.symbol,
         s.id                          AS stock_id,
         d.executed_at::date           AS d_date,
         d.size,
         (d.price - p.low) / (p.high - p.low) AS pos_in_bar
  FROM dark_pool_prints d
  JOIN stocks s ON s.symbol = d.symbol
  JOIN prices p ON p.stock_id = s.id
               AND p.timeframe = 'M5'
               AND p.ts = date_trunc('hour', d.executed_at)
                        + INTERVAL '5 min' * FLOOR(EXTRACT(minute FROM d.executed_at)::int / 5)
  WHERE p.high > p.low
    AND d.size >= 10000
)
SELECT symbol, stock_id, d_date,
       COUNT(*)                                     AS dp_n,
       SUM(size)                                    AS dp_weight,
       ROUND(AVG(pos_in_bar)::numeric, 4)           AS mean_pos,
       ROUND((AVG(pos_in_bar) - 0.5)::numeric, 4)   AS dp_skew
FROM pos
GROUP BY 1, 2, 3
HAVING COUNT(*) >= 5;

SELECT COUNT(*) AS symbol_days, COUNT(DISTINCT symbol) AS symbols,
       ROUND(AVG(dp_skew)::numeric, 4) AS avg_skew_should_be_near_0
FROM _dp_daily;

\echo
\echo ===== 2. Attach forward returns (3/5/10 trading days) from settled D1 bars =====
DROP TABLE IF EXISTS _dp_fwd;
CREATE TEMP TABLE _dp_fwd AS
WITH bars AS (
  SELECT stock_id, ts::date AS d_date, close,
         ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY ts) AS rn
  FROM prices WHERE timeframe = 'D1'
)
SELECT dd.*,
       ROUND((100.0 * (f3.close  - b.close) / b.close)::numeric, 3) AS fwd_3d_pct,
       ROUND((100.0 * (f5.close  - b.close) / b.close)::numeric, 3) AS fwd_5d_pct,
       ROUND((100.0 * (f10.close - b.close) / b.close)::numeric, 3) AS fwd_10d_pct
FROM _dp_daily dd
JOIN bars b   ON b.stock_id  = dd.stock_id AND b.d_date = dd.d_date
LEFT JOIN bars f3  ON f3.stock_id  = dd.stock_id AND f3.rn  = b.rn + 3
LEFT JOIN bars f5  ON f5.stock_id  = dd.stock_id AND f5.rn  = b.rn + 5
LEFT JOIN bars f10 ON f10.stock_id = dd.stock_id AND f10.rn = b.rn + 10
WHERE b.close > 0;

SELECT COUNT(*) AS rows_with_a_bar,
       COUNT(fwd_3d_pct)  AS resolvable_3d,
       COUNT(fwd_5d_pct)  AS resolvable_5d,
       COUNT(fwd_10d_pct) AS resolvable_10d
FROM _dp_fwd;

\echo
\echo ===== 3. THE TEST — correlation of skew with forward return, per horizon =====
\echo '   Success = consistently signed AND strengthening with dp_weight (section 4).'
\echo '   A near-zero correlation is a legitimate NULL RESULT, not a failure to fix.'
SELECT '3d'  AS horizon, COUNT(fwd_3d_pct)  AS n,
       ROUND(CORR(dp_skew, fwd_3d_pct)::numeric, 4)  AS corr FROM _dp_fwd
UNION ALL
SELECT '5d',  COUNT(fwd_5d_pct),  ROUND(CORR(dp_skew, fwd_5d_pct)::numeric, 4)  FROM _dp_fwd
UNION ALL
SELECT '10d', COUNT(fwd_10d_pct), ROUND(CORR(dp_skew, fwd_10d_pct)::numeric, 4) FROM _dp_fwd;

\echo
\echo ===== 4. Does the signal STRENGTHEN with block conviction? (the real check) =====
\echo '   If corr does not rise with the weight tercile, it is probably noise.'
WITH t AS (
  SELECT *, NTILE(3) OVER (ORDER BY dp_weight) AS weight_tercile FROM _dp_fwd
)
SELECT weight_tercile,
       COUNT(*) AS n,
       ROUND(MIN(dp_weight)) AS min_shares,
       ROUND(CORR(dp_skew, fwd_5d_pct)::numeric, 4) AS corr_5d
FROM t GROUP BY 1 ORDER BY 1;

\echo
\echo ===== 5. Directional buckets — is the sign monotonic? =====
SELECT CASE WHEN dp_skew >  0.10 THEN 'strong high (accum?)'
            WHEN dp_skew >  0.02 THEN 'mild high'
            WHEN dp_skew < -0.10 THEN 'strong low (distrib?)'
            WHEN dp_skew < -0.02 THEN 'mild low'
            ELSE 'neutral' END AS bucket,
       COUNT(*) AS n,
       ROUND(AVG(fwd_5d_pct)::numeric, 3) AS avg_fwd_5d,
       ROUND((100.0 * COUNT(*) FILTER (WHERE fwd_5d_pct > 0) / NULLIF(COUNT(fwd_5d_pct),0))::numeric, 1) AS win_pct
FROM _dp_fwd GROUP BY 1 ORDER BY 3 DESC NULLS LAST;

\echo
\echo ===== 6. OUT-OF-SAMPLE HOLDOUT — 70/30 chronological split =====
\echo '   A signal present in-sample and absent out-of-sample is overfitting, full stop.'
WITH split AS (
  SELECT *, PERCENT_RANK() OVER (ORDER BY d_date) AS pr FROM _dp_fwd
)
SELECT CASE WHEN pr <= 0.7 THEN '1_in_sample_70' ELSE '2_holdout_30' END AS slice,
       COUNT(*) AS n, MIN(d_date) AS from_date, MAX(d_date) AS to_date,
       ROUND(CORR(dp_skew, fwd_5d_pct)::numeric, 4) AS corr_5d
FROM split GROUP BY 1 ORDER BY 1;

\echo
\echo ===== 7. Sign persistence — often more robust than one day of magnitude =====
WITH runs AS (
  SELECT symbol, d_date, dp_skew, fwd_5d_pct,
         LAG(dp_skew, 1) OVER (PARTITION BY symbol ORDER BY d_date) AS prev1,
         LAG(dp_skew, 2) OVER (PARTITION BY symbol ORDER BY d_date) AS prev2
  FROM _dp_fwd
)
SELECT CASE WHEN dp_skew > 0 AND prev1 > 0 AND prev2 > 0 THEN '3d all high'
            WHEN dp_skew < 0 AND prev1 < 0 AND prev2 < 0 THEN '3d all low'
            ELSE 'mixed' END AS persistence,
       COUNT(*) AS n,
       ROUND(AVG(fwd_5d_pct)::numeric, 3) AS avg_fwd_5d
FROM runs WHERE prev2 IS NOT NULL GROUP BY 1 ORDER BY 3 DESC NULLS LAST;

\echo
\echo ===== DONE. Re-read section 0 before believing sections 3-7. =====
