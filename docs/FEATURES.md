# Feature Reference

Complete reference for every feature in StockAI — what it does, where to find it,
and how it works under the hood.

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
Credentials are stored in browser **localStorage**:
- `stockai_auth_users` — overrides the built-in default credentials
- `stockai_auth_session` — set on successful login, cleared on logout

The default account (`lausing` / `120402`) is hardcoded as a fallback in `lib/auth.ts`.

### Navigation
- Logged-in username displayed in the top-right nav bar
- **🔔 Notification Bell** — shows unread alert count, dropdown with recent notifications
- **⚙ Settings** icon — quick link to Settings page
- **Logout** button clears the session and redirects to `/login`

---

## Dashboard (`/`)

The main screen. Shows every stock in your universe as cards.

### Stock cards
- **Symbol + company name** — click to go to the stock detail page
- **Current price** — live real-time price (yfinance `fast_info`, refreshes every 60 s)
- **Day change** — percentage and absolute change from previous close, color-coded green/red
- **K-Score badge** — composite 0–100 score (green ≥ 70 / yellow ≥ 50 / red < 50)
- **BUY / SELL / HOLD badge** — real TA-based signal from the signal engine

### Toolbar
- **↻ Refresh** — re-fetches all five data sources simultaneously
- **⚡ Train All** — runs the full pipeline: ingest → refresh prices → schedule ML training
- **+ Add Stock** — opens the Add Stock modal

### Auto-refresh
Prices refresh automatically every 60 seconds via SWR `refreshInterval`.

---

## Stock Detail (`/stock/[symbol]`)

Full drill-down page for a single stock.

### Navigation
- **← Back** button — returns to the previous page

### Header
- Symbol, company name, market, exchange, sector
- **Fair Value** card — DCF-lite derived fair price with K-Score
- **Recommendation** card — BUY/SELL/HOLD with bullish probability %
- **↻ Refresh** / **☆ Watch** toggle

### Chart
- Candlestick chart (lightweight-charts)
- SMA20, SMA50, EMA20, Bollinger Bands overlaid
- Volume histogram, Support/Resistance levels, Fibonacci retracement

### Sidebar
- **AI Signal** — BUY/SELL/HOLD, confidence, bullish probability bar
- **K-Score** — composite + five sub-scores + fair price
- **ML Prediction** — predict/train per model, Train All shortcut
- **Chart Patterns** — detected patterns with confidence %
- **Support & Resistance** — up to 6 levels, color-coded
- **Fibonacci Levels** — key retracement levels

### Company Financials
Fetched from yfinance `.info`, Redis-cached 24 h.

**Valuation** — Market Cap, Enterprise Value, P/E (TTM), Forward P/E, P/B, EV/EBITDA

**Financials (TTM)** — Revenue (+ YoY growth), Gross Profit, Net Income, EBITDA, Free Cash Flow, Operating Cash Flow

**Three-column grid** — Balance Sheet (cash vs debt) · Margins (gross/operating/profit) · Returns & Growth (ROE, ROA, earnings growth)

**Per Share & Risk** — EPS, Forward EPS, Book Value, Dividend Yield, Beta, Shares Outstanding

**52-Week Range** — gradient bar showing current price position between 52W low/high

**Analyst Consensus** — recommendation label + target price + analyst count

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

## Rankings (`/rankings`)

Leaderboard of all tracked stocks sorted by K-Score.

- Sortable columns: K-Score, Technical, Momentum, Value, Growth, Volatility, Price, Change%
- Fair price column — compare current price to estimated fair value
- Click any row to go to stock detail

---

## Watchlist (`/watchlist`)

Your curated list of stocks to monitor closely.

### Features
- Signal stats bar (BUY / HOLD / SELL / TOTAL counts)
- Signal filter tabs (ALL / BUY / HOLD / SELL)
- Sort by: Symbol, Signal, K-Score, Change%, Price
- Auto-refreshing live prices every 60 s

### Per-stock card
- Price + day change, signal badge, K-Score bar, note preview, price alert banner
- **📝 Notes** — free-text, stored in localStorage
- **🔔 Alerts** — target price + Above/Below trigger (these are simple watchlist alerts, separate from the full Alerts page)
- **+ POS** — navigate to Positions with symbol pre-filled
- **✕** — remove from watchlist

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

Rule-based alerts that trigger when your tracked stocks meet a condition.

### Alert conditions

| Condition | Trigger |
|-----------|---------|
| Price rises above `$X` | Live price > threshold |
| Price falls below `$X` | Live price < threshold |
| Day gain exceeds `X%` | Daily % change > threshold |
| Day loss exceeds `X%` | Daily % change < −threshold |
| Signal becomes BUY | ML+TA signal flips to BUY |
| Signal becomes SELL | ML+TA signal flips to SELL |
| K-Score rises above `X` | K-Score > threshold |
| K-Score falls below `X` | K-Score < threshold |

### Creating an alert
- Select stock from your universe (dropdown)
- Choose condition type
- Enter threshold (where applicable)
- Set cooldown (15 min / 30 min / 1 h / 4 h / 24 h) — prevents repeated triggers

### Active alerts list
- Toggle each alert on/off individually (CSS toggle switch)
- Shows condition text, cooldown, last triggered time
- Delete individual alerts

### Notification History
- Up to 50 most recent triggered alerts
- Symbol badge links to stock detail page
- Relative timestamps (just now / 5m ago / 2h ago)
- **Clear history** button

### How alerts are checked
The global alert checker runs in the background every 60 seconds via `_app.tsx`:
1. Fetches latest prices, signals, and K-Scores
2. Evaluates every enabled alert against current values
3. Respects the cooldown — skips re-triggering within the cooldown window
4. Plays a notification sound (if enabled in Settings) and fires `stockai:notifications` DOM event
5. The **🔔 notification bell** in the nav bar shows the unread count badge

### Storage
- `stockai_alert_rules` — alert definitions (localStorage)
- `stockai_notifications` — last 100 triggered notifications (localStorage)

---

## Strategies (`/strategies`)

Build, save, and backtest rule-based trading strategies.

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
- Link to **Change Password** (Reset Password tab on the login page)

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

## API Reference

### Data
```
POST /admin/seed                       # seed default stock universe
POST /admin/ingest  {symbols:[...]}    # ingest price history (parallel, synchronous)
GET  /stocks                           # list all tracked stocks
GET  /stocks/{symbol}/prices           # OHLCV history from DB
GET  /stocks/latest_prices             # live prices (yfinance fast_info, Redis 60 s cache)
GET  /stocks/{symbol}/fundamentals     # company financials (yfinance .info, Redis 24 h cache)
GET  /stocks/{symbol}/news?sources=yfinance,google   # news + sentiment (filterable by source)
```

### Signals
```
GET  /signals/{symbol}               # compute signal (live)
GET  /signals/{symbol}?persist=true  # compute + save to DB
GET  /signals                        # latest persisted signal for all active stocks
```

### ML
```
POST /ml/train      {symbol, model}  # train a single model (~30 s)
POST /ml/predict    {symbol, model}  # get a prediction
POST /ml/train_all                   # schedule training for all active stocks
GET  /ml/models                      # list available models
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

### Watchlist
```
GET    /watchlist                    # get watchlist
POST   /watchlist/{symbol}           # add to watchlist
DELETE /watchlist/{symbol}           # remove from watchlist
GET    /watchlist/{symbol}           # check if watched (returns bool)
```

### Strategies & Portfolio
```
POST /strategies                         # create strategy
GET  /strategies                         # list strategies
POST /backtest   {strategy_id, symbol}   # run backtest
POST /portfolio/optimize                 # run portfolio optimization
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
| Stock detail chart (OHLCV) | DB `prices` table | As of last ingest |
| Company Financials | yfinance `.info` | Redis 24 h TTL (quarterly data) |
| News | yfinance + Google News RSS | Redis 30 min TTL per source combination |
| K-Score / Fair price | DB `rankings` table | As of last rankings refresh |
| AI Signal | DB `signals` table (TA + ML) | As of last stock detail view |
| ML prediction | Trained model inference | On demand |

---

## Redis cache keys

| Key | Contents | TTL |
|-----|----------|-----|
| `stockai:live_prices` | Array of live price objects for all active stocks | 60 s |
| `stockai:fundamentals:{SYMBOL}` | Company fundamentals JSON for one symbol | 24 h |
| `stockai:news:{SYMBOL}:{sources}` | News articles for one symbol + source combination | 30 min |

---

## Browser storage keys

| Key | Contents |
|-----|----------|
| `stockai_auth_users` | `{username: password}` — overrides default credentials |
| `stockai_auth_session` | `{username}` — active session |
| `stockai_settings` | All app settings (data sources, AI keys, intervals, etc.) |
| `stockai_alert_rules` | Array of alert rule objects |
| `stockai_notifications` | Last 100 triggered notifications |
| `stockai_positions` | Array of `{id, symbol, shares, avgCost, currency, addedAt}` |
| `stockai_trades` | Map of `{symbol: [{type, shares, price, date}]}` |
| `stockai_watch_notes` | Map of `{symbol: noteText}` |
| `stockai_price_alerts` | Map of `{symbol: {target, direction}}` (watchlist quick-alerts) |

Clearing browser storage resets all client-side data including login session and AI keys.

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
| Technical | 0–100 | SMA trend, RSI health, MACD direction, golden/death cross |
| Momentum | 0–100 | 1-week, 1-month, 3-month price rate-of-change |
| Value | 0–100 | P/E, P/B, EV/EBITDA vs sector; DCF-lite fair price |
| Growth | 0–100 | Revenue + earnings growth (YoY) |
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
