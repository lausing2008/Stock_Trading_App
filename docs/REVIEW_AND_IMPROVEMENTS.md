# StockAI — Expert Review & Improvement Roadmap

**Reviewed:** 2026-05-31  
**Last updated:** 2026-06-10 (Per-horizon alerts, consensus indicator, Add to Radar, admin health expansion, AUC fix)  
**Perspective:** Data Analyst + Quantitative Trading  
**Overall rating:** 9.2 / 10 *(was 8.5 → 8.7 → 8.8 → 8.9 → 9.0 → 9.2 — alert/UX improvements shipped 2026-06-10)*

---

## Executive Summary

StockAI is a well-architected personal trading intelligence platform with a genuinely impressive feature set for a self-built system. The microservice separation, dual-storage pipeline, multi-user auth, email alerts, and ML + TA signal fusion all reflect real systems thinking. **As of 2026-06-10, all Tier 1–4 improvements are complete plus alert intelligence enhancements.** The signal engine is regime-aware, ML features cover 34 inputs with weekly auto-calibration, portfolio risk is quantified, position sizing is ATR-driven, outcomes feedback loop fully wired, and signal alerts now operate per-horizon with optional multi-timeframe consensus gating.

This document is the single source of truth for everything that was found, why it matters, and how it was fixed.

**Remaining (Tier 5, low priority):** UI-08 walk-forward drill-down, UI-12 congress trading page.

---

## Implementation Log

| Date | Item | Files Changed | Status |
|------|------|--------------|--------|
| 2026-05-31 | K-Score falling knife gate | ranking-engine/kscore.py | ✅ Done |
| 2026-05-31 | K-Score RSI asymmetric curve | ranking-engine/kscore.py | ✅ Done |
| 2026-05-31 | Zero-volume bar filtering | market-data/ingestion.py | ✅ Done |
| 2026-05-31 | Macro data Redis caching | ml-prediction/builder.py | ✅ Done |
| 2026-05-31 | Stale price guard (logging) | signal-engine/signals.py | ✅ Done |
| 2026-05-31 | ML calibration (isotonic regression) | ml-prediction/trainer.py | ✅ Already implemented |
| 2026-05-31 | Look-ahead bias guard | ml-prediction/trainer.py | ✅ Done |
| 2026-05-31 | Symbol sanitisation (prompt injection) | research-engine/routes.py | ✅ Done |
| 2026-05-31 | Admin-only Improvements tab | frontend/_app.tsx | ✅ Done |
| 2026-06-01/02 | Factor exposure analysis | signal-engine/routes.py, frontend/signal-accuracy.tsx | ✅ Done |
| 2026-06-01/02 | ML weight validation chart | signal-engine/routes.py, frontend/signal-accuracy.tsx | ✅ Done |
| 2026-06-01/02 | ML test AUC formula fix | signal-engine/signals.py | ✅ Done |
| 2026-06-01/02 | Options flow integration | market-data/routes.py, signal-engine/signals.py, frontend/stock/[symbol].tsx | ✅ Done |
| 2026-06-01/02 | Trade board exit P&L | shared/db/models.py, frontend/board.tsx | ✅ Done |
| 2026-06-01/02 | Backtest engine (equity curve + Sharpe + drawdown) | signal-engine/routes.py, frontend/trade-performance.tsx | ✅ Done |
| 2026-06-04 | adj_close consistency (split-adjust upsert) | market-data/ingestion.py | ✅ Done |
| 2026-06-04 | Frontend strategy weight normalisation | frontend/opportunities.tsx | ✅ Done |
| 2026-06-04 | Research engine cache quality flag (banner) | frontend/research/[symbol].tsx | ✅ Done |
| 2026-06-04 | Sector-relative fundamental scoring | research-engine/routes.py | ✅ Done |
| 2026-06-04 | Sector rotation dashboard | ranking-engine/routes.py, frontend/sector-rotation.tsx, _app.tsx, api.ts | ✅ Done |
| 2026-06-05 | Signal alert auto-subscribe on watchlist add | market-data/watchlist.py, DB migration | ✅ Done |
| 2026-06-05 | Bulk-subscribe all watchlist stocks | DB (signal_alerts) | ✅ Done |
| 2026-06-05 | Fix watchlists.trading_style missing DB column | DB migration | ✅ Done |
| 2026-06-05 | Notify All / Mute All: Promise.all → Promise.allSettled | frontend/watchlist.tsx | ✅ Done |
| 2026-06-05 | Signal tier framework documented | docs/TRADING_WORKFLOW.md | ✅ Done |
| 2026-06-05 | **SA-8: SWING buy_threshold lowered** 0.65→0.62 (bull), 0.70→0.67 (high_vol), 0.73→0.70 (bear) | signal-engine/signals.py | ✅ Done |
| 2026-06-05 | **SA-8: SWING adx_min lowered** 20→15 (capture early-trend entries before ADX peaks) | signal-engine/signals.py | ✅ Done |
| 2026-06-05 | **SA-8: AUC floor** — ml_weight=0 when model AUC < 0.52 (near-random model falls back to TA-only) | signal-engine/signals.py | ✅ Done |
| 2026-06-05 | **SA-8: 4 new ML features** momentum_12_1, sma_200_gap, dist_52w_high, dist_52w_low (30→34 features) | ml-prediction/builder.py | ✅ Done |
| 2026-06-05 | **SA-8: Recency weight ratio** 3.0→5.0 (newest bar = 5× oldest; adapts faster to regime shifts) | ml-prediction/trainer.py, tuner.py | ✅ Done |
| 2026-06-05 | **SA-8: Horizon alignment** SWING trains on 10d labels, LONG on 20d (was all using 5d) | ml-prediction/routes.py | ✅ Done |
| 2026-06-05 | **signal_outcomes table** — fixed-window forward tracking for directional accuracy (SHORT=7d, SWING=14d, LONG=28d) | shared/db/models.py + session.py | ✅ Done |
| 2026-06-05 | **POST /signals/outcomes/evaluate** + **GET /signals/outcomes/summary** endpoints | signal-engine/routes.py | ✅ Done |
| 2026-06-05 | Scheduler hook: evaluate outcomes after every post-close ML retrain | market-data/scheduler.py | ✅ Done |
| 2026-06-05 | **Audit fix: ml_test_auc** added to reasons dict → signal_outcomes.ml_auc now populated | signal-engine/signals.py | ✅ Done |
| 2026-06-05 | **Audit fix: /train and /tune horizon routing** — single-symbol endpoints now derive horizon from style via _HORIZON_BY_STYLE (was always using 5d default) | ml-prediction/routes.py | ✅ Done |
| 2026-06-05 | **Audit fix: signal_alerts DDL** — last_sent_at column added to CREATE TABLE + ALTER TABLE migration for existing DBs | shared/db/session.py | ✅ Done |
| 2026-06-05 | **Audit fix: _recency_weights default** updated 3.0→5.0 to match all call sites | ml-prediction/trainer.py | ✅ Done |
| 2026-06-05 | Docs: SIGNAL_ACCURACY.md created (outcomes table, evaluate/summary endpoints, vectorbt, Optuna tuning workflow) | docs/SIGNAL_ACCURACY.md | ✅ Done |
| 2026-06-05 | Docs: AI_SIGNAL.md updated (SA-8 thresholds, 34-feature table, style horizons, AUC floor) | docs/AI_SIGNAL.md | ✅ Done |
| 2026-06-05 | **SA-1: ML/TA conflict weighting** — when ML and TA disagree by >25%, ML weight cut 25% (elif gap > 0.25: ml_w *= 0.75) | signal-engine/signals.py | ✅ Done |
| 2026-06-05 | **SA-2: Style-specific precision thresholds** — SHORT=70%, SWING=60%, LONG=50% minimum precision before BUY fires | ml-prediction/trainer.py | ✅ Done |
| 2026-06-05 | **SA-4: Weekly alignment min bars 26→15** — graduated confidence scaling so newer stocks aren't skipped entirely | signal-engine/signals.py | ✅ Done |
| 2026-06-04 | **Tier 2: S/R context** — swing pivot detection; at_resistance compresses 15%, breakout +5%, at_support +3%; sr_flag in SignalCard | signal-engine/signals.py | ✅ Done |
| 2026-06-04 | **Tier 2: ATR position sizer** — GET /stocks/{symbol}/atr; PositionSizer component; stop=price−2×ATR; shows shares, risk $, R:R | market-data/routes.py + frontend/stock/[symbol].tsx | ✅ Done |
| 2026-06-04 | **Tier 2: Drift detection (rolling accuracy)** — GET /signals/rolling_accuracy; 30d window accuracy chart; drift_warning flag at <55% | signal-engine/routes.py + frontend/signal-accuracy.tsx | ✅ Done |
| 2026-06-04 | **Tier 2: Peer comparison drawer** — PeerCompareDrawer; side-by-side K-Score + sub-scores; rankings "Compare (N)" multi-select; stock detail auto-suggests peers | frontend/rankings.tsx + stock/[symbol].tsx | ✅ Done |
| 2026-06-04 | **Tier 3: Portfolio risk** — GET /portfolio/risk; Wilder beta vs SPY/HSI; parametric 1-day 95% VaR; correlation matrix; sector concentration; Trade Board risk section | signal-engine/routes.py + frontend/board.tsx | ✅ Done |
| 2026-06-04 | **Tier 3: DCF valuation** — 2-stage DCF in research engine; 5-year FCF projection + Gordon Growth terminal value; WACC 10%; margin of safety %; high-conviction badge when DCF and K-Score agree within 15% | research-engine/routes.py | ✅ Done |
| 2026-06-04 | **Tier 3: Walk-forward backtest** — GET /signals/walkforward; non-overlapping test windows; per-window accuracy + equity curve + Sharpe + max drawdown; SPY/HSI benchmark; Walk-Forward tab on signal accuracy page | signal-engine/routes.py + frontend/signal-accuracy.tsx | ✅ Done |
| 2026-06-06 | **UI-01: Signal Outcomes Dashboard** — Outcomes tab on /signal-accuracy; win rate by confidence band (0-40/40-55/55-70/70-85/85+), by horizon, by regime; calls GET /signals/outcomes/summary | frontend/signal-accuracy.tsx + api.ts | ✅ Done |
| 2026-06-07 | **UI-04: Insider conviction screener** — net buy $ + distinct buyer count + buy/sell counts per ticker from trailing 90d; "Net buyers only" toggle; conviction bars; linked tickers; above Sudden Activity panel | frontend/insider.tsx | ✅ Done |
| 2026-06-07 | **UI-06: Position P&L heatmap** — flexbox grid above chart; cells sized by market value; green/red by P&L % intensity (alpha 0.08–0.38); tooltip shows P&L $ | frontend/positions.tsx | ✅ Done |
| 2026-06-07 | **UI-09: Data freshness chip** — header chip polls GET /stocks/data_freshness every 5 min; "Xh ago"; green <8h, yellow 8–30h, red >30h | frontend/_app.tsx + market-data/routes.py | ✅ Done |
| 2026-06-07 | **SA-3: Macro boolean ML features confirmed** — is_bear_market, vix_spiking, high_vol_regime, market_stress already in FEATURE_COLUMNS and flowing to XGBoost; marked done | ml-prediction/builder.py | ✅ Already live |
| 2026-06-07 | **SA-5: calibrate_ta_weights on schedule** — _weekly_full_refresh() now calls POST /signals/calibrate_ta_weights every Sunday after tune_all; fits logistic regression on signal history, writes ta_weights.json | market-data/scheduler.py | ✅ Done |
| 2026-06-07 | **SA-7: Regime-aware earnings compression** — bull+beat≥70%: skip compression +3% boost; bull+50-70%: beat_scale=2.0 (halved); bear/high_vol: beat_scale=0.75–1.0 (tightened); unknown: original ±20% formula | signal-engine/signals.py | ✅ Done |
| 2026-06-10 | **ML AUC key fix** — trainer bundles store `"auc"` / `"cv_auc_mean"` but `/ml/metrics` endpoint read `"test_auc"` / `"cv_auc"`; all 119 models returned null; fixed key names so admin health shows real values | ml-prediction/routes.py | ✅ Done |
| 2026-06-10 | **Per-horizon signal alerts** — `signal_alerts` schema extended with `horizon` column; unique constraint updated to `(user_id, symbol, horizon)`; each timeframe (SHORT/SWING/LONG/GROWTH) gets its own subscription row | shared/db/models.py + session.py + market-data/signal_alerts.py | ✅ Done |
| 2026-06-10 | **Require consensus setting** — `require_consensus` Boolean on alert subscription; scheduler skips alert if <2 of 4 horizons agree on the new signal direction | shared/db/models.py + market-data/scheduler.py | ✅ Done |
| 2026-06-10 | **4-horizon consensus indicator on stock detail** — stock detail fetches all 4 horizon signals concurrently; 2×2 grid shows signal + confidence per horizon plus consensus label (Strong bullish / Moderately bullish / Mixed / etc.) | frontend/stock/[symbol].tsx + api.ts | ✅ Done |
| 2026-06-10 | **Per-horizon alert rows on alerts page** — subscription list shows 4 rows per symbol with horizon badge, mode toggle, and ⚡ Consensus / Any toggle; add form includes horizon selector | frontend/alerts.tsx | ✅ Done |
| 2026-06-10 | **Add to Radar button on Opportunities** — 📡 button per stock card; adds symbol to "Radar" watchlist (auto-created if missing); already-added stocks show as checked | frontend/opportunities.tsx | ✅ Done |
| 2026-06-10 | **Admin health — SIGNAL REFRESH HEALTH** — BUY/SELL/WAIT/HOLD distribution, bull/bear ratio, fresh/stale counts, last US/HK refresh timestamps | frontend/admin-health.tsx | ✅ Done |
| 2026-06-10 | **Admin health — ML TRAINING HEALTH** — Avg AUC, good/weak/overfit model counts, last US/HK retrain timestamps with pass/fail badges | frontend/admin-health.tsx | ✅ Done |

---

## Scorecard

| Dimension | Score | Summary |
|-----------|-------|---------|
| Data pipeline | 8.5 / 10 | ↑ Data freshness chip (UI-09) + zero-vol filter + split-adjust + adj_close consistent |
| ML methodology | 9.0 / 10 | ↑ SA-3 boolean regime flags + SA-5 weekly TA weight calibration + SA-8 34 features + Optuna + AUC floor |
| Signal logic | 9.0 / 10 | ↑ SA-1 conflict weighting + SA-2 style thresholds + SA-7 regime earnings + S/R context + walk-forward |
| K-Score ranking | 8.2 / 10 | ↑ Falling knife gate + RSI curve + sector-relative peer scoring + peer comparison drawer |
| Research engine | 7.5 / 10 | ↑ DCF valuation + sector-relative fundamentals + cache quality flag; Nginx 150s timeout fixed |
| Frontend / UX | 9.5 / 10 | ↑ Per-horizon alerts + consensus indicator + Add to Radar + Outcomes tab + P&L heatmap + conviction screener |
| Risk management | 8.5 / 10 | ↑ Portfolio beta + VaR + correlation + ATR position sizer + unrealized P&L + portfolio heatmap |
| **Overall** | **9.2 / 10** | *(was 7.5 → 8.0 → 8.2 → 8.3 → 8.5 → 8.7 → 8.8 → 8.9 → 9.0 → 9.2 — alert intelligence + UX 2026-06-10)* |

---

## Part 1 — What Is Working Well

### 1.1 Architecture & Engineering
- Clean microservice separation: market-data, signal-engine, ranking-engine, research-engine are independently deployable and testable.
- Incremental 5-minute ingest with ThreadPoolExecutor + tenacity retry — rate-limit aware and efficient.
- Idempotent upserts and dual storage (Parquet + Postgres) shows real systems thinking.
- Multi-user JWT auth, namespaced localStorage, email alerts, and role-based admin — production-grade.

### 1.2 Signal Design
- Fusing TA + ML is the correct approach — neither alone is sufficient.
- Market regime filter (bear market raises BUY threshold from 65% to 73%) is genuinely good risk management.
- Earnings proximity penalty (75% signal compression 0–2 days before earnings) reduces blow-up risk.
- Multi-timeframe confirmation (daily + weekly alignment) catches trend vs. noise correctly.
- RSI divergence detection (10-bar lookback) is principled and standard.

### 1.3 Feature Engineering
- 26 features across momentum, volatility, trend, oscillators, volume, and 4 macro context inputs.
- Macro context (SPY returns, VIX, market vol) gives situational awareness most retail models skip entirely.
- Volatility-adjusted label threshold (dead-zone filtering) is a principled approach that prevents the model from training on ambiguous bars.

### 1.4 Confluence Score & Trade Decision System
- Tiered entry (screen → confirm → time → size → alert) matches professional discretionary workflow.
- Position sizing scaled to signal strength (8–10% for Strong, 2–4% for Moderate) enforces discipline.
- Entry zone (nearest support) + multi-target exit (analyst mean / high / K-Score fair value) is a complete trade plan in one panel.

---

## Part 2 — Critical Weaknesses

These are ordered by severity. Severity is assessed as potential impact on real capital decisions.

---

### CRITICAL-1: Look-Ahead Bias Risk ✅ IMPLEMENTED 2026-05-31
**File:** `services/ml-prediction/src/training/trainer.py`  
**Severity:** HIGH

**What is wrong:**  
If the daily ingest runs mid-session and a "today" bar is in the DB, it gets included in training feature windows (SMA, ATR, z-scores) even though its label is NaN and dropped. A partially-observed bar at 14:00 ET shifts rolling statistics compared to a full close bar, introducing subtle look-ahead contamination.

**Fix (implemented):**  
In `train_model()`, immediately after loading prices, bars timestamped today or later are filtered out before any feature computation:

```python
today = date.today()
df = df[pd.to_datetime(df["ts"]).dt.date < today].copy()
```

This ensures training always uses only fully-closed bars. The scheduler should additionally be configured to retrain only after the post-close (16:30 ET) ingest confirms a new bar — the code fix handles the data boundary; scheduling discipline handles the timing.

---

### CRITICAL-2: Survivorship Bias in K-Score Value Sub-score ✅ IMPLEMENTED 2026-05-31
**File:** `services/ranking-engine/src/scoring/kscore.py`  
**Severity:** HIGH

**What is wrong:**  
The Value proxy is `1 − (price / 52w_high)`. A stock down 80% from its annual high scores 80 on Value. A stock in terminal decline approaching zero scores near 100. This systematically surfaces falling knives as attractive value opportunities.

**Example:**  
- TSLA at ATH: Value score ≈ 0 (correctly identified as not a value play)  
- A failing regional bank down 90%: Value score ≈ 90 (incorrectly identified as deep value)

**Fix (implemented):**  
Added a trend direction gate in `_value_proxy()`. If both 1-month return < −5% and 3-month return < −15%, the value score is capped at 25. This prevents stocks in a sustained downtrend from receiving the full value bonus.

```python
if r1m < -0.05 and r3m < -0.15:
    return min(raw_score, 25.0)
```

Better long-term fix: replace the 52w-high discount proxy with analyst consensus upside (target_price / current_price − 1), which already factors in fundamental assessment.

---

### CRITICAL-3: ML Model Not Calibrated ✅ ALREADY IMPLEMENTED
**File:** `services/ml-prediction/src/training/trainer.py`  
**Severity:** HIGH

**Review finding:**  
Initial review flagged this as missing. On reading the actual code, isotonic regression calibration is already fully implemented — this was a false finding.

**What is actually in place:**  
- Three-way split: 70% train / 15% calibration / 15% test — calibrator fit on a held-out set the model never saw
- `IsotonicRegression(out_of_bounds="clip")` fitted on calibration probabilities and applied at both training evaluation and inference time
- Calibrator serialised in the joblib bundle alongside the model; `predict_latest()` applies it before returning `bullish_probability`
- Precision-optimised BUY threshold (`_precision_threshold()`) derived from the calibrated probabilities on the test set — not a fixed 0.5 cutoff

No further action needed on calibration.

---

### CRITICAL-4: Macro Data Silent Failures ✅ IMPLEMENTED 2026-05-31
**File:** `services/ml-prediction/src/features/builder.py`  
**Severity:** HIGH

**What is wrong:**  
When yfinance fails to fetch SPY/VIX data, macro features silently zero-fill. The model was trained on real macro values. At inference, zero-filled macros look like extreme market panic (VIX=0, SPY returns=0), which biases every signal toward defensiveness regardless of actual market conditions. This is a distribution shift between training data and inference data that happens silently.

**Fix (implemented):**  
Added `_redis_save_macro()` and `_redis_load_macro()` helpers in `builder.py`. On successful yfinance fetch, the macro DataFrame is serialised and stored in Redis under `stockai:macro_features` with a 24-hour TTL. On failure, the last cached DataFrame is returned instead of an empty one. Zero-fill only occurs if Redis also has no cached data (extreme fallback).

```python
_MACRO_CACHE_KEY = "stockai:macro_features"
_MACRO_CACHE_TTL = 86_400
# fetch → write to Redis; error → read from Redis; no cache → empty (original fallback)
```

---

### CRITICAL-5: Fundamental Scoring Uses Absolute Thresholds (Not Sector-Relative)
**File:** `services/research-engine/src/services/scoring.py`  
**Severity:** MEDIUM-HIGH

**What is wrong:**  
Every fundamental threshold is hardcoded to absolute values:
- P/E of 25 marked "fairly valued" for all stocks regardless of sector
- Revenue growth of 10% marked "good" for all companies
- D/E ratio above 2.0 marked "weak" regardless of industry

This means a utility company (correctly valued at 14× P/E) is flagged as "undervalued" and a high-growth SaaS (correctly valued at 40× P/E) is flagged as "overvalued" — inverting reality for both.

**Fix:**  
Group stocks by sector and compute percentile ranks within the sector:

```python
# In scoring.py:
def sector_percentile(value: float, sector_values: list[float]) -> float:
    """Returns 0–100 percentile rank of value within sector peer group."""
    if not sector_values or value is None:
        return 50.0
    below = sum(1 for v in sector_values if v < value)
    return round(below / len(sector_values) * 100, 1)

# Then replace absolute threshold logic with:
pe_score = sector_percentile(stock_pe, [s.pe for s in sector_peers if s.pe])
# Invert for PE (lower is better): pe_adj_score = 100 - pe_score
```

The sector peer group can be built from the existing universe — all stocks in the same `sector` field in the database.

---

### MEDIUM-1: ML Weight Formula Is Ad-Hoc
**File:** `services/signal-engine/src/signals/generator.py`  
**Severity:** MEDIUM

**What is wrong:**  
The formula `ml_weight = 0.40 + (auc - 0.50) / 0.20 * 0.35` maps AUC 0.50–0.70 to weight 40–75%. This was manually designed with no empirical backing. It also uses cross-validation AUC (in-sample estimate), not a held-out test AUC, making it prone to overfitting.

**Fix:**  
Run the signal engine on historical data with both TA-only and TA+ML modes. Compute Sharpe ratio for each. Use the weight that maximises historical Sharpe on a validation period that ends at least 6 months before today. Codify the winning weight as a constant rather than a dynamic formula until you have enough historical signal data to re-derive it.

---

### MEDIUM-2: RSI Peak at 55 Is Arbitrary ✅ IMPLEMENTED 2026-05-31
**File:** `services/ranking-engine/src/scoring/kscore.py`  
**Severity:** MEDIUM

**What is wrong:**  
`rsi_score = 100 - abs(RSI - 55)` peaks when RSI=55. RSI=70 (overbought) scores only 15. There is no empirical justification for 55 as the ideal RSI. Strong uptrends regularly sustain RSI above 60 for weeks.

**Fix (implemented):**  
Replaced with an asymmetric piecewise function in `_technical_score()`:
- RSI ≤ 30: score 50 (severely oversold)  
- RSI 30–50: 50 → 90 (emerging from oversold)  
- RSI 50–70: 90 → 100 (healthy bullish momentum, peak zone)  
- RSI > 70: drops 2.5 pts per point (overbought penalty)

A trending stock at RSI 70 now scores ~100 instead of being penalised to 85 under the old symmetric formula.

---

### MEDIUM-3: Dividend and Split Adjustment Inconsistency ✅ IMPLEMENTED 2026-06-04
**File:** `services/market-data/src/services/ingestion.py`  
**Severity:** MEDIUM

**What was wrong:**  
The incremental ingest always fetched from `last_bar_date + 1 day`. When yfinance retroactively adjusts all historical prices after a stock split, the old pre-split bars in the DB were never updated. `on_conflict_do_nothing` compounded the issue — even on force-refresh, deleted+re-inserted rows were idempotent but adjusted values never overwrote stale ones mid-session.

**Fix (implemented):**  
Two changes in `ingestion.py`:

1. **7-day lookback overlap for daily bars** — incremental fetches now start 7 days before `head` rather than 1 day after, so any split in the last week triggers a re-download of the affected bars:
```python
overlap = timedelta(days=7) if timeframe == "1d" else timedelta(days=0)
start = head.date() - overlap + timedelta(days=1)
```

2. **`on_conflict_do_update` instead of `on_conflict_do_nothing`** — re-downloaded bars now overwrite stale OHLCV + adj_close values in the DB:
```python
stmt = stmt.on_conflict_do_update(
    index_elements=["stock_id", "ts", "timeframe"],
    set_={"open": ..., "high": ..., "low": ..., "close": ..., "volume": ..., "adj_close": ...},
)
```

Splits older than 7 days are covered by the existing Sunday full force-reingest (delete + re-fetch 3 years). Together, split-adjusted prices are now corrected within one weekly cycle at most.

---

### MEDIUM-4: Prompt Injection Risk in Research Engine ✅ IMPLEMENTED 2026-05-31
**File:** `services/research-engine/src/api/routes.py`  
**Severity:** MEDIUM

**What is wrong:**  
The stock symbol from the URL path was interpolated directly into the Claude prompt without sanitisation. A crafted symbol containing newlines or instruction text could attempt to redirect the AI response.

**Fix (implemented):**  
Added `_sanitise_symbol()` at the module level — strips everything except `[A-Z0-9.\-:]` (covers US tickers, HK `0700.HK`, indices `^VIX`). Applied at the entry point of all four route handlers (GET, DELETE, POST, POST/chat). Invalid symbols return HTTP 400 before any prompt is constructed.

---

### MEDIUM-5: Research Engine Cache Poisoning
**File:** `services/research-engine/src/api/routes.py`  
**Severity:** MEDIUM

**What is wrong:**  
Reports are cached in-memory for 24 hours. If a report is generated with bad input data (yfinance failure, stale prices, AI timeout returning hardcoded fallback scores of 50/50/50), that bad report is served to all users for 24 hours with no indication it is low-quality.

**Fix:**
1. Store a `data_quality` flag alongside each cached report: `"quality": "full" | "partial" | "fallback"`.
2. Display a yellow warning banner in the UI when quality is `"partial"` or `"fallback"`.
3. Add a forced cache-bust endpoint: `DELETE /research/{symbol}/cache` (already partially exists).
4. Auto-invalidate the cache for a symbol whenever a new price bar is ingested for that symbol.

---

### MEDIUM-6: Frontend Strategy Weights Don't Normalise ✅ IMPLEMENTED 2026-06-04
**File:** `frontend/src/pages/opportunities.tsx`  
**Severity:** MEDIUM

**What was wrong:**  
The `scoreFor()` function had formulas where weights didn't sum to 100:
- `all`: missing `Math.min(100, ...)` — could return 108 with a BUY signal bonus
- `aisignal`: `bullish_probability` (0–1) multiplied by 50 while `conf` (0–100) multiplied by 0.70 — raw max was 145 before clamping, so top stocks all clustered at 100 with no differentiation
- `longterm`: upside bonus capped at 25 pts pushed raw max to 110

**Fix (implemented):**  
All formulas now produce genuine 0–100 with verified weight sums:

```typescript
// aisignal: bullish_probability normalised 0-1→0-100 first
const bullPct = (sig?.bullish_probability ?? 0) * 100;
// bullPct*0.45 + conf*0.35 + tech*0.10 + mom*0.10 = max 45+35+10+10 = 100
case 'aisignal': return Math.min(100, Math.round(bullPct * 0.45 + conf * 0.35 + tech * 0.10 + mom * 0.10));
```

Additionally, each opportunity card now displays the strategy score colour-coded alongside the K-Score (e.g. `85 · K72`), so users can see why a stock ranks where it does in the selected strategy.

---

### LOW-1: Zero-Volume Bars Pollute Features ✅ IMPLEMENTED 2026-05-31
**File:** `services/market-data/src/services/ingestion.py`  
**Severity:** LOW

**What is wrong:**  
The OHLCV validation accepts `volume >= 0`. A bar with zero volume (trading halt, data provider error) passes validation and is stored. Zero-volume bars inflate volatility metrics (large price move on no volume) and distort ATR and OBV calculations.

**Fix (implemented):**  
Changed `df = df[df["volume"] >= 0]` to `df = df[df["volume"] > 0]` in `validate_ohlcv()`. Zero-volume daily bars are now rejected at the ingest boundary and never stored in the database.

---

### LOW-2: Stale Price Fetch in Signal Generator ✅ IMPLEMENTED 2026-05-31
**File:** `services/signal-engine/src/generators/signals.py`  
**Severity:** LOW

**What is wrong:**  
The signal generator fetches the most recent 400 bars and assumes the last one is current. No timestamp validation checks whether the data is stale (e.g., fetched during a weekend, market holiday, or service restart after a gap). A signal computed on Friday's close on Monday morning is technically correct but could mislead if conditions have changed.

**Fix (implemented):**  
Added `_check_price_staleness()` in `signals.py`. After fetching prices, if the last bar date is more than 3 calendar days old, a structured log warning is emitted (`signal.stale_price_data` with `last_bar` and `days_old` fields). This makes pipeline data gaps observable in log aggregators without blocking the signal computation.

---

### LOW-3: ATR Calculation Non-Standard
**File:** `services/research-engine/src/services/scoring.py`  
**Severity:** LOW

**What is wrong:**  
The research engine computes ATR using simple moving average of true range, not the standard exponential moving average (Wilder's smoothing). The result is a slightly different number than what traders expect when they reference ATR from any standard platform.

**Fix:**  
```python
def atr(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    # Wilder's smoothing (standard):
    return tr.ewm(alpha=1/period, adjust=False).mean()
```

---

## Part 3 — Features That Would Significantly Differentiate the Platform

These are not bug fixes — they are new capabilities that would meaningfully improve signal quality or the trading workflow.

---

### 3.1 Walk-Forward Backtest Engine
**Priority:** HIGHEST  
**Effort:** 1–2 weeks

**Why it matters:**  
This is the single most important missing piece. Without a backtest, you cannot know whether the signals generate positive expectancy on out-of-sample data or whether you are measuring noise confidently. A walk-forward approach avoids curve-fitting: train on data up to month N, test on month N+1, slide forward, repeat.

**What to build:**
- Endpoint: `POST /backtest` — accepts symbol list, start date, end date, signal settings
- For each bar in the test window: compute what the signal was at market open using only data available at that moment (no future data)
- Record entry (BUY signal), exit (SELL signal or N-day timeout), and actual return
- Aggregate: win rate, average return per trade, max drawdown, Sharpe ratio, signal vs. SPY

**Key output metrics to show:**

| Metric | What it tells you |
|--------|------------------|
| Win rate | What % of BUY signals produce a positive return in horizon days |
| Average return per trade | Expected value of acting on a signal |
| Sharpe ratio | Return per unit of risk (>1.0 is acceptable, >2.0 is strong) |
| Max drawdown | Worst consecutive loss streak from signals |
| Signal vs. SPY | Alpha: does acting on signals beat just holding SPY? |

---

### 3.2 Options Flow Integration
**Priority:** HIGH  
**Effort:** 3–5 days

**Why it matters:**  
Unusual options activity is one of the highest-quality leading signals available to retail traders. Large institutions often build positions in options before moving the underlying. When call volume is 5× the 30-day average with short-dated OTM strikes, it frequently precedes a significant move.

**Data sources:**  
- Quiver Quant API (already have API key in settings)
- Market Chameleon (free tier)
- CBOE public data

**What to add:**
- Options flow score (0–100): weighted by call/put ratio deviation from baseline, OI change, short DTE premium
- Display on stock detail page alongside AI Signal
- Add `options_flow_bullish` as a signal component in the generator (small weight, 5–10%)
- Alert condition: `unusual_call_activity` — fire when call volume > 3× 30-day average

---

### 3.3 Earnings Surprise Model
**Priority:** HIGH  
**Effort:** 3–5 days

**Why it matters:**  
A stock's history of beating or missing analyst EPS estimates is one of the most predictive signals for short-term post-earnings moves. Companies that consistently beat estimates are systematically undervalued by analysts. Companies that consistently miss are systematically overvalued.

**What to build:**
- Fetch last 8 quarters of earnings surprise data from yfinance `earnings_history`
- Compute: beat rate (% of quarters beat), average surprise magnitude, trend (improving/worsening)
- Display on stock detail page in the Fundamentals section
- Use in research engine scoring: consistent beaters get +5 to fundamental score

---

### 3.4 Relative Strength vs. Sector
**Priority:** HIGH  
**Effort:** 2–3 days

**Why it matters:**  
A BUY signal on a stock that is underperforming its sector peers is a weaker signal than a BUY on a stock leading its sector. Relative strength filters out the noise of sector-wide moves and identifies genuine stock-specific alpha.

**What to build:**
- Compute `rs_rank = stock_20d_return / sector_etf_20d_return`
- Add to K-Score as a 6th sub-score (suggest 10% weight, reduce momentum to 20%)
- Add RS column to Rankings table
- Add `rs_above_1` filter to signal generator: if `rs_rank < 0.8`, reduce BUY confidence by 15%

**Sector ETF mapping:**

| Sector | ETF |
|--------|-----|
| Technology | QQQ |
| Financials | XLF |
| Healthcare | XLV |
| Energy | XLE |
| Consumer Discretionary | XLY |
| Industrials | XLI |

---

### 3.5 News Sentiment Layer
**Priority:** MEDIUM  
**Effort:** 3–5 days

**Why it matters:**  
Price moves often have news catalysts. The current system fetches news headlines but only displays them — it does not incorporate sentiment into any signal. Systematically negative news (regulatory action, leadership departure, product recall) should suppress BUY signals even if technicals are strong.

**What to build:**
- Score each news headline using Claude (already in the stack): `POSITIVE / NEGATIVE / NEUTRAL` with magnitude 0–100
- Compute aggregate 7-day news sentiment score per symbol
- Add as a signal modifier: strong negative news (score < 30) compresses AI signal by 20–30%
- Display sentiment bar on stock detail page (green/red gradient)

---

### 3.6 Market Regime Detection (Beyond Binary Bull/Bear)
**Priority:** MEDIUM  
**Effort:** 1 week

**Why it matters:**  
The current market regime is binary: S&P 500 above or below 200-day SMA. Reality has at least four distinct regimes that require different trading approaches:

| Regime | Characteristics | Best strategies |
|--------|----------------|----------------|
| Bull trend | SPY above 200MA, VIX < 18, breadth expanding | Momentum, breakouts, full position size |
| High volatility | VIX > 25, large daily swings, mixed breadth | Reduce size 50%, prefer mean-reversion |
| Bear trend | SPY below 200MA, VIX elevated, declining breadth | Only SELL/HOLD signals, cash or hedges |
| Recovery | SPY crossing back above 200MA, VIX falling | Early-cycle sectors, smaller initial entries |

**What to build:**
- Regime classifier: rule-based (VIX level + SPY vs. 200MA + market breadth index) or HMM
- Store current regime in Redis, update daily post-close
- Signal generator uses regime to set confidence thresholds (not just bull/bear)
- Confluence panel shows current regime with colour coding

---

### 3.7 Position P&L Feedback Loop
**Priority:** MEDIUM  
**Effort:** 1 week

**Why it matters:**  
The application already tracks positions. Every closed position is a labelled training example: the signal at entry, the market conditions, and the actual outcome. Using this data to retrain or adjust signal weights over time creates a closed feedback loop — the system learns from its own track record.

**What to build:**
- After each position closes, log: `{symbol, entry_signal, entry_confidence, entry_confluence, market_regime, actual_return, hold_days}`
- Store in `position_outcomes` table
- Weekly batch job: compute win rate and average return by `(signal, regime)` combination
- Adjust signal thresholds based on track record: if BUY signals in bear regime have 35% win rate, raise bear threshold
- Show on Signal Accuracy page: "Your personal win rate by signal type and market regime"

---

### 3.8 Factor Exposure Analysis
**Priority:** LOW  
**Effort:** 3–5 days

**Why it matters:**  
Without factor exposure analysis, you cannot distinguish between genuine alpha and hidden factor tilts. If all your BUY signals are in high-momentum stocks during a bull market, your "alpha" may disappear when the momentum factor reverses. This is how many systematic strategies fail in live trading.

**What to analyse:**
- Momentum exposure: average 12-month return of signalled stocks at time of signal
- Value exposure: average P/E relative to market at time of signal
- Size exposure: average market cap of signalled stocks
- Volatility exposure: average 60-day vol of signalled stocks

**Display:** A factor bar chart on the Signal Accuracy page showing portfolio tilt vs. SPY baseline.

---

## Part 3B — Signal Accuracy & ML Improvements (2026-06-05 Audit)

A deep audit of the signal fusion pipeline and ML training identified the following concrete improvements, ranked by impact-to-effort. These are incremental changes to existing files — no architectural overhaul required.

---

### SA-1: Lower ML/TA Disagreement Threshold 0.35 → 0.25

**File:** `services/signal-engine/src/generators/signals.py`, lines 765–767  
**Effort:** 1 day  
**Expected gain:** +3–8% accuracy  
**Status:** ⏳ Pending

**What is wrong:**  
When ML probability and TA score disagree, a dampening factor is applied — but only when the gap exceeds 0.35 (35 percentage points). This is a very high bar. A stock where ML says 0.70 and TA says 0.40 (gap = 0.30) passes through undampened even though the disagreement is substantial.

**Fix:**
```python
gap = abs(ml_prob - ta_prob)
if gap > 0.35:
    ml_w *= 0.5
elif gap > 0.25:   # NEW: intermediate dampening band
    ml_w *= 0.75
```
This adds a 25% dampening in the 0.25–0.35 gap range, making the system more conservative when ML and TA are in moderate disagreement — a common early-warning sign of regime transitions.

---

### SA-2: Style-Aware ML Precision Targets

**File:** `services/ml-prediction/src/training/trainer.py`, line 28 + line 82  
**Effort:** 1 day + retrain  
**Expected gain:** +1–3% SHORT accuracy (fewer false positives)  
**Status:** ⏳ Pending

**What is wrong:**  
All four trade horizons (SHORT/SWING/LONG/GROWTH) use the same 60% minimum precision when calibrating the buy threshold. SHORT trades (1–7 day holds) have the least time to recover from false entries — they need tighter precision. LONG trades (90-day holds) have more time to absorb noise and can afford more entries. GROWTH, like SWING, targets 10-day windows.

**Fix:**
```python
_PRECISION_BY_STYLE = {"SHORT": 0.70, "SWING": 0.60, "LONG": 0.50, "GROWTH": 0.60}
```
Use the style-specific floor in `_precision_threshold()` instead of the global `_MIN_PRECISION`. SHORT models calibrate to 70%+ precision (fewer signals, more reliable); LONG models accept 50%+ (more entries, wider net). GROWTH uses SWING's 60% floor.

---

### SA-3: Add 4 Macro Regime Boolean Features to ML

**File:** `services/ml-prediction/src/features/builder.py`, ~lines 242–266  
**Effort:** 3 days + retrain  
**Expected gain:** +3–8% AUC in bear markets, +1–2% overall  
**Status:** ⏳ Pending

**What is wrong:**  
The model receives raw macro values (VIX level, SPY returns, SPY volatility) but no boolean regime flags. The model must implicitly learn that VIX=35 means "fear regime" — but with limited training examples, it under-learns this. Explicit flags give XGBoost a clean decision boundary to split on.

**Fix — add these derived features to the feature builder:**
```python
# After fetching SPY/VIX data:
spy_200d = macro["spy"].rolling(200).mean()
features["is_bear_market"]       = (macro["spy"] < spy_200d).astype(int)
features["vix_spiking"]          = (macro["vix"] > macro["vix"].rolling(20).mean() * 1.3).astype(int)
features["market_breadth_weak"]  = (breadth_pct < 40).astype(int)   # breadth_pct already fetched
features["high_vol_regime"]      = (macro["spy_vol_20"] > 0.02).astype(int)
```
Retrain with `POST /ml/tune_all`. Check feature importance — expect these flags to rank in the top 10 for SWING and LONG horizons.

---

### SA-4: Weekly Alignment — Reduce Minimum Bars 26 → 15

**File:** `services/signal-engine/src/generators/signals.py`, ~line 357  
**Effort:** 1 day  
**Expected gain:** +1–3% for recently-added stocks  
**Status:** ⏳ Pending

**What is wrong:**  
The weekly alignment filter requires 26 weekly bars (6 months of data) before it activates. For stocks added to the watchlist in the last 6 months, the weekly filter is silently bypassed (treated as neutral = no boost/compress). A stock with only 3 months of data can still show a clear weekly downtrend that the filter misses entirely.

**Fix:**
```python
MIN_WEEKLY_BARS = 15          # was 26 (6 months → 3 months)
PARTIAL_WEEKLY_CONFIDENCE = 0.7   # scale boost/compress to 70% for 15–25 bars

if len(weekly_df) >= MIN_WEEKLY_BARS:
    confidence = 1.0 if len(weekly_df) >= 26 else PARTIAL_WEEKLY_CONFIDENCE
    # apply weekly boost/compress multiplied by confidence
```
Stocks with 15–25 weekly bars get 70% of the full weekly adjustment; stocks with ≥26 bars get 100% as before.

---

### SA-5: Data-Driven TA Component Weights (Logistic Regression Calibration)

**File:** `services/signal-engine/src/generators/signals.py`, lines 497–643 (`_ta_score`)  
**Effort:** 1 week  
**Expected gain:** +5–10% accuracy (highest single improvement)  
**Status:** ⏳ Pending

**What is wrong:**  
Every TA component has a hardcoded point allocation (e.g., `above SMA50 = +0.15`, `MACD positive = +0.10`, `OBV confirming = +0.08`) that was manually tuned. There is no empirical evidence that these weights are optimal for this stock universe. A component that rarely precedes profitable moves still gets the same weight as one that reliably does.

**Fix:**
1. For each stock in the universe, compute all 22 TA binary/continuous features at each bar for the last 3 years.
2. Compute the 5-day forward return for each bar.
3. Run logistic regression (or Lasso with L1 penalty): `P(5d_return > threshold) ~ f(ta_features)`.
4. The fitted coefficients become the new TA component weights (normalized so they sum to the same theoretical maximum as the current hand-tuned weights).
5. Validate on held-out 12-month window before applying to production.

This is the highest-impact change but requires careful analysis — don't implement before step 5 validation.

---

### SA-6: Filter Interaction Audit Endpoint

**File:** `services/signal-engine/src/api/routes.py` (new endpoint)  
**Effort:** 1 week  
**Expected gain:** +2–5% win rate (reduces over-suppression)  
**Status:** ⏳ Pending

**What is wrong:**  
When multiple filters stack (e.g., earnings compression + market breadth + news sentiment + ADX choppy), the signal collapses from 0.75 → 0.52 — not even close to the BUY threshold. But there is no data on whether those trades would actually have been bad. They might have been some of the best trades, blocked by over-cautious stacking.

**What to build:**  
New endpoint `GET /signals/filter_audit?lookback_days=180` that:
1. Loads all signals from the last N days with their stored `signal_reasons` JSON.
2. Counts how many compression filters were active per signal (earnings, breadth, news, ADX, weekly, etc.).
3. Cross-references against actual price outcome at the signal horizon (using price data).
4. Returns win rate grouped by filter count: `{"filters_0": 62%, "filters_1": 58%, "filters_2": 51%, "filters_3+": 38%}`.

If 3+ filters → win rate drops to 38%, the `max_compress_ratio` floor should be raised from 0.50 to 0.65 (allow more original signal to survive heavy stacking). If win rate is stable, the current floor is correct.

---

### SA-7: Regime-Aware Earnings Compression

**File:** `services/signal-engine/src/generators/signals.py`, lines 827–842  
**Effort:** 1 week  
**Expected gain:** +2–5% win rate (reduces false suppressions in strong markets)  
**Status:** ⏳ Pending

**What is wrong:**  
Earnings compression is fixed: SWING signals within 2 days of earnings get 50% compressed regardless of market conditions or the stock's earnings history. In a bull market where a sector beats EPS estimates >60% of the time (e.g., technology in 2023–2024), this 50% compression removes many trades that would have gone up on the earnings beat.

**Fix:**
- Fetch each stock's last 8 quarters of earnings surprise data from `yfinance.Ticker(symbol).earnings_history`.
- Compute `beat_rate = beats / total_quarters` (0.0–1.0).
- Scale compression: `effective_compression = base_compression × (1 + (0.5 - beat_rate))`.
  - Consistent beater (beat_rate=0.80): compression eases to 0.50 × 0.70 = 0.35 (35% — less suppressive)
  - Consistent misser (beat_rate=0.25): compression tightens to 0.50 × 1.25 = 0.625 (62.5% — more suppressive)
- Cache beat_rate in Redis with 7-day TTL (updates weekly on fresh earnings data).

---

### Summary Table

| # | Change | File | Effort | Expected Gain | Status |
|---|--------|------|--------|---------------|--------|
| SA-1 | Lower ML/TA disagreement threshold 0.35→0.25 | signals.py 765–767 | 1 day | +3–8% accuracy | ⏳ Pending |
| SA-2 | Style-aware ML precision targets SHORT:70% LONG:50% | trainer.py 28+82 | 1 day + retrain | +1–3% SHORT accuracy | ⏳ Pending |
| SA-3 | Add 4 macro regime boolean features to ML | builder.py 242–266 | 3 days + retrain | +3–8% bear market AUC | ⏳ Pending |
| SA-4 | Weekly alignment min bars 26→15, partial confidence | signals.py ~357 | 1 day | +1–3% new stocks | ⏳ Pending |
| SA-5 | Data-driven TA weights via logistic regression | signals.py 497–643 | 1 week | +5–10% accuracy | ⏳ Pending |
| SA-6 | Filter interaction audit endpoint | routes.py (new) | 1 week | +2–5% win rate | ⏳ Pending |
| SA-7 | Regime-aware earnings compression | signals.py 827–842 | 1 week | +2–5% win rate | ⏳ Pending |

**Recommended implementation order:** SA-1 → SA-4 → SA-2 → SA-3 → SA-6 → SA-5 → SA-7  
(Low-effort quick wins first; data-driven weight changes last, after filter audit validates assumptions.)

---

## Part 4 — Implementation Priority Matrix

### Tier 1 — Fix Before Trusting Signals (Do Now)

| Fix | File(s) | Effort | Impact | Status |
|-----|---------|--------|--------|--------|
| ML calibration (isotonic regression) | ml-prediction/trainer.py | — | Prevents overconfident signals | ✅ Already done |
| K-Score value gate (momentum quality filter) | ranking-engine/kscore.py | 1 day | Removes falling-knife false positives | ✅ Done |
| Macro data Redis caching | ml-prediction/builder.py | 1 day | Prevents silent distribution shift | ✅ Done |
| Look-ahead bias guard | ml-prediction/trainer.py | 0.5 days | Eliminates partially-observed bar contamination | ✅ Done |
| Symbol sanitisation (prompt injection) | research-engine/routes.py | 0.5 days | Security fix | ✅ Done |

### Tier 2 — Analytical Improvements (Next Sprint)

| Fix | File(s) | Effort | Impact | Status |
|-----|---------|--------|--------|--------|
| Sector-relative fundamental scoring | research-engine/routes.py | 3 days | Fixes PE/growth/margin thresholds | ✅ Done 2026-06-04 |
| RSI scoring curve fix | ranking-engine/kscore.py | 0.5 days | More accurate trend stock scoring | ✅ Done |
| adj_close consistency | market-data/ingestion.py | 1 day | Fixes split/dividend distortion | ✅ Done 2026-06-04 |
| Frontend strategy weight normalisation | opportunities.tsx | 0.5 days | Comparable cross-strategy scores | ✅ Done 2026-06-04 |
| Zero-volume bar filtering | market-data/ingestion.py | 0.5 days | Cleaner volatility calculations | ✅ Done |
| Stale price guard | signal-engine/signals.py | 0.5 days | Observable pipeline gaps | ✅ Done |
| Research engine cache quality flag | research-engine/routes.py + frontend/research/[symbol].tsx | 1 day | Prevents serving fallback as real data | ✅ Done 2026-06-04 |

### Tier 3 — New Features (Roadmap)

| Feature | Effort | Expected Signal Quality Improvement | Status |
|---------|--------|--------------------------------------|--------|
| Walk-forward backtest engine | 2 weeks | Validates whether signals generate alpha at all | ✅ Done |
| Options flow integration | 5 days | +15–20% signal accuracy on high-flow events | ✅ Done |
| Factor exposure analysis | 4 days | Distinguishes alpha from factor tilts | ✅ Done |
| Relative strength vs. sector | 3 days | Filters sector-rotation noise from signals | ✅ Done 2026-06-04 |
| Earnings surprise model | 4 days | Better earnings event handling | ⏳ Pending |
| News sentiment layer | 4 days | Suppresses signals ahead of negative catalysts | ⏳ Pending |
| Market regime detection (4-state) | 1 week | Better position sizing across market environments | ⏳ Pending |
| Position P&L feedback loop | 1 week | System learns from its own track record | ⏳ Pending |

### Tier 4 — Signal Accuracy & ML Tuning (2026-06-05 Audit)

| Item | File | Effort | Expected Gain | Status |
|------|------|--------|---------------|--------|
| SA-1: ML/TA disagreement threshold 0.35→0.25 | signals.py | 1 day | +3–8% accuracy | ⏳ Pending |
| SA-2: Style-aware precision targets (SHORT 70%, LONG 50%) | trainer.py | 1 day + retrain | +1–3% SHORT | ⏳ Pending |
| SA-3: 4 macro regime boolean ML features | builder.py | 3 days + retrain | +3–8% bear AUC | ⏳ Pending |
| SA-4: Weekly alignment min bars 26→15 | signals.py | 1 day | +1–3% new stocks | ⏳ Pending |
| SA-5: Data-driven TA weights (logistic regression) | signals.py | 1 week | +5–10% accuracy | ⏳ Pending |
| SA-6: Filter interaction audit endpoint | routes.py | 1 week | +2–5% win rate | ⏳ Pending |
| SA-7: Regime-aware earnings compression | signals.py | 1 week | +2–5% win rate | ⏳ Pending |

### Tier 5 — UI/Feature Gaps (2026-06-06 Audit)

Backend fully implemented for all items below. These are frontend exposure gaps only.

| Item | Effort | Priority | Status |
|------|--------|----------|--------|
| UI-01: Signal Outcomes Dashboard (confidence band win-rate table) | 1–2 days | High | ⏳ Pending |
| UI-02: Signal Reasons / Factor Breakdown ("Why BUY?") | 1–2 days | High | ⏳ Pending |
| UI-03: Options Flow page / stock detail tab | 1 day | Medium | ⏳ Pending |
| UI-04: Insider Buying Screener (net buy conviction filter) | 1 day | Medium | ⏳ Pending |
| UI-05: Earnings Surprise History Chart (8-quarter EPS beat/miss) | 1 day | Medium | ⏳ Pending |
| UI-06: Portfolio Position Heatmap (treemap by $ value, colored by P&L) | 1 day | Medium | ⏳ Pending |
| UI-07: Real-Time Unrealized P&L on Positions (live price × shares) | 1 day | Medium | ⏳ Pending |
| UI-08: Walk-Forward Drill-Down (click window → see signal list) | 1–2 days | Low-Medium | ⏳ Pending |
| UI-09: Data Freshness Indicator (last ingest timestamp in header) | 0.5 days | Low | ⏳ Pending |
| UI-10: ML Weight Auto-Calibration (apply optimal from validation curve) | 1–2 days | Medium | ⏳ Pending |
| UI-11: Factor Exposure Chart in Signal Accuracy page | 0.5 days | Low | ⏳ Pending |
| UI-12: Congressional Trading Page (/congress) | 1 day | Low | ⏳ Pending |
| Tech Debt: Pagination on /signals/accuracy (10k+ rows) | 1 day | Medium | ⏳ Pending |
| Tech Debt: N+1 query in trade_performance (group in SQL not Python) | 1 day | Medium | ⏳ Pending |
| Tech Debt: Redis cache for factor_exposure + walkforward endpoints | 1–2 days | Low | ⏳ Pending |

See **Part 3B** for full specifications and code snippets for each item.

---

## Part 3C — UI/Feature Gaps & Backend Endpoints Without UI (2026-06-06 Audit)

A full audit of all pages, endpoints, and database tables against the frontend identified the following gaps. Backend logic exists for all items — these are primarily UI exposure and feature completeness items.

**Platform stats:** 25 frontend pages, 100+ REST endpoints across 8 microservices. Signal generation → ML prediction → ranking → research pipeline is fully wired. Gaps are concentrated in signal analysis, feedback loops, and data visualization.

---

### UI-01: Signal Outcomes Dashboard *(High Priority)*

**Status:** ⏳ Pending  
**Effort:** 1–2 days  
**Impact:** High — closes the feedback loop between signals issued and actual accuracy

The `signal_outcomes` table tracks every BUY/SELL with entry price, exit price, hold days, pct_return, is_correct, confidence band, ML prob, TA score, and market regime. The `GET /signals/outcomes/summary` endpoint returns win-rate grouped by confidence band (0-40, 40-55, 55-70, 70-85, 85+), horizon, and market regime. **Neither is called by the frontend.** No page shows this data.

**What to build:** Add a new section to `/signal-accuracy` (or a new tab) showing:
- Table: confidence band vs. win rate vs. avg return — confirms or refutes that higher confidence → higher accuracy
- Table: win rate by horizon (SHORT / SWING / LONG)
- Table: win rate by market regime (bull / high_vol / bear)
- Once 500+ outcomes: "Optuna tuning recommended" banner with link to SIGNAL_ACCURACY.md

**API already ready:** `GET /signals/outcomes/summary?horizon=SWING&days=90`

---

### UI-02: Signal Reasons / Factor Breakdown *(High Priority)*

**Status:** ⏳ Pending  
**Effort:** 1–2 days  
**Impact:** High — makes signals explainable and debuggable

Every signal stores a full `reasons` JSON containing RSI, ADX, volume_z, ml_probability, ta_score, news_sentiment, RS score, earnings proximity, breadth %, market regime, and 30+ other factors. This data is **never displayed to the user**. The only place any of it appears is the suppressed signals filter breakdown.

**What to build:** On the stock detail page (or signal-accuracy drill-down):
- "Why BUY?" card: show bulleted list of contributing factors above neutral (RSI < 50, golden_cross, volume surge, etc.)
- Suppression reason inline: if signal was compressed, show which filter and by how much
- Link to `/signals/factor-exposure` chart which already exists in the backend

---

### UI-03: Options Flow Page *(Medium Priority)*

**Status:** ⏳ Pending  
**Effort:** 1 day  
**Impact:** Medium — unusual options activity is a leading indicator for smart money positioning

`GET /stocks/{symbol}/options-flow` endpoint exists and returns unusual call/put volume, put/call ratio, and net sentiment. Signal engine uses options_sentiment as a filter. **No standalone page or stock detail section shows this data.**

**What to build:** A tab on the stock detail page showing:
- Options sentiment gauge (bullish / neutral / bearish)
- Put/call ratio vs. 30-day average
- Unusual activity flag

---

### UI-04: Insider Buying Screener *(Medium Priority)*

**Status:** ⏳ Pending  
**Effort:** 1 day  
**Impact:** Medium — cluster insider buying is one of the strongest signals of management confidence

`GET /stocks/insider` and fundamentals endpoint include insider transaction data. The signals page has an insider page but it only shows raw transaction list. **No screener exists to filter "stocks with heavy insider buying this quarter."**

**What to build:** Add filter to the insider page:
- "Net insider sentiment" column (buy $ vs sell $)
- Sort by insider conviction score (number of distinct insiders buying)
- Merge with K-Score ranking to find "high K-Score + insider buying" combo

---

### UI-05: Earnings Surprise History Chart *(Medium Priority)*

**Status:** ⏳ Pending  
**Effort:** 1 day  
**Impact:** Medium — stocks with consistent EPS beats trade differently around earnings

Fundamentals data includes EPS history and beat/miss records. `earnings_beat_rate` is already computed and used in signal compression. **No chart shows the per-stock EPS surprise trend over time.**

**What to build:** Add to the earnings calendar page or stock detail:
- Bar chart: last 8 quarters EPS estimate vs actual
- Beat rate badge: "Beats 75% of the time (6/8 quarters)"
- Flag stocks whose beat_rate > 70% as "earnings quality" candidates

---

### UI-06: Portfolio Position Heatmap *(Medium Priority)*

**Status:** ⏳ Pending  
**Effort:** 1 day  
**Impact:** Medium — treemap view of holdings by $ value and % gain/loss

The positions page shows a table of holdings. **No treemap or visual allocation view exists.**

**What to build:** Add a treemap/grid above the positions table:
- Each cell = one position, sized by current market value
- Color = % gain (green) / loss (red)
- Hover shows: symbol, shares, avg cost, current price, unrealized P&L

---

### UI-07: Real-Time Unrealized P&L on Positions *(Medium Priority)*

**Status:** ⏳ Pending  
**Effort:** 1 day  
**Impact:** Medium — positions page shows avg cost but doesn't show current price delta

The positions page has `avg_cost` but doesn't fetch live prices to compute unrealized P&L. Users have to cross-reference the markets page manually.

**What to build:**
- On page load, fetch `GET /stocks/latest_prices` for all held symbols
- Compute unrealized P&L per position and total portfolio
- Display daily change, total unrealized gain/loss with color coding

---

### UI-08: Walk-Forward Drill-Down *(Low-Medium Priority)*

**Status:** ⏳ Pending  
**Effort:** 1–2 days  
**Impact:** Medium — walk-forward exists but you can't see which signals drove each window's return

The walk-forward backtest shows accuracy and return per time window. **Clicking a window doesn't show which signals were in it.**

**What to build:** Expand each walk-forward window row to show:
- List of signals evaluated in that window
- Which were correct, which were wrong
- Avg confidence, which factors were most predictive in that window

---

### UI-09: Data Freshness Indicator *(Low Priority)*

**Status:** ⏳ Pending  
**Effort:** 0.5 days  
**Impact:** Low — prevents acting on stale data without knowing it

If the nightly ingest fails, all prices are stale but no indicator tells the user.

**What to build:**
- Show "Last updated: 2h ago" label in the site header or market overview
- Highlight in orange/red if last price update was > 6 hours ago on a trading day
- Already have `GET /stocks/market_overview` which returns timestamps

---

### UI-10: ML Weight Auto-Calibration *(Medium Priority)*

**Status:** ⏳ Pending  
**Effort:** 1–2 days  
**Impact:** High once signal_outcomes accumulates data

The `/signals/ml-weight-validation` endpoint sweeps all ML weights and finds the empirically optimal blend weight. Currently this is only a visualisation — the system does not actually use the optimal weight. The fusion formula in `signals.py` is hardcoded at 0.40–0.75.

**What to build:**
- `POST /signals/calibrate_ml_weight` endpoint that reads the optimal weight from the validation curve and writes it to a config table (or updates `signals.py` config)
- Button in the Signal Accuracy page: "Apply optimal weight (0% ML currently)" with confirmation
- Automatic weekly recalibration in the scheduler

---

### UI-11: Factor Exposure Chart in Signal Accuracy *(Low Priority)*

**Status:** ⏳ Pending  
**Effort:** 0.5 days  
**Impact:** Medium — endpoint exists, just needs frontend wiring

`GET /signals/factor-exposure?lookback_days=90` returns RSI, ADX, volume_z, ML prob, news sentiment, and TA score averaged across correct vs wrong signals. **This endpoint exists in the backend but is never called by the frontend.** The Signal Accuracy page has a "Factor Analysis" section but it fetches this endpoint separately — verify whether it's actually rendering.

---

### UI-12: Congressional Trading Page *(Low Priority)*

**Status:** ⏳ Pending  
**Effort:** 1 day  
**Impact:** Low-Medium — congressional trade disclosures are publicly available and surprisingly predictive

`GET /congress/trades?days=90` endpoint exists. **No dedicated page.**

**What to build:** A simple table page at `/congress`:
- Politician, stock, transaction type (buy/sell), date, amount range
- Filter by stock symbol to see "Has any congressman bought/sold AAPL recently?"

---

### Technical Debt Items

| Item | File | Effort | Priority |
|------|------|--------|----------|
| **Pagination on /signals/accuracy** — can return 10k+ rows, frontend hangs | signal-engine/routes.py | 1 day | Medium |
| **N+1 query in trade_performance** — groups trades by symbol in Python instead of SQL | signal-engine/routes.py:857 | 1 day | Medium |
| **Redis cache for heavy endpoints** — factor_exposure, walkforward, filter_audit re-compute every request | signal-engine/routes.py | 1–2 days | Low |
| **ML weight range hardcoded** — `current_formula_range: [0.40, 0.75]` in routes.py:507 should read from config | signal-engine/routes.py | 0.5 days | Low |
| **Hold windows hardcoded** — `_OUTCOME_HOLD_DAYS = {"SHORT": 7, "SWING": 14, "LONG": 28}` should be config | signal-engine/routes.py:1608 | 0.5 days | Low |
| **WAIT signal handling inconsistent** — trade_performance handles WAIT optionally; signal_accuracy ignores WAIT | signal-engine/routes.py | 0.5 days | Low |

---

## Part 6 — Improvements Batch 2026-06-04/07 (Tier 2–4 Complete)

This section documents every improvement shipped in the second major batch. The first batch (2026-05-31 to 2026-06-05) covered Tier 1 critical fixes and SA-8. This batch closes out all remaining analytical and UI gaps identified in the expert review.

---

### SA-1: ML/TA Conflict Weighting ✅ Shipped 2026-06-05

**File:** `services/signal-engine/src/generators/signals.py`

**What changed:** When the ML model and the TA score disagree by more than 25 percentage points, the ML weight is cut by 25% (`ml_w *= 0.75`). Previously, a high-AUC model could override strong TA signals regardless of disagreement magnitude.

**Why it matters for trading:** ML models can be confidently wrong, especially in regime transitions the training data didn't capture. When ML says "bullish 0.75" and TA says "bearish 0.48", that disagreement is itself information — the system now treats it as a signal to reduce ML's influence rather than letting one dominate.

---

### SA-2: Style-Specific Precision Thresholds ✅ Shipped 2026-06-05

**File:** `services/ml-prediction/src/training/trainer.py`

**What changed:** Minimum precision (positive predictive value) is now enforced per trading style before a BUY fires: SHORT = 70%, SWING = 60%, LONG = 50%.

**Why it matters for trading:** A SHORT trade expires in days — you need high conviction or you take a guaranteed loss. A LONG trade has 4 weeks to play out, so you can accept lower base precision. Previously the same threshold applied to all styles, meaning SHORT trades were under-screened relative to their time risk.

---

### SA-3: Macro Boolean ML Features ✅ Already Live (Confirmed 2026-06-07)

**File:** `services/ml-prediction/src/features/builder.py`

**What was confirmed:** Four regime boolean features were already in `FEATURE_COLUMNS` and flowing to XGBoost training: `is_bear_market` (SPY < 200d SMA), `vix_spiking` (VIX > 20d MA × 1.3), `high_vol_regime` (realized vol > 2%), `market_stress` (SPY 5d ret < -3% and VIX elevated). These give the model explicit decision boundaries for regime states rather than requiring it to infer them from raw VIX and SPY return numbers.

**Why it matters for trading:** Bear markets and volatility spikes are when signals fail most often. A model that has a clean "yes/no this is a bear market" input will calibrate its BUY probability appropriately, rather than interpolating across continuous VIX values where the training data may not have enough bear-market examples to learn the non-linearity.

---

### SA-4: Weekly Alignment Min Bars 26→15 ✅ Shipped 2026-06-05

**File:** `services/signal-engine/src/generators/signals.py`

**What changed:** Weekly trend confirmation previously required 26 weekly bars (6 months). Stocks with fewer bars skipped the weekly gate. Now requires 15 bars minimum (3.5 months), with graduated confidence scaling: `weekly_confidence = 0.70 + (len - 15) / (26 - 15) × 0.30`.

**Why it matters for trading:** Newer listings, post-split stocks, and recently added watchlist stocks now get a weekly trend check rather than being evaluated without one. The graduated scale means a 15-bar stock doesn't get the same weight as a 26-bar stock — confidence builds as history grows.

---

### SA-5: TA Weights Auto-Calibration on Sunday Schedule ✅ Shipped 2026-06-07

**File:** `services/market-data/src/services/scheduler.py`

**What changed:** `_weekly_full_refresh()` now calls `POST /signals/calibrate_ta_weights` every Sunday after tune_all. The endpoint fits logistic regression on TA sub-features vs `is_correct` from `signal_outcomes` history and writes `ta_weights.json`. These fitted weights replace the hand-tuned defaults in the next signal generation cycle.

**Why it matters for trading:** TA weights (RSI 15%, momentum 15%, trend 20%, etc.) were manually set and never validated against outcomes. In a momentum-driven market, momentum weight should be higher. In a mean-reversion market, RSI should dominate. Weekly auto-calibration adapts the weights to what has actually been working over the past 90 days.

---

### SA-6: Filter Interaction Audit Endpoint ✅ Already Live

**File:** `services/signal-engine/src/api/routes.py`

**What is there:** `GET /signals/filter_audit` analyses win rate by number of active suppression filters and by specific filter combination. Once `signal_outcomes` accumulates 500+ rows (approximately 3–6 months at current signal volume), this endpoint can identify any filter that consistently reduces win rate when applied — a net-negative gate that should be disabled or inverted.

---

### SA-7: Regime-Aware Earnings Compression ✅ Shipped 2026-06-07

**File:** `services/signal-engine/src/generators/signals.py`

**What changed:** The earnings proximity compression (`earnings_compression` parameter in style profiles) is now modulated by both market regime and the stock's historical earnings beat rate. Four distinct paths:

| Condition | Effect |
|-----------|--------|
| Bull regime + beat_rate ≥ 70% | Skip compression entirely; +3% boost to fused signal |
| Bull regime + beat_rate 50–70% | `beat_scale = 2.0` — compression halved |
| Bear / high_vol regime | `beat_scale = 0.75 + 0.25 × beat_rate` — compression tightened |
| Unknown regime or no beat history | Original ±20% formula (beat_scale 0.80–1.20) |

**Why it matters for trading:** In a bull market, stocks that consistently beat earnings (NVDA, META historically) often gap up 8–15% the day after earnings. Suppressing the signal by 40–50% in that environment means missing the best entries of the year. Conversely, in a bear market even earnings beats tend to fade within a week — keeping compression high there is correct. One-size-fits-all compression was wrong in roughly half of all regime+history combinations.

---

### SA-8: ML Overhaul (Previously Documented, 2026-06-05) ✅

See original entry above. Key items: 34 features (was 26), 5× recency weighting, style-specific training horizons (SHORT=5d, SWING=10d, LONG=20d), AUC floor (ml_weight=0 when AUC < 0.52), SWING thresholds recalibrated, `signal_outcomes` tracking launched.

---

### Tier 2: S/R Context Detection ✅ Shipped 2026-06-04

**File:** `services/signal-engine/src/generators/signals.py`

**What changed:** `_sr_context()` detects swing pivots and 52-week high/low. Produces a flag: `breakout` (+5% boost), `at_support` (+3% boost), `at_resistance` (−15% compression), `neutral` (no change). The `sr_flag` is stored in `reasons` and shown in SignalCard.

**Why it matters for trading:** A BUY signal at the 52-week high resistance has a much lower probability of follow-through than the same signal after breaking above it. A BUY at a multi-month support level has a natural stop-loss reference point (just below support) and a statistically higher bounce probability. The system now knows the difference.

---

### Tier 2: ATR-Based Position Sizer ✅ Shipped 2026-06-04

**Files:** `services/market-data/src/api/routes.py` + `frontend/src/pages/stock/[symbol].tsx`

**What changed:** `GET /stocks/{symbol}/atr` endpoint computes 14-period Wilder ATR. On the stock detail page, the PositionSizer component reads account size and risk % from Settings, then calculates: stop price (entry − 2 × ATR), shares to buy (risk $ / (2 × ATR per share)), dollar risk, and reward-to-risk ratio.

**Why it matters for trading:** Volatility-based position sizing is the professional standard. A stock with low ATR (stable blue chip) warrants more shares; a stock with high ATR (volatile small-cap) warrants fewer. Without this, position size is arbitrary — you might put the same dollar amount into AAPL and a biotech, taking 5× more risk in the biotech without realising it. This enforces consistent 1–2% account risk per trade mathematically.

---

### Tier 2: Rolling Accuracy Drift Detection ✅ Shipped 2026-06-04

**Files:** `services/signal-engine/src/api/routes.py` + `frontend/src/pages/signal-accuracy.tsx`

**What changed:** `GET /signals/rolling_accuracy?window=30&lookback_days=180` returns a time series of 30-day rolling BUY accuracy with a `drift_warning` flag when accuracy drops below 55%. A line chart with 50%/55% reference lines appears on the Signal Accuracy page.

**Why it matters for trading:** Signal accuracy degrades when the market regime shifts and the model hasn't re-adapted. Without a drift monitor, you would keep trading on signals whose win rate had already fallen to coin-flip levels. The drift warning tells you: "the model is underperforming — wait for the next Optuna retrain before adding size."

---

### Tier 2: Peer Comparison Drawer ✅ Shipped 2026-06-04

**Files:** `frontend/src/pages/rankings.tsx` + `frontend/src/pages/stock/[symbol].tsx`

**What changed:** `PeerCompareDrawer` shows side-by-side K-Score and all sub-scores (value, momentum, quality, technical) for up to 4 stocks, with green/red cell coding. On Rankings, a "Compare (N)" button opens the drawer for multi-selected rows. On the stock detail page, the top 3 same-sector peers are auto-suggested with a Compare button.

**Why it matters for trading:** When choosing between two similar stocks in the same sector (e.g. AAPL vs MSFT), you want to see who scores better on each sub-factor rather than just the aggregate. The drawer makes this a 2-second check instead of navigating between pages.

---

### Tier 3: Portfolio Risk Quantification ✅ Shipped 2026-06-04

**Files:** `services/signal-engine/src/api/routes.py` + `frontend/src/pages/board.tsx`

**What changed:** `GET /portfolio/risk` computes: Wilder beta vs SPY (US positions) and ^HSI (HK positions), parametric 1-day 95% VaR in dollars, 30-day return correlation matrix across all positions, and sector concentration %. The Trade Board shows a risk section auto-populated from active positions: sector pie chart, correlation heatmap, beta + VaR stat cards, per-symbol betas, and warning chips for high correlation, concentration, or VaR.

**Why it matters for trading:** Most retail traders discover they have too much concentration only after a sector-wide drawdown. Knowing your portfolio beta before a bad day tells you how much you'll bleed if SPY drops 2%. Seeing that 6 of your 8 positions have correlation > 0.7 tells you your "diversification" is illusory — you're effectively running one concentrated bet.

---

### Tier 3: DCF Valuation ✅ Shipped 2026-06-04

**File:** `services/research-engine/src/api/routes.py`

**What changed:** 2-stage DCF integrated into the research report: Stage 1 projects free cash flow for 5 years using analyst growth rate (or trailing 3-year CAGR as fallback). Stage 2 applies Gordon Growth terminal value (terminal growth 3%, WACC 10% default). Discounts to present value. Returns `dcf_fair_value`, `dcf_margin_of_safety_pct`. If DCF and K-Score fair values agree within 15%, a "High conviction" badge appears.

**Why it matters for trading:** K-Score fair value is derived from a multiple-based approach (sector P/E, EV/EBITDA). DCF is derived from cash flow fundamentals. When two independent valuation methods agree that a stock is undervalued, the signal is stronger than either alone. The margin of safety percentage tells you your downside cushion — a stock at 30% discount to DCF can absorb significant bad news before you're underwater.

---

### Tier 3: Walk-Forward Backtest ✅ Shipped 2026-06-04

**Files:** `services/signal-engine/src/api/routes.py` + `frontend/src/pages/signal-accuracy.tsx`

**What changed:** `GET /signals/walkforward` runs non-overlapping test windows over historical persisted signals. For each window: evaluates accuracy, computes compounded equity curve, Sharpe ratio, and max drawdown. Benchmarks against SPY or ^HSI. The Signal Accuracy page has a "Walk-Forward" tab showing: test/hold controls, stat cards (accuracy, Sharpe, return, drawdown, profitable windows %), per-window accuracy heatmap, equity curve vs benchmark, and an alpha interpretation chip.

**Why it matters for trading:** In-sample accuracy metrics are always flattering — the model sees the data it trained on. Walk-forward testing simulates real experience: the model has never seen the test period. A walk-forward Sharpe > 1.0 indicates genuine alpha. If walk-forward accuracy is materially lower than reported accuracy, the model is overfitting and you should not trade on its signals at full size.

---

### UI-01: Signal Outcomes Dashboard ✅ Shipped 2026-06-06

**Files:** `frontend/src/pages/signal-accuracy.tsx` + `frontend/src/lib/api.ts`

**What changed:** "Outcomes" tab added to `/signal-accuracy`. Calls `GET /signals/outcomes/summary`. Displays: overall win rate, avg return, median return; confidence band table (0–40, 40–55, 55–70, 70–85, 85+) with win rate bars; breakdown by horizon (SHORT/SWING/LONG); breakdown by market regime; Optuna tuning guidance banner when outcomes table has enough rows.

**Why it matters for trading:** This is the feedback loop. Every BUY/SELL signal is tracked in `signal_outcomes` with its actual outcome (did price reach target within hold window?). The Outcomes tab is where you verify: does 70–85% confidence actually translate to ~70% win rate? If not, the system is miscalibrated and Optuna should be run. Without this tab, confidence numbers are assertions — with it, they're verified claims.

---

### UI-04: Insider Conviction Screener ✅ Shipped 2026-06-07

**File:** `frontend/src/pages/insider.tsx`

**What changed:** `convictionScores` useMemo groups all insider transactions by ticker over the trailing 90 days. Computes net buy $ (buys minus sells), distinct buyer count, distinct seller count, buy count, sell count. Conviction Screener table shows top 15 tickers by net buy $, with "Net buyers only" toggle, linked tickers, and green conviction bars sized by net buy amount.

**Why it matters for trading:** Insider buying — especially cluster buying (multiple insiders buying simultaneously) — is one of the few genuine information edges retail investors have legal access to. It indicates management believes the stock is undervalued at the current price. Previously you had to scroll hundreds of raw transaction rows to find clustered buying. The screener surfaces it in seconds.

---

### UI-06: Portfolio P&L Heatmap ✅ Shipped 2026-06-07

**File:** `frontend/src/pages/positions.tsx`

**What changed:** Flexbox heatmap grid added above the chart section on the positions page. Each cell represents one position, sized proportionally by market value (minimum 4% width). Cell color: green for profit, red for loss, with alpha intensity proportional to P&L % magnitude (0.08 at breakeven to 0.38 at ±15%). Tooltip shows symbol, P&L %, and P&L in dollars.

**Why it matters for trading:** Reading a table row by row to understand portfolio composition is slow. The heatmap answers "where is my money and how is it doing?" in one glance. A cell taking 40% of the visual space that's dark red tells you immediately that your largest position is your biggest loser — before you've read a single number.

---

### UI-09: Data Freshness Chip ✅ Shipped 2026-06-07

**Files:** `frontend/src/pages/_app.tsx` + `services/market-data/src/api/routes.py`

**What changed:** `GET /stocks/data_freshness` queries `MAX(Price.ts)` from the Price table (daily bars only) and returns `last_bar_ts`, `hours_ago`, and `status` (fresh/stale/very_stale). The site header shows a colored chip polling this every 5 minutes: green ("Xh ago") when data is < 8 hours old, yellow for 8–30 hours, red for > 30 hours.

**Why it matters for trading:** If the nightly yfinance ingest fails silently — EC2 disk full, yfinance outage, network timeout — all prices and signals remain from the previous session. Without the freshness chip, you might act on a BUY signal generated from yesterday's data during a significant overnight gap move. The chip makes data staleness visible before you open a position.

---

### Nginx Research Timeout Fix ✅ Shipped (EC2, 2026-06-06)

**File:** `/etc/nginx/conf.d/stockai.conf` (EC2)

**What changed:** Added a dedicated `location /api/research/` block with `proxy_read_timeout 150s` and `proxy_send_timeout 150s`. The default `location /` block had 30s, which was causing NetworkErrors for AI research reports that take 60–90 seconds to generate.

**Why it matters for trading:** Research reports are used for pre-trade due diligence (DCF fair value, business quality, bear thesis). If the report fails with a NetworkError, you make the trade without the analysis. The fix ensures reports always complete.

---

## Part 5 — What Would Make This a Serious Trading Tool (9/10)

The gap between 6.5 and 9.0 is closed by three things:

**1. A validated backtest showing positive expectancy**  
Until you can show that BUY signals have produced positive average returns on out-of-sample data (not the data the model was trained on), you cannot know if the system is working or just measuring noise confidently. The walk-forward backtest engine is the most critical addition.

**2. Calibrated probabilities**  
Every confidence percentage displayed in the UI and used in the confluence score should reflect true probabilities. A signal showing "78% confidence" should be right approximately 78% of the time. Without Platt scaling, this number is meaningless.

**3. A feedback loop from real trades**  
The position tracking is already there. Connecting closed trade outcomes back to the signal engine — so the system can learn which signals work in which regimes — would turn StockAI from a static alert system into a continuously improving one. This is the core of what separates systematic trading desks from retail tools.

---

## Appendix — Quick Reference: Methodology Notes

### Why calibration matters
XGBoost, like most gradient-boosted classifiers, outputs scores (not true probabilities). The raw score is a function of the margin from the decision boundary — it is monotonically related to the true probability but not equal to it. A model with 70% raw output might only be correct 58% of the time. Platt scaling fits a logistic regression on top of the raw scores using a held-out validation set, transforming raw scores into true probability estimates.

### Why walk-forward beats in-sample testing
In-sample testing (evaluating on the same data you trained on) always shows good results — the model memorises the training set. Walk-forward testing simulates the real experience: the model has never seen the test data, so it cannot memorise it. A model that is profitable in walk-forward testing has genuinely learned predictive patterns, not historical noise.

### Why sector-relative thresholds are correct
A P/E ratio only has meaning relative to alternatives. A utility company at 14× P/E is reasonably valued — utilities trade at 12–16× because their earnings are stable but not growing. A technology company at 14× P/E is deeply discounted — tech typically trades at 20–35× because the market prices in growth. Treating both with the same threshold penalises the correctly-priced utility and rewards the cheaply-valued tech company, inverting the correct interpretation.

### Why the value sub-score needs a momentum gate
The value proxy (discount from 52-week high) is designed to find stocks that have pulled back from their highs temporarily. It works well when the pullback is caused by temporary sentiment or sector rotation. It fails when the pullback is caused by fundamental deterioration. The momentum sub-score is a proxy for whether the company's fundamentals are still intact — a company in fundamental decline will show sustained momentum below 25. Requiring a minimum momentum score before awarding value points prevents value-traps from surfacing.
