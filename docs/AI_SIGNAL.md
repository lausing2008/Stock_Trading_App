# AI Signal — How It Works & How to Read It

Source: [`services/signal-engine/src/generators/signals.py`](../services/signal-engine/src/generators/signals.py)

---

## What the signal is

The AI Signal is a **BUY / HOLD / WAIT / SELL** label that fuses two layers of analysis:

1. **Technical Analysis (TA)** — nine price-based indicators computed from daily OHLCV history
2. **Machine Learning (ML)** — an XGBoost model trained on the same stock's price patterns

Both layers produce a single number called the **fused bullish probability** (0–1). That probability is then filtered through style-specific compression factors and converted into a signal label based on the current market regime.

Every stock carries three signals simultaneously — one per trading style (SHORT, SWING, LONG). All three are computed in a single data-fetch pass for efficiency.

---

## Trading Styles

The system supports three trading style profiles. Select your style in **Settings → Trading Style — AI Signal Horizon**. The choice propagates to every signal display across the app (Dashboard, Rankings, Watchlist, Screener, Opportunities, Positions, Forecast).

| Style | Horizon | Character |
|-------|---------|-----------|
| **SHORT** | 1 – 5 days | Pure technical analysis. No earnings or news compression. Ideal for volatile small-caps where fundamentals don't apply short-term. |
| **SWING** | 5 – 20 days | Balanced TA + momentum. Standard earnings and news filters. The default for most stocks. |
| **LONG** | 30 – 90 days | Fundamentals-heavy. K-Score boost/penalty applied. Strong weekly alignment required. Designed for position trades. |

### Style profile parameters

| Parameter | SHORT | SWING | LONG |
|-----------|-------|-------|------|
| **ML weight cap** | 30% | 75% | 45% |
| **BUY threshold — bull market** | 0.60 | 0.65 | 0.60 |
| **BUY threshold — high-vol** | 0.65 | 0.70 | 0.65 |
| **BUY threshold — bear market** | 0.68 | 0.73 | 0.70 |
| **HOLD threshold — bull** | 0.46 | 0.50 | 0.46 |
| **HOLD threshold — bear** | 0.52 | 0.56 | 0.54 |
| **ADX filter (min trending)** | 25 | 20 | off |
| **ADX compression** | 0.85× | 0.90× | — |
| **High-vol compression** | 0.92× | 0.85× | 0.90× |
| **Breadth compression** | off | 0.90× | 0.92× |
| **Weekly align boost / compress** | 1.08× / 0.93× | 1.12× / 0.85× | 1.18× / 0.80× |
| **Earnings compression (≤2d / ≤5d / ≤10d)** | off | 0.50× / 0.75× / 0.90× | off |
| **News compression (sent ≤ −25 / ≤ −35)** | off | 0.75× / 0.85× | off |
| **RS compression** | 0.90× | 0.85× | 0.80× |
| **K-Score boost** | off | off | **on** |
| **Max compression floor** | 0.70 | 0.55 | 0.65 |

**ML weight cap** controls how much the XGBoost probability can dominate. SHORT keeps ML at ≤ 30% because short-term price movements are noisier — TA momentum is more actionable. SWING allows ML up to 75% when the model has high AUC. LONG caps ML at 45% to let weekly alignment and K-Score have meaningful weight.

**Max compression floor** prevents stacked filters from making a BUY mathematically impossible. If all filters combined would compress the fused probability below this floor, the system restores the probability to the floor before applying thresholds. For example: SWING floor is 0.55, so a stock at 0.80 with earnings in 2 days (0.50×) and weak breadth (0.90×) compresses to max(0.80 × 0.50 × 0.90, 0.55) = 0.55 — still HOLD-eligible rather than forced to WAIT.

---

## The two-layer pipeline

```
Daily price history (last 400 bars)
         │
         ├─► TA score  ──────────────────────────────────────────────────────┐
         │   (9 indicators → probability 0–1)                               │
         │                                                                   ▼
         └─► ML probability  ──────► Fused probability = ML×weight + TA×(1-weight)
             (XGBoost, if trained)           │
                                             │
                          ┌──────────────────┼──────────────────────────────┐
                          ▼                  ▼                              ▼
                    SHORT filters      SWING filters               LONG filters
                    (no earn/news)     (earn+news compression)    (K-Score boost)
                          │                  │                              │
                          ▼                  ▼                              ▼
                   AIConfidence        AIConfidence                  AIConfidence
                   (horizon=SHORT)    (horizon=SWING)               (horizon=LONG)
```

All three results are persisted to the `signals` table in a single pass. The frontend reads whichever horizon matches the user's current style setting.

---

## Signal labels and what they mean

| Label | Meaning |
|-------|---------|
| **BUY** | TA, ML, and filters are all aligned bullish. Entry is supported. |
| **HOLD** | Mildly bullish. No strong entry yet; don't exit existing positions. |
| **WAIT** | Slightly bearish lean. Watch for confirmation before acting. |
| **SELL** | Bearish. Technical picture has deteriorated. |

Exact thresholds depend on style and market regime — see the table above. In bear markets all BUY/HOLD thresholds are raised, requiring stronger conviction before entry signals fire.

---

## Confidence and Bullish Probability

Two numbers accompany every signal in the UI:

**Bullish Probability %** — the fused probability after all style filters, scaled to a percentage.
- 70%+ = genuinely bullish
- 50% = neutral (coin-flip)
- 30%− = genuinely bearish

**Confidence** — how far the final probability is from 50%, scaled to 0–100:
```
confidence = |fused_probability − 0.5| × 200
```
A BUY at 80% bullish probability has confidence 60. A HOLD at 55% has confidence 10. High confidence + BUY is very different from low confidence + BUY — the latter can flip at the next refresh.

---

## The Technical Analysis layer

The TA score is built from nine independent indicators. Each contributes a fixed weight to a probability (0–1).

### 1. SMA trend alignment (up to +0.35)

| Condition | Score | What it means |
|-----------|-------|---------------|
| Price above SMA(50) | +0.15 | Short-term trend is up |
| SMA(50) above SMA(200) | +0.10 | Medium-term trend is up |
| Golden cross just fired | +0.10 | SMA(50) crossed above SMA(200) — regime change |
| Death cross just fired | −0.10 | SMA(50) crossed below SMA(200) — regime turned bearish |

### 2. RSI (14-period) (up to +0.15)

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

ADX threshold is 25 for SHORT and SWING, disabled entirely for LONG. When ADX is below threshold, the fused probability is multiplied by the style's `adx_compression` factor.

### 8. OBV (up to +0.10)

| Condition | Score | Signal |
|-----------|-------|--------|
| OBV 10-day avg > OBV 30-day avg | +0.10 | Net volume flow is bullish |

### 9. Volume Z-score (up to +0.05)

| Condition | Score | Signal |
|-----------|-------|--------|
| Volume > 0.5σ above 20-day average | +0.05 | Above-average participation |

---

## The Machine Learning layer

The ML layer uses an XGBoost classifier trained on the stock's own price history. It predicts the bullish probability — the likelihood the price is higher at the target horizon given the current pattern.

- **Trained per-symbol**: MU's model is trained on MU data only.
- **22 features**: returns at multiple timeframes, RSI, MACD, ATR, volume ratios, SMA ratios, Stoch RSI, BB %B, and others derived from daily bars.
- **AUC-gated blending**: ML weight is scaled down when `test_auc < 0.52`. A model that barely beats random gets a low vote. High-AUC models (> 0.65) get full weight up to the style's `ml_weight_cap`.
- **Retrained nightly** at post-close. Intra-day refreshes use the previous close model.
- **Cold-start fallback**: New stocks with no trained model fall back to 100% TA until **Train All** is clicked.

The ML probability is blended with the TA score. The exact weight depends on the style's `ml_weight_cap`, the model's AUC, and whether weekly alignment data is available.

---

## Style-specific filters applied after ML blending

After the initial fused probability is computed, additional multipliers are applied in order:

1. **Weekly alignment** — Checks if the weekly timeframe agrees with the daily signal. Bull weekly → probability boosted by `weekly_boost`; bear weekly → multiplied by `weekly_compress`. LONG has the strongest weekly alignment requirement (1.18× boost / 0.80× compress).

2. **ADX / trend strength** (SHORT and SWING only) — If ADX is below the style minimum, the probability is multiplied by `adx_compression`. LONG skips this filter to allow fundamentals to dominate even in ranging markets.

3. **High-volatility regime** (all styles) — When market breadth VIX/ATR indicates elevated volatility, a compression is applied and the BUY threshold is raised from "bull" to "high_vol".

4. **Breadth compression** (SWING and LONG only) — If fewer than 50% of tracked stocks are in uptrend, the probability is further compressed. SHORT skips this — short-term trades don't require broad participation.

5. **Pattern adjustment** — Candlestick pattern recognition applies a small multiplier (±5–8%) for known reversal patterns.

6. **Earnings compression** (SWING only) — If earnings are within N days, the probability is multiplied down to reduce false signals around earnings gaps. The exact multipliers are: `{≤2d: 0.50×, ≤5d: 0.75×, ≤10d: 0.90×}`. SHORT and LONG skip this entirely.

7. **News sentiment compression** (SWING only) — If the stock's aggregated news sentiment is strongly negative (score ≤ −25), the probability is multiplied by 0.75× or 0.85×. SHORT ignores news (too reactive); LONG ignores it (price-in already).

8. **Relative Strength compression** — If the stock is underperforming its sector significantly, a compression applies. LONG has the strongest RS filter (0.80×), penalising laggards most.

9. **K-Score boost** (LONG only) — The K-Score (0–100 fundamental composite) is fetched from the Ranking Engine. Score > 70 boosts the probability by up to 10%. Score < 30 penalises by up to 12%.

10. **Compression cap** — After all multipliers, if the probability has been pushed below the style's `max_compress_ratio`, it is restored to that floor. This prevents mathematically impossible BUY thresholds from stacking.

---

## Market regime

The signal engine determines the current market regime on every computation:

| Regime | How detected | Effect |
|--------|-------------|--------|
| `bull` | S&P 500 above 200-day MA, VIX < 20 | Normal thresholds |
| `high_vol` | VIX ≥ 20 or S&P in elevated-vol period | Raised thresholds, additional compression |
| `bear` | S&P 500 below 200-day MA | Highest thresholds — requires strongest conviction |
| `unknown` | Data unavailable | Slightly raised thresholds as a precaution |

SWING thresholds by regime:

| Regime | BUY threshold | HOLD threshold |
|--------|--------------|----------------|
| Bull | 0.65 | 0.50 |
| High-vol | 0.70 | 0.54 |
| Bear | 0.73 | 0.56 |
| Unknown | 0.65 | 0.50 |

---

## The `signals` table

Every computed signal is persisted to the `signals` table in PostgreSQL.

```sql
CREATE TABLE signals (
    id                 BIGSERIAL PRIMARY KEY,
    stock_id           BIGINT NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
    ts                 TIMESTAMP NOT NULL DEFAULT now(),
    signal             signal_type NOT NULL,     -- BUY | HOLD | WAIT | SELL
    horizon            signal_horizon NOT NULL,  -- SHORT | SWING | LONG
    confidence         FLOAT NOT NULL,           -- 0–100
    bullish_probability FLOAT,                   -- 0–1, nullable
    reasons            JSONB,                    -- full indicator breakdown
    source             VARCHAR(64) DEFAULT 'signal-engine'
);

CREATE INDEX ix_signals_stock_ts ON signals (stock_id, ts);
```

### Column reference

| Column | Type | Description |
|--------|------|-------------|
| `id` | bigint | Auto-increment primary key |
| `stock_id` | bigint | FK → `stocks.id`. Cascades on delete. |
| `ts` | timestamp | When the signal was computed (UTC) |
| `signal` | enum | `BUY`, `HOLD`, `WAIT`, or `SELL` |
| `horizon` | enum | `SHORT`, `SWING`, or `LONG` — the trading style this signal belongs to |
| `confidence` | float | 0–100. Distance of `bullish_probability` from 0.50, scaled. |
| `bullish_probability` | float | 0–1. The final fused probability after all style filters. |
| `reasons` | jsonb | Full indicator snapshot (see Reasons field reference below). |
| `source` | varchar | Always `signal-engine` for automated signals. |

### `horizon` enum values

| Value | Meaning |
|-------|---------|
| `SHORT` | 1–5 day trading style |
| `SWING` | 5–20 day trading style |
| `LONG` | 30–90 day trading style |

Prior to the trading style refactor, all signals were stored with `horizon = SWING`. After the refactor, each stock receives **three rows per refresh cycle** — one per horizon. The API's `/signals/latest` endpoint accepts a `?style=SWING` query parameter to filter by horizon.

### Uniqueness and "latest signal" queries

The latest signal per `(stock_id, horizon)` pair is determined by `MAX(ts)` within a subquery grouped on both columns. This means a single stock has up to three "current" signals — one per style — and changing your style setting switches which row the UI displays.

### Reasons JSONB field

The `reasons` column stores the full indicator state at signal time. Key fields:

| Key | Type | Description |
|-----|------|-------------|
| `market_regime` | string | `bull`, `high_vol`, `bear`, or `unknown` |
| `trend_above_sma50` | bool | Price is above SMA(50) |
| `sma50_above_sma200` | bool | SMA(50) is above SMA(200) — uptrend intact |
| `golden_cross_event` | bool | SMA(50) just crossed above SMA(200) |
| `death_cross_event` | bool | SMA(50) just crossed below SMA(200) |
| `rsi` | float | 14-period RSI value |
| `stoch_rsi_k` | float | Stochastic RSI K line (0–1) |
| `stoch_rsi_cross_up` | bool | Stoch RSI just recovered from oversold |
| `rsi_divergence` | string | `"bullish"`, `"bearish"`, or `"none"` |
| `macd_hist` | float | MACD histogram value |
| `macd_rising` | bool | Histogram grew since last bar |
| `macd_zero_cross_up` | bool | MACD line just turned positive |
| `bb_pct_b` | float | Bollinger Bands %B (0–1) |
| `adx` | float | ADX value |
| `adx_trending` | bool | ADX is above the style's minimum threshold |
| `adx_bullish` | bool | ADX trending AND DI+ > DI− |
| `obv_bullish` | bool | OBV 10-day avg > OBV 30-day avg |
| `volume_z` | float | Volume standard deviations above 20-day mean |
| `ta_score` | float | Raw TA probability before ML blending (0–1) |
| `ml_probability` | float\|null | XGBoost bullish probability. `null` = not trained. |
| `ml_weight` | float | Actual ML blending weight used (0–1) |
| `weekly_alignment` | bool | Weekly timeframe agrees with daily direction |
| `days_to_earnings` | int\|null | Calendar days until next earnings report |
| `news_sentiment` | float\|null | Aggregated VADER sentiment score (−100 to 100) |
| `kscore` | float\|null | K-Score from ranking engine (0–100). LONG style only. |
| `horizon` | string | Which style profile was applied |

---

## The `trade_plans` table

The Trade Board Kanban cards are persisted in `trade_plans`. Each row represents one card for one user.

```sql
CREATE TABLE trade_plans (
    id                 SERIAL PRIMARY KEY,
    user_id            INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol             VARCHAR(32) NOT NULL,
    stage              VARCHAR(20) NOT NULL DEFAULT 'watch',
    game_plan          JSONB,
    entry_price        FLOAT,
    stop_loss          FLOAT,
    take_profit        FLOAT,
    notes              TEXT,
    source             VARCHAR(32),
    exit_price         FLOAT,
    actual_entry_price FLOAT,
    shares             FLOAT,
    closed_at          TIMESTAMP,
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    updated_at         TIMESTAMP NOT NULL DEFAULT now()
);
```

### Column reference

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Auto-increment primary key |
| `user_id` | int | FK → `users.id`. Each card belongs to one user. |
| `symbol` | varchar(32) | Ticker symbol (e.g. `NVDA`, `0700.HK`) |
| `stage` | varchar(20) | Kanban column: `watch`, `planning`, `active`, or `closed` |
| `game_plan` | jsonb | Full AI game plan object (targets, rationale, catalysts, risks) |
| `entry_price` | float | **Planned** limit/target entry price from the game plan |
| `stop_loss` | float | Planned stop loss level |
| `take_profit` | float | Planned take profit target (primary) |
| `notes` | text | Free-form user notes |
| `source` | varchar(32) | Origin: `gameplan`, `forecast`, or `manual` |
| `exit_price` | float | Actual exit price when card is moved to Closed |
| `actual_entry_price` | float | **Actual fill price** captured when card is moved to Active via the Fill modal |
| `shares` | float | Number of shares filled, captured alongside `actual_entry_price` |
| `closed_at` | timestamp | When the card was moved to Closed |
| `created_at` | timestamp | Row creation time |
| `updated_at` | timestamp | Last update time (auto-updates on any write) |

### Stage lifecycle

```
watch → planning → active → closed
```

Cards can be dragged between columns on the Trade Board. The transition to `active` triggers the Fill modal — prompting for `actual_entry_price` and `shares` to capture the real fill rather than the planned price.

### P&L calculation

Dollar P&L is computed at display time from persisted fields:

```
effective_entry = actual_entry_price ?? entry_price
pct_pnl  = (exit_price - effective_entry) / effective_entry × 100
dollar_pnl = (exit_price - effective_entry) × shares   (only when shares is set)
```

`actual_entry_price` takes priority over `entry_price` wherever P&L is shown — on the card itself, in the performance stats row, and in the overall board stats.

---

## Signal age and freshness

Dashboard cards show a colored timestamp below each signal badge:

| Color | Age | Meaning |
|-------|-----|---------|
| Green | < 1 hour | Fresh — computed this session |
| Yellow | 1–8 hours | Current trading day, still reliable |
| Orange | 8–24 hours | From earlier today or overnight |
| Red | > 24 hours | Stale — may not reflect today's price action |

Signals are refreshed 5× per trading day by the scheduler. On weekends and non-trading days, red age is expected and does not mean the signal changed. Click **⚡ Refresh Signals** to recompute immediately.

---

## What this signal cannot do

- **Earnings gaps**: A 15–30% overnight gap is unpredictable by any technical signal. Check the earnings calendar before entering. SWING style applies compression within 10 days of earnings specifically to reduce this risk.
- **Macro shocks**: Rate decisions, geopolitical events, and sector rotations can override any technical setup. The market regime filter partially accounts for sustained trends but not sudden shocks.
- **Thin liquidity**: For illiquid stocks with few daily bars, TA indicators are less reliable. ADX may be weak, OBV erratic. Consider using SHORT style for these — it drops earnings/news filters and is less demanding on data quality.
- **New stocks**: Less than 200 bars means SMA(200) and some momentum sub-scores are incomplete. The signal will have lower confidence until more history is ingested.
- **ML cold start**: A newly added stock has no trained model. Signal is 100% TA until **Train All** is clicked. Visible when `ml_probability = null` in the reasons panel.

---

## Strongest setups by style

### SHORT — highest conviction

1. Price above SMA(50) — uptrend intact
2. RSI 45–60 and Stoch RSI just recovered from oversold — dip bought
3. MACD histogram positive and rising — momentum accelerating
4. ADX > 25 with DI+ > DI− — trending strongly
5. Volume spike (Z > 1.5) — institutional participation

### SWING — highest conviction

All of the above, plus:
- OBV bullish — volume confirming direction
- No earnings in next 10 days — no binary event risk
- News sentiment not deeply negative
- ML probability > 70% — model agrees with TA

### LONG — highest conviction

1. K-Score > 70 — strong fundamentals composite
2. Weekly timeframe also bullish — trend confirmed on higher timeframe
3. ML probability > 65% — multi-week pattern recognized
4. RS score above sector median — stock is outperforming peers
5. ADX filter is off — trend strength less critical for long holds

---

For how to combine AI Signal with K-Score, analyst ratings, insider activity, and earnings timing, see [SCORING.md](SCORING.md).
