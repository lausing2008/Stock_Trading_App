# AI Signal — How It Works & How to Read It

Source: [`services/signal-engine/src/generators/signals.py`](../services/signal-engine/src/generators/signals.py)
ML training: [`services/ml-prediction/src/training/trainer.py`](../services/ml-prediction/src/training/trainer.py)

Last updated: 2026-06-06 (SA-8 accuracy improvements)

---

## What the signal is

The AI Signal is a **BUY / HOLD / WAIT / SELL** label backed by a **Bullish Probability** (0–100%) and a **Confidence** score (0–100). It fuses two independent layers of analysis:

1. **Technical Analysis (TA)** — nine price-based indicators computed from up to 400 daily bars
2. **Machine Learning (ML)** — an XGBoost + Random Forest ensemble trained per-symbol on the stock's own history

Both layers produce a single number called the **fused bullish probability** (0–1). That probability is then filtered through style-specific compression factors and converted into a signal label based on the current market regime.

Every stock carries three signals simultaneously — one per trading style (SHORT, SWING, LONG). All three are computed in a single data-fetch pass for efficiency.

---

## Trading Styles

| Style | Horizon | Character |
|-------|---------|-----------|
| **SHORT** | 1 – 5 days | Pure technical momentum. No earnings or news compression. Ideal for volatile stocks where fundamentals don't apply short-term. |
| **SWING** | 5 – 20 days | Balanced TA + ML. Standard earnings and news filters. Default for most stocks. |
| **LONG** | 30 – 90 days | Fundamentals-heavy. K-Score boost applied. Strong weekly alignment required. Designed for position trades. |

### Style profile parameters

| Parameter | SHORT | SWING | LONG |
|-----------|-------|-------|------|
| **ML weight cap** | 30% | 75% | 45% |
| **BUY threshold — bull market** | 0.60 | **0.62** | 0.60 |
| **BUY threshold — high-vol** | 0.65 | **0.67** | 0.65 |
| **BUY threshold — bear market** | 0.68 | **0.70** | 0.70 |
| **HOLD threshold — bull** | 0.46 | 0.50 | 0.46 |
| **HOLD threshold — bear** | 0.52 | 0.56 | 0.54 |
| **ADX filter (min trending)** | 25 | **15** | off |
| **ADX compression** | 0.85× | 0.90× | — |
| **High-vol compression** | 0.92× | 0.85× | 0.90× |
| **Breadth compression** | off | 0.90× | 0.92× |
| **Weekly align boost / compress** | 1.08× / 0.93× | 1.12× / 0.85× | 1.18× / 0.80× |
| **Earnings compression (≤2d / ≤5d / ≤10d)** | off | 0.50× / 0.75× / 0.90× | off |
| **News compression (score < 25 / < 35)** | off | 0.75× / 0.85× | off |
| **RS compression** | 0.90× | 0.85× | 0.80× |
| **K-Score boost** | off | off | **on** |
| **Max compression floor** | 0.70 | 0.55 | 0.65 |

**ML weight cap** controls how much the ensemble probability can dominate. SHORT keeps ML at ≤ 30% because short-term price movements are noisier — TA momentum is more actionable. SWING allows ML up to 75% when the model has high AUC. LONG caps ML at 45% to let weekly alignment and K-Score have meaningful weight.

**Max compression floor** prevents stacked filters from making a BUY mathematically impossible. If all filters combined would compress the fused probability below this floor, the system restores the probability to the floor before applying thresholds.

---

## Full calculation pipeline — step by step

```
Daily price history (last 400 bars)
         │
         ├─► Stage 1: TA Score  (9 indicators → probability 0–1)
         │
         ├─► Stage 2: ML Prediction  (XGBoost + RF ensemble → bullish_probability 0–1)
         │
         ▼
Stage 3: Fusion  →  fused = ml_weight × ml_prob + (1 − ml_weight) × ta_prob
         │
         ▼
Stage 4: Style Filters (applied separately for SHORT, SWING, LONG)
         │  weekly alignment, ADX, regime, breadth, patterns, earnings,
         │  news, relative strength, options flow, K-Score, stale/bar-count
         ▼
Stage 5: Threshold Decision  →  fused > buy_t = BUY, fused < sell_t = SELL
         │
         ▼
Stage 6: Confidence  →  |fused − 0.5| × 200
```

---

## Stage 1 — Technical Analysis Score

The TA score is a weighted sum of nine independent indicators, each scored 0→1 (bearish→bullish). The result is a single probability from 0–1, where 0.5 = perfectly neutral.

### 1. SMA trend alignment (up to +0.35)

| Condition | Score | What it means |
|-----------|-------|---------------|
| Price above SMA(50) | +0.15 | Short-term trend is up |
| SMA(50) above SMA(200) | +0.10 | Medium-term trend is up |
| Golden cross just fired | +0.10 | SMA(50) crossed above SMA(200) |
| Death cross just fired | −0.10 | SMA(50) crossed below SMA(200) |

### 2. RSI 14-period (up to +0.15)

Uses Wilder's exponential smoothing — matches TradingView, Bloomberg, ThinkOrSwim.

| RSI range | Score | Zone |
|-----------|-------|------|
| 45–65 | +0.15 | Ideal entry zone |
| 35–45 | +0.08 | Oversold recovery |
| 65–72 | +0.06 | Extended but not extreme |
| > 72 | 0 | Overbought — pullback risk |
| < 35 | 0 | Extreme oversold — too early |

### 3. Stochastic RSI (up to +0.10, down to −0.08)

| Condition | Score | Signal |
|-----------|-------|--------|
| Stoch RSI K < 0.20 | +0.10 | RSI at low extreme — potential dip entry |
| Stoch RSI K just crossed up through 0.20 | +0.05 | Fresh oversold recovery |
| Stoch RSI K > 0.80 | −0.08 | RSI stretched — upside may be limited |

### 4. RSI divergence (up to ±0.10)

Detected over a 10-bar lookback. Only computed when ≥ 50 bars of history exist.

| Divergence | Score | What it means |
|------------|-------|---------------|
| Bearish: price higher, RSI lower | −0.10 | Momentum fading as price rises |
| Bullish: price lower, RSI higher | +0.08 | Momentum recovering as price falls |
| None | 0 | No divergence |

### 5. MACD (up to +0.20)

| Condition | Score | Signal |
|-----------|-------|--------|
| MACD histogram > 0 AND rising | +0.15 | Momentum accelerating upward |
| MACD histogram > 0 (not rising) | +0.08 | Upward momentum, but slowing |
| MACD line just crossed zero from below | +0.05 | Trend-direction confirmation |

### 6. Bollinger Bands %B (up to +0.10)

| %B | Score | Zone |
|----|-------|------|
| 0.20 to 0.80 | +0.10 | Middle zone — not at an extreme |
| < 0.20 or > 0.80 | 0 | Near band — overextended or breaking down |

### 7. ADX — trend strength (up to +0.10)

| Condition | Score | Signal |
|-----------|-------|--------|
| ADX > threshold AND DI+ > DI− | +0.10 | Strong upward trend |
| ADX > threshold AND DI+ ≤ DI− | 0 | Strong trend, but downward |
| ADX ≤ threshold | 0 (compressed) | Ranging — signals less reliable |

ADX threshold: 25 for SHORT, **15 for SWING** (captures early-trend entries), off entirely for LONG.

### 8. OBV (up to +0.10)

| Condition | Score | Signal |
|-----------|-------|--------|
| OBV 10-day avg > OBV 30-day avg | +0.10 | Net volume flow is bullish |

### 9. Volume Z-score (up to +0.05)

| Condition | Score | Signal |
|-----------|-------|--------|
| Volume > 0.5σ above 20-day average | +0.05 | Above-average participation |

---

## Stage 2 — Machine Learning Prediction

The ML layer uses a per-symbol **XGBoost + Random Forest ensemble** trained on the stock's own price and macro history.

### What it predicts

The target label is: **did the stock's close price rise by more than its volatility-adjusted threshold within the hold horizon?**

The horizon varies by trading style — predictions are trained on data that matches how the signal will actually be used:

| Style | Training horizon | Rationale |
|-------|-----------------|-----------|
| SHORT | 5 trading days | Matches the 1–5 day intended hold |
| SWING | 10 trading days | Matches the 1–4 week intended hold |
| LONG | 20 trading days | Matches the 1–3 month intended hold |

```
label = 1  if forward_return_N > threshold
label = 0  if forward_return_N < −threshold
(dead-zone rows where |return| < threshold are dropped from training)
```

The threshold is roughly `0.5 × daily_vol × √N`, clipped to [0.5%, 3%]. This removes noise from tiny moves that are indistinguishable from random fluctuation.

### 34 input features

| Category | Features |
|----------|----------|
| Momentum | 1d, 5d, 10d, 20d, 60d returns; `momentum_12_1` (12m−1m return, avoids short-term reversal) |
| Volatility | 20d and 60d rolling vol, ATR(14) as % of price, ATR vs its 20d avg |
| Trend | Price/SMA(20), /SMA(50), /SMA(100), /SMA(200) gap ratios |
| Range | 20-day high/low position; `dist_52w_high` (breakout proximity); `dist_52w_low` (support proximity) |
| Oscillators | RSI(14), MACD, MACD signal, MACD histogram (all normalised by price), Bollinger %B, Stochastic K |
| Volume | Volume Z-score vs 20d avg; OBV 20d change Z-score; CMF(20) |
| Macro — raw | SPY 1d & 5d returns; VIX level; SPY 20d realised vol |
| Macro — regime | `is_bear_market`, `vix_spiking`, `high_vol_regime`, `market_stress` (binary flags) |

The four macro regime flags (`is_bear_market` etc.) were silently always-zero prior to SA-3 due to a pandas index alignment bug — they are now correctly computed.

### Training pipeline

1. **Data prep** — load up to 5 years of adjusted closes. Filter to fully-closed bars only (today's bar excluded). Drop dead-zone rows.
2. **Cross-validation** — time-series walk-forward CV to measure AUC stability.
3. **Three-way temporal split** — 70% train / 15% calibration / 15% test. Strictly time-ordered — no shuffle.
4. **Recency weighting** — recent training bars are given higher sample weights so the model adapts to recent market character.
5. **XGBoost early stopping** — trained with early stopping on the calibration set to prevent overfitting.
6. **Isotonic regression calibration** — fit on the calibration set. Maps raw XGBoost scores into true probabilities. A raw score of 0.72 that actually corresponds to only 60% historical hit-rate will be calibrated to 0.60.
7. **Precision-optimised threshold** — tested on the holdout set. Finds the lowest probability threshold where precision ≥ 60% and recall ≥ 5%, ensuring BUY signals hit at least 3-in-5 before being called a buy.
8. **Random Forest** is trained in parallel with the same pipeline and calibration.

### Ensemble fusion

```
ensemble_prob = w_xgb × xgb_calibrated_prob + w_rf × rf_calibrated_prob
```

Each model's weight (`w_xgb`, `w_rf`) is proportional to its test-set AUC. The higher-AUC model gets more vote. The result is the `bullish_probability` returned by the ML service.

### Dynamic ML/TA blending weight

The weight given to the ML probability in the overall signal depends on how good the model is:

```
if test_auc < 0.52:
    ml_weight = 0.0   # near-random model — fall back to TA only
else:
    ml_weight = clip(0.40 + (test_auc − 0.50) / 0.20 × 0.35, 0.40, 0.75)
```

| Model test AUC | ML weight | Interpretation |
|---------------|-----------|---------------|
| < 0.52 | **0%** | Near-random — TA-only fallback |
| 0.52 | 40% | Just above floor |
| 0.55 | 49% | Weak model |
| 0.60 | 58% | Decent model |
| 0.65 | 66% | Good model |
| 0.70+ | 75% (capped) | Strong model — ML dominates |

This weight is further capped by the style's `ml_weight_cap` (e.g. SHORT caps at 30%). When `ml_weight = 0`, the signal is TA-only and `reasons["ml_weight"]` will be `0.0` in the response.

### ML/TA conflict dampening

If the ML probability and TA score disagree by more than 35 percentage points, the ML weight is reduced by up to 25%. This prevents one overconfident model from overriding a clear signal from the other:

```
if |ml_prob − ta_prob| > 0.35:
    ml_weight *= 1.0 − 0.25 × min((gap − 0.35) / 0.30, 1.0)
```

### Cold-start fallback

If no model file exists (new stock, never trained):
- ML probability = `None`
- Signal falls back to 100% TA score
- `reasons["ml_weight"] = 0.0` in the API response
- Visible on the SignalCard when `ml_probability` is null

---

## Stage 3 — Fusion

```python
if ml_prob is not None:
    fused = ml_weight × ml_prob + (1 − ml_weight) × ta_prob
else:
    fused = ta_prob   # pure TA — ML service down or not trained
```

At this point `fused` is a raw blend of the two layers, ranging 0–1.

---

## Stage 4 — Style-Specific Filters (applied in order)

After fusion, ten additional filters compress or boost the probability. They are applied **sequentially** — each one modifies the output of the previous.

### Filter 1 — Weekly multi-timeframe alignment

Weekly bars are resampled from the daily history. The current (incomplete) week is always excluded to avoid using partial data. A simplified weekly TA score (weekly RSI + SMA20 + MACD) determines the weekly direction.

```
if daily_direction == weekly_direction:
    fused = 0.5 + daily_direction × weekly_boost   (e.g. 1.12× for SWING)
else:
    fused = 0.5 + daily_direction × weekly_compress (e.g. 0.85× for SWING)
```

LONG has the strongest weekly requirement (0.80× compress) — a daily BUY against a weekly downtrend is compressed aggressively.

### Filter 2 — ADX choppy-market compression

SHORT and SWING only. If ADX < threshold (25 or 20), the signal is in a ranging market where TA indicators are unreliable:
```
fused = 0.5 + (fused − 0.5) × adx_compression
```

### Filter 3 — High-volatility regime

When the market regime is `high_vol` (VIX elevated despite SPY holding up):
```
fused = 0.5 + (fused − 0.5) × high_vol_compression
```
Buy and sell thresholds are also raised (see Stage 5).

### Filter 4 — Market breadth compression

When fewer than 40% of tracked stocks are above their 200-day SMA (weak breadth). Applied in **all regimes** including bear — a BUY signal during broad market weakness deserves skepticism regardless of trend:
```
fused = 0.5 + (fused − 0.5) × breadth_compression
```

SWING uses 0.90×, LONG uses 0.92×. SHORT skips this filter.

### Filter 5 — Candlestick pattern adjustment

Recognised reversal patterns add a small probability adjustment (±5–8%). Bullish engulfing, morning star, hammer etc. boost toward bullish; bearish patterns compress.

### Filter 6 — Earnings proximity (SWING only)

| Days to earnings | Multiplier | Rationale |
|-----------------|-----------|-----------|
| ≤ 2 days | 0.50× | Binary event risk — results tomorrow |
| ≤ 5 days | 0.75× | Earnings this week |
| ≤ 10 days | 0.90× | Elevated uncertainty |
| > 10 days | 1.0× | No adjustment |

SHORT and LONG skip this filter entirely.

### Filter 7 — News sentiment compression (SWING only)

Aggregated VADER sentiment from the last 10 yfinance news articles, mapped to 0–100 (50 = neutral):

| Sentiment score | Multiplier | Meaning |
|----------------|-----------|---------|
| < 25 | 0.75× | Strongly negative news |
| < 35 | 0.85× | Negative news |
| ≥ 35 | 1.0× | Neutral or positive |

SHORT ignores news (too reactive to individual articles); LONG ignores it (negative news is priced in quickly for long-term positions).

### Filter 8 — Relative strength vs sector

Stock's 20-day return vs its sector ETF (XLK, XLV, XLF for US; ^HSI for HK):
```
rs_rank = (1 + stock_20d) / (1 + etf_20d)
```

If `rs_rank < 0.8` (stock meaningfully lagging its sector):
```
fused = 0.5 + (fused − 0.5) × rs_compression
```
LONG has the strongest filter (0.80×) — sector laggards are penalised most for long-term holdings.

### Filter 9 — Options flow adjustment

From the nearest two options expiries (via yfinance):

| Condition | Effect | Notes |
|-----------|--------|-------|
| C/P ratio ≥ 2.0 AND put_vol ≥ 100 | +0.07 | Strongly bullish options flow |
| C/P ratio ≥ 1.3 | +0.03 | Elevated call activity |
| C/P ratio ≤ 0.5 | ×0.85 compress | Elevated put activity |
| C/P ratio ≤ 0.8 | ×0.92 compress | Slightly elevated puts |

**Important:** `strongly_bullish` requires at least 100 put contracts to be traded. If put volume is zero or near-zero (illiquid options), the ratio is capped at 10.0 and the signal remains at most `bullish` — zero put volume means no options market, not extreme bullishness.

### Filter 10 — K-Score fundamental boost (LONG only)

| K-Score | Adjustment |
|---------|-----------|
| ≥ 70 | +0.08 |
| 55 – 69 | +0.04 |
| 35 – 54 | none |
| < 35 | −0.06 |

### Filter 11 — Stale price penalty

If the latest price bar is > 3 calendar days old:
```
fused = 0.5 + (fused − 0.5) × 0.6   (40% compression toward neutral)
```
Sets `reasons["stale_price_warning"] = true`.

### Filter 12 — Insufficient history penalty

If the stock has fewer than 50 bars of price history (new IPO, recently added), SMA200, ADX, and RSI are all unreliable:
```
fused = 0.5 + (fused − 0.5) × 0.5   (50% compression toward neutral)
```
Sets `reasons["insufficient_history_warning"] = true` and `reasons["bar_count"]` in the API response.

### Compression cap

After all 12 filters, if the cumulative compression has pushed the signal below the style's `max_compress_ratio` floor, it is restored to that floor. This prevents mathematically impossible BUY thresholds from piling up. Example for SWING (floor = 0.55):

```
Stock at 0.80 fused, earnings in 2d (×0.50), weak breadth (×0.90):
→ compressed to 0.80 × 0.50 × 0.90 = 0.36
→ floor applied: restored to 0.5 + (0.30) × 0.55 = 0.665
→ still HOLD (below 0.65 SWING BUY threshold in bull market)
```

---

## Stage 5 — BUY / HOLD / WAIT / SELL Decision

Regime-specific thresholds are applied to the final `fused` value:

| Regime | BUY threshold | HOLD threshold | WAIT threshold | SELL threshold |
|--------|--------------|----------------|----------------|----------------|
| bull | **0.62** | 0.50 | 0.46 | 0.35 |
| high_vol | **0.67** | 0.54 | 0.50 | 0.30 |
| bear | **0.70** | 0.56 | 0.52 | 0.27 |
| unknown | **0.62** | 0.50 | 0.46 | 0.35 |

*(Values shown for SWING. SHORT and LONG have different thresholds — see style profile table.)*

| Signal | Meaning |
|--------|---------|
| **BUY** | TA, ML, and filters are all aligned bullish. Entry is supported. |
| **HOLD** | Mildly bullish. No strong entry yet; don't exit existing positions. |
| **WAIT** | Slightly bearish lean. Watch for confirmation before acting. |
| **SELL** | Bearish. Technical picture has deteriorated. |

---

## Stage 6 — Confidence Score

```
confidence = |fused_probability − 0.5| × 200
```

This is how far the final probability is from neutral (0.5), scaled to 0–100.

| Fused probability | Signal (SWING/bull) | Confidence |
|------------------|---------------------|-----------|
| 0.50 | HOLD | 0% — complete uncertainty |
| 0.55 | HOLD | 10% — slight bullish lean |
| 0.65 | BUY (threshold) | 30% — just crossed the line |
| 0.70 | BUY | 40% — moderately confident |
| 0.75 | BUY | 50% — good conviction |
| 0.85 | BUY | 70% — high conviction |
| 0.92 | BUY | 84% — very strong |
| 1.00 | BUY | 100% — theoretical maximum |

**A 30% confidence BUY is very different from a 70% confidence BUY.** The former barely crossed the threshold and can flip to HOLD at the next refresh. The latter reflects strong agreement across all layers.

The **Bullish Probability %** shown in the UI is simply `fused × 100`. A 72% bullish probability means the combined model believes there is a 72% chance the stock will be higher at the target horizon given today's conditions.

---

## Market regime detection

The signal engine determines the current market regime before every computation:

| Regime | Detected when | Effect on signals |
|--------|--------------|-------------------|
| `bull` | SPY above 200-day MA, Fear & Greed ≥ 30 | Normal thresholds |
| `high_vol` | SPY in bull territory but Fear & Greed < 30 | Raised thresholds + extra compression |
| `bear` | SPY below 200-day MA | Highest thresholds — requires strongest conviction |
| `unknown` | SPY/VIX data unavailable | Conservative thresholds as precaution |

Market breadth (% of tracked stocks above their 200-day SMA) is also fetched. If breadth < 40%, breadth compression applies regardless of regime — even a bull regime with weak internals compresses BUY signals.

---

## When to trust the signal

### Trust it when:

- `bar_count ≥ 200` — sufficient history for SMA200 and all momentum indicators
- `ml_probability` is not null — model was trained (not TA-only cold start)
- `ml_ta_conflict = false` — ML and TA agree on direction
- `weekly_alignment = true` — daily and weekly timeframes agree
- `stale_price_warning = false` — data is current
- `insufficient_history_warning` absent or false
- `market_regime` = `bull` or `unknown` — not suppressed by regime filters
- Confidence ≥ 50% — well away from the threshold boundary
- `adx_compression = false` — stock is trending, not ranging

### Be skeptical when:

- `insufficient_history_warning = true` — fewer than 50 bars; indicators are partial
- `ml_ta_conflict = true` — ML and TA disagree strongly; one layer may be wrong
- `stale_price_warning = true` — price data is > 3 days old
- `earnings_warning = "caution"` — earnings within 2 days (50% compressed)
- `options_flag = "unusual_call_activity"` on a thinly-traded stock — verify put volume is substantial
- `weekly_alignment = false` and you're in LONG or SWING style — swimming against the weekly trend
- Confidence < 30% — signal is at the threshold boundary and is fragile
- `market_regime = bear` + `breadth_pct < 30%` — dual headwinds are suppressing everything

### Never use the signal for:

- Earnings trades — the compression reduces but cannot eliminate gap risk
- Macro shock events — rate decisions, geopolitical events override all technicals
- Stocks with `bar_count < 30` (rare but possible on newly added stocks)

---

## Signal freshness

Dashboard cards show a colored timestamp below each signal badge:

| Color | Age | Meaning |
|-------|-----|---------|
| Green | < 1 hour | Fresh — computed this session |
| Yellow | 1–8 hours | Current trading day, still reliable |
| Orange | 8–24 hours | From earlier today or overnight |
| Red | > 24 hours | Stale — may not reflect today's price action |

Signals are refreshed 5× per trading day by the scheduler. On weekends and non-trading days, red age is expected and does not mean the signal changed. Click **⚡ Refresh Signals** to recompute immediately.

---

## The `signals` table

Every computed signal is persisted to the `signals` table in PostgreSQL.

```sql
CREATE TABLE signals (
    id                  BIGSERIAL PRIMARY KEY,
    stock_id            BIGINT NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    ts                  TIMESTAMP NOT NULL DEFAULT now(),
    signal              signal_type NOT NULL,      -- BUY | HOLD | WAIT | SELL
    horizon             signal_horizon NOT NULL,   -- SHORT | SWING | LONG
    confidence          FLOAT NOT NULL,            -- 0–100
    bullish_probability FLOAT,                     -- 0–1, nullable
    reasons             JSONB,                     -- full indicator breakdown
    source              VARCHAR(64) DEFAULT 'signal-engine'
);
```

### Reasons JSONB field reference

| Key | Type | Description |
|-----|------|-------------|
| `market_regime` | string | `bull`, `high_vol`, `bear`, or `unknown` |
| `fear_greed_score` | float\|null | CNN Fear & Greed index (0–100) |
| `breadth_pct` | float\|null | % of tracked stocks above 200-day SMA |
| `trend_above_sma50` | bool | Price is above SMA(50) |
| `sma50_above_sma200` | bool | SMA(50) is above SMA(200) |
| `golden_cross_event` | bool | SMA(50) just crossed above SMA(200) |
| `death_cross_event` | bool | SMA(50) just crossed below SMA(200) |
| `rsi` | float | 14-period Wilder RSI value |
| `stoch_rsi_k` | float | Stochastic RSI K line (0–1) |
| `stoch_rsi_cross_up` | bool | Stoch RSI just recovered from oversold |
| `rsi_divergence` | string | `"bullish"`, `"bearish"`, or `"none"` |
| `macd_hist` | float | MACD histogram value |
| `macd_rising` | bool | Histogram grew since last bar |
| `macd_zero_cross_up` | bool | MACD line just turned positive |
| `bb_pct_b` | float | Bollinger Bands %B (0–1) |
| `adx` | float | ADX value |
| `adx_trending` | bool | ADX above style threshold |
| `adx_bullish` | bool | ADX trending AND DI+ > DI− |
| `obv_bullish` | bool | OBV 10-day avg > OBV 30-day avg |
| `volume_z` | float | Volume standard deviations above 20-day mean |
| `ta_score` | float | Raw TA probability before ML blending (0–1) |
| `ml_probability` | float\|null | Ensemble bullish probability. `null` = not trained. |
| `ml_weight` | float | Actual ML blending weight used (0–0.75) |
| `ml_ta_conflict` | bool | ML and TA disagreed by > 35 points |
| `weekly_ta_score` | float | Weekly timeframe TA score (0–1) |
| `weekly_alignment` | bool | Weekly direction agrees with daily |
| `active_patterns` | list | Detected candlestick patterns |
| `pattern_adjustment` | float | Probability delta from pattern recognition |
| `days_to_earnings` | int\|null | Calendar days to next earnings |
| `earnings_warning` | string\|null | `"caution"`, `"note"`, `"watch"`, or null |
| `news_sentiment` | float\|null | VADER sentiment 0–100 (50 = neutral) |
| `news_sentiment_flag` | string | `"strongly_negative"`, `"negative"`, or `"neutral_or_positive"` |
| `rs_score` | float\|null | Relative strength score 0–100 vs sector ETF |
| `rs_rank` | float\|null | Raw RS ratio (>1 = outperforming sector) |
| `rs_flag` | string | `"lagging_sector"` or `"in_line_or_leading"` |
| `options_sentiment` | string\|null | `"strongly_bullish"`, `"bullish"`, `"neutral"`, `"slightly_bearish"`, `"bearish"` |
| `options_cp_ratio` | float\|null | Call/put volume ratio (capped at 10.0) |
| `options_flag` | string\|null | `"unusual_call_activity"`, `"elevated_call_volume"`, `"elevated_put_volume"`, etc. |
| `kscore` | float\|null | K-Score composite (0–100). LONG style only. |
| `kscore_used` | float | The K-Score value that was applied (LONG only) |
| `adx_compression` | bool | ADX compression was applied |
| `high_vol_compression` | bool | High-vol regime compression was applied |
| `breadth_compression` | bool | Breadth compression was applied |
| `weekly_alignment` | bool | Weekly TA agreed with daily direction |
| `earnings_warning` | string | Earnings proximity status |
| `stale_price_warning` | bool | Price data was > 3 days old |
| `insufficient_history_warning` | bool | Fewer than 50 bars of history |
| `bar_count` | int | Number of daily bars available for this stock |
| `compression_cap_applied` | bool | Max compression floor was enforced |
| `horizon` | string | Style profile applied (`SHORT`, `SWING`, or `LONG`) |

---

## Strongest setups by style

### SHORT — highest conviction

1. Price above SMA(50) — uptrend intact
2. RSI 45–60 and Stoch RSI just recovered from oversold — dip bought
3. MACD histogram positive and rising — momentum accelerating
4. ADX > 25 with DI+ > DI− — trending strongly
5. Volume spike (Z > 1.5) — institutional participation

### SWING — highest conviction

All SHORT conditions, plus:
- OBV bullish — volume confirming direction
- No earnings in next 10 days — no binary event risk
- News sentiment ≥ 35 — not suppressed by negative headlines
- ML probability > 70% — model agrees with TA
- Weekly alignment = true — higher timeframe confirms

### LONG — highest conviction

1. K-Score > 70 — strong fundamentals composite
2. Weekly timeframe also bullish — trend confirmed on higher timeframe
3. ML probability > 65% — multi-week pattern recognised
4. RS score above sector median — stock is outperforming peers
5. No earnings in next 30 days (no compression)

---

## Known limitations

- **Earnings gaps**: A 15–30% overnight gap is unpredictable by any technical signal. SWING applies compression within 10 days but this reduces probability, not eliminates risk.
- **Macro shocks**: Rate decisions, geopolitical events, and sector rotations override any technical setup. The market regime filter partially accounts for sustained trends but not sudden shocks.
- **New stocks** (`bar_count < 50`): Signal is now compressed 50% toward neutral and flagged with `insufficient_history_warning`. SMA200, ADX, and RSI are unreliable with fewer bars.
- **Illiquid options**: Options boost now requires ≥ 100 put contracts before declaring `strongly_bullish`. Zero put volume = illiquid options market, not extreme bullishness.
- **ML cold start**: A newly added stock has no trained model. Signal is 100% TA until **Train All** is clicked. Visible when `ml_probability = null` in the reasons panel.
- **Hardcoded boost values**: The K-Score boost (+0.08), options boost (+0.07), and some compression multipliers are hand-tuned from domain knowledge. They will be empirically validated via Optuna once sufficient signal outcomes accumulate — see [SIGNAL_ACCURACY.md](SIGNAL_ACCURACY.md).

---

For how to combine AI Signal with K-Score, analyst ratings, insider activity, and earnings timing, see [SCORING.md](SCORING.md).

For how signal accuracy is measured over time and how parameters are tuned using real outcomes, see [SIGNAL_ACCURACY.md](SIGNAL_ACCURACY.md).
