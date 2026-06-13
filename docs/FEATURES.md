# Feature Reference

Complete reference for every feature in StockAI — what it does, where to find it,
and how it works under the hood.

> For K-Score and Fair Value calculation details, see [SCORING.md](SCORING.md).
> For a full breakdown of how the AI Signal is computed and how to interpret it, see [AI_SIGNAL.md](AI_SIGNAL.md).
> For the full Research Engine technical reference (scoring formulas, Claude prompt, architecture), see [RESEARCH_ENGINE.md](RESEARCH_ENGINE.md).

---

## Login (`/login`)

The entry gate for the application. Every page redirects here if no session is active.

### Sign In tab
- Username + password fields
- Brief loading animation on submit
- Red error banner on wrong credentials

### Reset Password tab
- Enter username, current password, new password, and confirm
- Green success banner + auto-switches back to Sign In on success
- New password must be at least 4 characters

### How auth works
Authentication is JWT-based. On login, the `market-data` service verifies credentials with **bcrypt** and issues a signed **HS256 JWT** (30-day expiry). The token is stored in `stockai_jwt` in `localStorage` and sent as a `Bearer` header on every API request.

- Tokens contain `sub` (username) and `role` (`admin` or `user`)
- The frontend decodes the token client-side to read the active session — no extra `/me` call needed
- Logout clears the token from `localStorage`
- The default admin account (`lausing` / `120402`) is created automatically on first boot via `init_db()`

### Navigation
The top nav uses **grouped dropdown menus** — four groups that open on hover:

| Group | Pages |
|---|---|
| Markets | Dashboard, Heatmap, Rankings, Forecast |
| Research | Screener, Opportunities, Earnings, Analyst, Short Squeeze, **Research Engine** |
| Portfolio | Watchlist, Trade Board, Positions, Portfolio, Journal |
| Tools | Strategies, Alerts, Signal Accuracy, Trade Performance, Insider / Congress |

- Active group is underlined in indigo; active page shown with a purple dot in the dropdown
- Each dropdown has a 120 ms close delay so moving the mouse to a child item doesn't flicker
- Right-side controls: 🔔 Notification Bell, username + **ADMIN** badge, ⚙ Settings, Logout

---

## Dashboard (`/`)

The main screen. Shows a live market overview and the current user's tracked stocks as cards. Each user sees only the stocks they have added to their watchlist — the dashboard is fully per-user.

### Market Overview panel
Displayed between the toolbar and the stock grid. Three cards updated every 60 s:

**🇺🇸 US Markets**
- Live quotes: S&P 500, NASDAQ, Dow Jones, VIX — each showing price and day change %
- VIX colour is inverted (green = fear falling = good, red = fear rising = bad)
- Status badge: `● Open` / `● Pre-mkt` / `● Closed` based on NYSE hours (9:30 AM–4:00 PM ET)

**🇭🇰 HK Markets**
- Live Hang Seng Index price and day change %
- Status badge respects HKEX session including the 12:00–1:00 PM lunch break

**Portfolio Pulse**
- Stacked bar showing BUY / HOLD / WAIT / SELL distribution across the **current user's watchlist stocks only**
- Count breakdown in a 2×2 grid

Data source: `GET /stocks/market_overview` — yfinance fast_info for indices ^GSPC, ^IXIC, ^DJI, ^VIX, ^HSI. Redis-cached 60 s.

### Stock cards
The grid shows only the stocks in the logged-in user's watchlist. Each card shows:
- **Symbol + company name** — click to go to the stock detail page; HK stocks show the Chinese name as a subtitle (e.g. 騰訊控股)
- **✕ Remove button** — top-left corner, appears on card hover. First click shows inline `Remove? / Cancel` confirmation; confirming removes the stock from the user's watchlist (the stock and its price history remain in the global DB)
- **Current price** — live real-time price (yfinance `fast_info`, refreshes every 60 s)
- **Day change** — percentage and direction arrow, color-coded green/red
- **K-Score** — composite 0–100 score (green ≥ 70 / yellow ≥ 50 / red < 50)
- **BUY / HOLD / WAIT / SELL badge** — real TA + ML signal from the signal engine
- **Fair price** — 200-day SMA from the ranking engine (see [SCORING.md](SCORING.md))

### Toolbar
- **↻ Refresh** — re-fetches all data sources simultaneously
- **⚡ Train All** — ingests latest prices and schedules ML training for the current user's watchlist stocks only
- **+ Add Stock** — opens the Add Stock modal; the stock is added to the global DB and automatically added to the user's watchlist

### Auto-refresh
Prices refresh automatically every 60 seconds via SWR `refreshInterval`.

### Empty state
When the user's watchlist is empty the grid shows "Your watchlist is empty — click + Add Stock to start tracking stocks."

---

## Refresh & Schedule

This section explains how often each piece of data is refreshed, what triggers each refresh, and which components are updated together.

### Scheduled market refresh — up to ~60× per trading day

The background scheduler (`scheduler.py` inside the `market-data` container) runs a full refresh cycle across three phases per market. **All four steps happen in sequence on every run:**

1. **Ingest prices** — fetches the latest daily OHLCV bar for every active stock via `yf.download()` (one batch call) and writes it to the `prices` table
2. **Refresh rankings** — recomputes K-Scores, momentum, and sub-scores for every active stock using the new prices (pure local math — no external API)
3. **Refresh signals** — reruns the TA + ML signal engine (TA indicators + XGBoost) for every active stock; new signal is persisted in the `signals` table (pure local math — no external API)
4. **Check signal alerts** — checks every subscribed alert against the new signals; fires entry/exit emails where conditions are met

Since signals and momentum are entirely local computation (no Claude API, no rate limits), refreshing every 10 minutes during regular hours is safe and free.

**US market (America/New_York timezone, DST-adjusted):**

| Phase | Times | Frequency | Notes |
|-------|-------|-----------|-------|
| Open burst | 09:25 – 09:45 | Every 5 min | Gap opens, early momentum |
| Regular hours | 10:00 – 15:00 | Every 10 min | Prices + rankings + signals |
| Close burst | 15:30 – 16:15 | Every 5 min | Final bar capture |
| Post-close | 16:30 | Once | + ML retrain on day's data |

**HK market (Asia/Hong_Kong timezone, UTC+8, no DST):**

| Phase | Times | Frequency | Notes |
|-------|-------|-----------|-------|
| Open burst | 09:25 – 09:45 | Every 5 min | Gap opens, early momentum |
| Regular hours | 10:00 – 15:00 | Every 10 min | Prices + rankings + signals |
| Close burst | 15:30 – 16:15 | Every 5 min | Final bar capture |
| Post-close | 16:30 | Once | + ML retrain on day's data |

> The 16:30 post-close run is the most reliable. Intra-day runs use the latest available bar — on some symbols this is the previous day's close until the session ends. The signal computed at 16:30 reflects the full completed trading day.

### 5-minute intraday ingest — every 5 min during market hours

Two additional cron jobs run continuously during each market session to keep 5-minute candle data current for the stock detail chart:

**US market (America/New_York):** every 5 minutes from 09:30–16:00 Mon–Fri  
**HK market (Asia/Hong_Kong):** every 5 minutes from 09:30–16:00 Mon–Fri

Each run calls `ingest_universe(symbols, "5m")`. The ingest is **incremental** — it reads the last stored `ts` per symbol from the DB and passes `start=last_ts` to yfinance, fetching only new bars since then. This keeps each run lightweight (typically 1–3 bars per symbol). A `ThreadPoolExecutor` with 6 workers parallelises yfinance calls across all symbols; `tenacity` retries handle transient 429 responses. At ~780 requests per session for 100 symbols, well within Yahoo Finance's unofficial rate limit of ~2000/hr.

### ML model retrain

Runs **once per day**, at post-close (16:30) for both US and HK markets. Trains on the latest available price history. More frequent retraining has no benefit — the XGBoost model learns from daily bar outcomes, and intraday data doesn't change the training labels.

#### Model & features

The default model is **XGBoost** (gradient-boosted trees). Each symbol gets its own trained artifact saved to disk. The feature matrix has **26 inputs** — 22 stock-specific plus 4 macro market-context features:

| Group | Features |
|---|---|
| Momentum | `ret_1`, `ret_5`, `ret_10`, `ret_20`, `ret_60` |
| Volatility | `vol_20`, `vol_60`, `atr_14_pct` (ATR as % of price), `atr_ratio` (vol regime) |
| Trend | `sma_20_gap`, `sma_50_gap`, `sma_100_gap` |
| Oscillators | `rsi_14`, `macd`, `macd_signal`, `macd_hist`, `bb_pct`, `stoch_k` |
| Volume / flow | `volume_z`, `obv_z`, `cmf_20` (Chaikin Money Flow) |
| Range | `high_20_pct` (position in 20-day H-L channel) |
| **Macro** | `spy_ret_1`, `spy_ret_5` (S&P 500 direction), `vix_level` (fear gauge), `spy_vol_20` (market regime) |

The macro features give the model **situational awareness** — a BUY setup during extreme market fear is very different from the same setup during a bull rally. SPY and VIX data is fetched from yfinance and joined by date before training and inference.

**Label definition:** binary direction of the **5-day forward return**, after excluding a **volatility-adjusted dead zone**. The threshold is `0.5 × median_daily_vol × √horizon`, clamped to [0.5%, 3%]. Rows where `|fwd_ret| < threshold` are excluded — these near-zero moves are unclassifiable noise. After filtering, `y=1` means the stock rose (fwd_ret > 0) and `y=0` means it fell. This removes ~35–45% of rows but produces much cleaner labels since each retained row represents a genuinely directional move.

Scaler + feature list + calibrator + precision threshold are all saved alongside each model so inference is always consistent with training.

#### Signal quality improvements

Eight techniques are applied on every retrain to improve prediction accuracy:

| Improvement | What it does |
|---|---|
| **Volatility-adjusted dead zone** | Per-symbol threshold (`0.5 × vol × √horizon`, clamped [0.5%, 3%]). High-vol stocks need larger moves to be classifiable; low-vol stocks use a narrower filter. Drops ~35–45% of rows but leaves only clean directional signals. |
| **Macro features** | Adds SPY 1-day return, SPY 5-day return, VIX level, SPY 20-day vol to every bar. Model knows the market environment, not just stock-specific indicators. |
| **Recency weighting** | Recent bars get 3× more weight than oldest bars via exponential decay. Keeps the model current with recent market regime instead of equally weighting 5-year-old data. |
| **Probability calibration** | XGBoost raw probabilities are over-confident; isotonic regression calibration on a dedicated calibration set makes the ML/TA fusion and BUY thresholds reflect actual win rates. |
| **Precision-optimised threshold** | Instead of a fixed 0.5 threshold, each symbol gets a per-symbol BUY threshold set where test-set precision ≥ 60%. Quality over quantity — fewer but more reliable BUY signals. |
| **MACD price-normalised** | MACD, MACD signal, and MACD histogram are divided by the current price (`/ close`) so the feature is comparable across different price levels and stable over time as prices change. |
| **OBV flow-based z-score** | OBV z-score now measures the **20-day change in OBV** (recent money flow) rather than the cumulative OBV level, which was dominated by long-term trend bias and insensitive to short-term momentum shifts. |
| **Three-way train/calibration/test split** | Holdout is split 70% train / 15% calibration / 15% threshold evaluation. Calibrator is fit on the calibration set; BUY threshold is optimised on the separate test set. Eliminates the double-dipping that previously inflated reported precision metrics. |

#### Evaluation metrics

Each retrain reports:
- Holdout accuracy, AUC, precision, recall, F1 (last 15% of data as test set, evaluated on calibrated probs)
- Per-symbol `buy_threshold` — the lowest probability where test-set precision ≥ 60% (falls back to 0.5 if none achievable)
- 5-fold TimeSeriesSplit CV mean AUC and std (no data leakage, recency-weighted)
- Train / calibration / test set sizes (`n_train`, `n_cal`, `n_test`)
- Top-5 most important features for that symbol (logged on each train)
- All metrics stored in the model artifact and returned by `POST /ml/predict`

#### Inference fix (prediction uses today's bar)

Previously, `predict_latest()` used features from 5 bars ago (the last bar with a known forward return) instead of today's actual bar. This is now fixed — inference uses `inference_mode=True` which skips the label mask, so predictions are based on today's technical conditions.

#### Signal engine v3 — additional accuracy layers

Four techniques were added on top of v2 to improve timing accuracy:

| Improvement | What it does |
|---|---|
| **Multi-timeframe confirmation** | Weekly TA score (SMA, RSI, MACD on weekly bars) is computed separately and cross-checked with the daily signal. If daily and weekly direction agree, the fused probability is amplified by 12%. If they disagree, it is compressed by 15%. A daily BUY signal that contradicts the weekly trend is less reliable — this adjustment encodes that. |
| **Rolling 20-day VWAP** | Price above VWAP = institutional buyers active. Adds a small positive weight to the TA score when price is above VWAP; a small negative weight when below. |
| **Earnings proximity penalty** | If earnings are within 10 days, the fused probability is compressed toward 0.50. Earnings create binary outcome risk that overrides any technical signal — the model should not project a directional conviction when a coin-flip event is imminent. |
| **Chart pattern fusion** | Detects 5 classic patterns on the last 60 bars. Bullish patterns (bull flag, cup-and-handle, double bottom) apply a +0.04 to +0.06 boost; bearish patterns (head-and-shoulders, double top, bear flag) apply a −0.03 to −0.05 compression. Pattern bonuses are bounded so they can tilt but not override the combined ML+TA signal. |
| **News sentiment filter** | Last 10 news articles are scored with VADER sentiment (via yfinance, range −1 to +1), mapped to a 0–100 scale and averaged. Score < 25 (strongly negative) compresses the fused signal 30%; score 25–35 (moderately negative) compresses 20%. Neutral or positive news has no effect. This suppresses BUY signals ahead of regulatory action, leadership crises, or product recalls that technicals cannot yet detect. Sentiment score and flag are included in the signal `reasons` dict and shown in the trade plan on the stock detail page. |
| **Relative strength vs sector ETF** | The stock's 20-day return is compared to its sector benchmark ETF (US stocks: SPDR sector ETFs XLK/XLV/XLF/XLY/XLP/XLE/XLU/XLB/XLI/XLRE/XLC; HK stocks: ^HSI; no-sector fallback: SPY). `rs_rank = (1 + stock_20d_ret) / (1 + etf_20d_ret)`. If rs_rank < 0.8 (stock lagging sector by > 20% over 20 days) the fused signal is compressed 15%. A BUY on a sector-lagging stock is a significantly weaker setup than a BUY on a sector leader. The rs_rank and mapped 0–100 RS score are included in the signal `reasons` dict. |

**Market regime adaptive thresholds:**

| Regime | BUY threshold | HOLD threshold | Rationale |
|--------|--------------|----------------|-----------|
| Bull (S&P 500 above 200MA) | 0.65 | 0.50 | Normal — most signals are valid in trending markets |
| Bear (S&P 500 below 200MA) | 0.73 | 0.56 | Raised — broad market headwind means individual stock BUY signals have a higher false-positive rate; require stronger conviction to fire |

#### Signal engine v4 — signal quality & regime improvements

Four additional layers were added to prevent the ML model from overriding clear TA evidence and to handle market regimes where signals are inherently less reliable:

| Improvement | What it does |
|---|---|
| **ML probability soft-cap** | Raw XGBoost output is clipped to [0.05, 0.95] before fusion. XGBoost frequently outputs 0.0 or 1.0 when a small set of features pattern-match strongly — these extremes misrepresent certainty. The cap preserves signal direction while keeping TA meaningful. Without this, a 100% ML prediction with a 68% ML weight would contribute 0.68 to the fused score regardless of what every TA indicator says. |
| **ML-TA disagreement dampening** | When the absolute gap between the ML probability and the TA score exceeds 0.35, the ML weight is scaled down by up to 25%. Gap 0.35 → no dampening; gap ≥ 0.65 → full −25% reduction. This prevents a stock where TA is strongly bearish (score 20%) but ML is strongly bullish (95%) from being called a BUY purely on ML conviction — the result becomes a low-confidence HOLD instead. The conflict is flagged as `ml_ta_conflict: true` in the signal reasons and shown on the signal card. |
| **ADX choppy market compression** | When ADX falls below 20, the market is directionless — there is no confirmed trend for a BUY or SELL to follow. The fused probability is compressed 10% toward 0.50 in these conditions, reducing false BUY/SELL signals in range-bound stocks. ADX ≥ 20 is no compression. The flag `adx_compression: true` appears in signal reasons and the card shows "ADX N: Choppy" as a warning. |
| **4-state market regime** | The binary bull/bear regime is extended to four states using the Fear & Greed index score alongside the S&P 500 vs 200MA signal. A `high_vol` regime fires when the S&P 500 is in bull territory (above 200MA) but Fear & Greed drops below 30 — this captures market stress events (fast drawdowns, VIX spikes) where price has not yet broken the 200MA but conditions are unstable. In `high_vol`, the BUY threshold is raised to 0.70 and all signals are additionally compressed 15% toward neutral. |

**Updated market regime adaptive thresholds (v4):**

| Regime | BUY threshold | HOLD threshold | Signal compression | Trigger |
|--------|--------------|----------------|-------------------|---------|
| Bull | 0.65 | 0.50 | None | S&P 500 above 200MA, Fear & Greed ≥ 30 |
| High-Vol | 0.70 | 0.54 | −15% toward neutral | S&P 500 above 200MA, Fear & Greed < 30 |
| Bear | 0.73 | 0.56 | None | S&P 500 below 200MA |

#### Dynamic ML/TA fusion weighting

The 60/40 ML/TA split is no longer hardcoded. The signal engine now reads each symbol's `cv_auc_mean` from the model artifact and adjusts the ML weight proportionally:

| CV AUC | ML weight | TA weight | Interpretation |
|--------|-----------|-----------|----------------|
| 0.50 (random) | 40% | 60% | Model barely beats random — rely more on TA rules |
| 0.55 | 51% | 49% | Modest predictive ability |
| 0.60 | 61% | 39% | Good model — trust ML more than TA |
| 0.65 | 71% | 29% | Strong model — ML drives the signal |
| ≥ 0.70 | 75% (max) | 25% | Excellent model — TA is a minor check |

This means symbols with well-trained models (high AUC) get more ML weight, while symbols where the model barely works fall back to the hand-tuned TA rules. The current ML weight used is reported in the signal's `reasons` dict as `ml_weight`.

#### Hyperparameter tuning — automatic every Sunday 14:00 PST

Runs automatically as part of the **weekly full refresh** (Sunday 14:00 PST). After the force re-ingest and signals refresh complete (~10–15 min), the scheduler fires `POST /ml/tune_all`. The tune job runs entirely in the background inside the ml-prediction container and takes ~2–4 hours.

Each symbol gets **60 Optuna trials**, each scored by 5-fold TimeSeriesSplit AUC. Parameters searched: `n_estimators`, `max_depth`, `learning_rate`, `subsample`, `colsample_bytree`, `min_child_weight`, `gamma`, `reg_alpha`, `reg_lambda`. Best params are saved as a per-symbol JSON file. All subsequent daily retrains (Mon–Fri post-close) automatically load those best params — one Sunday tuning run improves the whole week.

To trigger manually: `POST /ml/tune_all` (or `POST /ml/tune {"symbol": "AAPL"}` for one symbol).

### Price alerts — every 1 minute

A separate job (`check_price_alerts`) runs independently every 60 seconds, 24/7. It fetches live prices via yfinance `fast_info` and fires an email the moment a price threshold (`above` / `below`) is crossed. Unlike signal alerts, price alerts are not tied to the market refresh cycle.

### Technical alerts — every market refresh (same schedule as prices/rankings/signals)

`check_technical_alerts()` runs at the end of every market refresh cycle (5× per trading day per market). It reads the last 260 daily bars per symbol from the DB `prices` table, computes EMA/SMA series, and checks all untriggered technical alerts:

| Alert type | How it fires |
|-----------|-------------|
| `cross_above_ema` / `cross_below_ema` | Compares price vs EMA on the last two bars |
| `golden_cross` / `death_cross` | Compares EMA50 vs EMA200 on the last two bars |
| `new_52wk_high` / `new_52wk_low` | Compares today's close vs prior 251-bar high/low |

Technical alerts fire at most once (marked triggered). Requires ≥ EMA period bars for crossovers; ≥ 200 bars for Golden/Death Cross.

### Frontend auto-refresh (browser)

These happen automatically while you have the app open — no page reload needed:

| Component | Refresh interval |
|-----------|----------------|
| Dashboard price cards | Every 60 s |
| Watchlist prices | Every 60 s |
| Positions prices | Every 60 s |
| Stock detail live price card | Every 60 s |
| Market overview indices | Every 60 s |
| In-app alert checker | Every 60 s |

The interval can be changed in **Settings → Price Refresh Interval** (30 s / 60 s / 2 min / 5 min).

### On-demand refreshes

Some data can be force-refreshed from the UI without waiting for the scheduler:

| Button | Where | What it refreshes |
|--------|-------|------------------|
| **↻ Refresh** (stock detail header) | Stock detail | Re-fetches signal + ranking for this stock only |
| **Full Refresh** (stock detail header) | Stock detail | Re-ingests price history + recomputes signal + ranking |
| **Refresh** (Analyst Ratings section) | Stock detail | Bypasses 24 h fundamentals cache; fetches fresh yfinance data |
| **⚡ Train All** (stock detail) | Stock detail | Triggers ML retrain for all watchlist stocks immediately |

### Summary — what refreshes together

```
Every market refresh (5×/day):
  └─ Prices (DB)
  └─ Rankings / K-Scores (DB)
  └─ AI Signals (DB)
  └─ Signal alert emails (if conditions met)
  └─ Technical alert emails (EMA crossover, Golden/Death Cross, 52wk high/low)

Every minute (independent):
  └─ Price alert emails (above/below threshold — live price check)

Every 60 s in the browser:
  └─ Live price display (dashboard, watchlist, positions, stock detail)

Every hour (Redis cache):
  └─ Fear & Greed Index + Market Regime

Every 24 hours (Redis cache):
  └─ Company fundamentals, analyst ratings, earnings calendar, insider activity
```

---

## Stock Detail (`/stock/[symbol]`)

Full drill-down page for a single stock.

### Navigation
- **← Back** button — returns to the previous page

### Header
- Symbol, company name (with Chinese name subtitle for HK stocks), market, exchange, sector
- **Live Price card** — real-time price, day change % (color-coded), and previous close. Fetched from yfinance `fast_info` via the shared `latest-prices` SWR key, auto-refreshes every 60 s. Falls back to "Last Close" from the DB if the live quote is unavailable.
- **Fair Value** card — 200-day SMA fair price with K-Score (see [SCORING.md](SCORING.md))
- **AI Signal** card — BUY / HOLD / WAIT / SELL with bullish probability %; colour-coded green / yellow / orange / red
- **Earnings Calendar badge** — shown when yfinance reports a future earnings date. Displays number of days until the next earnings release and the date itself. Color-coded by urgency:
  - Indigo — more than 21 days away
  - Yellow — within 21 days (⚠ watch for volatility)
  - Red — within 7 days (⚠ Earnings Soon — results may invalidate the current signal)
- **↻ Refresh** / **☆ Watch** toggle

### Key Metrics Strip

A row of compact pill cards shown directly below the header, providing instant access to the most-used valuation ratios without scrolling to Company Financials.

| Metric | Source field | Notes |
|--------|-------------|-------|
| **P/E (TTM)** | `trailingPE` | Trailing 12-month price-to-earnings |
| **Fwd P/E** | `forwardPE` | Forward 12-month P/E based on analyst EPS estimates |
| **EV / Sales** | `enterpriseToRevenue` | Enterprise Value ÷ Revenue — valuation independent of capital structure |
| **EV / EBITDA** | `enterpriseToEbitda` | Enterprise Value ÷ EBITDA — most used for cross-sector comparison |
| **P/B** | `priceToBook` | Price ÷ Book Value per share |
| **Beta** | `beta` | Market sensitivity (1.0 = moves with market; >1 = more volatile) |

Displayed as `—` when data is unavailable for the specific stock.

### Chart

**Time range selector** — row of buttons above the chart to switch between intraday and historical windows.

| Button | Mode | Data source | Bars shown |
|--------|------|-------------|------------|
| 5m | Intraday | Separate API fetch on click | Up to 100 most-recent 5-minute candles |
| 1D | Daily | Loaded on page open | 1 bar — yesterday's close |
| 5D | Daily | Loaded on page open | 5 bars — last week |
| 1M | Daily | Loaded on page open | 21 bars — last month |
| 3M | Daily | Loaded on page open | 63 bars — last quarter (default) |
| 6M | Daily | Loaded on page open | 126 bars — last 6 months |
| 1Y | Daily | Loaded on page open | 252 bars — last year |
| All | Daily | Loaded on page open | Full history in DB (up to ~5 years) |

For daily ranges (1D–All), all data is loaded once on page open; switching between them is instant (no extra API call). A small bar count is displayed next to the buttons. SMA, Bollinger Band, RSI, and MACD overlays are cropped to the selected window automatically.

**Intraday mode (5m):**
- Triggered by clicking the `5m` button; fires a separate `GET /prices?timeframe=5m&limit=100` request.
- Timestamps are stored in UTC and rendered using `UTCTimestamp` (Unix seconds) so lightweight-charts plots the correct intraday positions. The bar count label shows `N bars · UTC` to make the timezone explicit.
- SMA, Bollinger Bands, RSI, and MACD overlays are **not available** in intraday mode (they require a longer history window). S/R levels are also hidden. A toolbar note explains this.
- A "Loading 5m bars…" overlay is shown while the fetch is in progress.
- 5-minute candles are refreshed automatically by the scheduler every 5 minutes during market hours (see [Scheduler](#scheduler) below). Each run is incremental — only bars since the last stored timestamp are fetched, keeping API calls minimal.
- **60-day cap** — yfinance only serves intraday bars from the last 60 days regardless of when the stock was added. The ingestion layer automatically caps the start date at `today − 59 days` for all intraday timeframes (1m, 5m, 15m, 1h) so force-refresh requests don't silently return empty results.

**History depth:** The overview endpoint fetches up to `1260` daily bars (~5 years of trading days). Actual depth depends on how far back yfinance has data for the stock — typically 5+ years for major US and HK equities. Run **Full Refresh** on the stock detail page to re-fetch the maximum available history.

**Chart features:**
- Candlestick chart (lightweight-charts)
- **SMA 20, SMA 50, SMA 200** and **EMA 20, EMA 50** overlaid (daily mode only)
- **Individual line toggles** — each line has its own on/off button in the toolbar: `SMA [20] [50] [200]  EMA [20] [50]  [BB] [Vol]  Panel [RSI] [MACD]`
- Volume histogram, Support/Resistance levels, Fibonacci retracement (daily mode only)

**Indicator colors:**
| Indicator | Color |
|---|---|
| SMA 20 | Sky blue `#38bdf8` |
| SMA 50 | Amber `#f59e0b` |
| SMA 200 | Purple `#a78bfa` |
| EMA 20 | Green `#34d399` |
| EMA 50 | Pink `#f472b6` |
| Bollinger Bands | Faint blue fill |

### Sidebar
- **AI Signal** — BUY/SELL/HOLD, confidence, bullish probability bar
- **Signal Alert button** — subscribe to email notifications for both entry signals (AI Signal improving) and exit warnings (AI Signal deteriorating from BUY). See [Signal Change Email Notifications](#signal-change-email-notifications) below. Shows 🔔 "Signal alert on" (purple, active) or 🔕 "Notify on signal improvement" (grey, inactive). Displays the last known signal as a badge when active.
- **K-Score** — composite + five sub-scores + fair price
- **Fear & Greed Index gauge** — semi-circular dial (0–100) with five color zones: red (Extreme Fear) → orange (Fear) → yellow (Neutral) → light green (Greed) → green (Extreme Greed). Computed every hour from VIX + S&P 500 data. Shows previous close, 1-week, 1-month, and 1-year historical scores below the dial. Includes a **Market Regime** sub-section:
  - Green dot + "Bull Market" — S&P 500 is currently **above** its 200-day MA
  - Red dot + "Bear Market" — S&P 500 is currently **below** its 200-day MA
  - Percentage shown (e.g. "+9.8% vs 200MA") indicates how far above or below
- **ML Prediction** — predict/train per model, Train All shortcut
- **Chart Patterns** — detected patterns with confidence %
- **Support & Resistance** — up to 6 levels, color-coded
- **Fibonacci Levels** — key retracement levels

### Company Financials
Fetched from yfinance `.info`, Redis-cached 24 h.

**Valuation** — Market Cap, Enterprise Value, P/E (TTM), Forward P/E, P/B, EV/Sales, EV/EBITDA

**Financials (TTM)** — Revenue (+ YoY growth), Gross Profit, Net Income, EBITDA, Free Cash Flow, Operating Cash Flow

**Three-column grid** — Balance Sheet (cash vs debt) · Margins (gross/operating/profit) · Returns & Growth (ROE, ROA, earnings growth)

**Per Share & Risk** — EPS, Forward EPS, Book Value, Dividend Yield, Beta, Shares Outstanding

**Volume** — two additional cards in the Per Share & Risk grid:
- **Volume (Today)** — live share volume for the current session (from yfinance `last_volume`)
- **Avg Vol (3M)** — 3-month average daily volume (from yfinance `three_month_average_volume`), with a colour-coded RVOL ratio badge (green > 1.5×, red < 0.5×)

**52-Week Range** — gradient bar showing current live price position between 52W low/high, with percentage-of-range label

**EPS Surprise History** — last 8 reported quarters of earnings vs. analyst consensus (sourced from yfinance `earnings_history`):

| Column | What it contains |
|--------|-----------------|
| Quarter | Reporting period (e.g. Q1 2025) |
| Actual EPS | Reported earnings per share (green = beat, red = miss) |
| Estimate EPS | Analyst consensus estimate at time of report |
| Surprise % | (Actual − Estimate) / |Estimate| × 100, colour-coded |

- **Beat rate badge** — fraction of the last 8 quarters where actual > estimate (e.g. "Beat 6/8"). Green if ≥ 75%, yellow if ≥ 50%, red otherwise.
- **Avg surprise** — mean absolute surprise % across reported quarters
- **Trend arrow** — compares second-half vs first-half average surprise; ↑ improving, ↓ declining

The EPS surprise trend feeds into the Research Engine's fundamental score: a ≥ 75% beat rate adds +5 to the 0–100 fundamental component; ≥ 50% beat rate adds +2. This is also exposed as `eps_beat_rate`, `eps_avg_surprise_pct`, and `eps_surprise_trend` in the `/stocks/{symbol}/fundamentals` API response.

> **Availability:** EPS history requires at least 2 quarters of data in yfinance. Newly listed companies or very small stocks may show "No EPS history available."

**Analyst Ratings & Price Targets** — full analyst consensus section powered by Yahoo Finance (Wall Street aggregate):
- **Rating distribution bar** — stacked colored bar (Strong Buy / Buy / Hold / Underperform / Sell) with individual counts and color-coded labels
- **Consensus badge** — `STRONG BUY` / `BUY` / `HOLD` etc. label + star rating derived from `recommendation_mean` (1.0 = strong buy → 5.0 = sell)
- **Price target range visualization** — gradient bar with absolute-positioned markers for Low / Median / Mean / High analyst targets, plus the current live price as a white dot; upside % from current to mean target shown inline
- **Buy Zone card** — suggested entry range from analyst low target to current price; shows nearest technical support level and upside % to mean target; warns if price is already above analyst consensus
- **Sell / Target Zone card** — suggested take-profit range from analyst mean to high target; also shows K-Score fair value and nearest resistance level
- **Recent Analyst Actions** — table of individual firm activity from the last 90 days (see below)
- **Reliability disclaimer** — "Via Yahoo Finance · consensus of Wall Street analysts · updated daily · not a personalised recommendation" shown in the section header

#### Recent Analyst Actions table

Shows up to 20 individual analyst firm events from the last 90 days, sorted newest first. Each row shows:

| Column | What it contains |
|--------|-----------------|
| Date | MM-DD of the analyst action |
| Firm | Investment bank or brokerage name (e.g. Goldman Sachs, Morgan Stanley) |
| Action | What the analyst did — see colour coding below |
| Grade change | Previous rating → new rating (e.g. Hold → Buy), colour-coded by grade |

**Action colour coding:**

| Colour | Actions |
|--------|---------|
| Green | Upgraded |
| Red | Downgraded |
| Purple | Initiated Coverage On |
| Light green | Raised Target |
| Orange | Lowered Target |
| Grey | Maintained, Reiterated |

**Grade colour coding:** Strong Buy / Overweight / Outperform / Buy = green · Hold / Neutral / Equal Weight = yellow · Sell / Underweight / Underperform = red

**Data source:** `ticker.upgrades_downgrades` via yfinance, cached as part of the 24-hour fundamentals cache. Click **Refresh** in the section header to bypass the cache.

**Why there are no per-firm dollar price targets:** Individual analyst price targets (e.g. "Goldman Sachs: $180") are proprietary data sold by Bloomberg Terminal, FactSet, and Refinitiv. They are not available through Yahoo Finance's free API. The aggregate Low / Mean / Median / High targets shown in the Price Target Range bar above represent the spread across all contributing analysts — that is the closest available proxy for what individual firms are targeting. Adding per-firm targets would require a paid integration (e.g. Financial Modeling Prep).

> **Reliability note:** Analyst data is sourced from Yahoo Finance, which aggregates ratings from major investment banks. Coverage is excellent for US large-cap stocks (20–50 analysts) and thinner for small caps or HK stocks. The consensus mean is a useful directional indicator but analyst price targets scatter widely — treat them as one input alongside K-Score, technical signals, and your own research.

**Insider Activity (Last 6 Months)** — summarises open-market insider transactions reported to the SEC (sourced from Yahoo Finance).
- **Buy / Sell bar** — proportional green/red bar showing the relative volume of purchases vs sales
- **Share counts** — number of shares purchased and sold, with transaction count next to purchases
- **Net summary card** — net shares (purchases minus sales); green if net buyers, red if net sellers. Shows % of float when available.

| Reading insider activity | What it suggests |
|--------------------------|-----------------|
| Strong net buying (large share count, multiple transactions) | Insiders believe the stock is undervalued. Most bullish signal. |
| Net buying in single large transaction | Likely an option exercise or compensation — less meaningful |
| Net selling, but small % of holdings | Routine liquidity, portfolio balancing — neutral |
| Heavy net selling (>2% of float) | Worth investigating — insiders may know something. Check recent filings. |

> **Important:** insider activity from yfinance covers only the most recent 6-month SEC filing summary. It does not include exercise-of-options transactions (which inflate sell counts artificially). Small companies may have very few transactions. Always check the raw SEC Form 4 filings for full context.

> **Source:** SEC Form 4 filings aggregated by Yahoo Finance. Data is refreshed as part of the 24-hour fundamentals cache.

### AI Game Plan

A one-click AI-generated 10-day swing trade plan for the stock. Available on every stock detail page when an AI provider is configured in Settings.

Click **📋 Generate 10-Day Game Plan** to generate. The AI returns a structured plan including:
- **Title** — one-line trade thesis
- **Entry zones** — up to 3 labelled price levels with rationale (Aggressive / Base / Conservative)
- **Stop Loss** — price + rationale
- **Take Profit** — price + rationale
- **Catalysts** — key upcoming events or technical setups
- **Key Risk** — the main thing that could invalidate the trade

Once generated:
- **⎘ Copy** — copies the full plan as formatted plain text to your clipboard
- **📌 Save to Board** — saves the plan to your Trade Board (`/board`) with stage = Planning and entry/stop/target prices pre-filled

> Previously this button was only shown for BUY or HOLD signals. It now appears for all stocks regardless of signal, so you can plan shorts or research any name freely.

### AI Chat Panel
Collapsible "Ask AI" panel below the financials section.

- Powered by whichever AI provider you configured in **Settings → AI Assistant**
- The AI receives full stock context automatically: current price, signal (BUY/SELL/HOLD + confidence), K-Score, fair value, and the 5 most recent news headlines
- **Suggested questions**: "Should I buy now?", "What are the key risks?", "Summarise the latest news", "What does the K-Score mean?"
- Full conversation history within the session; **Clear** button to reset
- Press **Enter** or click **Send** to submit
- Shows which AI provider is active (Claude / DeepSeek badge)
- If no provider is configured, shows a link to Settings

### News & Sentiment
- Articles from the enabled news sources (configurable in Settings)
- Bullish / Bearish / Neutral sentiment badges (VADER scoring)
- Click headline to open original article

---

## Signal Change Email Notifications

Proactive email alerts that cover **two scenarios**: entry signals (when the AI Signal is improving and conditions are aligned) and exit warnings (when a stock you hold on a BUY signal starts to deteriorate). One subscription covers both — no separate setup required.

### How to subscribe

On any stock detail page, click the **🔕 Notify on signal improvement** button in the sidebar (below the AI Signal card). It turns purple with a 🔔 icon when active. Click again to unsubscribe. The alert is stored server-side (PostgreSQL `signal_alerts` table) and fires even if you aren't logged in at the time.

By default, alert emails are sent to the email address on your account (set in Settings). You can change the email address via `PUT /auth/me` or by updating your profile.

### Trigger conditions

There are two independent trigger types. Each has different filtering rules.

---

#### Type 1 — Entry Signal (AI Signal improving)

Fires when the signal improves **and** analyst consensus is bullish. Designed for stocks you're watching but haven't entered yet — the email tells you when both the AI and Wall Street are aligned.

**Signal must improve** (one of these transitions):

| Previous signal | New signal | Meaning |
|-----------------|-----------|---------|
| SELL | HOLD | Stock is moving out of sell territory |
| SELL | WAIT | Slight recovery from sell — still cautious |
| SELL | BUY | Strong reversal — SELL flipping directly to BUY |
| WAIT | HOLD | Stabilising from a bearish lean |
| WAIT | BUY | Turning bullish from a cautious signal |
| HOLD | BUY | Bullish confirmation from a neutral position |

**Analyst consensus must also be bullish:**

| Rating value | Qualifies? |
|-------------|-----------|
| `strong_buy` | ✓ Yes |
| `buy` | ✓ Yes |
| `outperform` | ✓ Yes |
| `hold` | ✗ No |
| `underperform` | ✗ No |
| `sell` / `strong_sell` | ✗ No |

Both conditions must be true simultaneously. If the analyst rating is neutral or bearish, no email is sent even if the AI signal improved.

**For BUY transitions specifically — 5-layer conviction gate:**

When the new signal is BUY (the highest-priority entry), a stricter gate is applied on top of the analyst filter. All five layers must pass or the email is suppressed:

| Layer | What is checked | Threshold |
|-------|----------------|-----------|
| 1 — Trend | SMA50 above SMA200 AND price above SMA50 | Both must be true |
| 2 — K-Score | Composite ranking conviction score | ≥ 55 |
| 3a — RSI | RSI in healthy bullish zone | 45–65 |
| 3b — Stoch RSI | Recovering from oversold (cross-up or still oversold, not overbought) | %K < 80 AND (cross-up OR %K < 25) |
| 3c — MACD | Histogram positive and rising, or zero-line crossover | Any one of the three |
| 4 — OBV | On-Balance Volume confirming price direction | `obv_bullish` = true |
| 5 — ML | XGBoost bullish probability | > 70% |

**Disqualifiers** — even if all layers pass, the BUY email is suppressed if:
- Bearish RSI divergence is detected (price rising but momentum fading)
- Stoch RSI is overbought (> 80) without a recent cross-up

This gate exists because raw BUY signals have a 20–30% false-positive rate without further filtering. Requiring all layers to align reduces noise and ensures you only receive an email when the setup is genuinely high-conviction.

---

#### Type 2 — Exit Warning (AI Signal deteriorating from BUY)

Fires whenever the signal weakens from BUY — **regardless of analyst consensus**. Designed for stocks you are already holding: you need this warning even when analysts still like the stock, because the AI may be picking up technical deterioration before the fundamentals show it.

**Signal must be deteriorating from BUY** (one of these transitions):

| Previous signal | New signal | Email subject prefix | Urgency |
|-----------------|-----------|----------------------|---------|
| BUY | HOLD | ⚠ Signal Weakening | Low — momentum fading, monitor the position |
| BUY | WAIT | ⚠ Signal Weakening | Medium — signal deteriorating, consider reducing |
| BUY | SELL | ⚠ SELL Alert | High — full exit signal, review immediately |

No analyst filter is applied. The email fires as soon as the transition is detected at any of the 5 daily check times.

The analyst rating is still **displayed** in the email body (so you can see if analysts have also turned negative) but it is not a gating condition for exit warnings.

---

### When checks run

Signal alerts are checked at the end of every scheduled market refresh — 5 times per trading day per market:

| Market | Check times (local market time) |
|--------|---------------------------------|
| US (NYSE/NASDAQ) | 09:00, 10:45, 12:45, 14:45, 16:30 |
| HK (HKEX) | 09:00, 10:30, 14:15, 15:30, 16:30 |

The 16:30 post-close check is the most complete — it runs after the final price bar of the day is ingested and rankings/signals are refreshed.

### What the email contains

Both entry and exit emails share the same structure. Exit warnings are visually differentiated by the severity of the transition:

| Element | Entry signal email | ⚠ Signal Weakening (BUY→HOLD/WAIT) | ⚠ SELL Alert (BUY→SELL) |
|---------|-------------------|-------------------------------------|--------------------------|
| Subject prefix | `Signal Alert:` | `⚠ Signal Weakening:` | `⚠ SELL Alert:` |
| Header colour | Purple | Red | Red |
| Header icon | 📊 | ⚠ | ⚠ |
| Call-to-action banner | "Both indicators are aligned — review before acting" | Red banner: "AI signal has reversed — consider reviewing your position" | Red banner: "AI signal has reversed — consider reviewing your position" |

All emails include:
- **Signal transition** — the previous and new signal values in a styled badge (e.g. BUY → SELL)
- **Analyst consensus** — the current Wall Street rating and a description of the transition mood
- **Bullish probability and confidence** — from the signal engine at the time of the alert
- **Reasons table** — a full breakdown of every indicator behind the signal so you can see *why* it changed:

| Row | What it shows |
|-----|--------------|
| Market regime | Bull (S&P above 200MA) or Bear (higher BUY threshold applied) |
| Trend above SMA50 | Yes / No |
| SMA50 above SMA200 | Yes / No — golden cross regime |
| Golden cross fired | Yes / No — recent SMA50 × SMA200 crossover event |
| Death cross fired | ⚠ Yes / No — recent SMA50 dropping below SMA200 |
| RSI (14) | Numeric value with zone note (oversold / recovering / bullish / overbought) |
| Stoch RSI %K | 0–100 with oversold / overbought / cross-up note |
| RSI divergence | Bullish (price down, RSI up) or Bearish (price up, RSI fading) |
| MACD histogram | Value + ↑ rising or ↓ falling; notes zero-line crossover |
| Bollinger %B | 0–1 position within the bands |
| ADX | Trend strength with zone note (weak / moderate / strong) |
| Volume (OBV bullish) | Yes / No — On-Balance Volume confirming price direction |
| Volume Z-score | Standard deviations above average daily volume |
| ML probability | XGBoost bullish probability % |
| Next earnings | Date + days away |
| Insider activity (6M) | Shares bought / sold + net, % of float |

- **Earnings warning** — if earnings are within 7 days, a yellow warning banner appears reminding you that results may override the signal. If within 21 days, a plain note is included.

**BUY transition emails additionally include:**

- **5-Layer Conviction Gate summary** — a green checklist showing exactly which layers passed and why. Each item is a plain-English description (e.g. "K-Score: 72 — conviction positive", "ML probability 78% > 70% threshold"). This confirms at a glance why the conviction gate opened.

- **10-Day Game Plan** — a structured trade setup derived from the technical data at the time the alert fires:

| Section | Contents |
|---------|---------|
| Entry Strategy | Three entry levels: Limit buy 50% (1.5–2% below current), Limit buy 50% (deeper pullback 3.5–4%), Breakout 50% (2% above current). Each has a note explaining the technical rationale (e.g. "pullback to SMA50 support zone"). |
| Stop Loss | Absolute price level ~5.5% below current. Note describes what a close below this level means (e.g. "golden-cross breakdown" if SMA50 > SMA200). |
| Take Profit | Analyst mean price target if available and > 3% above current; otherwise +12% from current. |
| Catalysts | Up to 3 bullet points: earnings runway, analyst upgrade potential, SMA structure, RSI zone, MACD confirmation, OBV trend. |
| Key Risk | One sentence — bear market regime warning, upcoming earnings binary risk, or generic market override note. |

### What the email does NOT do

- It does not repeat for the same transition. Once fired, `last_signal` is updated — the same BUY→SELL will not fire again unless the signal recovers and then re-deteriorates.
- A stock can generate multiple alerts in sequence as the signal moves through stages (e.g. BUY→HOLD fires, then separately HOLD→WAIT fires if conditions worsen further).
- Price alerts (separate feature — see [Alerts](#alerts-alerts)) are not affected by this system.
- This is not personalized investment advice. The email includes a disclaimer.

### Unsubscribing

Click the 🔔 "Signal alert on" button on the stock detail page to unsubscribe. This deletes the alert from the server — no further emails will be sent for that stock unless you re-subscribe.

---

## Fear & Greed Index

A market sentiment gauge (0–100) shown on the stock detail page sidebar. Based on CNN's Fear & Greed methodology, computed from VIX levels and S&P 500 price data. Updated hourly.

### Score interpretation

| Range | Label | What it means |
|-------|-------|---------------|
| 0–24 | **Extreme Fear** | Panic selling — market is deeply oversold |
| 25–44 | **Fear** | Cautious, risk-off mood — investors are nervous |
| 45–55 | **Neutral** | Balanced sentiment — no strong directional bias |
| 56–74 | **Greed** | Optimistic, risk-on mood — buyers in control |
| 75–100 | **Extreme Greed** | Euphoria — everyone is buying, market overextended |

### Higher score = do NOT buy more

The index is a **contrarian indicator**. High scores signal danger, not opportunity:

- **High score (Extreme Greed)** — the crowd is already all-in. Prices are extended, pullback risk is elevated. Avoid chasing. Consider trimming positions.
- **Low score (Extreme Fear)** — the crowd is panic-selling. Stocks are often oversold. Better risk/reward for entering quality positions.

> "Be fearful when others are greedy, and greedy when others are fearful." — Warren Buffett

### How to use it alongside AI signals

| Fear & Greed | AI Signal | Interpretation |
|--------------|-----------|----------------|
| Extreme Fear + BUY | BUY | Highest-conviction buy setup — market overreaction, individual stock has technical strength |
| Extreme Fear + SELL | SELL | Broad selloff — wait for stabilization before acting |
| Extreme Greed + BUY | BUY | Be cautious — good stock, but market is extended; size position conservatively |
| Extreme Greed + SELL | SELL | Double warning — take profits or exit |
| Neutral | Any | No sentiment overlay — rely on AI signal and K-Score alone |

### What it does not tell you

- It is a market-wide sentiment measure, not stock-specific. A single stock can be a great buy during Extreme Greed (e.g. sector rotation) or a bad buy during Extreme Fear (e.g. fundamental collapse).
- It is not a timing tool. The market can remain in Extreme Greed for weeks before correcting.
- Use it as a **risk filter** to size positions and manage stops, not as a standalone buy/sell trigger.

### Historical scores

The gauge shows four reference points below the dial: **Previous Close**, **1 Week Ago**, **1 Month Ago**, and **1 Year Ago**. These help you see whether sentiment is improving (fear → greed) or deteriorating (greed → fear), which provides directional context beyond the current reading.

### Market Regime

Displayed directly below the Fear & Greed dial:

| Regime | Condition | Meaning |
|--------|-----------|---------|
| 🟢 Bull Market | S&P 500 above its 200-day MA | Long-term uptrend intact — trend-following signals are more reliable |
| 🔴 Bear Market | S&P 500 below its 200-day MA | Long-term downtrend — treat BUY signals with extra caution; prefer smaller positions |

The percentage shown (e.g. `+9.8% vs 200MA`) indicates how far above or below the S&P 500 currently sits. Large positive values in a Bull Market confirm trend strength; large negative values in a Bear Market suggest deeper selling pressure.

**Best combination**: Extreme Fear + Bull Market regime + AI Signal = BUY. This means the broad market is panicking short-term but the long-term trend is intact — a classic buy-the-dip setup.

---

## Opportunities (`/opportunities`)

Strategy-filtered stock screener. Surfaces the best candidates from the **current user's watchlist** for each trading style. Only stocks the logged-in user is tracking are scored and ranked. Linked in the nav bar (highlighted purple).

### Strategies

| Strategy | Icon | Horizon | How stocks are ranked | Min filter |
|----------|------|---------|----------------------|-----------|
| **Top Picks** | ⭐ | Any | K-Score + AI signal bonus (+8 BUY, +3 HOLD) | None |
| **Swing Trade** | 📊 | 5–30 days | Technical (40%) + Momentum (25%) + signal strength | Signal = BUY or HOLD, Technical ≥ 45 |
| **Short-Term** | ⚡ | 1–5 days | Momentum (50%) + Technical (25%) + today's % move × 3 | Momentum ≥ 40 |
| **Long-Term** | 🏛️ | 6–24 months | Value (40%) + Growth (30%) + upside to fair value × 0.6 | Value ≥ 40 or Growth ≥ 50 |
| **Growth** | 🚀 | Medium | Growth (50%) + Momentum (30%) + Technical (20%) | Growth ≥ 50 |

**Top Picks scoring note:** The raw K-Score is blended with a signal bonus so that two stocks with equal composite scores are ranked with the BUY-signal stock higher. Non-BUY stocks are not excluded — they remain visible if their K-Score is high enough.

### Score normalisation

All strategy scores are capped at **100** via `Math.min(100, score)` before display and sorting. Without this cap, stocks with extreme sub-scores (e.g. very high momentum combined with a strong BUY signal) could produce scores above 100, making the ranking uninterpretable and the progress bar visualisation overflow. The cap also ensures the Confluence grade thresholds (80+, 65–79, 50–64, <50) apply consistently across all strategies.

**Per-strategy score formulas (all capped at 100):**

| Strategy | Formula |
|----------|---------|
| Swing | `tech × 0.40 + mom × 0.25 + signal_bonus + conf × 0.15` |
| Short-term | `mom × 0.50 + tech × 0.25 + min(|1d_chg| × 3, 15) + vlt × 0.10` |
| Long-term | `val × 0.40 + grow × 0.30 + min(max(upside, 0), 25) + vlt × 0.15` |
| Growth | `grow × 0.50 + mom × 0.30 + tech × 0.20` |
| AI Signal | `conf × 0.70 + bullish_prob × 50 + tech × 0.15 + mom × 0.10` |

Where: `tech` = technical sub-score (0–100), `mom` = momentum sub-score, `val` = value sub-score, `grow` = growth sub-score, `conf` = AI signal confidence, `vlt` = volatility (inverse), `signal_bonus` = 8 for BUY, 3 for HOLD, 0 otherwise.

### Filters
- **Market filter** — All / US / HK
- Each strategy also applies the minimum sub-score filter shown in the table above

### Per-stock card
- **Rank badge** — gold / silver / bronze for top 3
- **Signal badge** — BUY / HOLD / WAIT / SELL with colour coding
- **Market badge** — US (blue) / HK (pink)
- **Why this stock** — up to 3 specific reasons generated from the data (e.g. "AI signal BUY — 72% confidence", "+18.3% upside to fair value $215.40", "Strong price momentum (82/100)")
- **T / M / V / G mini progress bars** — sub-score visualisation at a glance
- **Key metric** — strategy-specific highlight (e.g. Upside % for Long-Term, Today % for Short-Term)
- **Live price + day change** — same 60 s refresh as dashboard
- **🔔 bell button** — click to open the alert suggestion panel (see below)
- Click the card body → stock detail page

### 🔔 Alert suggestion panel

Click the bell icon on any opportunity card to open a panel of up to 4 data-driven alert suggestions for that stock. The panel fetches the full technical overview (RSI, MACD, Bollinger Bands, SMA levels, support/resistance) on first open and caches it for the session.

Suggestions are generated in priority order:

| Source | What is checked | Alert suggested |
|--------|----------------|----------------|
| RSI ≥ 74 | Heavily overbought | Stop loss at SMA20 |
| RSI ≥ 65 | Extended | Stop loss −7% |
| RSI ≤ 25 | Severely oversold | Alert above SMA20 (bounce trigger) |
| RSI ≤ 38 | Oversold | EMA20 crossover (recovery entry) |
| Bollinger upper (position ≥ 90%) | Near upper band | Stop at BB mid (mean reversion) |
| Bollinger lower (position ≤ 10%) | Near lower band | Target BB mid (bounce play) |
| MACD histogram just flipped positive | Bullish crossover | EMA20 crossover to confirm momentum |
| MACD histogram just flipped negative | Bearish crossover | Stop at SMA20 |
| Price within 4% of SMA200 | Key trend line test | Alert at SMA200 (above or below) |
| SMA50/200 gap < 2.5% | Crossover imminent | Golden Cross or Death Cross alert |
| Nearby S/R levels (0.5%–7% away) | Support/Resistance | Breakout or breakdown alert at the level |
| Fair price > current × 1.03 | Upside exists | Take profit at fair value |
| No stop loss generated above | Fallback | Stop loss −8% |

Each suggestion shows a main label (the technical reason) and a sub-label (the specific price or action). Click **Set Alert** to create it instantly without navigating away. Multiple alerts can be set from the same panel. Indicator data is loaded once and cached — reopening the panel for the same stock is instant.

### Near-Term AI Outlook

A collapsible section at the top of the page powered by the configured AI assistant. Click **✦ Generate Outlook** to run an AI analysis of all opportunity stocks:

1. Fetches the 3 most recent news headlines per stock
2. Combines news, AI signal, K-Score sub-scores, and today's price change into a prompt
3. Returns a BULLISH / BEARISH / NEUTRAL prediction with a horizon, confidence level, one-sentence reason, and 3 bullet-point catalysts
4. Results are sorted: BULLISH (high confidence first) → NEUTRAL → BEARISH
5. Each card links to the stock detail page

The outlook section shows a summary count (e.g. "▲ 8 bullish · ▼ 3 bearish") and can be collapsed with the Hide button. Click **↺ Refresh** to re-run the analysis.

Requires an AI provider configured in **Settings → AI Assistant**.

### Data source
Reuses SWR keys `rankings-all`, `signals-all`, `latest-prices`, and `watchlist` (all already fetched by the dashboard — no extra network calls). All scoring is pure frontend computation; watchlist filtering is applied client-side before ranking. Alert suggestion data is fetched on-demand per stock via `GET /aggregate/overview/{symbol}`.

---

## Stock Screener (`/screener`)

Filter and sort across every tracked stock in the system — not just your watchlist. Designed for discovery: finding stocks that meet a specific set of technical, momentum, or signal criteria on any given day.

### Filters

All filters are combinable. Results update instantly as you type or toggle.

| Filter | How it works |
|--------|-------------|
| **Search** | Match symbol or company name (partial, case-insensitive) |
| **Market** | All / US / HK |
| **AI Signal** | Multi-select pills — BUY, HOLD, WAIT, SELL. Selecting multiple shows stocks matching any of the selected signals |
| **Min K-Score** | Only show stocks with a composite K-Score at or above the threshold (0–100) |
| **Min Technical** | Minimum technical sub-score threshold |
| **Min Momentum** | Minimum momentum sub-score threshold |
| **Min Value** | Minimum value sub-score threshold |
| **Min Growth** | Minimum growth sub-score threshold |
| **Min Bullish %** | Minimum AI bullish probability, entered as a percentage (e.g. 65 = ≥65%) |
| **Day Chg % (range)** | Min and/or max day change — e.g. Min +2 for today's breakouts, Max −3 for oversold dips |
| **My Watchlist** | Toggle to restrict results to stocks you are already tracking |

A **Reset filters** button appears whenever any filter is active.

### Results table

All columns are sortable — click a header to sort descending; click again to sort ascending.

| Column | Description |
|--------|-------------|
| Symbol / Name | Ticker and company name; click any row to open stock detail |
| Market | US or HK badge |
| Signal | AI Signal badge (BUY / HOLD / WAIT / SELL), colour-coded |
| K-Score | Composite score with a mini colour bar (green ≥70, indigo 50–70, amber <50) |
| Technical | Technical sub-score bar |
| Momentum | Momentum sub-score bar |
| Value | Value sub-score bar |
| Growth | Growth sub-score bar |
| Bullish % | AI bullish probability — green ≥65%, yellow 50–65%, red <50% |
| Confidence | Signal confidence % |
| Day Chg | Day change %, green positive / red negative |
| Price | Latest price |
| Actions | **+ Watch** button for stocks not yet in your watchlist; "★ Watching" for those already tracked |

The footer shows the total number of stocks currently displayed.

### How it differs from Rankings

| | Screener | Rankings |
|--|----------|---------|
| Stock universe | All active stocks in the system | Your watchlist only |
| Filters | 10+ (thresholds, signal, search, day-change range) | Market + Signal tab |
| Sort | Any column, click to toggle | Fixed: K-Score descending |
| Search | Yes | No |
| Add to watchlist | Per-row button | No |
| Use case | Discovery — finding new stocks to watch | Monitoring — checking on stocks you already follow |

### Data sources

Uses the same three SWR-cached endpoints as the dashboard — `rankings-all`, `all-signals`, and `latest-prices`. All filtering and sorting is client-side; no additional network requests are made when you change filters.

---

## Rankings (`/rankings`)

Leaderboard of all active stocks sorted by K-Score. A quick, always-sorted view — open it and immediately see which stocks are scoring highest right now.

- **Market tabs** — switch between All / US / HK
- **Signal filter tabs** — ALL / BUY / HOLD / WAIT / SELL
- **Sortable columns** — K-Score, Technical, Momentum, Value, Growth, Volatility, Price, Change%
- **Fair price column** — compare current price to the K-Score estimated fair value
- **Volume column** — today's share volume, formatted as B/M/K (e.g. 42.3M)
- **vs Avg column** — today's volume vs 3-month average volume (RVOL), colour-coded: green ≥ 2×, light green ≥ 1.5×, red < 0.5×
- HK stocks show Chinese name as a subtitle in the Name column
- Click any row to go to stock detail

> **Rankings vs Screener:** Rankings is a fast leaderboard — the default view is sorted by K-Score with no filters, so you immediately see the highest-quality stocks. The Screener is for deliberate filtering sessions when you have specific criteria in mind (e.g. BUY signal + Technical ≥ 60 + up >1% today). Both pages pull from the same underlying data.

> **Performance note:** Rankings reads from pre-computed scores in the database (refreshed 5× per trading day by the scheduler). The leaderboard endpoint is O(1) regardless of how many stocks are tracked — no price history is recomputed on each page load.

---

## Watchlist (`/watchlist`)

Your curated list of stocks to monitor closely. Each watchlist is independent — it has its own stock list, its own trading style, and its own signal view.

### Features
- Multiple named lists — create / delete / switch via tabs; move stocks between lists
- **Per-list trading style** — assign SHORT, SWING, or LONG to each list so signal columns automatically show the right horizon for that list's purpose (see [Trading Style System](#trading-style-system) below)
- Signal stats bar (BUY / HOLD / WAIT / SELL counts with colour-coded tiles)
- Signal filter tabs (ALL / BUY / HOLD / WAIT / SELL)
- Sort by: Symbol, Signal, K-Score, Change%, Price
- Auto-refreshing live prices every 60 s
- **Compare view** — select up to 8 stocks for a base-100 relative performance SVG chart (30 / 60 / 90 / 180 / 365 day periods)

### Per-list trading style

Every watchlist can be assigned its own trading style, overriding the global setting for that list only.

**Setting the style when creating a list** — the New Watchlist modal includes a style picker with four options:

| Option | Signals used |
|--------|-------------|
| Global default | Follows Settings → Trading Style (default) |
| Short Term | SHORT signals — 1–5 day pure TA |
| Swing Trade | SWING signals — 5–20 day balanced (default) |
| Long Term | LONG signals — 30–90 day fundamentals-heavy |
| Growth / Momentum | GROWTH signals — relaxed thresholds for high-vol AI/tech names |

**Changing style on an existing list** — each tab shows a small colored badge when a style is assigned (`SHORT` in red / `SWING` in indigo / `LONG` in green / `GROWTH` in purple). Click the badge to cycle to the next style. On the active tab with no style set, a `+style` prompt appears as a reminder.

When you switch between lists, all signal columns (BUY/HOLD/WAIT/SELL, confidence, bullish probability) immediately reload using that list's style — no manual switching needed.

**Practical use** — create separate lists for different time horizons:
- "Swing Trades" → SWING signals (earnings compression active, balanced TA+ML)
- "Long Holds" → LONG signals (K-Score boost, fundamentals weight, no earnings filter)
- "Spec Plays" → SHORT signals (pure TA, no news/earnings filter, ideal for small caps)
- "Growth Watch" → GROWTH signals (relaxed ADX/RSI thresholds, no RS penalty, ideal for NVDA-style momentum names)

### Per-stock card
- Price + day change, signal badge, K-Score bar, fair value, note preview, price alert banner
- HK stocks show Chinese company name as a subtitle
- **📝 Notes** — free-text per stock, stored in namespaced localStorage per user
- **🔔 Price Alerts** — target price + Above/Below trigger; yellow highlight when triggered
- **📡 Signal Alerts** — toggle AI signal email notifications for that stock; purple when active
- **+ POS** — navigate to Positions with symbol pre-filled
- **⇄ Move** — move to another watchlist (shown when >1 list exists)
- **✕** — remove from watchlist

### Bulk signal alert controls
Shown in the watchlist tab row when stocks are present:

- **📡 Notify All (N)** — subscribes all N unsubscribed stocks to AI signal alerts in one click. Shows "All notified (N)" and greys out when all are already subscribed.
- **Mute All** — removes signal alert subscriptions for every stock in the current list. Only visible when at least one stock is subscribed.

Both buttons use the email address from your account profile (Settings → Profile).

---

## Trade Board (`/board`)

A persistent Kanban board for tracking trade ideas across four lifecycle stages. Every card is stored server-side per user, so it survives page refreshes and sessions.

### Stages

| Stage | Colour | Purpose |
|---|---|---|
| Radar | Grey | On radar — shortlisted from screener or forecast |
| Planning | Indigo | AI game plan generated; evaluating entry |
| Active | Green | In trade — monitoring |
| Closed | Dark grey | Trade completed — P&L recorded |

> The stage is stored as `"watch"` in the database; the display label is **Radar**.

### Cards

Each card shows:
- **Symbol** (links to stock detail page) + source badge (📋 Game Plan / 🔮 Forecast / ✏️ Manual)
- **Entry / Stop / Target prices** in colour-coded monospace
- **R:R ratio** — auto-calculated as `(target − entry) / (entry − stop)` when all three prices are set
- **Notes** — truncated to 120 chars; expand with ▼
- **Full game plan details** — when expanded, shows title, entry zones with rationale, catalysts, and risk summary (only if saved from a game plan)
- **Stage selector** — click any stage pill to move the card instantly
- **Relative date** — "Today / Yesterday / Nd ago" based on last update

### Fill price tracking (actual entry)

When a card is moved to **Active**, a **Record Fill** modal appears:
- **Fill Price** (required) — the actual price you were filled at, which may differ from the planned entry
- **Shares** (optional) — number of shares bought; enables dollar P&L calculation

Both values are saved to the card and displayed in the prices row as "Fill $X × N" alongside the original "Plan $X". All P&L calculations use the fill price when available, falling back to the planned entry price if no fill was recorded.

The fill can be skipped — clicking "Skip, use plan price" moves the card to Active without recording fill data.

**Auto-sync to Positions:** If shares are entered in the Fill modal, the position is automatically created on the Positions page (`/positions`) the moment you confirm. If a position for that symbol already exists, a BUY trade is added and the average cost is recalculated. No manual re-entry required.

### Editing an Active card

Active cards show a **✎** button at the right of the price chip row. Clicking it opens a 2-column inline edit panel directly on the card — no modal, no page navigation:

| Field | What it changes |
|---|---|
| Shares | `shares` on the trade plan — updates the dollar P&L and risk calculations live |
| Fill Price | `actual_entry_price` — the real fill price used for P&L |
| Stop Loss | `stop_loss` — updates the Stop chip and all stop-monitoring alerts |
| Take Profit | `take_profit` — updates the Target chip and "near target" warnings |

Press **Save** to write all changed fields in a single `PUT /board/{id}` call. Press **Cancel** to discard. Fields left blank are not overwritten — only non-empty inputs are sent.

**Position sync on edit:** If shares are changed (e.g., 100 → 150 for an add-on, or 100 → 50 after a partial close), the difference is applied to the linked position automatically — a BUY is added for an increase, a SELL is recorded for a decrease. The fill price in the edit form is used as the trade price.

> This is useful when you partially filled a position, adjusted your stop after entry, or need to correct a mis-typed fill price.

### Closed trade P&L tracking

When a card is moved to **Closed**:
- An **exit price input** appears on the card — type the price you closed at and press Enter to save it.
- **P&L %** is calculated as `(exit − effective_entry) / effective_entry × 100`, where `effective_entry = actual_entry_price ?? entry_price`.
- **Dollar P&L** is shown when shares are recorded: `(exit − effective_entry) × shares`.
- **% of target reached** — shown when a take_profit level was set.
- `closed_at` timestamp is set automatically the first time a card enters the Closed stage.

**Auto-sync SELL to Positions:** When exit price is saved and the card has `shares` recorded, a SELL trade is automatically posted to the Positions page for the full share count at the exit price. If shares in the position are fewer than the board quantity (e.g., a partial close was recorded earlier), the sell is capped at the available position size.

### Trading style badge on closed cards

Each closed card shows a small colored badge in the P&L section — **SHORT** (red) / **SWING** (indigo) / **LONG** (green) / **GROWTH** (purple) — recording which trading style was active when the position was opened. This lets you compare performance across different signal approaches at a glance.

The style is captured automatically when a card is activated (from the global or per-list style setting at that moment). It can only be set once — it records historical context, not current state.

### Performance Summary bar

Appears above the board when at least one closed trade has both an entry and exit price:

**Overall stats row:**

| Stat | Description |
|---|---|
| Closed | Total closed trades with P&L data |
| Win Rate | % of closed cards with positive P&L |
| Avg Return | Average P&L % across all closed cards |
| Best | Highest individual P&L % |
| Worst | Lowest individual P&L % |

**By Style breakdown row** (shown when trades from multiple styles exist):

For each style that has at least one closed trade, a chip shows:
- Style label (SHORT / SWING / LONG) with its color
- Trade count
- Win rate %
- Average return %

This lets you evaluate whether SHORT, SWING, LONG, or GROWTH signals have been more accurate for your actual trades over time.

### Adding cards

Four ways to create a board card:

1. **Stock detail page** — after the AI generates a game plan, click **📌 Save to Board** in the game plan card header. Saves with stage = Planning, entry/stop/target prices pre-filled.
2. **Forecast page** — each AI pick has a **📌 Save to Board** button. Saves with stage = Radar, notes from the pick's setup/catalyst/risk text.
3. **Manual** — click **+ Add** in the Radar column header on the board itself. Enter a symbol and optional notes.
4. **Unified + Add ▾ button** — appears on Screener results and Forecast cards. Opens a dropdown with two sections: **Watchlists** (add to any named watchlist with item count shown) and **Trade Board** (add to Radar). A checkmark appears once added — prevents duplicates within the same session.

### Portfolio Risk Dashboard

Shown below the Kanban board when ≥ 2 active positions have shares and an entry price recorded. Click **Compute Risk** to trigger the calculation — it is on-demand because fetching correlation data for a large position set takes 15–30 seconds.

| Stat | Description |
|---|---|
| Portfolio β | Weighted-average beta vs S&P 500. >1.5 = high market sensitivity (red), <0.8 = defensive (green) |
| 1-day VaR 95% | Value-at-Risk: estimated max 1-day loss in 95% of scenarios |
| Sector weights | Donut chart of sector concentration |
| Correlation matrix | Heatmap of pairwise return correlations |
| Individual betas | Beta for each position vs the benchmark |

Results are cached for 5 minutes — clicking Compute Risk again within that window returns the cached result instantly.

### Drag-and-drop between columns

Cards can be dragged from any column to any other. Dragging highlights the target column with a colored border. Dropping onto **Active** triggers the Fill modal to capture the actual fill price — same as clicking the stage pill.

### API endpoints
```
GET    /board              # list all trade plans for the current user (ordered by last update)
POST   /board              # create a plan {symbol, stage, game_plan, entry_price, stop_loss, take_profit, notes, source, trading_style}
PUT    /board/{id}         # update stage, notes, prices, exit_price, actual_entry_price, shares, trading_style
DELETE /board/{id}         # delete a plan
```

Key columns on `trade_plans`:

| Column | Type | Description |
|--------|------|-------------|
| `entry_price` | float | Planned limit/target price from the game plan |
| `actual_entry_price` | float | Real fill price, captured via the Fill modal |
| `shares` | float | Shares filled — enables dollar P&L |
| `trading_style` | varchar(16) | SHORT / SWING / LONG / GROWTH — style active at activation time |
| `exit_price` | float | Closing price, entered manually on the closed card |
| `closed_at` | timestamp | Set automatically the first time `stage = "closed"` is submitted |

---

---

## Research Engine (`/research`, `/research/[symbol]`)

A full Planning Stage Research Intelligence Engine. When a stock enters the **Planning** stage on the Trade Board, a green **Research** button links directly to its report. The report can also be reached from `/research` by entering any symbol.

> For the scoring formula and weighted model, see [Scoring Engine](#scoring-engine) below.

### What it generates

One click triggers a comprehensive AI-powered analysis across ten dimensions — comparable to a professional equity research report:

| Dimension | What is covered |
|---|---|
| **Executive Summary** | Overall score, confidence, recommendation, top 5 bullish/bearish factors, key risks, key opportunities |
| **Technical Analysis** | Price vs 50/200-day SMA, Golden/Death Cross detection, RSI, MACD + histogram, volume (RVOL), support/resistance levels, ATR + volatility rating |
| **Fundamental Analysis** | Revenue growth, EPS growth, margins (gross/operating/net), balance sheet (cash, debt, D/E), cash flow + FCF margin, valuation (P/E, Forward P/E, PEG, EV/EBITDA), profitability (ROE, ROA) |
| **Company Research** | Business model summary, competitive advantage matrix, moat rating (Very Strong → None), insider activity, institutional ownership trend, management quality |
| **Industry Research** | Industry status (Growing/Mature/Declining/Disrupted), TAM size + growth, market share position, competitor comparison, regulatory risk, industry verdict |
| **Economic Research** | Federal Reserve policy (Hiking/Holding/Cutting) and impact, inflation trend (CPI), GDP status, employment, recession risk checklist (yield curve, GDP, unemployment, consumer confidence), favored market style |
| **Master Checklist** | 27 binary PASS/WARNING/FAIL checks across four layers — Company, Industry, Economy, Technical |
| **Trading Plan** | Aggressive and conservative entry zones, stop loss (support + ATR method), three profit targets, risk/reward ratio and assessment |
| **Position Sizing** | Dollar risk, share quantity, and position size based on configurable portfolio size and max risk % per trade |
| **AI Verdict** | Can I buy today? (YES/NO/WAIT), detailed reasoning, biggest risks, conditions that must improve, catalysts for a Strong Buy upgrade, final recommendation with confidence % |

### Scoring Engine

All five dimension scores (0–100) are weighted to produce the overall score:

| Dimension | Weight | Computed by |
|---|---|---|
| Technical | 25% | Python (EMAs, RSI, MACD, volume, support proximity) |
| Fundamental | 30% | Python (revenue growth, margins, FCF, valuation, ROE) |
| Company | 15% | Claude AI |
| Industry | 15% | Claude AI |
| Economic | 15% | Claude AI |

**Recommendation thresholds:**

| Overall Score | Recommendation |
|---|---|
| 90–100 | STRONG BUY |
| 80–89 | BUY |
| 65–79 | WATCH |
| 50–64 | AVOID |
| 0–49 | SELL |

### Any symbol works

The Research Engine works for **any valid stock ticker** — it does not require the symbol to be in your watchlist or tracked in the database. For untracked symbols, the engine fetches data directly from yfinance (1-year daily price history, fundamentals, live price) and computes all TA indicators locally. For tracked symbols, it uses the pre-computed data from the database services plus a live price call to get the real-time price.

### Generating a report

1. Navigate to any stock detail page and save a Game Plan → board card enters **Planning** stage automatically
2. On the Trade Board, click the green **Research** button on any Planning card
3. — or — navigate directly to `/research` and enter any symbol
4. Click **Generate Report** (first generation takes 20–60 s; subsequent loads from 24-hour cache are instant)
5. If a cached report exists it loads automatically when the page opens — no need to click Generate
6. Click **Clear & Regenerate Report** to force a fresh analysis

### AI key is optional

The report generates even without an AI (Claude/DeepSeek) key configured. Without a key, all **computed scores** (Technical, Fundamental, entry zones, stop loss, targets, checklist) are fully populated. Only the **AI narrative sections** (Company, Industry, Economic analysis and AI Verdict) will show placeholder text. Configure a key in **Settings → AI Assistant** for the full report.

### Configuration

The generate dialog has optional config fields (click ⚙ Config):

| Field | Default | What it controls |
|---|---|---|
| API Key | From Settings → AI Assistant | Override the AI key for this session only |
| Portfolio Size ($) | $100,000 | Used to calculate dollar risk and share quantity |
| Max Risk Per Trade (%) | 2% | Used to calculate dollar risk and share quantity |

The AI provider and model are inherited from **Settings → AI Assistant** (Claude or DeepSeek). The report uses whichever is configured.

### Report tabs

The report is organized into nine tabs:

| Tab | Contents |
|---|---|
| **Summary** | 2×2 grid: bullish factors, bearish factors, key risks, key opportunities |
| **Technical** | Trend analysis, RSI + MACD cards, volume, support/resistance, ATR, histogram status |
| **Fundamental** | Revenue + EPS, margins, balance sheet, cash flow, valuation, profitability |
| **Company** | Business model, competitive moat, advantage matrix, management, insider activity, institutional ownership |
| **Industry** | Status, TAM, market share position, competitor table, regulatory risk, verdict |
| **Economic** | Fed policy, CPI trend, GDP, employment, recession risk checklist, market environment |
| **Checklist** | Four layers of PASS/WARNING/FAIL badges with individual item notes |
| **Trading Plan** | Entry zones, stop loss, profit targets (T1/T2/T3), risk/reward assessment, position sizing grid, trade invalidation conditions |
| **AI Verdict** | YES/NO/WAIT hero banner, biggest risks, must-improve conditions, Strong Buy catalysts |

### Checklist layers

**Layer 1 — Company (8 items)**
Can explain business · Revenue growing · EPS growing · FCF positive · D/E < 2 · Competitive moat · Insiders buying or holding · Institutions > 50%

**Layer 2 — Industry (5 items)**
Industry growing · Large TAM · Market share stable or gaining · Low regulatory risk · Industry tailwind

**Layer 3 — Economy (5 items)**
Fed supportive · Inflation improving · GDP expanding · No major recession signals · Favorable market style

**Layer 4 — Technical (7 items)**
Price above 200 SMA · Price above 50 SMA · Golden Cross · RSI healthy · MACD bullish/neutral · Volume confirming · Support level identified

### Trade Board integration

On the Trade Board (`/board`), Planning-stage cards show a green **Research** button in the footer action strip. Clicking it navigates directly to `/research/{symbol}` for that card's stock, carrying the full report context.

The report does **not** automatically update the board card's entry/stop/target prices — you can review the Trading Plan tab and manually update the card prices if you wish to use the AI-generated levels.

### AI Analyst Chatbot

A conversational AI panel appears below every generated report. It has full context of the report (scores, trend verdict, SMAs, RSI, MACD, entry zones, stop loss, targets, fundamental metrics, AI verdict, key risks).

**How to use:**
- Four suggested starter questions appear before the first message: *What is the entry strategy? / Is the valuation attractive? / What are the biggest risks? / Should I buy today?* — click any to pre-fill it
- Type any question and press **Enter** or click **Send**
- Conversation history is shown (your messages on the right, AI answers on the left)
- Requires an AI key configured in **Settings → AI Assistant**

The chatbot uses the same provider and model configured in Settings. Each chat message sends the full conversation history so follow-up questions work correctly.

### API endpoints

All endpoints are under `/research` (proxied via the API Gateway → research-engine service on port 8008).

```
POST /research/{symbol}
  Body: {
    "provider":       "claude" | "deepseek",   # optional, default "claude"
    "model":          "claude-sonnet-4-6",      # optional
    "api_key":        "sk-ant-...",             # optional; computed scores work without it
    "portfolio_size": 100000,                   # optional, default 100000
    "max_risk_pct":   2.0                       # optional, default 2.0
  }
  → Full ResearchReport JSON
  → Cached server-side for 24 hours

GET /research/{symbol}
  → Cached report if available (within 24 h); 404 otherwise

DELETE /research/{symbol}
  → Clears cached report, forces regeneration on next POST

POST /research/{symbol}/chat
  Body: {
    "messages": [{"role": "user", "content": "What is the stop loss?"}],
    "api_key":  "sk-ant-...",
    "model":    "claude-sonnet-4-6",    # optional
    "provider": "claude"                # optional
  }
  → {"role": "assistant", "content": "The stop loss is set at ..."}
  → Requires a cached report to exist (POST first to generate)
```

### How the technical score is computed

The Python scoring algorithm starts at 50 and adjusts based on:

| Signal | Effect |
|---|---|
| Price above 200 SMA | +15 |
| Price below 200 SMA | −10 |
| Price above 50 SMA | +10 |
| Price below 50 SMA | −7 |
| Golden Cross detected | +10 |
| Death Cross detected | −10 |
| Above golden cross (no recent event) | +5 |
| RSI 40–60 (Healthy) | +5 |
| RSI 60–70 (Strong) | +8 |
| RSI 30–40 (Weak) | −5 |
| RSI > 70 (Overbought) | −8 |
| MACD bullish crossover | +10 |
| MACD bearish crossover | −10 |
| MACD line > signal (no crossover) | +3 |
| MACD line < signal (no crossover) | −3 |
| Histogram green and growing | +2 |
| Histogram red and growing | −2 |
| RVOL ≥ 1.5x | +5 |
| RVOL 1.0–1.5x | +2 |
| RVOL < 1.0x | −3 |
| Price within 3% above nearest support | +3 |
| Price 3–8% above nearest support | +1 |

Final score is clamped to [0, 100]. Trend verdict: ≥ 80 = Strong Bullish · ≥ 65 = Bullish · ≥ 50 = Neutral · ≥ 35 = Bearish · < 35 = Strong Bearish.

### How the fundamental score is computed

Starts at 50 and adjusts based on:

| Metric | Effect |
|---|---|
| Revenue growth ≥ 20% | +10 |
| Revenue growth 10–20% | +5 |
| Revenue growth < 0% | −5 |
| EPS growth ≥ 25% | +10 |
| EPS growth 10–25% | +5 |
| EPS growth < 0% | −7 |
| Gross margin > 40% | +5 |
| Gross margin < 20% | −3 |
| Operating margin > 20% | +5 |
| Operating margin < 5% | −3 |
| D/E ratio < 0.5 | +5 |
| D/E ratio > 2.0 | −5 |
| FCF positive with margin ≥ 20% | +10 |
| FCF positive | +5 |
| FCF negative | −5 |
| P/E < 15 | +8 (Undervalued) |
| P/E 15–25 | +3 (Fairly Valued) |
| P/E > 40 | −5 (Overvalued) |
| ROE ≥ 20% | +8 (Excellent) |
| ROE 12–20% | +4 (Good) |
| ROE < 6% | −4 (Poor) |

---

## Positions (`/positions`)

Portfolio tracker for your actual or simulated stock holdings.

### Summary stats bar
Positions count · Invested (cost basis) · Market Value · Today's P&L · Total P&L

### Allocation donut chart
Plotly.js pie/donut when you have more than one position.

### Per-position row
Symbol + shares + avg cost · Current price + day change · Market value · P&L ($ and %) · Signal badge · K-Score · BUY/SELL trade buttons · ▼ Trade history drawer

### Trade logging
BUY updates cost-basis average; SELL reduces share count (removes when fully sold).

### Two-step remove confirmation
Clicking the remove button first shows an inline "Remove? Yes / No" prompt. Confirming removes the position; No cancels without any change. This prevents accidental deletion.

### Toast notifications
Errors (e.g. failed trade log or remove) show as a red toast banner at the bottom of the screen for 4 seconds, replacing the previous silent failures.

### CSV export
Downloads `positions.csv` with all position fields.

---

## Portfolio Optimizer (`/portfolio`)

Quantitative portfolio allocation using four methods.

### Methods

| Method | Badge | What it does |
|--------|-------|-------------|
| **Max Sharpe (MVO)** | Recommended | Finds weights that maximise the Sharpe ratio. Uses Ledoit-Wolf covariance shrinkage and James-Stein return shrinkage to reduce estimation noise. |
| **Risk Parity** | Stable | Each asset contributes equally to total portfolio risk. Defensive, less sensitive to return estimates. |
| **Hierarchical Risk Parity** | Robust | Clusters assets by correlation (Ward linkage), allocates risk within/across clusters via recursive bisection. Never inverts the covariance matrix — most robust to estimation error. |
| **AI Allocation** | AI-Powered | Filters out stocks below the K-Score threshold, blends K-Score views (40%) with historical returns (60%), then maximises Sharpe. Holds a defensive cash buffer. |

### Technical details
- **Ledoit-Wolf covariance shrinkage** (scikit-learn) — dramatically reduces estimation error vs raw sample covariance, especially for small datasets
- **James-Stein return shrinkage** — blends individual asset returns toward the grand mean (α=0.5) to reduce noise
- **Tangency portfolio** — MVO directly maximises Sharpe ratio (not a utility function), which is the theoretically correct efficient frontier target
- **HRP** — Ward hierarchical clustering → quasi-diagonalized correlation matrix → recursive bisection with cluster-variance weights

### Response metrics
Every method returns: `expected_return`, `expected_vol`, `sharpe_ratio`, `max_drawdown`, `diversification` (1 − HHI).

### UI
- Method selector cards with descriptions and badges
- Metrics bar: Expected Return / Volatility / Sharpe / Max Drawdown / Diversification
- Horizontal allocation bars scaled to the largest position
- Quick-add buttons from your tracked stocks
- Equal-weight baseline shown for comparison
- Lookback selector: 6 months / 1 year / 2 years / 3 years

### API
```
POST /portfolio/optimize
{
  "symbols": ["AAPL", "MSFT", "NVDA"],
  "method": "mean_variance" | "risk_parity" | "hierarchical_risk_parity" | "ai_allocation",
  "lookback_days": 365,
  "min_score": 60.0   // ai_allocation only
}
```

---

## Alerts (`/alerts`)

Server-side email alerts. Rules are stored in PostgreSQL and checked automatically — alerts fire even when you are not logged in. Each alert sends an email to the address on your account (or a custom address you specify when creating the alert).

### Alert conditions

#### Price alerts — checked every minute

| Condition | `condition` value | Threshold field |
|-----------|------------------|----------------|
| Price rises above $X | `above` | Target price |
| Price falls below $X | `below` | Target price |

Live prices are fetched via yfinance `fast_info`. Once triggered the alert is marked as fired and will not repeat.

#### Technical alerts — checked after every market refresh (5× per trading day)

| Condition | `condition` value | Threshold field | How it fires |
|-----------|------------------|----------------|-------------|
| Price crosses above EMA | `cross_above_ema` | EMA period (20, 50, or 200) | Price was below EMA on yesterday's bar, above on today's bar |
| Price crosses below EMA | `cross_below_ema` | EMA period (20, 50, or 200) | Price was above EMA on yesterday's bar, below on today's bar |
| Golden Cross | `golden_cross` | — (stored as 0) | EMA50 crossed above EMA200 between the last two daily bars |
| Death Cross | `death_cross` | — (stored as 0) | EMA50 crossed below EMA200 between the last two daily bars |
| New 52-week high | `new_52wk_high` | — (stored as 0) | Today's close exceeds the prior 251-bar high |
| New 52-week low | `new_52wk_low` | — (stored as 0) | Today's close is below the prior 251-bar low |

EMA crossovers require at least as many bars as the EMA period. Golden/Death Cross requires ≥ 200 bars. All technical checks run from the ingested price history in the DB — no live price API call is needed.

### Creating an alert

**From `/alerts` (management page)**
- Select stock from the full universe dropdown
- Choose condition from the grouped dropdown (Price / Price vs EMA / EMA50 vs EMA200 / Milestone)
- For `above`/`below`: enter a target price
- For `cross_above_ema`/`cross_below_ema`: choose EMA period (20 / 50 / 200)
- For `golden_cross`, `death_cross`, `new_52wk_high`, `new_52wk_low`: no threshold needed
- Optionally add a note (shown in the email)
- Email address pre-filled from last-used value (stored in `localStorage`)

**From the stock detail page**
- Click **+ New Alert** in the Price Alerts section
- Same grouped condition dropdown + dynamic threshold input
- Pre-fills the current price as the default threshold for price alerts

**From the Opportunities page**
- Click the 🔔 bell icon on any opportunity card
- The panel fetches live technical indicator data for that stock and suggests up to 4 data-driven alerts:
  - RSI-based (e.g. stop at SMA20 when RSI is overbought; EMA20 crossover when oversold)
  - Bollinger Band position (near upper/lower band)
  - MACD histogram crossover (just turned bullish or bearish)
  - SMA200 test (price within 4% of the 200-day average)
  - Approaching Golden/Death Cross (SMA50/200 gap < 2.5%)
  - Nearby support/resistance levels (within 7% of current price)
  - Fair value target (if fair price is > 3% above current price)
- Click **Set Alert** on any suggestion to create it instantly

### Active and triggered alerts

The `/alerts` page shows two lists:
- **Active** — alert has not yet fired; condition label + email + created time + delete button
- **Triggered** — alert fired at least once; shows past-tense label + trigger time + delete button

The stock detail page shows only alerts for the current symbol.

### Alert email

Price alerts include: symbol, condition met, threshold, actual price at trigger, and any note.

Technical alerts include: symbol, a descriptive condition label (e.g. "crossed above EMA20 (value: 152.30)"), the last bar's closing price, and any note.

### How alerts are stored and checked

- Alert rules are stored server-side in the PostgreSQL `price_alerts` table
- `check_price_alerts()` runs every minute via APScheduler — fetches live prices for all untriggered alert symbols at once using yfinance `Tickers`, marks triggered alerts, and fires emails
- `check_technical_alerts()` runs at the end of every market refresh cycle — reads the last 260 daily bars per symbol from the DB `prices` table, computes EMA/SMA series, and checks all untriggered technical alerts
- Email is sent via the configured email service (Gmail SMTP or AWS SES — see `.env`)

---

## Strategies (`/strategies`)

Build, save, and backtest rule-based trading strategies. Each strategy is **user-scoped** — only the creating user can view, edit, delete, or backtest their own strategies. Authentication (JWT Bearer token) is required for all strategy endpoints.

### Strategy DSL
JSON rule tree for entry and exit conditions:
```json
{
  "entry": {"op": "<", "left": "rsi_14", "right": 30},
  "exit":  {"op": ">", "left": "rsi_14", "right": 70}
}
```

Available fields: `rsi_14`, `macd_hist`, `close`, `sma_20`, `sma_50`, `ema_20`, `volume`, `bb_upper`, `bb_lower`

### Backtest results
Total return %, Sharpe ratio, Max drawdown %, CAGR, Win rate %, Profit factor, Equity curve chart

### Saved runs
- Each saved backtest run shows a **date stamp** ("May 29, 14:30") alongside the symbol and date range so you can identify when it was run
- Date range validation — end date must be after start date; an error is shown inline if not
- **Two-step delete** — clicking ✕ on a saved run first shows inline "Delete? Yes / No" confirmation before removing, preventing accidental deletes

### Compare table
- When two or more runs are loaded for comparison, the best value in each row is **highlighted in green** based on whether higher or lower is better for that metric (e.g. higher return is better; lower max drawdown magnitude is better)

---

## Settings (`/settings`)

Central configuration page for all app preferences, data sources, and AI integration.
Changes take effect immediately on save (persisted to localStorage).

### Trading Style — AI Signal Horizon

Controls which signal profile is used across all pages by default.

| Style | Horizon | Character |
|-------|---------|-----------|
| **Short Term** | 1–5 days | Pure TA, no earnings/news filters. Ideal for volatile small-caps. |
| **Swing Trade** | 5–20 days | Balanced TA + momentum. Earnings + news compression active. Default. |
| **Long Term** | 30–90 days | Fundamentals-heavy. K-Score boost applied. Weekly alignment required. |

Switching style takes effect immediately — all signal columns across Dashboard, Rankings, Watchlist, Screener, and Opportunities reload automatically.

Individual watchlists can override this setting with their own per-list style (see [Watchlist](#watchlist-watchlist)). The global setting is the fallback for any list that has no style assigned.

For a full technical breakdown of what changes per style (ML weight caps, BUY thresholds, compression multipliers, filter enables), see [AI_SIGNAL.md — Style profile parameters](AI_SIGNAL.md#style-profile-parameters).

### Stock Price Data Sources
Toggle which providers supply historical OHLCV data and fundamentals.

| Source | Cost | Notes |
|--------|------|-------|
| **yfinance** | Free | Always on. Primary source for live prices, OHLCV history, fundamentals, and news. |
| **Alpha Vantage** | Free tier (25 req/day) | Toggle + API key input. US equities only. Key from alphavantage.co. |
| **Polygon.io** | Free tier (5 req/min) | Toggle + API key input. US equities, multiple timeframes. Key from polygon.io. |

API keys are stored in browser localStorage — never sent to the server except as part of individual data requests.

### News Sources
Toggle which sources contribute to the per-symbol news feed.

| Source | Notes |
|--------|-------|
| **Yahoo Finance News** | Best for US equities. 7-day freshness filter applied. |
| **Google News RSS** | Essential for HK stocks. Supplements when yfinance returns < 3 articles. |

Toggling a source invalidates the Redis news cache (different cache key per source combination) so the change applies on next fetch.

### AI Assistant
Configure which AI model powers the chat panel on stock detail pages.

| Provider | Notes |
|----------|-------|
| **Disabled** | No AI chat shown. Default. |
| **Claude (Anthropic)** | Claude Opus 4.7 / Sonnet 4.6 / Haiku 4.5. Key from console.anthropic.com. |
| **DeepSeek** | deepseek-chat / deepseek-reasoner (R1). Key from platform.deepseek.com. |

- API keys stored in localStorage only — the gateway proxies requests but never stores keys
- **Test Connection** button verifies the key works before saving
- Once configured, the AI chat panel appears on every stock detail page

### Data & Refresh
- **Price Refresh Interval** — how often dashboard/watchlist/positions auto-refresh (30 s / 60 s / 2 min / 5 min)
- **News Max Age** — discard articles older than N days (3 / 7 / 14 / 30)
- **Default Chart Limit** — historical bars shown in price chart (100 / 200 / 400 / 730 days)

### Notifications
- **Sound** — play a chime when an alert triggers (On / Off)
- **Default Cooldown** — pre-fills the cooldown when creating new alerts

### ML & Analysis
- **Default ML Model** — pre-selects the model in the Stock Detail ML Prediction panel

### Account
- **Change Password** — inline form to change your own password (enter current password + new password)

### User Management (admin only)
Visible only to users with the `admin` role.

- **User list** — shows all accounts with username, role, and active status; toggle active/inactive per user
- **Create user** — enter a username, password, and role (user / admin)
- **Delete user** — permanently removes the account
- **Admin password reset** — reset any user's password without knowing their current password

---

## Notification Bell (nav bar)

The 🔔 bell icon appears in the top-right navigation bar when logged in.

- Red badge shows the number of unread notifications (capped at 99+)
- Click to open the dropdown panel:
  - Last 30 notifications with symbol, message, and relative time
  - Unread notifications have a subtle indigo highlight
  - Opening the panel marks all as read
  - **Clear all** button removes all notifications
- Footer link "Manage alerts →" navigates to `/alerts`

---

## Signal Accuracy vs Trade Performance — Key Difference

These two pages answer different questions about the same signals:

| | Signal Accuracy | Trade Performance |
|---|---|---|
| **Question** | Is the model's direction right? | Does following the signals make money? |
| **Entry** | Price on signal date | Price on BUY signal date |
| **Exit** | Today's price (always) | Price at next SELL or WAIT signal |
| **Holding period** | Signal date → now (varies wildly) | BUY → SELL/WAIT (realistic) |
| **Deduplication** | One entry per (stock, signal type, day) — scheduler noise filtered out | Consecutive BUY refreshes collapsed; only one open trade tracked per stock at a time |
| **Bias** | Optimistic — recent signals look good even if they'd reverse tomorrow | Honest — you only get credit when the trade is closed |
| **Best used for** | Checking model direction quality | Evaluating the system as an actual trading strategy |

**In short:** Signal Accuracy tells you if the AI is pointing in the right direction. Trade Performance tells you if you'd actually make money following it.

---

## Signal Accuracy (`/signal-accuracy`)

Measures how often past BUY/SELL signals predicted the correct direction, evaluated against the latest available price (running P&L from signal date to today).

- **Lookback** — 30 / 60 / 90 / 180 days
- **Entry price** — most recent close on or just after the signal date (handles weekend/holiday signals)
- **Exit price** — most recent available close price (today's close or latest bar)
- **Minimum age** — signals from the last 24 hours are excluded (need at least one price bar after the signal)
- **Deduplication** — one entry per (stock, signal type, calendar day). The scheduler refreshes every few hours; duplicate intraday signals are collapsed so each unique day counts once.
- **Note** — because exit is always "today", a BUY signal from 3 months ago and one from last week are both judged against today's price. Holding periods are not comparable across signals, so this metric measures directional accuracy, not trading P&L.

### Summary cards
| Card | What it means |
|------|--------------|
| Overall Accuracy | % of BUY + SELL signals that pointed the right direction vs today's price |
| BUY Accuracy | % of BUY signals where price is higher today than on signal date |
| SELL Accuracy | % of SELL signals where price is lower today than on signal date |
| Avg BUY Return | Average % gain from BUY signal date to today |
| Avg SELL Return | Average % decline from SELL signal date to today |
| Profit Factor | Total gain from correct signals ÷ total loss from wrong signals. Above 1.5 = good system. |

### Factor Exposure chart

Below the summary cards, a **Factor Exposure** bar chart shows which signal factors drive correct vs wrong BUY signals. Each row is a factor; bars show deviation from the neutral baseline — green = correct signals, red = wrong signals.

| Factor | Neutral baseline | What a rightward green bar means |
|---|---|---|
| RSI | 50 | Correct signals tend to fire on higher-momentum stocks |
| ADX | 20 | Correct signals are in stronger trends |
| Volume Z-score | 0 | Correct signals have above-average volume |
| ML Probability | 50% | Correct signals have higher ML confidence |
| News Sentiment | 50 | Correct signals have more positive recent news |
| TA Score | 0.5 | Correct signals have stronger technical setups |

If the green bar for a factor sits further right than the red bar, that factor is a positive predictor of success. If they're both at the same position, the factor doesn't differentiate. Powered by `GET /signals/factor-exposure?lookback_days=N`.

---

## Trade Performance (`/trade-performance`)

Shows real P&L by pairing each BUY signal with its next SELL or WAIT exit signal for the same stock. This is the harder, more honest measure — you only get credit for a closed trade, and holding periods are realistic.

### How it works — no look-ahead bias

The engine replays signals the system already generated in real time. It does **not** re-compute signals on historical data after the fact. Because every BUY and SELL signal was generated at market time using only data available at that moment (live RSI, ADX, ML probability, news sentiment, etc.), the backtest inherits no look-ahead bias.

**Trade construction:**
1. Every BUY signal in the lookback window becomes a trade entry
2. Entry price = closing price on the BUY signal date
3. Scan forward for the next SELL or WAIT signal for the same stock → that becomes the exit
4. Exit price = closing price on that exit signal date
5. No SELL/WAIT yet → trade is **Open**, using the latest available price

**Deduplication guards** prevent the scheduler's repeated refreshes from inflating the trade count:
- `last_exit_ts guard` — a new BUY is ignored if it arrives *before* the previous closed trade's exit timestamp (same-day refresh duplicate)
- `in_open_trade guard` — while an open position already exists for a stock, any further BUYs for that stock are skipped until it closes

- **Entry** — BUY signal date → entry price is the close on that date
- **Exit** — next SELL or WAIT signal for the same stock → exit price is the close on that date
- **Open trades** — BUY signals with no exit yet use the latest available price (marked "OPEN")
- **Lookback** — 90 / 180 / 365 days

### How the backtest metrics are calculated

All metrics are derived from closed trades only (open trades use an estimated exit price and are excluded from compounding).

**Equity curve** — closed trades sorted chronologically, each compounded in sequence:
```
equity = 1.0
for each trade (by entry date):
    equity = equity × (1 + pct_return / 100)
```
Represents the growth of $1 if you followed every signal in order, fully reinvesting after each trade.

**Total Return** = `(final_equity − 1) × 100%`

**Sharpe ratio** (annualised):
```
Sharpe = mean(returns) / stdev(returns) × √(252 / avg_hold_days)
```
Treats each trade's return as one period of length `avg_hold_days`, then scales to annual. Risk-free rate assumed 0.

**Max Drawdown** — worst peak-to-trough decline along the equity curve:
```
for each equity point:
    running_peak = max(equity seen so far)
    drawdown = (equity − running_peak) / running_peak × 100
max_drawdown = min(all drawdown values)   # most negative, e.g. −12.3%
```

**Calmar ratio** = `annualised_return / |max_drawdown|`  
Where `annualised_return = total_return / total_calendar_days × 252`.  
*Only meaningful over 6+ months of history — with fewer trades the annualisation magnifies noise.*

**vs SPY** — SPY's total return over the same date range (first trade entry → last trade exit). Shows `—` if SPY is not tracked in the database.

### Summary cards — Row 1: Backtest metrics
| Card | Good threshold | What it means |
|------|---------------|--------------|
| Win Rate | > 50% | % of closed trades that made money |
| Profit Factor | > 1.5 | Total profit from winners ÷ total loss from losers. Above 1.0 = system makes money over time |
| Total Return | > 0% | Compounded equity growth if you followed every signal |
| vs SPY | > 0% | System return minus SPY buy-and-hold return over the same period |
| Sharpe | > 1.0 | Annualised risk-adjusted return. > 1 = good, > 2 = excellent |
| Max Drawdown | > -20% | Worst peak-to-trough equity decline |
| Calmar | > 1.0 | Annualised return ÷ max drawdown. Only meaningful over 6+ months |

### Summary cards — Row 2: Trade statistics
| Card | Good threshold | What it means |
|------|---------------|--------------|
| Avg Return | > 1% | Average % gain or loss per closed trade |
| Avg Win | — | Typical winning trade size |
| Avg Loss | — | Typical losing trade size. You want Avg Win > Avg Loss |
| Avg Hold | — | How long the system typically stays in a trade |
| Open Trades | — | Positions currently awaiting a SELL or WAIT exit signal |

### Equity curve
SVG line chart showing the growth of a hypothetical $1 deployed sequentially across all closed signal trades, compounded in chronological order. A dashed gold overlay shows SPY's return over the same period for comparison (when SPY data is available).

- Y-axis labelled as % return relative to starting equity
- Dashed baseline at 0% (break-even)
- Green line = net positive; red line = net negative
- X-axis: first trade entry date → last trade exit date

### By-symbol breakdown
Table showing win rate, average return, and average hold days per stock — tells you which symbols the signal engine works best on.

### Trade list
Every individual trade with: entry date / exit date / entry price / exit price / return % / hold days / exit signal (SELL, WAIT, or OPEN) / Win or Loss.

Filters: All / Closed / Open · All / Win / Loss · Symbol search · Sort by Date / Return / Hold Days

### API
```
GET /signals/trade_performance?lookback_days=180   # full report
  → win_rate, profit_factor, avg_return_pct, avg_win_pct, avg_loss_pct
     total_return, sharpe, max_drawdown, calmar, spy_return
     equity_curve: [{date, equity}, ...]   # compounded equity over time
     by_symbol: [{symbol, trades, win_rate, avg_return, avg_hold_days}]
     trades: [{symbol, entry_date, exit_date, entry_price, exit_price,
               pct_return, hold_days, win, exit_signal, entry_confidence}]
```

---

## API Reference

### Data
```
POST /admin/seed                       # seed default stock universe
POST /admin/ingest  {symbols:[...]}    # ingest price history (parallel, synchronous)
DELETE /admin/stocks/{symbol}          # soft-delete stock (active=False, preserves history)
GET  /stocks                           # list all tracked stocks
GET  /stocks/{symbol}/prices           # OHLCV history from DB
GET  /stocks/latest_prices             # live prices (yfinance fast_info, Redis 60 s cache)
GET  /stocks/market_overview           # live index quotes: S&P 500, NASDAQ, DJI, VIX, HSI (Redis 60 s)
GET  /stocks/fear_greed                # computed Fear & Greed index (Redis 1 h cache)
                                       #   → score, rating, history, sp500_regime, sp500_vs_ma200_pct
GET  /stocks/{symbol}/fundamentals     # company financials (yfinance .info, Redis 24 h cache)
                                       #   ?refresh=true to bypass cache
                                       #   → includes next_earnings_date, days_to_earnings,
                                       #       insider_buy_shares_6m, insider_sell_shares_6m,
                                       #       insider_buy_transactions_6m, insider_net_pct
GET  /stocks/{symbol}/news?sources=yfinance,google   # news + sentiment (filterable by source)
```

### Price & Technical Alerts
```
GET    /alerts                         # list current user's price alerts
POST   /alerts   {symbol, condition, threshold, email?, note?}   # create alert
DELETE /alerts/{id}                    # delete alert

# Valid condition values:
#   "above"           — price rises above threshold
#   "below"           — price falls below threshold
#   "cross_above_ema" — price crosses above EMA; threshold = period (20/50/200)
#   "cross_below_ema" — price crosses below EMA; threshold = period (20/50/200)
#   "golden_cross"    — EMA50 crosses above EMA200; threshold = 0
#   "death_cross"     — EMA50 crosses below EMA200; threshold = 0
#   "new_52wk_high"   — price hits a new 52-week high; threshold = 0
#   "new_52wk_low"    — price hits a new 52-week low; threshold = 0
```

### Signal Change Alerts
```
GET    /signal-alerts                  # list current user's signal alerts
POST   /signal-alerts   {symbol, email?}   # subscribe (idempotent — returns existing if duplicate)
DELETE /signal-alerts/{id}             # unsubscribe
```

### Signals
```
GET  /signals/{symbol}               # compute signal (live)
GET  /signals/{symbol}?persist=true  # compute + save to DB
GET  /signals                        # latest persisted signal for all active stocks
GET  /signals/accuracy?lookback_days=90&symbol=AAPL
                                     # historical BUY/SELL accuracy vs fixed 5-day outcome
                                     # (exit price = first trading day ≥ signal_date + 7 days)
GET  /signals/trade_performance?lookback_days=180&symbol=AAPL
                                     # BUY→SELL/WAIT trade-pair P&L stats
                                     # returns: win_rate, profit_factor, avg_return, avg_hold_days
                                     # per-symbol breakdown + individual trade list
```

### ML
```
POST /ml/train      {symbol, model}        # train a single model (~30 s per symbol)
POST /ml/train_all                         # retrain all active stocks (uses tuned params if available)
POST /ml/train_all_ensemble                # train XGBoost + RandomForest for every active symbol
POST /ml/tune       {symbol, n_trials=60}  # Optuna search for one symbol, then retrain (~5 min)
POST /ml/tune_all   ?n_trials=60           # weekend batch: tune every active symbol (~2-4 h)
POST /ml/predict         {symbol, model}   # XGBoost prediction for one symbol
POST /ml/predict_ensemble {symbol}         # XGBoost + RF ensemble (weighted by CV AUC);
                                           # falls back to XGBoost-only if RF not yet trained
GET  /ml/models                            # list available model types
```

### Rankings
```
GET  /rankings                       # K-Score leaderboard
GET  /rankings/{symbol}              # K-Score for one stock
POST /rankings/refresh               # recompute all K-Scores
```

### Aggregate
```
GET  /aggregate/overview/{symbol}    # all-in-one: price, indicators, patterns,
                                     # levels, ranking, signal, price history, fundamentals
```

### Auth
All auth endpoints are under `/auth/*` (proxied from `/api/auth/*`).
```
POST /auth/login                             # → {access_token, token_type, username, role}
POST /auth/reset-password                    # change own password (no JWT needed)
GET  /auth/me                                # current user info (JWT required)
PUT  /auth/change-password                   # change password (JWT required)
GET  /auth/users                             # list all users (admin JWT required)
POST /auth/users                             # create user (admin JWT required)
DELETE /auth/users/{username}                # delete user (admin JWT required)
PUT  /auth/users/{username}/reset-password   # admin reset any user's password
PUT  /auth/users/{username}/toggle           # toggle user active/inactive
```

### Watchlist
All watchlist endpoints require JWT. Each user sees only their own watchlist items.
```
GET    /watchlist                    # get current user's watchlist
POST   /watchlist/{symbol}           # add to current user's watchlist
DELETE /watchlist/{symbol}           # remove from current user's watchlist
GET    /watchlist/{symbol}           # check if watched (returns bool)
```

### Strategies & Portfolio
All strategy endpoints require JWT. Strategies are scoped to the authenticated user.
```
POST   /strategies                         # create strategy
GET    /strategies                         # list user's strategies
GET    /strategies/{id}                    # get one strategy
DELETE /strategies/{id}                    # delete strategy
POST   /backtest   {strategy_id, symbol}   # run backtest
POST   /portfolio/optimize                 # run portfolio optimization
```

### Research Engine
```
POST /research/{symbol}           # generate + cache a full research report
  {provider, model, api_key, portfolio_size?, max_risk_pct?}
  → ResearchReport JSON (cached 24 h server-side)

GET  /research/{symbol}           # return cached report (404 if none / expired)

DELETE /research/{symbol}         # clear cache → forces regeneration on next POST
```

### AI Chat
```
POST /ai/chat
{
  "provider": "claude" | "deepseek",
  "model": "claude-sonnet-4-6",
  "api_key": "sk-ant-...",
  "messages": [{"role": "user", "content": "..."}],
  "system": "optional system prompt",
  "max_tokens": 2048
}
→ { "content": "...", "model": "...", "provider": "..." }
```

---

## Price data quality

### Adjusted close prices

All daily bar fetches from yfinance use `auto_adjust=True`. This means the `Close` column returned is already **split- and dividend-adjusted** — the price series is restated retroactively whenever a stock split or dividend occurs, so the chart and all indicators (SMA, RSI, MACD, ATR, OBV) compute on a consistent continuous series.

Intraday bars (1m, 5m, 15m, 1h) do **not** use auto-adjust — yfinance does not reliably adjust intraday data and applying corporate-action adjustments to sub-daily bars can introduce artefacts. Intraday data is used only for live price display, not for signal computation.

### ATR calculation — Wilder's method

Average True Range (ATR) in the Research Engine uses **Wilder's exponential smoothing** rather than a plain SMA:

1. Compute True Range for each bar: `TR = max(high − low, |high − prev_close|, |low − prev_close|)`
2. Seed the first ATR with a simple average of the first `period` TR values (standard is 14 bars)
3. For each subsequent bar: `ATR = ATR_prev × (1 − α) + TR × α` where `α = 1 / period`

Wilder's ATR converges more slowly than a simple rolling window, meaning it is less jumpy on single-day volatility spikes and gives a smoother, more representative estimate of "normal" trading range. This is the same method used by the original ATR definition (J. Welles Wilder, 1978) and by most professional charting platforms.

ATR is used in the Research Engine for: stop loss calculation (`entry − ATR`), profit target spacing, and volatility classification (low / normal / high relative to its own 20-day average).

---

## Data freshness reference

| Data | Source | Cache / Freshness |
|------|--------|-------------------|
| Dashboard / Watchlist / Positions prices | yfinance `fast_info` | Redis 60 s TTL; auto-refreshes every 60 s in UI |
| Market overview indices (S&P 500, NASDAQ, DJI, VIX, HSI) | yfinance `fast_info` | Redis 60 s TTL; SWR 60 s refresh on dashboard |
| Stock detail **live price card** | yfinance `fast_info` (via `/stocks/latest_prices`) | Redis 60 s TTL; SWR 60 s refresh on stock detail page |
| Stock detail chart (OHLCV) | DB `prices` table | As of last ingest |
| Company Financials | yfinance `.info` | Redis 24 h TTL (quarterly data) |
| Analyst ratings & price targets | yfinance `.info` + `recommendations_summary` | Redis 24 h TTL (updates in step with fundamentals) |
| Recent analyst firm actions | yfinance `upgrades_downgrades` (last 90 days, up to 20 rows) | Redis 24 h TTL — click Refresh to force |
| **Earnings calendar** | yfinance `.calendar` | Redis 24 h TTL (same fundamentals cache) |
| **Insider activity (6M)** | yfinance `.insider_purchases` | Redis 24 h TTL (same fundamentals cache) |
| News | yfinance + Google News RSS | Redis 30 min TTL per source combination |
| K-Score / Fair price | DB `rankings` table | Refreshed 5× per trading day (09:00, 10:45/10:30, 12:45/14:15, 14:45/15:30, 16:30) — see [Refresh & Schedule](#refresh--schedule) |
| AI Signal | DB `signals` table (TA + ML) | Refreshed 5× per trading day, immediately after K-Scores — same schedule |
| ML prediction | Trained model inference | Retrained once per day at post-close (16:30); intra-day runs use the previous close model |
| **Research report** | research-engine (Claude AI + all services) | Cached in-memory 24 h per symbol; POST to generate, GET to retrieve, DELETE to force refresh |
| **Fear & Greed Index** | Computed from yfinance ^GSPC + ^VIX | Redis 1 h TTL; SWR 1 h refresh on stock detail page |
| **Market Regime (S&P vs 200MA)** | Computed alongside Fear & Greed | Redis 1 h TTL (same cache entry) |

---

## Redis cache keys

| Key | Contents | TTL |
|-----|----------|-----|
| `stockai:live_prices` | Array of live price objects for all active stocks | 60 s |
| `stockai:market_overview` | Array of index quotes (^GSPC, ^IXIC, ^DJI, ^VIX, ^HSI) | 60 s |
| `stockai:fear_greed` | Computed Fear & Greed score, rating, history, S&P regime | 1 h |
| `stockai:fundamentals:v2:{SYMBOL}` | Company fundamentals JSON — includes analyst ratings, earnings calendar, and insider activity | 24 h |
| `stockai:news:{SYMBOL}:{sources}` | News articles for one symbol + source combination | 30 min |

> To force a fresh fundamentals fetch (bypass the 24 h cache), use `GET /stocks/{symbol}/fundamentals?refresh=true`. To force a fresh Fear & Greed fetch, delete the `stockai:fear_greed` Redis key and re-request the endpoint.

---

## Browser storage keys

### Auth token (global — not user-namespaced)

| Key | Contents |
|-----|----------|
| `stockai_jwt` | Raw JWT string for the active session |

### Per-user namespaced keys

All user-specific keys follow the pattern `stockai:{username}:{key}`. This ensures each user's data is fully isolated in the browser.

| Key suffix | Contents |
|------------|----------|
| `settings` | All app settings (data sources, AI keys, intervals, etc.) |
| `alert_rules` | Array of alert rule objects |
| `notifications` | Last 100 triggered notifications |
| `positions` | Array of `{id, symbol, shares, avgCost, currency, addedAt}` |
| `trades` | Map of `{symbol: [{type, shares, price, date}]}` |
| `watch_notes` | Map of `{symbol: noteText}` |
| `watch_price_alerts` | Map of `{symbol: {target, direction}}` (watchlist quick-alerts) |

Clearing `stockai_jwt` from `localStorage` logs the user out. Clearing all storage resets all per-user data and AI keys.

---

## ML models — when to use each

| Model | Best for | Notes |
|-------|---------|-------|
| **XGBoost** | Production — best overall accuracy | Default; fastest to train; handles missing features |
| **Random Forest** | Stable baseline / sanity check | Low variance; good ensemble member |
| **Gradient Boosting** | Higher-accuracy longer-horizon predictions | Slower; can overfit noisy data |
| **LSTM** | Sequential momentum patterns | Needs more data; slow; best for liquid trending stocks |

---

## K-Score sub-scores

| Sub-score | Weight | Range | What drives it |
|-----------|--------|-------|---------------|
| Technical | 22% | 0–100 | SMA(50/200) trend alignment, RSI(14), ADX(14) trend-strength bonus |
| Momentum | 23% | 0–100 | 1-week, 1-month, 3-month price rate-of-change |
| Value | 13% | 0–100 | Discount from 52-week high (price proxy; fundamentals not yet integrated) |
| Growth | 14% | 0–100 | 12-month price CAGR (price proxy; earnings/revenue growth not yet integrated) |
| Volatility | 18% | 0–100 | Inverse of 30-day realized volatility |
| Relative Strength | 10% | 0–100 | 20-day return vs sector ETF benchmark — 50 = in-line with sector, > 60 = leading, < 40 = lagging. Uses SPDR sector ETFs for US stocks (XLK, XLV, etc.) and ^HSI for HK stocks. ETF price history is stored in the DB; no live API calls during refresh. |
| **K-Score** | — | 0–100 | Weighted composite — above 70 is strong, below 40 is weak. If RS data is unavailable the 10% RS weight is redistributed proportionally among the other five factors. |

> **Rankings page RS column:** The RS score (0–100) appears as a column in the rankings table. Green (≥ 60) = stock is leading its sector. Red (< 40) = stock is lagging. Grey = no data.

### How to use relative strength

RS is a **quality filter on top of the AI signal**, not a standalone buy/sell trigger.

**Daily workflow — Rankings page**

Scan the RS column after sorting by K-Score. Prioritise stocks that are green on both.

| RS | AI Signal | Interpretation | Action |
|----|-----------|---------------|--------|
| ≥ 60 (green) | BUY | Highest-conviction setup — stock leading sector with technical entry | Act on signal |
| 45–59 (grey) | BUY | Standard setup — in-line with sector | Act normally |
| < 40 (red) | BUY | Weakened signal — stock lagging sector despite technical BUY. Signal engine has already reduced confidence 15%. | Investigate why before entering |
| < 40 (red) | HOLD/SELL | Capital leaving this stock specifically | Avoid new positions |
| ≥ 60 (green) | HOLD | Sector strength without a timing trigger yet | Watch for signal upgrade |

**Sector rotation signal**

If multiple stocks in the same sector simultaneously drop to red RS while their sector ETF is rising, capital is rotating *within* the sector away from those names. Cross-reference with the Sector Performance panel on the dashboard.

**What RS does not tell you**

- RS = 100 does not mean buy — a stock up 30% in 20 days may be extended and due for a pullback. Wait for a pullback entry or use the Confluence Score to assess timing.
- RS resets every 20 trading days. It reflects recent momentum, not long-term trend quality.
- RS is computed vs the stock's assigned sector ETF. If a stock's sector is unassigned (shown as "Other"), SPY is used as the fallback benchmark.

---

## Portfolio Optimizer — algorithm details

### Why naive MVO fails
Raw Mean-Variance Optimization is notorious for "error maximization" — tiny errors in expected return estimates get amplified into extreme, unstable weight allocations. StockAI uses three fixes:

1. **Ledoit-Wolf covariance shrinkage** — the Oracle estimator from scikit-learn shrinks the sample covariance toward a structured target, dramatically reducing estimation error for typical portfolio sizes (5–20 assets over 1–3 years of data).

2. **James-Stein return shrinkage** — individual asset return estimates are shrunk 50% toward the grand mean of the universe, reducing the noise that causes extreme corner solutions.

3. **Sharpe-ratio objective** — instead of a risk-aversion utility `(w·μ - λ/2·w·Σ·w)` with an arbitrary λ, the optimizer directly maximizes `(w·μ - Rf) / √(w·Σ·w)`, giving the theoretically correct tangency portfolio.

### HRP algorithm
```
1. Compute Ledoit-Wolf covariance → derive correlation matrix
2. Distance matrix:  d(i,j) = √((1 - ρ(i,j)) / 2)
3. Ward linkage clustering → dendrogram
4. Quasi-diagonalize: re-order assets by dendrogram leaf order
5. Recursive bisection:
   - Split into left/right sub-clusters
   - Weight each cluster by inverse of its equal-weight variance
   - Recurse until single assets
```

HRP never inverts the covariance matrix, making it numerically stable for any number of assets and robust to near-singular correlation structures.

---

## Confluence Score & Trade Decision System

The Confluence Score is a 0–100 composite that measures how strongly all four signal layers agree on a stock. It is the primary tool for deciding **when to buy, how much to buy, and when to sell**.

### Signal layers and what each one answers

| Layer | Signal | Timeframe | Question |
|-------|--------|-----------|----------|
| Fundamental filter | Analyst Rating | Months | Is this worth owning at all? |
| Conviction score | K-Score + Research | Weeks–months | How strong is the overall case? |
| Timing trigger | AI Signal | Days–weeks | Is now a good time to enter or exit? |
| Technical confirmation | Support/Resistance, RSI, MACD | Intraday–days | Where exactly to enter and exit? |

### Confluence Score formula

**On the Rankings and Opportunities pages** (no analyst data in bulk fetch):
```
AI component   = signal_direction × confidence / 100    weight 35%
K-Score        = composite ranking score (0–100)        weight 30%
Technical      = TA sub-score (0–100)                   weight 20%
Momentum       = momentum sub-score (0–100)             weight 15%
```
where `signal_direction` = BUY → 100, HOLD → 50, WAIT → 25, SELL → 0.

**On the Stock Detail page** (full data including analyst consensus):
```
AI component   = signal_direction × confidence / 100    weight 30%
K-Score        = composite ranking score (0–100)        weight 25%
Analyst        = (5 − recommendation_mean) / 4 × 100   weight 20%
Technical      = TA sub-score (0–100)                   weight 15%
Momentum       = momentum sub-score (0–100)             weight 10%
```
`recommendation_mean` is the yfinance value: 1.0 = Strong Buy → 5.0 = Sell, mapped linearly to 0–100.

### Grade thresholds and position sizing

| Score | Grade | Max position | Meaning |
|-------|-------|--------------|---------|
| 80–100 | Strong | 8–10% | All signals align — highest-conviction entry |
| 65–79 | Good | 5–7% | Most signals agree — size normally |
| 50–64 | Moderate | 2–4% | Mixed signals — reduce size, wait for confirmation |
| < 50 | Weak | Avoid | Signals conflict — no entry recommended |

Position sizes are as a percentage of total portfolio. Adjust down in a Bear Market (S&P 500 below 200-day MA).

### Where the score appears

- **Rankings page** — Confluence column beside K-Score; hover for grade and max position hint
- **Opportunities page** — new **Confluence** tab (🎯) filters stocks with score ≥ 65; key metric card shows score and grade
- **Stock Detail sidebar** — full Confluence Panel showing score bar, grade, max position size, entry zone, and exit targets

### Entry Playbook

**Step 1 — Screen (Opportunities → Confluence tab)**
Only stocks with score ≥ 65 appear. This is your filtered shortlist where the majority of signals agree.

**Step 2 — Confirm conviction (Stock Detail page)**
- Confluence Panel score ≥ 65 (ideally ≥ 80 for a full position)
- K-Score fair value: current price below fair value
- Market Regime: green dot (Bull Market). In Bear Market, require score ≥ 75 and reduce position size by half.
- Fear & Greed gauge: avoid entering when Extreme Greed (> 80) — wait for a pullback

**Step 3 — Time the entry (Chart)**
- Price is at or near a Support level shown in the Trade Setup panel
- 52-week position: below 60% of range is preferable
- RSI below 65 (not overbought)

**Step 4 — Size the position**
Use the grade's max position % as your ceiling. Apply it to the Position Sizer below to calculate exact share count based on your stop loss distance.

**Step 5 — Set alerts**
- Price alert at the support level below entry — your stop reference
- Signal alert on — get emailed if AI Signal deteriorates
- (Optional) In-browser confluence alert via the local alert system

### Exit Playbook

| Trigger | Action |
|---------|--------|
| AI Signal drops to SELL, Analyst still Buy | Trim 50% — momentum gone but fundamental intact; wait for re-entry signal |
| AI Signal = SELL + Analyst = Hold/Underperform | Full exit |
| Price reaches Analyst **mean price target** | Trim to 50% of position; raise stop to breakeven |
| Price reaches Analyst **high price target** | Full exit — full upside captured |
| K-Score fair value hit | Reassess — stock may be fully valued; tighten stop |
| Confluence score drops below 50 | Review position; prepare to exit if no improvement next session |

### Highest-conviction setup (all four aligned)

The rarest and highest-quality entries occur when:
- Analyst: **Strong Buy** (recommendation_mean ≤ 1.5)
- AI Signal: **BUY** with confidence > 70%
- K-Score: above 65
- Chart: price sitting at a Support level, not extended

At this combination, confluence score will typically be 80+. This is the signal to use your maximum position allocation.

### In-browser Confluence Alert

A `confluence_above` alert condition is available in the local alert system. When the confluence score for a watched symbol crosses a threshold (e.g. 70), an in-browser notification fires and the notification bell updates. Set via the alert checker that runs every 60 seconds in `_app.tsx`.

---

## K-Score — sector-relative value and growth scoring

### Background

The original K-Score used price-only proxies for the Value and Growth sub-scores:

| Sub-score | Proxy |
|-----------|-------|
| Value | Distance below 52-week high (discount = potential value) |
| Growth | 12-month CAGR of the closing price |

These proxies are noisy — a stock can be far below its 52w high simply because it collapsed, and price CAGR conflates capital gains with fundamental earnings growth.

### Sector-relative percentile ranking

When real fundamentals are available (cached in Redis from `GET /stocks/{symbol}/fundamentals`), the ranking engine replaces both proxies with sector-relative percentile ranks. Each metric is ranked within the stock's sector peer group across all active symbols.

**Value score inputs (lower = better value, so ranks are inverted):**

| Metric | Direction |
|--------|-----------|
| Trailing P/E | Lower is cheaper |
| Forward P/E | Lower is cheaper |
| Price-to-Book | Lower is cheaper |
| EV/EBITDA | Lower is cheaper |

**Growth score inputs (higher = better growth, ranks are direct):**

| Metric | Direction |
|--------|-----------|
| Earnings growth (YoY) | Higher is better |
| Revenue growth (YoY) | Higher is better |
| Return on Equity (ROE) | Higher is better |

Each group of metrics is averaged into a 0–100 score where 100 = best in sector. If fewer than 3 peers have data for a metric, it is excluded to avoid ranking noise from a single outlier.

### Fallback

If a symbol has no fundamentals cached in Redis, or fewer than 2 peers exist in the sector, the original price-based proxies (`_value_proxy`, `_growth_proxy`) are used unchanged. This ensures the ranking engine never crashes due to missing data.

### Implementation

- **`services/market-data/src/api/routes.py`** — `GET /stocks/fundamentals_bulk` reads all active symbols' Redis-cached fundamentals in one request and returns a flat dict, avoiding per-symbol yfinance calls at ranking time.
- **`services/ranking-engine/src/api/routes.py`** — `_fetch_fundamentals_bulk()` calls the above endpoint once per ranking run; `_sector_relative_scores(symbol, sector, all_fundamentals)` computes the percentile ranks and returns `(value_score, growth_score)` or `(None, None)` on fallback.
- **`services/ranking-engine/src/scoring/kscore.py`** — `compute_kscore()` accepts optional `value_score` and `growth_score` parameters; when provided they replace the price proxies.

---

## Signal engine — weekly TA score fix

### Problem

The weekly TA sub-score (`weekly_ta_score`) was always 0.50 (neutral), causing a permanent weekly conflict compression (×0.85) on every signal regardless of the actual weekly trend. This was suppressing many legitimate BUY signals — the compression was firing universally rather than selectively.

**Root cause:** The DB only stores D1 (daily) and M5 (5-minute intraday) price bars. The `_fetch_weekly_prices(symbol)` function queried for `timeframe = '1w'` rows, which don't exist, so it always returned an empty DataFrame.

### Fix

Weekly bars are now synthesised in memory by resampling the daily bars that are already fetched for signal generation. The `_resample_to_weekly(df)` function groups daily OHLCV bars into weekly candles using `pandas.DataFrame.resample("W-MON")`:

```
open  = first bar of week
high  = max high of week
low   = min low of week
close = last bar of week
volume = sum of week
```

The resulting weekly DataFrame is passed to `_weekly_ta_score()` exactly as before — no other code changed. The minimum 10-week requirement for a valid weekly score is enforced; if fewer than 10 weeks of data exist the score falls back to 0.50.

**Effect:** The weekly conflict compression now only fires when the weekly trend genuinely disagrees with the daily trend, instead of on every stock.

---

## Admin shared AI key

### Problem

Non-admin users (e.g. `lauwing2`) had provider set to "none" by default and no API key in their localStorage namespace. The client-side `isAiConfigured()` check blocked all AI calls before they reached the backend, so features like Game Plan, Trade Board alert suggestions, and AI Chat were completely unavailable.

### Backend fix — Redis fallback in `ai_proxy.py`

`POST /ai/chat` in the API gateway now accepts an empty `api_key` field. When the key is empty, the endpoint looks up a shared admin-configured key from Redis:

| Redis key | Purpose |
|-----------|---------|
| `stockai:admin:claude_api_key` | Shared Claude key for all users |
| `stockai:admin:deepseek_api_key` | Shared DeepSeek key for all users |
| `stockai:admin:claude_model` | Default Claude model when no personal key |
| `stockai:admin:deepseek_model` | Default DeepSeek model when no personal key |

If neither the user's key nor the Redis admin key exists, a clear error is returned asking the user to configure a key or ask the admin.

### Frontend fix — `isAiConfigured()` no longer requires a local key

`frontend/src/lib/ai.ts` — `isAiConfigured()` previously returned `false` for any user without a local API key, blocking the UI before any network call was made. It now returns `true` whenever a provider is selected (not "none"), trusting the backend to either find the user's key or fall back to the shared Redis key. The client sends an empty `api_key` string; the backend resolves it.

### Admin UI — "Share my key with all users"

**Settings → AI Assistant** now shows a **"Share my key with all users"** button for admin accounts. Clicking it pushes the currently configured key and model to Redis via `POST /admin/config` on the market-data service. This is the zero-friction way to set the shared key — no API calls or Redis CLI needed.

**`/admin/config` new fields:**

| Field | Description |
|-------|-------------|
| `claude_api_key` | Stores to `stockai:admin:claude_api_key` in Redis |
| `deepseek_api_key` | Stores to `stockai:admin:deepseek_api_key` in Redis |
| `claude_model` | Stores to `stockai:admin:claude_model` in Redis |
| `deepseek_model` | Stores to `stockai:admin:deepseek_model` in Redis |

### How to enable AI for all users

1. Log in as admin → **Settings → AI Assistant**
2. Select your provider (Claude or DeepSeek) and enter your API key
3. Click **"Share my key with all users"** — confirmation message appears
4. Non-admin users now need only to select a provider in their own Settings (not "Disabled") — no key required

---

## Signal engine v5 — market breadth regime filter

### Background

The v4 regime detection was two-dimensional: S&P 500 vs 200-day MA (bull/bear) and Fear & Greed score (high_vol). A limitation of using only the index price is that the S&P 500 can remain above its 200-day MA while the majority of individual stocks have already broken down — a condition known as "narrow breadth" or "internal deterioration." BUY signals during narrow breadth have a higher false-positive rate because the broad market backdrop is weakening even though headline indices have not yet confirmed it.

### Market breadth metric

**Market breadth** is the percentage of active US stocks in the tracked universe that are currently trading above their 200-day simple moving average. The SMA-200 is already computed and stored as `fair_price` in the `rankings` database table during each ranking refresh, so no additional price computation is required.

**Endpoint:** `GET /stocks/market_breadth`

| Field | Description |
|-------|-------------|
| `breadth_pct` | % of active US stocks above their 200-day SMA |
| `above_200ma` | Count trading above SMA-200 |
| `below_200ma` | Count trading below SMA-200 |
| `label` | "Healthy" (≥60%) / "Mixed" (40–60%) / "Weak" (<40%) |
| `color` | Green / Yellow / Red |

Cached in Redis for 4 hours. The endpoint compares each stock's latest live price (from the live-prices Redis cache) against its stored `fair_price`. If live price is unavailable for a stock it is excluded from the count.

### Signal engine integration

`_fetch_market_breadth()` is called once per `generate_signal()` invocation alongside `_fetch_market_regime()`. The result is stored in `reasons["breadth_pct"]`.

**Compression rule:** when `breadth_pct < 40%` in a `bull`, `high_vol`, or `unknown` regime, the fused signal is compressed 10% toward neutral:

```
fused = 0.5 + (fused - 0.5) × 0.90
reasons["breadth_compression"] = True
```

The compression is not applied in `bear` regime because the bear thresholds (0.73 BUY, 0.56 HOLD) already account for broad market weakness.

### Interaction with other v4/v5 compressions

| Layer | Condition | Effect |
|-------|-----------|--------|
| ADX compression | ADX < 20 | ×0.90 toward neutral |
| High-vol compression | Regime = high_vol | ×0.85 toward neutral |
| **Breadth compression** | **Breadth < 40% + bull/high_vol** | **×0.90 toward neutral** |

Compressions stack multiplicatively. A stock in a high-vol regime with breadth < 40% and ADX < 20 receives all three, reducing a 90% bullish signal to roughly 80% — still a BUY in bull conditions but less extreme.

### Dashboard — Market Breadth panel

The **Dashboard** now shows a Market Breadth tile alongside the Portfolio Pulse panel:

- Horizontal progress bar filled to `breadth_pct`%
- Large percentage figure in colour (green / yellow / red)
- "Healthy / Mixed / Weak" label
- Stock counts: ↑ N above · ↓ N below

Data refreshes every 4 hours (matching the Redis TTL).

### Implementation files

| File | Change |
|------|--------|
| `services/market-data/src/api/routes.py` | Added `GET /stocks/market_breadth` endpoint + `_MARKET_BREADTH_KEY` Redis constant |
| `services/signal-engine/src/generators/signals.py` | Added `_fetch_market_breadth()`, wired into `generate_signal()` with compression logic; updated docstring to v5 |
| `frontend/src/lib/api.ts` | Added `MarketBreadth` type + `api.marketBreadth()` |
| `frontend/src/pages/index.tsx` | Fetches breadth via SWR, passes to `MarketOverview`; added breadth tile to dashboard |

---

## S/R detection — three-tier logic with Fibonacci fallback

**Problem:** The Confluence Score entry zone for SMTC showed $63.40 when the stock was trading at $151. Root cause: `detect_support_resistance` ranked all pivot levels by touch count across 400 bars of history. SMTC spent 370 of those bars in the $54–$81 range, so those pivots had the highest strength scores. It only crossed $100 on 2026-04-16 (~30 bars), leaving no established S/R in the $100–$155 range.

### Three-tier detection strategy

`detect_support_resistance()` in `services/technical-analysis/src/indicators/trendlines.py` now uses a cascading approach:

**Tier 1 — Local structure (last 90 bars, within 25% of current price)**
Run pivot detection on only the most recent 90 bars. If 2+ levels fall within 25% of the current price, use those. This captures fresh S/R that has formed since a breakout.

**Tier 2 — Full history within 35%**
Run pivot detection on the full 400-bar history. Filter to levels within 35% of current price. Use if 2+ found. This catches established S/R that is still within a reasonable trading range.

**Tier 3 — Fibonacci retracement fallback**
When no meaningful S/R exists nearby (stock at all-time high with no prior pivot structure in range), synthesise Fibonacci retracement levels from the 90-bar high/low swing:

| Ratio | Role |
|-------|------|
| 23.6% | Resistance |
| 38.2% | Support |
| 50.0% | Support |
| 61.8% | Support |
| 78.6% | Support |

For SMTC, the 90-bar range spans roughly $63 (pre-breakout low) to $151 (recent high), producing Fib levels at approximately $91, $108, $113, $121, $133, $148 — all realistic entry/support zones for a stock at $151.

**Why not a 60% proximity band?** The previous version tried bands of 35% → 60% → unlimited before falling back to Fibonacci. With a 60% band, SMTC's old $63–$81 pivots qualified (60% below $151 = $60.40), so Fibonacci was never reached. The 60% fallback was removed; Tier 2 caps at 35%, then immediately falls through to Fibonacci.

### Implementation file

| File | Change |
|------|--------|
| `services/technical-analysis/src/indicators/trendlines.py` | Refactored `detect_support_resistance()` into 3-tier logic; added `_cluster_pivots()` helper; added `_fib_levels_from_range()` Fibonacci synthesiser |

---

## Signal & ML Quality Improvements (batch — 2026-05-31 to 2026-06-01)

A set of correctness and quality fixes applied across the signal engine, ML pipeline, and data layer.

### ML model calibration
The model's raw probabilities were over-confident (spiky histogram, not well-calibrated). An **IsotonicRegression calibrator** is now fitted on a held-out 15% calibration set, saved alongside the model in the joblib bundle, and applied at inference time via `predict_latest()`. Three-way split (70/15/15) prevents the calibrator from fitting on training data.

### K-Score — falling knife gate
The value sub-score previously rewarded stocks at low absolute P/E regardless of whether earnings were deteriorating. A **falling knife gate** was added to `_value_proxy()` in `kscore.py`: if TTM revenue growth or earnings growth is deeply negative (below a threshold), the value score is capped even if valuation metrics appear cheap.

### Macro features — Redis cache fallback
`fetch_macro_features()` in `builder.py` now caches VIX, S&P 500, and Fed Funds data in Redis. On yfinance failure the feature builder reads the cached values rather than zero-filling, which previously caused the ML model to receive corrupted inputs at inference time.

### Look-ahead bias guard
`train_model()` in `trainer.py` filters today's bars from the training set before fitting. Without this, the model could see partial intraday bars as complete bars, introducing subtle look-ahead bias in feature computation.

### Prompt injection sanitisation
All four Research Engine route handlers (`GET`, `DELETE`, `POST`, `POST/chat`) call `_sanitise_symbol()` before using the ticker in any Claude prompt. The function strips all characters outside `[A-Z0-9.\-:]`, covering US tickers, HK codes (e.g. `0700.HK`), and indices (`^VIX`). Invalid symbols return HTTP 400 before a prompt is constructed.

### RSI scoring curve — asymmetric piecewise
The RSI component in `_technical_score()` was symmetric around 50. Replaced with an asymmetric piecewise curve: oversold (RSI < 30) scores highest, momentum zone (50–65) scores well, overbought (RSI > 75) penalised. This avoids rewarding late-stage breakouts with the same RSI score as early-entry setups.

### Adjusted close consistency
`yfinance_adapter.py` now sets `auto_adjust=True` for all daily bar fetches, ensuring that all feature computation (SMA, RSI, MACD, returns) uses split- and dividend-adjusted closes. Previously, raw closes could cause SMA crossover signals on ex-dividend days.

### Strategy weight normalisation
`scoreFor()` in `opportunities.tsx` normalises strategy weights to sum to 1 before blending sub-scores. Previously, strategies whose weights didn't add to 100 would produce scores that were not on the 0–100 scale and were not comparable across strategy presets.

### Zero-volume bar filter
`validate_ohlcv()` in `ingestion.py` drops bars where volume = 0. Zero-volume bars are data artefacts (market closures, data vendor errors) that cause flat RSI readings and incorrect VWAP calculations. Affected primarily certain HK small-caps during holidays.

### Research engine cache quality flag
The report object now carries a `report_quality` field (`fresh` | `cached` | `stale`). The Research page displays a yellow warning banner when the report is from cache and more than 24 h old, so the user knows when to regenerate.

### Stale price data warning
`_check_price_staleness()` in `signals.py` logs a structured `signal.stale_price_data` warning with `last_bar` and `days_old` fields if the most recent price bar is more than 3 days old. Signal computation is not blocked, but the gap is observable in logs.

### Earnings surprise model
Three new fields added to the fundamentals endpoint and surfaced in the Research Engine:
- `eps_beat_rate` — fraction of the last 8 quarters where EPS beat consensus estimate
- `eps_avg_surprise_pct` — mean EPS surprise percentage
- `eps_surprise_trend` — direction of recent surprises (improving/declining)

Research Engine scoring: +5 pts if `beat_rate ≥ 75%`; +2 pts if `≥ 50%`. The stock detail page shows a per-quarter beat/miss grid with colour coding.

### Relative strength vs sector
`rs_rank = (1 + stock_20d_return) / (1 + sector_ETF_20d_return)`, mapped to RS score 0–100 (50 = in-line with sector). Added as a 10% weight in the K-Score. US stocks are compared against the appropriate SPDR sector ETF (XLK, XLV, XLF, etc.); HK stocks against ^HSI. Signal engine: `rs_rank < 0.8` compresses the fused signal 15% toward neutral regardless of regime. The Rankings table shows a colour-coded RS column (green ≥ 60, red < 40).

### News sentiment compression
The last 10 news headlines per stock are scored with VADER sentiment (mapped to 0–100). Results are stored in the signal `reasons` dict and shown in the stock detail page. If the 7-day aggregated sentiment score is below 25, the fused signal is compressed 30% toward neutral; below 35, it is compressed 20%. This suppresses BUY signals ahead of coverage of material negative events (regulatory action, leadership departure, etc.) that technicals do not detect.

### Implementation file summary

| Improvement | File |
|---|---|
| ML calibration | `services/ml-prediction/src/ml/trainer.py` |
| Falling knife gate | `services/ranking-engine/src/kscore.py` |
| Macro Redis cache | `services/ml-prediction/src/ml/builder.py` |
| Look-ahead guard | `services/ml-prediction/src/ml/trainer.py` |
| Prompt injection | `services/research-engine/src/api/routes.py` |
| RSI curve | `services/ranking-engine/src/kscore.py` |
| Adj close | `services/market-data/src/adapters/yfinance_adapter.py` |
| Weight normalisation | `frontend/src/pages/opportunities.tsx` |
| Zero-volume filter | `services/market-data/src/services/ingestion.py` |
| Cache quality flag | `services/research-engine/src/api/routes.py` + `frontend/src/pages/research/[symbol].tsx` |
| Stale price check | `services/signal-engine/src/generators/signals.py` |
| Earnings surprise | `services/market-data/src/api/routes.py` + `services/research-engine/src/api/routes.py` + `frontend/src/pages/stock/[symbol].tsx` |
| Relative strength | `services/ranking-engine/src/` + `frontend/src/pages/rankings.tsx` |
| News sentiment | `services/signal-engine/src/generators/signals.py` |

---

## Unified + Add ▾ workflow (Screener & Forecast)

A **WatchlistPickerButton** component (`frontend/src/components/WatchlistPickerButton.tsx`) provides a unified `+ Add ▾` dropdown on Screener result rows and Forecast picks. This replaces the previous per-page "Add to Watchlist" buttons with a single workflow that covers both Watchlists and the Trade Board.

### Dropdown sections

**WATCHLISTS** — Lists all of the current user's named watchlists, each showing its item count. Clicking a list adds the symbol immediately and replaces the count with a green `✓ Added` checkmark. Multiple lists can be added to in one session without closing the dropdown.

**TRADE BOARD** — A single row "Add to Radar". Clicking adds the stock to the Trade Board at the Radar (watch) stage. Shows `✓ Added` after the first click.

### Positioning
The dropdown opens leftward (`right: 0` CSS) so it doesn't overflow the right edge of the screen regardless of where the button appears in the table.

### Implementation
| File | Role |
|---|---|
| `frontend/src/components/WatchlistPickerButton.tsx` | Dropdown component — `size="xs"` for Screener, `size="sm"` for Forecast |
| `frontend/src/pages/screener.tsx` | Replaced `+ Watch` column with `<WatchlistPickerButton />` |
| `frontend/src/pages/forecast.tsx` | Added `<WatchlistPickerButton />` next to existing card footer buttons |

---

## ML fusion weight — empirical validation (2026-06-02)

### Background

The signal engine blends the ML model's bullish probability with the hand-tuned TA score using a dynamic weight:

```
ml_weight = clip(0.40 + (auc − 0.50) / 0.20 × 0.35,  min=0.40, max=0.75)
```

This maps AUC 0.50 (random) → 40% ML and AUC 0.70 (excellent) → 75% ML. Two problems existed before this fix:

1. **Wrong AUC input** — the formula was fed the cross-validation (CV) AUC, which is computed on training folds and is upward-biased. The held-out test AUC is the honest, unbiased measure.
2. **No empirical backing** — the 40–75% range was hand-designed with no validation against actual signal history.

### Fix 1 — Switch to held-out test AUC

`predict_ensemble` in `trainer.py` now reads each model bundle's `"auc"` field (computed on the 15% held-out test set) instead of `"cv_auc_mean"` when weighting XGBoost vs Random Forest internally. It exposes `test_auc_mean` in its response metrics. The signal engine (`signals.py`) reads `test_auc_mean` with fallback to `auc` then `cv_auc_mean` for model bundles trained before this fix.

### Fix 2 — Empirical weight sweep

A new endpoint `GET /signals/ml-weight-validation?lookback_days=N` sweeps the ML fusion weight from 0.00 to 1.00 in 0.05 steps and, for each value, computes:

- **Accuracy** — % of BUY signals (that would have fired at that weight) where price rose after the signal
- **Avg return %** — average price change for those signals

The sweep uses real historical signal data from the `signals` table: `ml_probability` and `ta_score` from the `reasons` JSON are re-blended at each test weight, and actual price outcomes are matched via the prices table.

### Empirical result

Over 180 days of signal history (170 signals):

| ML Weight | Accuracy |
|---|---|
| 0% (TA only) | 39.3% |
| **40% (formula min)** | **41.2% — empirical optimum** |
| 50% | ~40% |
| 75% (formula max) | ~39% |
| 100% (ML only) | ~38% |

The accuracy curve is relatively flat across all weights (±2%), confirming that TA and ML mostly agree on signal direction. The formula's lower bound of **0.40 is validated** as the empirical optimum — the ML model adds directional value most reliably when TA is still given 60% weight.

### Weight validation chart

The Signal Accuracy page (`/signal-accuracy`) shows a bar chart visualising the full accuracy curve. Bars are coloured:
- **Green** — empirical optimum weight
- **Purple** — weights inside the current formula range (40–75%)
- **Dark grey** — weights outside the formula

### Implementation files

| File | Change |
|---|---|
| `services/ml-prediction/src/training/trainer.py` | `predict_ensemble` uses test AUC for internal model weighting; exposes `test_auc_mean` in response |
| `services/signal-engine/src/generators/signals.py` | `_fetch_ml_data` reads `test_auc_mean` with fallback to `auc` / `cv_auc_mean` |
| `services/signal-engine/src/api/routes.py` | New `GET /signals/ml-weight-validation` endpoint |
| `frontend/src/lib/api.ts` | `mlWeightValidation()` call + `MLWeightValidation` / `MLWeightCurvePoint` types |
| `frontend/src/pages/signal-accuracy.tsx` | `MLWeightChart` component + `useSWR` fetch |

---

## Options Flow (2026-06-02)

### What it is

Options flow is real-time tracking of unusual activity in the options market. When large institutions expect a stock to move, they often buy options *before* buying the underlying stock — it's cheaper to build a large directional position through options. This shows up as a spike in call volume ahead of a price move up, or put volume ahead of a drop.

The signal is captured through the **call/put ratio (C/P)**: total call volume divided by total put volume across the nearest two expiry dates. A C/P of 2.0+ means calls are being bought at twice the rate of puts — institutions are positioning for upside.

### The unusual contract flag

A contract is flagged as unusual when `volume > 30% of open interest` for that strike. This means today's trading created a significant fraction of the total outstanding positions — it's a fresh, large directional bet, not routine hedging.

**Most significant pattern:** `Vol/OI > 1.0×` (highlighted in amber in the UI) means volume *exceeded* open interest — the position was built from scratch today. Short-dated OTM calls with Vol/OI > 1 are the classic institutional front-running pattern.

### Signal engine integration

Options flow is fetched by the signal engine as part of `generate_signal()` and applied after the relative strength adjustment:

| Sentiment | C/P range | Signal effect |
|---|---|---|
| Strongly bullish | ≥ 2.0 | +7% boost to fused signal |
| Bullish | ≥ 1.3 | +3% nudge |
| Neutral | 0.8 – 1.3 | No change |
| Slightly bearish | ≤ 0.8 | 8% compress toward neutral |
| Bearish | ≤ 0.5 | 15% compress toward neutral |

The result is stored in `reasons` as `options_sentiment` and `options_cp_ratio` so it is visible in the signal detail. Only applies to US stocks — HK stocks and symbols without listed options return `available: false` and are skipped silently.

### Stock detail page

The **Options Flow** section appears on `/stock/[symbol]` below Institutional Holdings for any US stock with listed options. It shows:

- **C/P ratio bar** — green (calls) vs red (puts) proportional bar with call/put volume counts and the ratio
- **Sentiment badge** — colour-coded: green for bullish variants, red for bearish
- **Unusual contracts table** — contracts where volume exceeded 30% of open interest, sorted by volume descending

| Column | Description |
|---|---|
| Side | CALL or PUT |
| Strike | Contract strike price |
| Expiry | Expiration date |
| Volume | Contracts traded today |
| OI | Open interest (existing positions) |
| Vol/OI | Volume ÷ OI — values > 1.0× (amber) are the most unusual |
| IV | Implied volatility — high IV on OTM options = expensive, aggressive bet |
| ITM | Whether the contract is currently in the money |

### How to use it as a confirmation tool

1. A stock surfaces from the Screener or Forecast with a BUY signal
2. Open the stock detail page and check Options Flow
3. **High C/P + OTM calls + Vol/OI > 1×** → institutional positioning aligns with the signal — higher conviction
4. **Puts dominating despite a BUY signal** → treat the signal with caution; someone large is hedging against upside

### Data source and caching

Options chain data is fetched from yfinance (same library used for price data — no additional API key required). The response is cached in Redis for **15 minutes**. Only the two nearest expiration dates are fetched to keep response time fast. HK stocks and symbols without listed options return `{"available": false}` immediately without an external call.

### API

```
GET /stocks/{symbol}/options-flow
```

Response (when available):
```json
{
  "symbol": "AAPL",
  "available": true,
  "call_volume": 186847,
  "put_volume": 83431,
  "cp_ratio": 2.24,
  "sentiment": "strongly_bullish",
  "unusual_count": 12,
  "unusual": [
    { "expiry": "2026-06-03", "side": "call", "strike": 310.0,
      "volume": 23086, "oi": 1120, "vol_oi": 20.61, "iv": 27.2, "itm": false }
  ],
  "expiries_used": ["2026-06-03", "2026-06-05"]
}
```

### Implementation files

| File | Change |
|---|---|
| `services/market-data/src/api/routes.py` | New `GET /stocks/{symbol}/options-flow` endpoint with Redis cache |
| `services/signal-engine/src/generators/signals.py` | `_fetch_options_flow()` helper; options boost/compress applied after RS filter |
| `frontend/src/lib/api.ts` | `getOptionsFlow()` call + `OptionsFlow` / `OptionsFlowContract` types |
| `frontend/src/pages/stock/[symbol].tsx` | Options Flow section with C/P bar, sentiment badge, unusual contracts table |

---

## Trading Style System

The trading style system lets you match AI signal criteria to your actual holding horizon — SHORT (1–5 days), SWING (5–20 days), LONG (30–90 days), or GROWTH (10–40 days, relaxed thresholds for high-velocity momentum stocks). All four signal profiles are computed in a single data pass for every stock on every refresh cycle, so switching style is instant.

### How it works

All four signals are generated by `generate_all_signals(symbol)` in `services/signal-engine/src/generators/signals.py` and stored as separate rows in the `signals` table with `horizon = SHORT / SWING / LONG / GROWTH`. The frontend reads whichever horizon matches the active style.

### Where the style setting is resolved

The system has a three-level hierarchy:

| Level | Where set | Scope |
|-------|----------|-------|
| **Global default** | Settings → Trading Style | All pages that have no override |
| **Per-list override** | Watchlist tab badge / Create modal | That watchlist only |
| **Historical capture** | Trade Board (set at activation) | Individual closed trade record |

### Global default (Settings page)

Set once in **Settings → Trading Style — AI Signal Horizon**. Applies to Dashboard, Rankings, Screener, Opportunities, Forecast, Positions, Alerts, and any watchlist that has no style assigned. Stored in browser localStorage as `tradingStyle` inside the settings JSON blob. Read at page load via `getSignalStyle()` from `frontend/src/lib/settings.ts`.

### Per-list style (Watchlist page)

Each watchlist carries a `trading_style` column (nullable). When you view a watchlist:
- If the list has a style set, signal columns use that style
- If the list has no style, the global setting is used as fallback

**Create** — the New Watchlist modal has a style picker (Global default / Short Term / Swing Trade / Long Term / Growth / Momentum). Stored via `POST /watchlists` with `trading_style` in the body.

**Change** — the tab badge cycles through `null → SHORT → SWING → LONG → GROWTH → null` on each click. Stored via `PUT /watchlists/{id}` with `trading_style` in the body. Sending an empty string clears the style back to null (inherits global).

**Display** — the tab shows a small colored chip: `SHORT` (red), `SWING` (indigo), `LONG` (green), `GROWTH` (purple). Lists with no style show a `+style` prompt on the active tab only.

### Historical style capture (Trade Board)

When a card is moved to Active (via drag-and-drop or the stage pill), the system records the style that was active at that moment — either the global setting or the relevant list's override — and stores it as `trading_style` on the `trade_plans` row. This value is immutable after it is set; it represents the signal context at trade entry, not the current setting.

The captured style appears as a colored badge on closed cards and is used to generate the **By Style** breakdown in the Performance Summary bar.

### Signal API

The signal engine API accepts a `?style=` query parameter:

```
GET /signals/latest?style=SWING     # latest SWING signal for all stocks
GET /signals/latest?style=SHORT     # latest SHORT signal for all stocks
GET /signal_for/{symbol}?style=LONG # LONG signal for one stock (regenerated if stale)
```

All three styles are computed and persisted in one call. The `?style=` parameter only filters what is returned — it does not affect what is computed and stored.

### `signals` table — `horizon` column

| Value | Meaning |
|-------|---------|
| `SHORT` | 1–5 day signal — pure TA, no earnings/news compression, ADX floor = 25 |
| `SWING` | 5–20 day signal — balanced, earnings + news compression, ADX floor = 15 |
| `LONG` | 30–90 day signal — K-Score boost, heavy weekly alignment, ADX filter off |
| `GROWTH` | 10–40 day signal — relaxed thresholds, no RS compression, TA score adjusted for momentum names |

The unique "latest signal" per stock is defined by `MAX(ts)` grouped on `(stock_id, horizon)` — not just `stock_id`. Each stock therefore has up to four current signals simultaneously.

### `watchlists` table — `trading_style` column

| Value | Meaning |
|-------|---------|
| `NULL` | No override — inherits global setting |
| `'SHORT'` | Force SHORT signals for this list |
| `'SWING'` | Force SWING signals for this list |
| `'LONG'` | Force LONG signals for this list |
| `'GROWTH'` | Force GROWTH signals for this list |

### `trade_plans` table — `trading_style` column

Records which style was active at the time the position was opened. Set once at activation, never updated. Used for the per-style performance breakdown in the Trade Board performance stats.

### Style profile parameter summary

| Parameter | SHORT | SWING | LONG | GROWTH |
|-----------|-------|-------|------|--------|
| ML weight cap | 30% | 75% | 45% | 70% |
| BUY threshold (bull) | 0.60 | 0.62 | 0.60 | **0.57** |
| BUY threshold (bear) | 0.68 | 0.70 | 0.70 | 0.68 |
| ADX filter | 25 min | 15 min | off | **12 min** |
| Earnings compression | off | 0.50× / 0.75× / 0.90× | off | 0.60× / 0.80× / 0.92× |
| News compression | off | 0.75× / 0.85× | off | 0.80× / 0.90× |
| RS compression | 0.90× | 0.85× | 0.80× | **off** |
| K-Score boost | off | off | on | off |
| Max compression floor | 0.70 | 0.55 | 0.65 | 0.60 |
| Weekly align boost/compress | 1.08× / 0.93× | 1.12× / 0.85× | 1.18× / 0.80× | 1.08× / 0.92× |
| Weekly RSI gate | — | applies | applies | **skipped** |
| TA score adjustment | — | — | — | **SMA20>SMA50 +0.10, RSI 72–85 +0.04** |

For the complete parameter table and compression pipeline walkthrough, see [AI_SIGNAL.md](AI_SIGNAL.md).

### Implementation files

| File | Role |
|------|------|
| `services/signal-engine/src/generators/signals.py` | `_STYLE_PROFILES` dict, `generate_all_signals()`, `_apply_style_signal()`, compression cap |
| `services/signal-engine/src/generators/__init__.py` | Exports `generate_all_signals` |
| `services/signal-engine/src/api/routes.py` | `?style=` filter, per-`(stock_id, horizon)` latest-signal subquery, bulk persist of all 3 styles |
| `shared/db/models.py` | `Signal.horizon` column; `Watchlist.trading_style`; `TradePlan.trading_style`, `actual_entry_price`, `shares` |
| `shared/db/session.py` | Migrations: `signals.horizon`, `watchlists.trading_style`, `trade_plans.trading_style`, `actual_entry_price`, `shares` |
| `services/market-data/src/api/watchlist.py` | `trading_style` in `WatchlistOut`, `CreateWatchlistRequest`, `RenameWatchlistRequest`; create/update handlers |
| `services/market-data/src/api/board.py` | `trading_style`, `actual_entry_price`, `shares` in `PlanIn`, `PlanUpdate`, `PlanOut`; captured at activation |
| `frontend/src/lib/settings.ts` | `tradingStyle` field in `AppSettings`; `getSignalStyle()` helper |
| `frontend/src/lib/api.ts` | `allSignals(style?)`, `signal(symbol, style?)`, `WatchlistMeta.trading_style`, `TradePlan.trading_style` |
| `frontend/src/pages/settings.tsx` | Trading Style selector section |
| `frontend/src/pages/watchlist.tsx` | Per-list style picker in create modal; tab badge with click-to-cycle; `effectiveStyle` signal fetch |
| `frontend/src/pages/board.tsx` | Fill modal; `trading_style` captured on activate; style badge on closed cards; By Style perf breakdown |
| All signal-consuming pages | `api.allSignals(getSignalStyle())` with `'signals-' + getSignalStyle()` SWR cache key |

---

## VWAP + Support/Resistance Context

Adds real-time price-level awareness to the signal engine. Every signal now knows whether the stock is sitting at a meaningful support or resistance level before the final score is issued.

### How it works

`_sr_context()` in `signals.py` scans the last 60 daily bars for swing pivots (local highs and lows) plus the 52-week high/low. The current price is compared to each level with a ±1 % proximity band.

| Condition | Adjustment |
|-----------|------------|
| At resistance | −15% compression on final score |
| Breakout above resistance | +5% boost |
| At support | +3% boost |

The VWAP (computed from the current-day intraday bars) is shown alongside the S/R context for reference.

### Where it appears

- **Stock detail page → Signal card**: shows the identified level (e.g. `Support $148.20`) plus the adjustment applied
- **Signal reasons** include an `sr_context` field with the level type and price

### Implementation files

| File | Role |
|------|------|
| `services/signal-engine/src/generators/signals.py` | `_sr_context()` pivot detection; compression/boost applied in `_build_signal()` |
| `frontend/src/pages/stock/[symbol].tsx` | S/R context chip in SignalCard |

---

## ATR Position Sizing Engine

Turns the abstract "buy signal" into a concrete share count and dollar risk figure, calibrated to the user's own account size and risk tolerance.

### Settings

On the **Settings page**, two new fields are stored in `localStorage`:

| Setting | Default | Description |
|---------|---------|-------------|
| Account Size | — | Total portfolio value in dollars |
| Risk Per Trade % | 1% | Maximum loss per trade as a percentage of account |

### Position Sizer widget

The **Stock detail page** shows a **Position Sizer** panel that reads the ATR stop-loss distance and computes:

| Output | Formula |
|--------|---------|
| Stop Loss | Current price − 2 × ATR(14) |
| Shares | `(accountSize × riskPct) / (price − stopLoss)` |
| Dollar Risk | `shares × (price − stopLoss)` |
| R:R ratio | `analystTarget / stopLoss` distance vs risk distance |

The ATR is fetched from `GET /stocks/{symbol}/atr?period=14` which returns the Wilder smoothed ATR and the pre-computed 2-ATR stop level.

### Implementation files

| File | Role |
|------|------|
| `services/market-data/src/api/routes.py` | `GET /stocks/{symbol}/atr` — Wilder ATR + stop_loss_2atr |
| `frontend/src/pages/settings.tsx` | Account Size + Risk Per Trade % inputs |
| `frontend/src/pages/stock/[symbol].tsx` | PositionSizer component with useEffect localStorage sync |

---

## Model Drift Detection

Monitors the live accuracy of buy signals over time and warns when model performance is degrading.

### How it works

`GET /signals/rolling_accuracy?window=30&lookback_days=180` returns a time series of rolling BUY signal accuracy computed over a sliding 30-day window across the past 180 days. Accuracy is the fraction of BUY signals where the stock was higher than entry after the signal horizon.

A `drift_warning` flag is set when the latest window accuracy falls below **55%**.

### Where it appears

- **Signal Accuracy page** shows a line chart of rolling accuracy with two reference lines:
  - 50% — random baseline
  - 55% — drift warning threshold
- If `drift_warning` is true, a red warning chip appears above the chart

### Implementation files

| File | Role |
|------|------|
| `services/market-data/src/api/routes.py` | `GET /signals/rolling_accuracy` — deduped by `(stock_id, sig_date, horizon)`, off-by-one window fixed |
| `frontend/src/pages/signal-accuracy.tsx` | Line chart + drift warning chip |

---

## Peer Comparison Table

Side-by-side K-Score sub-score breakdown for up to 4 stocks at once. Useful for picking the strongest name within a sector or watchlist.

### How to use it

**From the Rankings page:**
1. Click **+** in the new Compare column next to any row (up to 4 stocks)
2. A **Compare (N)** button appears in the toolbar
3. Click it to open the full comparison drawer

**From a Stock detail page:**
- A **Sector Peers** panel automatically suggests the top 3 stocks in the same sector sorted by K-Score
- Click **Compare** to open the drawer with the current stock pre-selected alongside its peers

### What the drawer shows

The drawer is a fixed overlay on the right side of the screen. Columns are the selected stocks; rows are metrics:

| Metric | Description |
|--------|------------|
| Price | Live price from the price feed |
| K-Score | Overall composite score |
| Technical | RSI, MACD, moving average sub-score |
| Momentum | Price momentum sub-score |
| Value | P/E, P/B, EV/EBITDA sub-score |
| Growth | Revenue + earnings growth sub-score |
| Volatility | ATR-based risk sub-score (lower = better) |
| Relative Strength | Performance vs. market sub-score |
| Upside | (Fair Price − Current Price) / Current Price |

Cells are color-coded: **green** = top quartile within the selected set, **red** = bottom quartile.

### Implementation files

| File | Role |
|------|------|
| `frontend/src/components/PeerCompareDrawer.tsx` | Comparison overlay — METRICS array, `cellColor()`, `getValue()` |
| `frontend/src/components/RankingsTable.tsx` | `selectedSymbols` + `onToggleCompare` props; toggle column |
| `frontend/src/pages/rankings.tsx` | `compareSymbols` Set state; "Compare (N)" + "Clear" toolbar buttons |
| `frontend/src/pages/stock/[symbol].tsx` | `sectorPeers` useMemo; Sector Peers panel JSX; `compareRows` construction |

---

## Portfolio Risk Dashboard

Gives a quantitative view of the risk in the current Trade Board positions: correlation between holdings, market sensitivity (beta), and downside exposure (VaR).

### Where it appears

The **Trade Board** page shows a **Portfolio Risk** section below the positions list. It only appears when at least 2 active positions have shares filled in.

### Risk metrics

| Metric | Method |
|--------|--------|
| Portfolio Beta | Weighted average of individual betas; each beta = `cov(stock, bench) / var(bench)` over 30 days |
| 1-Day VaR 95% | Parametric: `portfolio_vol × 1.645 × 100` (percentage of portfolio) |
| Benchmark | SPY when US positions dominate; ^HSI when HK positions dominate |

### Warnings

Yellow/red chips appear automatically when:

- Portfolio beta > 1.5 (high market sensitivity)
- VaR > 3% (significant tail risk)
- Any single sector > 40% of portfolio weight (concentration risk)
- Any pair of holdings has correlation > 0.8 (redundant positions)

### Visualizations

- **Sector pie chart** — SVG pie with legend showing weights per GICS sector
- **Correlation heatmap** — grid of colored cells; red > 0.8, yellow > 0.5, grey ≥ 0, blue < 0
- **Per-symbol beta chips** — quick scan of which holdings are driving the portfolio beta

### Implementation files

| File | Role |
|------|------|
| `services/market-data/src/api/portfolio.py` | `GET /portfolio/risk` — `_fetch_returns()`, `_beta()`, VaR, sector weights |
| `services/market-data/src/main.py` | Registers `portfolio_router` |
| `frontend/src/lib/api.ts` | `portfolioRisk(symbols, weights?)` typed method |
| `frontend/src/pages/board.tsx` | Risk section JSX — stat cards, warnings, SVG pie, heatmap, beta chips |

---

## DCF Fair Value

A two-stage Discounted Cash Flow model that produces an independent fair value estimate, separate from the K-Score model's fair price. Shown on the Research page alongside analyst targets.

### Model

**Stage 1 — 5-year FCF projection:**  
Each year's free cash flow is projected at the growth rate and discounted back at WACC:

```
PV = Σ (FCF × (1+g)^t) / (1+WACC)^t   for t = 1..5
```

**Stage 2 — Terminal value (Gordon Growth):**  
```
TV = FCF_5 × (1+g_terminal) / (WACC − g_terminal)
TV_PV = TV / (1+WACC)^5
```

**Per-share fair value:**  
```
(PV + TV_PV) / shares_outstanding
```

### Growth rate fallback chain

1. Analyst consensus growth rate (from financials API)
2. Trailing 3-year revenue CAGR
3. Sector default (7%)

### WACC by sector

| Sector | WACC |
|--------|------|
| Utilities, Consumer Staples | 8% |
| Industrials, Healthcare, Real Estate, Materials | 9% |
| Technology, Energy, Consumer Discretionary, Communication Services | 10% |
| Financials | 11% |

The model returns `null` (not shown) when FCF ≤ 0 — the Gordon Growth model is not meaningful for loss-making companies.

### HIGH CONVICTION badge

When the DCF fair value and the analyst consensus target agree within **15 percentage points** of upside/downside, a **HIGH CONVICTION** badge is shown. This indicates two independent valuation methods are aligned.

### Where it appears

The **Research page** (`/research/[symbol]`) shows the DCF chip in the signal + ranking row:
- Green when DCF upside > 10%
- Yellow when DCF upside 0–10%
- Red when DCF shows downside

### Implementation files

| File | Role |
|------|------|
| `services/research-engine/src/api/routes.py` | `_WACC` dict, `_TERMINAL_GROWTH`, `_dcf_fair_value()`, injected into `generate_research()` |
| `frontend/src/pages/research/[symbol].tsx` | DCF chip + HIGH CONVICTION badge in signal row |

---

## Paper Portfolio (`/paper-portfolio`)

A live paper trading simulator that runs concurrently with real market data. Admin-only. The engine executes the same signal pipeline used for real signals — every trade decision is driven by the same BUY signals, K-Score thresholds, and conviction gate logic, just without real capital.

### How it works

The scheduler calls `paper_trading_step()` every 5–10 minutes during market hours. Each step:

1. **Monitor open positions** — check stop-loss breach, take-profit hit, trailing stop, signal decay, stall detection
2. **Scan for new entries** — query fresh BUY signals, score each via the conviction gate, size via ATR, enter if all checks pass

All decisions are logged to `paper_decisions` with full context (why entered, why skipped, why exited).

### Tabs

| Tab | Content |
|---|---|
| **Positions** | Open trades with entry price, current price, unrealised P&L, stop/target distances |
| **Decisions** | Entry decision log — every signal considered, with pass/fail reason per conviction gate layer |
| **Closed Trades** | All completed trades with entry/exit price, hold days, P&L %, exit reason |
| **Equity Curve** | Equity vs SPY/QQQ with market regime shading (bull=green, bear=red, choppy=amber) |
| **Attribution** | Win rate broken down by entry score band, confidence band, market regime, R:R ratio |

### Summary stats

| Stat | Description |
|---|---|
| Total Return % | `(current_equity − initial_capital) / initial_capital × 100` |
| Sharpe Ratio | Annualised `(mean_return − risk_free) / std_return` over equity curve |
| Max Drawdown | Largest peak-to-trough decline in equity curve |
| Calmar Ratio | `annualised_return / max_drawdown` |
| Alpha | Excess return above `beta × SPY_return` (CAPM alpha) |
| Beta | `cov(portfolio, SPY) / var(SPY)` over equity curve |
| Information Ratio | `alpha / tracking_error` |
| Win Rate | Closed trades with `pnl > 0` / total closed |
| vs SPY / vs QQQ | Equity return minus benchmark return over same period |

### Engine config (admin)

Editable from the page footer. Key parameters:

| Param | Default | Effect |
|---|---|---|
| `max_positions` | 10 | Hard cap on simultaneous open trades |
| `risk_per_trade_pct` | 1% | Position size = `risk_pct × equity / stop_distance` |
| `min_confidence` | 62% | Signal confidence floor for entry |
| `min_entry_score` | 3 | Minimum conviction gate score (0–10+) |
| `trading_style` | GROWTH | Which style profile to use for signal filtering |

### Database tables

| Table | Purpose |
|---|---|
| `paper_portfolios` | Portfolio config, cash balance, is_active flag |
| `paper_trades` | Every trade — entry/exit price, size, reasons, P&L |
| `paper_equity_curve` | Daily EOD snapshot of equity + benchmark closes + market regime |
| `paper_decisions` | Every entry/skip/exit decision with full reasoning |

---

## Multi-Portfolio A/B Testing (`/paper-portfolio`) — Planned

> **Status: Design complete, implementation pending (AL-2 / PT-A4)**

Run SWING, GROWTH, and LONG paper portfolios simultaneously on the same signals and compare their live performance empirically.

### Why

A single paper portfolio can't tell you whether GROWTH parameters outperform SWING under the same market conditions. Running them sequentially (reset and restart) gives different time periods — not a fair comparison. Running them in parallel on identical signal universes is the only valid A/B test.

### Page layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Paper Portfolios                             [+ New Portfolio]  │
│  3 strategies running in parallel                               │
├──────────────────┬──────────────────┬───────────────────────────┤
│  SWING           │  GROWTH ★ best   │  LONG                     │
│  $54,200         │  $56,100         │  $49,800                   │
│  +8.4%           │  +12.2% ▲        │  -0.4% ▼                  │
│  Win 64%         │  Win 71%         │  Win 55%                   │
│  Sharpe 1.2      │  Sharpe 1.8      │  Sharpe 0.4               │
│  8 open          │  6 open          │  3 open                    │
│  ● Running       │  ● Running       │  ● Running                 │
│  [View Detail]   │  [View Detail ✦] │  [View Detail]             │
├──────────────────┴──────────────────┴───────────────────────────┤
│  Equity Curves (overlaid, since portfolio start)                │
│  ── SWING (indigo)  ── GROWTH (purple)  ── LONG (green)         │
│  ── SPY (grey dashed, benchmark)                                │
│                      [Plotly chart]                             │
├─────────────────────────────────────────────────────────────────┤
│  ▼ SWING Portfolio  (selected by clicking a card above)         │
│  [Positions] [Decisions] [Closed Trades] [Equity] [Attribution] │
│                  [existing tab content, unchanged]              │
└─────────────────────────────────────────────────────────────────┘
```

### Key UI decisions

**Portfolio cards (not tabs)** — all portfolios visible simultaneously so you don't need to switch tabs to compare. The `★ best` badge marks the highest Sharpe ratio at a glance.

**Overlaid equity chart** — the single most valuable comparison view. Shows divergence points where one strategy started outperforming. One chart, all curves, SPY as benchmark.

**Click card → detail panel** — clicking any portfolio card expands the full existing detail UI (positions, closed trades, attribution, equity, decisions) below the comparison section. No new tabs required. Default: first portfolio selected.

**`+ New Portfolio` modal** — form with: Name, Style (SWING / GROWTH / LONG), Starting Capital. The engine already loops over all active portfolios — creating a new one is sufficient to start it.

### Backend changes required

| Endpoint | Change |
|---|---|
| `GET /paper-portfolio/list` | New — returns id, name, style, equity, return %, sharpe, win rate, open count for all active portfolios |
| `POST /paper-portfolio/create` | New — creates portfolio with `{name, trading_style, initial_capital}` |
| `GET /paper-portfolio/compare` | New — returns all equity curves in one call for the overlay chart |
| All existing endpoints | Add optional `?portfolio_id=N` query param; default to first active for backwards compatibility |

The scheduler's `paper_trading_step()` already iterates over all `is_active=True` portfolios — no scheduler change needed.

### Frontend changes required

| Component | Change |
|---|---|
| Page header | Replace single-portfolio title with comparison card grid |
| Overlay chart | New Plotly chart — one trace per portfolio + SPY dashed |
| Detail panel | Unchanged content; all SWR keys updated to include `portfolio_id` |
| `+ New Portfolio` | Modal with name / style / capital fields |

### What does NOT change

Everything inside the detail panel (positions table, decisions log, closed trades, attribution heatmap) is the existing code with only the `portfolio_id` parameter added to each API call. Zero regression risk.

### Implementation order

1. Backend: `list`, `create`, `compare` endpoints + `portfolio_id` param on existing endpoints
2. Frontend: comparison card grid + overlay chart + selected portfolio state
3. Frontend: wire detail panel SWR keys to selected `portfolio_id`
