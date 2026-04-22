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
- Red error banner if current password is wrong or passwords don't match
- New password must be at least 4 characters

### How auth works
Credentials are stored in browser **localStorage**:
- `stockai_auth_users` — overrides the built-in default credentials
- `stockai_auth_session` — set on successful login, cleared on logout

The default account (`lausing` / `120402`) is hardcoded as a fallback in `lib/auth.ts` and always works even if localStorage is cleared.

### Navigation
- Logged-in username displayed in the top-right of the nav bar
- **Logout** button clears the session and redirects to `/login`

---

## Dashboard (`/`)

The main screen. Shows every stock in your universe as cards.

### Stock cards
- **Symbol + company name** — click to go to the stock detail page
- **Current price** — live real-time price (yfinance `fast_info`, refreshes every 60 s)
- **Day change** — percentage and absolute change from previous close, color-coded green/red
- **K-Score badge** — composite 0–100 score (green ≥ 70 / yellow ≥ 50 / red < 50)
- **BUY / SELL / HOLD badge** — real TA-based signal from the signal engine; falls back to K-Score threshold if no signal has been persisted for that stock yet

### Toolbar
- **↻ Refresh** — re-fetches all five data sources simultaneously (stocks, watchlist, rankings, live prices, signals)
- **⚡ Train All** — runs the full pipeline:
  1. Ingests latest prices for every stock (parallel, synchronous — waits for completion)
  2. Refreshes price cards on the dashboard immediately
  3. Schedules XGBoost training for all stocks in the background (~2–5 min)
- **+ Add Stock** — opens the Add Stock modal

### Add Stock modal
- Monospace search field — type any ticker (US or HK, e.g. `0700.HK`)
- Quick-add grid — AAPL, NVDA, MSFT, TSM, BABA, SHOP, PLTR, COIN
- Green success / red error feedback card after submission

### Auto-refresh
Prices refresh automatically every 60 seconds via SWR `refreshInterval`. No user action needed to keep prices current.

---

## Stock Detail (`/stock/[symbol]`)

Full drill-down page for a single stock. Accessed by clicking any stock card.

### Navigation
- **← Back** button at the top — returns to the previous page (dashboard, watchlist, positions)

### Header
- Symbol, company name, market, exchange, sector
- **Fair Value** card — DCF-lite derived fair price with K-Score
- **Recommendation** card — BUY/SELL/HOLD with bullish probability percentage
- **↻ Refresh** — refetches price data and news simultaneously
- **☆ Watch / ★ Watching** — toggle adds/removes from watchlist

### Chart (left panel)
- TradingView-style candlestick chart (lightweight-charts)
- Overlaid indicators: SMA20, SMA50, EMA20, Bollinger Bands
- Volume histogram
- Support/Resistance levels as horizontal lines
- Fibonacci retracement levels

### Sidebar (right panel)

**AI Signal card**
- BUY / SELL / HOLD with horizon label (short / swing / long)
- Confidence score 0–100
- Bullish probability progress bar

**K-Score card**
- Composite score + all five sub-scores: Technical, Momentum, Value, Growth, Volatility
- Fair price estimate from DCF-lite model

**ML Prediction panel**
- Model selector: `xgboost`, `random_forest`, `gradient_boosting`, `lstm`
- **Predict** — runs inference against a trained model instantly
- **Train This** — trains the selected model for this symbol (~30 s)
- **⚡ Train All Stocks** — ingest all stocks → wait for completion → refresh chart → schedule ML training for all
- Direction (UP/DOWN), bullish probability bar, confidence %

**Chart Patterns**
- Detected patterns with confidence % (Head & Shoulders, Double Top/Bottom, Triangles, Flag, Cup & Handle)

**Support & Resistance levels**
- Up to 6 levels, color-coded green (support) or red (resistance), with touch-count strength

**Fibonacci Levels**
- Key retracement levels (0%, 23.6%, 38.2%, 50%, 61.8%, 100%) with price values

### News & Sentiment (full width below chart)
- News articles fetched per symbol
- Each article: headline (2-line clamp), source, publish time, sentiment badge
- **Bullish** (green) / **Bearish** (red) / **Neutral** (gray) sentiment badges
- Click headline to open original article in a new tab

---

## Rankings (`/rankings`)

Leaderboard of all tracked stocks sorted by K-Score.

- Sortable columns: K-Score, Technical, Momentum, Value, Growth, Volatility, Price, Change%
- Fair price column — compare current price to estimated fair value
- Color-coded scores per cell
- Click any row to go to stock detail

---

## Watchlist (`/watchlist`)

Your curated list of stocks to monitor closely.

### Adding stocks
- **☆ Watch** button on any stock detail page
- Quick-add from the dashboard Add Stock modal

### Signal stats bar
Three colored boxes at the top:
- **BUY** count (green) / **HOLD** count (yellow) / **SELL** count (red) / **TOTAL**

### Signal filter tabs
Show only: **ALL / BUY / HOLD / SELL**

### Sort controls
Sort by: Symbol, Signal, K-Score, Change%, Price — with asc/desc toggle

### Per-stock card
- Symbol + company name (links to stock detail)
- Current price (live, auto-refreshes every 60 s) + day change %
- Signal badge (BUY/SELL/HOLD)
- K-Score with visual progress bar (green/yellow/red)
- Note preview (first line of saved note, if any)
- Price alert triggered banner (yellow) when price crosses target

### Action buttons per card

| Button | What it does |
|--------|-------------|
| 📝 | Opens note modal — write/save a private note for this stock |
| 🔔 | Opens alert modal — set a target price with Above/Below trigger |
| + POS | Navigates to `/positions?add=SYMBOL` to pre-fill the add position modal |
| ✕ | Removes from watchlist |

### Notes (📝)
- Free-text textarea
- Stored in `localStorage` key `stockai_watch_notes`
- First line previewed on the card

### Price Alerts (🔔)
- Target price + direction (Above / Below)
- Stored in `localStorage` key `stockai_price_alerts`
- Yellow triggered banner appears on the card when live price crosses the target

---

## Positions (`/positions`)

Portfolio tracker for your actual or simulated stock holdings.

### Summary stats bar
- **Positions** — open position count
- **Invested** — total cost basis
- **Market Value** — current value at live prices
- **Today's P&L** — unrealized gain/loss for the current session
- **Total P&L** — total unrealized gain/loss with color coding and percentage

### Allocation donut chart
- Plotly.js pie/donut chart of portfolio allocation by market value
- Shown when you have more than one position
- Legend on the right with symbol labels

### Best / Worst performer cards
- Best position by P&L% highlighted in green
- Worst position by P&L% highlighted in red

### Signal summary
- BUY / HOLD / SELL count for your current holdings
- Colored progress bars showing proportion

### Sort controls
Sort by: Symbol, Value, P&L$, P&L%, Today, K-Score — with asc/desc toggle

### Position rows
Each row shows:
- Symbol (link to stock detail) + shares + average cost + currency
- Current price (live) + day change %
- Market value
- P&L in dollars and percentage, color-coded
- Signal badge + K-Score color indicator
- **★** Watch toggle — adds/removes from watchlist
- **BUY** / **SELL** buttons — open trade modal
- **▼ History** — expands inline trade history drawer

### Adding a position
- Click **+ Add Position** button, or navigate from watchlist **+ POS** (pre-fills the symbol)
- Fields: symbol, shares, average cost, currency
- Currencies: USD, HKD, CAD, GBP, EUR, AUD

### Trade logging
- BUY: logs trade, updates average cost (cost-basis averaging)
- SELL: logs trade, reduces share count; removes position when all shares sold
- Trade history visible in expandable drawer per position

### CSV export
Downloads `positions.csv` with: Symbol, Shares, Avg Cost, Current Price, Market Value, P&L$, P&L%, Currency

### Train All (in positions page)
Same pipeline as the dashboard **⚡ Train All** button.

### Storage
- `stockai_positions` — array of position objects `{id, symbol, shares, avgCost, currency, addedAt}`
- `stockai_trades` — map of `{symbol: [{type, shares, price, date}]}`

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

## API Reference

### Data
```
POST /admin/seed                     # seed default stock universe
POST /admin/ingest  {symbols:[...]}  # ingest price history (parallel, synchronous)
GET  /stocks                         # list all tracked stocks
GET  /stocks/{symbol}/prices         # OHLCV history from DB
GET  /stocks/latest_prices           # live prices (yfinance fast_info, Redis 60s cache)
GET  /news/{symbol}                  # news + sentiment
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
                                     # levels, ranking, signal, price history
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

---

## Live price data

| Data | Source | Freshness |
|------|--------|-----------|
| Dashboard / Watchlist / Positions prices | yfinance `fast_info` (Redis 60 s cache) | Near real-time |
| Stock detail chart | DB prices table (OHLCV history) | As of last ingest |
| Latest price in stock detail header | yfinance via aggregate overview | Near real-time |
| K-Score / Fair price | DB rankings table | As of last rankings refresh |
| AI Signal | DB signals table (TA + ML) | As of last stock detail view |
| ML prediction | Trained model inference | On demand |
| News & sentiment | yfinance news API | Current session |

---

## Browser storage keys

| Key | Contents |
|-----|----------|
| `stockai_auth_users` | `{username: password}` — overrides default credentials |
| `stockai_auth_session` | `{username}` — active session |
| `stockai_positions` | Array of `{id, symbol, shares, avgCost, currency, addedAt}` |
| `stockai_trades` | Map of `{symbol: [{type, shares, price, date}]}` |
| `stockai_watch_notes` | Map of `{symbol: noteText}` |
| `stockai_price_alerts` | Map of `{symbol: {target, direction}}` |

Clearing browser storage resets all client-side data including login session.

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

## Portfolio Optimizer

The Portfolio Optimizer answers: *"given these stocks, how much of my money should go into each one?"*

**Endpoint:** `POST /portfolio/optimize`

**Methods:**

| Method | What it does |
|--------|-------------|
| `mean_variance` | Markowitz MVO — maximizes return for a given risk level. Caps any single stock at 35%. |
| `risk_parity` | Weights each stock so it contributes *equally* to total portfolio risk. Defensive by design. |
| `ai_allocation` | Filters by K-Score (min 60) first, then runs MVO on the survivors. Holds 5% cash buffer. |

**Inputs:** `symbols` list, `method`, `lookback_days` (default 365), `min_score` (for ai_allocation).

**Output:** `weights` map (e.g. `{AAPL: 0.22, NVDA: 0.18}`), `cash` %, `expected_return`, `expected_vol`.

Not yet wired into a frontend page — accessible via `POST http://localhost:8007/portfolio/optimize` directly.
