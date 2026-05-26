# Professional Trading Features

Added in May 2026. These features bring the app to the level of premium platforms like Finviz, TrendSpider, Koyfin, Benzinga Pro, and QuiverQuant.

---

## New Pages

### Sector Heat Map (`/heatmap`)

Finviz-style sector performance visualization. Color-coded tiles show average intraday change per sector — green intensity = magnitude of gains, red = losses. Click any tile to expand a table of individual stocks in that sector sorted by day change.

- Refreshes live prices every 60 seconds
- Filter by US / HK market
- Shows stock count per sector
- Summary bar counts sectors up vs. down

**Backend:** `GET /stocks/sector_performance` — groups all active stocks by sector, fetches prices from Redis live-price cache.

---

### Earnings Calendar (`/earnings`)

Koyfin-style upcoming earnings calendar. Shows every tracked stock with a cached `next_earnings_date` within the selected window (14 / 30 / 45 / 90 days).

Each card displays:
- Days until report (color-coded: red ≤ 3d, orange ≤ 7d, yellow ≤ 14d)
- EPS estimate vs. trailing EPS
- Revenue growth and earnings growth YoY
- Market cap

**Sorting:** soonest first, by market cap, or by earnings growth.

**Data note:** Only stocks whose fundamentals have been cached (i.e., visited on the stock detail page) will appear. The cache TTL for fundamentals is 24 hours.

**Backend:** `GET /stocks/earnings_calendar?days_ahead=45`

---

### Analyst Ratings Feed (`/analyst`)

Benzinga Pro-style feed of Wall Street upgrades, downgrades, and new coverage initiations. Data is sourced from Yahoo Finance's `upgrades_downgrades` table, aggregated across all tracked stocks.

Each row shows:
- Date (relative: "Today", "Yesterday", "3d ago")
- Action type with color coding (▲ Upgrade = green, ▼ Downgrade = red, ◆ Initiated = indigo)
- Symbol and firm
- Grade change (e.g., Hold → Buy) with color coding by bullishness
- Current analyst consensus and price target

**Filters:** date range (7 / 14 / 30 / 90 days), market, action type, text search.

**Summary chips** at top show total upgrade / downgrade / initiation counts — click to filter.

**Data note:** Same cache dependency as earnings — stock must have been viewed to appear.

**Backend:** `GET /stocks/analyst_ratings?days=30`

---

### Short Squeeze Scanner (`/short-squeeze`)

Identifies stocks with high short interest that are showing bullish momentum — the classic setup for a short squeeze. Modeled after Finviz's Short Float scanner.

Columns:
- **Short %** — percentage of float sold short (color-coded: yellow ≥ 15%, orange ≥ 25%, red ≥ 40%)
- **Days to Cover** — short interest ratio (how many average-volume days to cover all shorts)
- **Shares Short** — absolute number of shares short
- **Momentum score** — from the K-Score model (higher = more bullish trend)
- **K-Score** — overall composite score

🔥 Prime candidates: stocks with ≥ 15% short float AND momentum score > 50 are flagged and shown in a banner.

**Filters:** minimum short %, market, text search, sortable columns.

**Backend:** `GET /stocks/short_squeeze?min_short_float=10`

---

## Stock Detail Page Enhancements (`/stock/[symbol]`)

### Dividends Section

Collapsible panel (click "Dividends" header to expand). Loads on-demand via `GET /stocks/{symbol}/dividends`.

Shows:
- Annual dividend rate (sum of last 12 months' payments)
- Dividend yield
- Payout ratio
- Ex-dividend date
- Full dividend payment history (last 40 payments)

**Cache:** 72-hour Redis cache per symbol.

### Institutional Holdings Section

Collapsible panel (click "Institutional Holdings" header to expand). Loads on-demand via `GET /stocks/{symbol}/institutional`.

Shows:
- % held by institutions
- % held by insiders
- Float shares and shares outstanding
- Top 20 institutional holders (name, shares, % of outstanding, market value, report date)

Data sourced from Yahoo Finance / SEC 13F filings. **Cache:** 72-hour Redis cache per symbol.

---

## Screener Enhancements (`/screener`)

Three new filter controls added to the existing filter panel:

| Filter | Description |
|--------|-------------|
| **Price range** | Min/max price filter. Works with live price data. |
| **Sector** | Dropdown of all sectors present in the rankings dataset. |
| **Min Underval %** | Filters stocks where `(fair_price - price) / fair_price ≥ threshold`. A value of 10 means the stock trades at least 10% below its K-Score fair value estimate. Stocks without a fair value estimate are excluded. |

These filters stack with all existing filters (K-Score, signal, momentum, etc.).

---

## Watchlist Compare View (`/watchlist`)

A new **Compare** button appears next to the sort controls. Clicking it switches to a relative performance chart mode.

- Select up to 8 symbols from the current watchlist
- Chart shows base-100 normalized daily close prices (base = first day of the selected period)
- Time range selector: 30 / 60 / 90 / 180 / 365 days
- Each symbol gets its own colored line with a return label at the endpoint
- Legend below the chart shows symbol, line color, and total return % for the period
- Returns to list view by clicking "List"

The chart is a pure SVG rendering — no extra dependencies. Data comes from the database (daily close prices already ingested).

**Backend:** `GET /stocks/relative_performance?symbols=AAPL,MSFT&days=90`

---

## Data Availability Notes

Several features depend on fundamentals being cached per-stock:

- **Earnings Calendar** — requires `next_earnings_date` in fundamentals cache
- **Analyst Ratings Feed** — requires `analyst_actions` in fundamentals cache
- **Short Squeeze Scanner** — requires `short_percent_of_float` in fundamentals cache
- **Dividends / Institutional tabs** — fetched live from Yahoo Finance on demand (not pre-cached)

Fundamentals are cached automatically when you visit a stock's detail page. To pre-populate all stocks, an admin can trigger a full refresh from the Dashboard.

---

## Backend Endpoints Added

All new endpoints are in `services/market-data/src/api/routes.py` (prefixed `/stocks`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/stocks/sector_performance` | Sector-grouped performance from live prices |
| GET | `/stocks/earnings_calendar` | Upcoming earnings from fundamentals cache |
| GET | `/stocks/analyst_ratings` | Recent analyst actions from fundamentals cache |
| GET | `/stocks/short_squeeze` | High-short-interest stocks with momentum |
| GET | `/stocks/relative_performance` | Base-100 normalized multi-symbol price series |
| GET | `/stocks/{symbol}/dividends` | Dividend history (72h Redis cache) |
| GET | `/stocks/{symbol}/institutional` | Institutional holders (72h Redis cache) |

Fundamentals model extended with 5 new fields:
- `short_percent_of_float` — short interest as % of float
- `short_ratio` — days to cover
- `shares_short` — absolute short shares
- `held_percent_institutions` — % held by institutions
- `held_percent_insiders` — % held by insiders

---

## Enhancements and Fixes (May 2026)

### Grouped Dropdown Navigation

Replaced 17 flat nav links with 4 grouped dropdown menus (Markets / Research / Portfolio / Tools). Opens on hover with a 120 ms close delay. Active group is underlined; active page shows a purple dot inside the dropdown.

### Analyst Ratings — Action Short-Code Fix

yfinance stores action codes as `"up"`, `"down"`, `"reit"`, `"main"`, `"init"` (not the full words). The frontend `actionKey()` function was updated to match short codes first, then fall back to substring matching. This fixed upgrade/downgrade counts showing 0.

### Screener — Signal Column Header Fix

The Signal column header was incorrectly using a sortable `<Th col="symbol">` component (duplicating the Symbol sort). Replaced with a plain non-sortable `<th>`, matching the Market column style.

### Watchlist — Bulk Signal Alert Controls

Added two ways to manage AI signal email alerts directly from the watchlist:

1. **📡 per-card toggle** — purple when on, grey when off. Click to subscribe or unsubscribe that single stock without leaving the page.
2. **Notify All / Mute All buttons** — in the watchlist tab bar. "Notify All (N)" subscribes all N unsubscribed stocks in one click. "Mute All" removes subscriptions for the whole list. Uses the email address from Settings → Profile.

### Forecast — 504 Timeout Fix

Added `location /api/ai/` block to nginx with 180 s `proxy_read_timeout`, taking precedence over the 30 s catch-all that was killing long-running Claude API calls. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#10-forecast--ai-endpoints-return-504) for details.

### Trade Board (`/board`)

New persistent Kanban board for managing trade ideas across four stages: **Watch → Planning → Active → Closed**.

**Backend:**
- New `trade_plans` DB table (`TradePlan` SQLAlchemy model) with columns: symbol, stage, game_plan (JSON), entry_price, stop_loss, take_profit, notes, source, timestamps
- New CRUD router at `/board` in `services/market-data/src/api/board.py` — list, create, update, delete; all endpoints require JWT auth
- `StoredGamePlan` typed interface in `board.tsx` to safely render JSON game plan blobs in TypeScript strict mode

**Frontend:**
- Kanban layout with one column per stage; cards sorted by last-updated within each column
- Per-card: R:R ratio auto-calculated, expandable full game plan, inline stage selector, delete with confirm
- **📌 Save to Board** button on the stock detail game plan card (stage = Planning, prices pre-filled)
- **📌 Save to Board** button on each Forecast pick card (stage = Watch, notes from setup/catalyst/risk)
- Manual add form in the Watch column for any symbol
- SWR `globalMutate` invalidates board cache when a card is saved from another page

### Stock Detail — Game Plan Signal Restriction Removed

The **📋 Generate 10-Day Game Plan** button previously only appeared for stocks with a BUY or HOLD signal. Removed that condition so the button shows for all stocks regardless of signal — allows planning shorts, researching SELL-signal stocks, or generating a plan at any time.
