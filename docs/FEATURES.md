# Feature Reference

Complete reference for every feature in StockAI — what it does, where to find it,
and how it works under the hood.

---

## Dashboard (`/`)

The main screen. Shows every stock in your universe as cards.

### Stock cards
- **Symbol + company name** — click to go to the stock detail page
- **Current price** — live price from the latest-prices endpoint
- **Day change** — absolute and percentage change from previous close, color-coded green/red
- **K-Score badge** — composite 0–100 score, color-coded (green ≥ 70 / yellow ≥ 50 / red < 50)
- **BUY / SELL / HOLD badge** — real TA-based signal from the signal engine (falls back to K-Score threshold if no signal has been generated yet)

### Toolbar
- **↻ Refresh** — re-fetches all five data sources simultaneously (stocks, watchlist, rankings, prices, signals)
- **⚡ Train All** — runs the full pipeline: ingest latest prices for every stock, then schedule XGBoost training for each one. Progress panel shows ingest count and training status. Models are ready in ~2–5 minutes.
- **+ Add Stock** — opens the Add Stock modal

### Add Stock modal
- Search field (monospace) — type any ticker (US or HK e.g. `0700.HK`)
- Quick-add grid — one-click buttons for AAPL, NVDA, MSFT, TSM, BABA, SHOP, PLTR, COIN
- Green success / red error feedback card after submission

---

## Stock Detail (`/stock/[symbol]`)

Drill-down page for a single stock. Accessed by clicking any stock card.

### Header
- Symbol, company name, market, exchange, sector
- **Fair Value** card — DCF-lite derived fair price with K-Score
- **Recommendation** card — BUY/SELL/HOLD with bullish probability
- **↻ Refresh** — refetches price data and news in one click
- **☆ Watch / ★ Watching** — adds or removes from your watchlist

### Chart (left)
- TradingView-style candlestick chart (lightweight-charts)
- Overlaid indicators: SMA20, SMA50, EMA20, Bollinger Bands
- Volume histogram
- Support/Resistance levels drawn as horizontal lines
- Fibonacci retracement levels

### Sidebar (right)

**AI Signal card**
- BUY / SELL / HOLD with horizon (short / swing / long)
- Confidence score 0–100
- Bullish probability progress bar

**K-Score card**
- Composite score with all five sub-scores: Technical, Momentum, Value, Growth, Volatility
- Fair price estimate

**ML Prediction panel**
- Model selector: xgboost, random_forest, gradient_boosting, lstm
- **Predict** button — runs inference against a trained model
- **Train This** button — trains the selected model for this symbol (~30s)
- **⚡ Train All Stocks** button — runs the full ingest + train pipeline for every active stock
- Direction (UP/DOWN), bullish probability bar, confidence

**Chart Patterns**
- Detected patterns (Head & Shoulders, Double Top/Bottom, Triangles, Flag, Cup & Handle) with confidence %

**Support & Resistance levels**
- Up to 6 levels color-coded green (support) or red (resistance) with touch-count strength

**Fibonacci Levels**
- Key retracement levels (0%, 23.6%, 38.2%, 50%, 61.8%, 100%) with price values

### News & Sentiment (full width below chart)
- News articles fetched per symbol
- Each article shows: headline (2-line clamp), source, publish time, sentiment badge
- Sentiment badge: **Bullish** (green) / **Bearish** (red) / **Neutral** (gray)
- Click headline to open original article

---

## Rankings (`/rankings`)

Leaderboard of all tracked stocks sorted by K-Score.

- Sortable columns: K-Score, Technical, Momentum, Value, Growth, Volatility, Price, Change%
- Fair price column — compare current price to estimated fair value
- Color-coded scores
- Click any row to go to stock detail

---

## Watchlist (`/watchlist`)

Your curated list of stocks to monitor closely.

### Adding stocks
- **☆ Watch** button on any stock detail page
- Quick-add from the dashboard Add Stock modal

### Signal stats bar
Three colored summary boxes at the top:
- **BUY** count (green)
- **HOLD** count (yellow)
- **SELL** count (red)
- **TOTAL** watched stocks

### Signal filter tabs
Filter your watchlist to show only: **ALL / BUY / HOLD / SELL**

### Sort controls
Sort by: Symbol, Signal, K-Score, Change%, Price — with asc/desc toggle

### Per-stock card
- Symbol + company name (links to stock detail)
- Current price + day change %
- Signal badge (BUY/SELL/HOLD) from real signal engine
- K-Score with visual progress bar (green/yellow/red)
- Note preview (if a note has been saved)
- Price alert triggered banner (yellow) when price crosses target

### Action buttons per card
| Button | What it does |
|--------|-------------|
| 📝 | Opens note modal — write and save a private note for this stock |
| 🔔 | Opens alert modal — set a target price (above or below trigger) |
| + POS | Navigates to `/positions?add=SYMBOL` to pre-fill the add position modal |
| ✕ | Removes from watchlist |

### Notes (📝)
- Free-text textarea stored in `localStorage` under key `stockai_watch_notes`
- Previewed on the card (first line)
- Persists across sessions in the same browser

### Price Alerts (🔔)
- Set a target price and choose **Above** or **Below** trigger direction
- Stored in `localStorage` under key `stockai_price_alerts`
- A yellow banner appears on the card when the current price crosses the target
- Persists across sessions

---

## Positions (`/positions`)

Portfolio tracker for your actual or simulated stock holdings.

### Summary stats bar (top)
- **Positions** — number of open positions
- **Invested** — total cost basis
- **Market Value** — current value at live prices
- **Today's P&L** — unrealized gain/loss for today
- **Total P&L** — total unrealized gain/loss with color coding and percentage

### Allocation donut chart
- Plotly.js pie/donut chart showing portfolio allocation by market value
- Each position gets a color slice; legend on the right
- Shown when you have more than one position

### Best / Worst performer cards
- Highlights the position with the highest and lowest P&L%
- Trophy emoji for best, chart-down for worst

### Signal summary
- BUY / HOLD / SELL count for your current holdings
- Colored progress bars showing proportion

### Sort controls
Sort by: Symbol, Value, P&L$, P&L%, Today, K-Score — with asc/desc toggle

### Position rows
Each position shows:
- Symbol (link to stock detail) + shares + average cost + currency
- Current price + day change%
- Market value
- P&L in dollars and percentage, color-coded
- Signal badge (BUY/SELL/HOLD) + K-Score color indicator
- **★** Watch toggle — adds/removes from watchlist
- **BUY** button — opens trade modal to buy more shares
- **SELL** button — opens trade modal to sell shares
- **▼ History** — expands inline trade history drawer

### Adding a position
- Click **+ Add Position** or navigate from watchlist with **+ POS**
- Fill in: symbol, shares, average cost, currency
- Supports: USD, HKD, CAD, GBP, EUR, AUD
- Pre-filled when coming from `/positions?add=SYMBOL`

### Trade logging (BUY / SELL)
- Each buy/sell is logged with date, price, shares
- Trade history visible in the expandable drawer per position
- BUY increases/updates average cost (cost-basis averaging)
- SELL reduces share count; removes position when all shares sold

### CSV export
- **Export CSV** button downloads a spreadsheet with: symbol, shares, avg cost, currency, current price, market value, P&L$, P&L%

### Train All (in positions)
- **⚡ Train All** button — same pipeline as dashboard: ingest all prices → train XGBoost for all stocks

### Storage
Positions and trades are stored in `localStorage`:
- `stockai_positions` — array of position objects
- `stockai_trades` — map of symbol → trade history array

---

## Strategies (`/strategies`)

Build, save, and backtest rule-based trading strategies.

### Strategy DSL
Define entry and exit conditions using a JSON rule tree:
```json
{
  "entry": {"op": "<", "left": "rsi_14", "right": 30},
  "exit":  {"op": ">", "left": "rsi_14", "right": 70}
}
```

Available fields: `rsi_14`, `macd_hist`, `close`, `sma_20`, `sma_50`, `ema_20`, `volume`, `bb_upper`, `bb_lower`

### Backtest results
- **Total return %**
- **Sharpe ratio**
- **Max drawdown %**
- **CAGR**
- **Win rate %**
- **Profit factor**
- Equity curve chart

---

## API Reference (key endpoints)

### Data
```
POST /admin/seed                     # seed default stock universe
POST /admin/ingest  {symbols:[...]}  # ingest/refresh price history
GET  /stocks                         # list all tracked stocks
GET  /stocks/{symbol}/prices         # OHLCV history
GET  /stocks/prices/latest           # latest close per all active stocks
GET  /news/{symbol}                  # news + sentiment per symbol
```

### Signals
```
GET  /signals/{symbol}               # compute signal (live)
GET  /signals/{symbol}?persist=true  # compute + save to DB
GET  /signals                        # latest persisted signal for all active stocks
```

### ML
```
POST /ml/train     {symbol, model}   # train a single model
POST /ml/predict   {symbol, model}   # get a prediction
POST /ml/train_all                   # schedule training for all active stocks
GET  /ml/models                      # list available models
```

### Rankings
```
GET  /rankings                       # K-Score leaderboard
GET  /rankings/{symbol}              # K-Score for one stock
POST /rankings/refresh               # recompute all K-Scores
```

### Aggregate (stock detail page data)
```
GET  /aggregate/overview/{symbol}    # all-in-one: price, indicators, patterns,
                                     # levels, ranking, signal, prices history
```

### Watchlist
```
GET  /watchlist                      # get watchlist
POST /watchlist/{symbol}             # add to watchlist
DELETE /watchlist/{symbol}           # remove from watchlist
GET  /watchlist/{symbol}             # check if watched (returns bool)
```

### Strategies & Portfolio
```
POST /strategies                     # create strategy
GET  /strategies                     # list strategies
POST /backtest    {strategy_id, symbol}  # run backtest
POST /portfolio/optimize             # run portfolio optimization
```

---

## Data sources and refresh

| Data | Source | Freshness |
|------|--------|-----------|
| Price history | yfinance (default), Alpha Vantage, Polygon | On ingest |
| Latest price | yfinance real-time quote | On refresh / page load |
| Indicators | Computed from price history | On ingest |
| K-Score | Computed from indicators + fundamentals | On rankings refresh |
| AI Signal | Computed from TA + ML, persisted on first view | On stock detail view |
| ML prediction | Trained model inference | On demand |
| News & sentiment | yfinance news API | On stock detail view |

---

## Browser storage keys

| Key | Contents |
|-----|----------|
| `stockai_positions` | Array of position objects `{id, symbol, shares, avgCost, currency, addedAt}` |
| `stockai_trades` | Map of `{symbol: [{type, shares, price, date}]}` |
| `stockai_watch_notes` | Map of `{symbol: noteText}` |
| `stockai_price_alerts` | Map of `{symbol: {target, direction}}` |

All data is local to the browser. Clearing browser storage resets positions, trades, notes, and alerts.

---

## ML models — when to use each

| Model | Best for | Notes |
|-------|---------|-------|
| **XGBoost** | Production — best overall accuracy | Default; fastest to train; handles missing features gracefully |
| **Random Forest** | Sanity checks, stable baseline | Low variance; useful as ensemble member |
| **Gradient Boosting** | High-accuracy longer horizon | Slower training; can overfit noisy data |
| **LSTM** | Capturing sequential momentum patterns | Needs more data; slow to train; best for liquid, trending stocks |

For most users: use XGBoost. The other models are available for comparison and research.

---

## K-Score sub-scores explained

| Sub-score | Range | What drives it |
|-----------|-------|---------------|
| Technical | 0–100 | SMA trend, RSI health, MACD direction, golden/death cross |
| Momentum | 0–100 | 1-week, 1-month, 3-month price rate-of-change |
| Value | 0–100 | P/E, P/B, EV/EBITDA relative to sector; Fair price from DCF-lite |
| Growth | 0–100 | Revenue and earnings growth trajectory (YoY) |
| Volatility | 0–100 | Inverse of 30-day realized volatility (less volatile = higher score) |
| **K-Score** | 0–100 | Weighted composite of all five |

A score above 70 is generally considered strong; below 40 is weak.
The fair price shown on stock detail is derived from the Value sub-score using fundamental data from yfinance.
