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

**History depth:** The overview endpoint fetches up to `1260` daily bars (~5 years of trading days). Actual depth depends on how far back yfinance has data for the stock — typically 5+ years for major US and HK equities. Run **Full Refresh** on the stock detail page to re-fetch the maximum available history.

**Chart features:**
- Candlestick chart (lightweight-charts)
- SMA20, SMA50, SMA200, Bollinger Bands overlaid (daily mode only)
- Volume histogram, Support/Resistance levels, Fibonacci retracement (daily mode only)

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

Your curated list of stocks to monitor closely.

### Features
- Multiple named lists — create / delete / switch via tabs; move stocks between lists
- Signal stats bar (BUY / HOLD / WAIT / SELL counts with colour-coded tiles)
- Signal filter tabs (ALL / BUY / HOLD / WAIT / SELL)
- Sort by: Symbol, Signal, K-Score, Change%, Price
- Auto-refreshing live prices every 60 s
- **Compare view** — select up to 8 stocks for a base-100 relative performance SVG chart (30 / 60 / 90 / 180 / 365 day periods)

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
| Watch | Grey | Tracking — no position yet |
| Planning | Indigo | AI game plan generated; evaluating entry |
| Active | Green | In trade — monitoring |
| Closed | Dark grey | Trade completed |

### Cards

Each card shows:
- **Symbol** (links to stock detail page) + source badge (📋 Game Plan / 🔮 Forecast / ✏️ Manual)
- **Entry / Stop / Target prices** in colour-coded monospace
- **R:R ratio** — auto-calculated as `(target − entry) / (entry − stop)` when all three prices are set
- **Notes** — truncated to 120 chars; expand with ▼
- **Full game plan details** — when expanded, shows title, entry zones with rationale, catalysts, and risk summary (only if saved from a game plan)
- **Stage selector** — click any stage pill to move the card instantly
- **Relative date** — "Today / Yesterday / Nd ago" based on last update

### Adding cards

Three ways to create a board card:

1. **Stock detail page** — after the AI generates a game plan, click **📌 Save to Board** in the game plan card header. Saves with stage = Planning, entry/stop/target prices pre-filled.
2. **Forecast page** — each AI pick has a **📌 Save to Board** button. Saves with stage = Watch, entry_low as entry price, notes from the pick's setup/catalyst/risk text.
3. **Manual** — click **+ Add** in the Watch column header on the board itself. Enter a symbol and optional notes.

### API endpoints
```
GET    /board              # list all trade plans for the current user (ordered by last update)
POST   /board              # create a plan {symbol, stage, game_plan, entry_price, stop_loss, take_profit, notes, source}
PUT    /board/{id}         # update stage, notes, prices, or game_plan
DELETE /board/{id}         # delete a plan
```

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

---

## Trade Performance (`/trade-performance`)

Shows real P&L by pairing each BUY signal with its next SELL or WAIT exit signal for the same stock. This is the harder, more honest measure — you only get credit for a closed trade, and holding periods are realistic.

- **Entry** — BUY signal date → entry price is the close on that date
- **Exit** — next SELL or WAIT signal for the same stock → exit price is the close on that date
- **Open trades** — BUY signals with no exit yet use the latest available price (marked "OPEN")
- **Lookback** — 90 / 180 / 365 days
- **Deduplication** — consecutive BUY refreshes for the same stock are collapsed into one trade. Only one open position is tracked per stock at a time; a new entry is only recorded after the previous trade closes (SELL/WAIT signal received).

### Summary cards
| Card | Good threshold | What it means |
|------|---------------|--------------|
| Win Rate | > 50% | % of closed trades that made money |
| Profit Factor | > 1.5 | Total profit from winners ÷ total loss from losers. Above 1.0 = system makes money over time |
| Avg Return | > 1% | Average % gain or loss per closed trade |
| Avg Win | — | Typical winning trade size |
| Avg Loss | — | Typical losing trade size. You want Avg Win > Avg Loss |
| Avg Hold | — | How long the system typically stays in a trade |

### By-symbol breakdown
Table showing win rate, average return, and average hold days per stock — tells you which symbols the signal engine works best on.

### Trade list
Every individual trade with: entry date / exit date / entry price / exit price / return % / hold days / exit signal (SELL, WAIT, or OPEN) / Win or Loss.

Filters: All / Closed / Open · All / Win / Loss · Symbol search · Sort by Date / Return / Hold Days

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

| Sub-score | Range | What drives it |
|-----------|-------|---------------|
| Technical | 0–100 | SMA(50/200) trend alignment, RSI(14), ADX(14) trend-strength bonus |
| Momentum | 0–100 | 1-week, 1-month, 3-month price rate-of-change |
| Value | 0–100 | Discount from 52-week high (price proxy; fundamentals not yet integrated) |
| Growth | 0–100 | 12-month price CAGR (price proxy; earnings/revenue growth not yet integrated) |
| Volatility | 0–100 | Inverse of 30-day realized volatility |
| **K-Score** | 0–100 | Weighted composite — above 70 is strong, below 40 is weak |

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
