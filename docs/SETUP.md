# Setup Guide

## Prerequisites

- Docker Desktop ≥ 24 (with Compose v2)
- GNU Make
- (Optional) Python 3.11 + Node 20 for running services outside Docker

## 1. Clone and configure

```bash
git clone <repo-url> stock_trading_app
cd stock_trading_app
cp .env.example .env
```

Edit `.env`:
- `POSTGRES_PASSWORD` — pick any value for local dev
- `ALPHA_VANTAGE_API_KEY` / `POLYGON_API_KEY` — optional; yfinance is the default provider and needs no key

> **Note:** AI provider API keys (Claude, DeepSeek) are configured in the app's Settings page and stored in your browser — not in `.env`.

## 2. Build and launch

```bash
make build   # builds all 10 Docker images
make up      # starts postgres, redis, 8 backend services, frontend
```

Check everything is running:
```bash
make ps      # container status
make logs    # tail logs
```

Services and ports:

| Container | Port | What it serves |
|-----------|------|----------------|
| frontend | 3000 | Next.js app |
| api-gateway | 8000 | Main entry point (OpenAPI at /docs) |
| market-data | 8001 | Live prices, ingestion, price history, news |
| technical-analysis | 8002 | Indicators, patterns |
| ml-prediction | 8003 | ML models |
| ranking-engine | 8004 | K-Score leaderboard |
| signal-engine | 8005 | BUY/SELL/HOLD signals |
| strategy-engine | 8006 | Strategy DSL + backtester |
| portfolio-optimizer | 8007 | MVO / HRP / optimization |

## 3. Login

Open http://localhost:3000. You will be redirected to the login page.

**Default admin account:**
- Username: `lausing`
- Password: `120402`

The admin account is created automatically on first boot. To reset your password, go to the Login page and click the **Reset Password** tab, or use **Settings → Change Password** while logged in.

**Adding more users:** Log in as `lausing`, go to **Settings → User Management**, and use the Create User form. You can also reset other users' passwords and toggle accounts from that panel.

## 4. Seed and ingest data

```bash
# Seed ~20 blue-chip US + HK tickers into the stocks table
curl -X POST http://localhost:8000/admin/seed

# Ingest 3 years of daily bars (runs in parallel — ~5-10 s for 20 symbols)
curl -X POST http://localhost:8000/admin/ingest \
  -H 'content-type: application/json' \
  -d '{"symbols":["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","0700.HK","0005.HK","9988.HK"]}'
```

Or open the dashboard and click **⚡ Train All** — it runs ingest + ML training for every stock in one click.

## 5. Live prices

Dashboard, Watchlist, Positions, and **Stock Detail** pages all show **real-time prices** fetched from yfinance on every load. Prices auto-refresh in the background every 60 seconds — no manual action needed.

The stock detail page displays a **Live Price card** in the header showing the real-time price, day change %, and previous close. It fetches from the shared `/stocks/latest_prices` endpoint (same Redis 60 s cache used by the dashboard).

The first load after a cache expiry takes ~3–5 seconds (parallel yfinance fetches). Subsequent loads within 60 seconds are instant (Redis cache).

If yfinance is unavailable, the UI falls back to the last ingested close price from Postgres.

## 6. Train ML models

**Option A — from the UI:** Click **⚡ Train All** on the dashboard or any stock detail page.

The Train All button:
1. Ingests latest prices for all active stocks (synchronous, parallel — waits until done)
2. Refreshes the dashboard price cards immediately with new data
3. Schedules XGBoost training for every stock in the background
4. Models are ready in ~2–5 minutes

**Option B — via API:**
```bash
# Train one stock
curl -X POST http://localhost:8000/ml/train \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","model":"xgboost"}'

# Train all active stocks
curl -X POST http://localhost:8000/ml/train_all

# Get a prediction
curl -X POST http://localhost:8000/ml/predict \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","model":"xgboost"}'
```

Available models: `xgboost` (default), `random_forest`, `gradient_boosting`, `lstm`.

## 7. Configure AI Chat (optional)

To enable the AI chat panel on stock detail pages:

1. Open http://localhost:3000/settings
2. Scroll to **AI Assistant**
3. Select your provider: **Claude (Anthropic)** or **DeepSeek**
4. Paste your API key:
   - Claude: get a key at [console.anthropic.com](https://console.anthropic.com)
   - DeepSeek: get a key at [platform.deepseek.com](https://platform.deepseek.com)
5. Choose a model (Claude Sonnet 4.6 or DeepSeek Chat recommended)
6. Click **Test Connection** to verify, then **Save Settings**

Once configured, every stock detail page will show an "Ask AI" panel at the bottom. The AI receives current price, signal, K-Score, and recent news as context automatically.

API keys are stored in your browser's localStorage only — they are never saved on the server.

## 8. Configure data sources (optional)

By default, StockAI uses **yfinance** (free, no API key needed) for prices and **both Yahoo Finance News + Google News RSS** for news.

To change these:

1. Open http://localhost:3000/settings
2. **Stock Price Data Sources** — toggle Alpha Vantage or Polygon.io on and enter your API key
3. **News Sources** — toggle yfinance news and/or Google News RSS on/off

Source changes take effect on the next data fetch. News source changes immediately invalidate the Redis cache (different cache key per source combination).

## 9. Set up Alerts

1. Open http://localhost:3000/alerts (or click **Alerts** in the nav)
2. Select a stock, choose a condition (price, % change, signal, K-Score), set threshold and cooldown
3. Click **+ Add Alert**

Alerts are checked every 60 seconds in the background. When triggered:
- A notification appears in the **🔔 bell** in the top-right nav
- A sound plays (configurable in Settings → Notifications)
- The alert goes into Notification History on the Alerts page

## 10. Generate signals

Signals are computed and persisted automatically when you view a stock detail page.
To generate in bulk via API:

```bash
# Single symbol — saves to DB
curl http://localhost:8000/signals/AAPL?persist=true

# Latest signal for all active stocks
curl http://localhost:8000/signals
```

## 11. Run the Portfolio Optimizer

Navigate to http://localhost:3000/portfolio.

- Enter comma-separated ticker symbols
- Choose a method:
  - **Max Sharpe (MVO)** — Sharpe-maximizing with Ledoit-Wolf covariance
  - **Risk Parity** — equal risk contribution
  - **Hierarchical Risk Parity** — cluster-based, most robust
  - **AI Allocation** — K-Score filtered + return views + Sharpe max
- Select lookback period (6m / 1y / 2y / 3y)
- Click **Optimize Portfolio**

Results show allocation bars, Sharpe ratio, expected return/volatility, max drawdown, and a diversification score.

## 12. Run a backtest

Strategies and backtests require a JWT. Get a token first:

```bash
# Login and capture the token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"lausing","password":"120402"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create a strategy
curl -X POST http://localhost:8000/strategies \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"name":"RSI dip","rule_dsl":{"entry":{"op":"<","left":"rsi_14","right":30},"exit":{"op":">","left":"rsi_14","right":70}}}'
# → {"id": 1, ...}

# Run backtest
curl -X POST http://localhost:8000/backtest \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"strategy_id":1,"symbol":"AAPL"}'
```

## 13. Tests

```bash
make test   # runs pytest across all services
```

## 14. Rebuilding after code changes

```bash
# Rebuild a single service
docker compose -f docker/docker-compose.yml build <service-name>
docker compose -f docker/docker-compose.yml up -d --force-recreate <service-name>

# Rebuild all
make build && make up
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "No price data for X" | Run `POST /admin/ingest` with that symbol first |
| "Model not trained yet" | Click Train on the stock detail page, or run `POST /ml/train` |
| Signal shows K-Score fallback (not TA) | View the stock detail page once — this persists the TA signal to DB |
| Dashboard prices look stale | Click ↻ Refresh — prices update every 60 s automatically otherwise |
| yfinance HTTP 429 (rate limit) | Wait and retry; adapters use exponential backoff |
| Port collision on 5432/6379/3000/8000-8007 | Stop the conflicting process or edit `docker/docker-compose.yml` |
| Can't log in | Default credentials: `lausing` / `120402`. Use Reset Password tab or Settings → Change Password to change. |
| Positions/notes disappeared | Stored in namespaced browser localStorage (`stockai:{username}:key`) — clearing browser data removes them |
| Logged in as wrong user, data missing | Each user has isolated positions, notes, alerts, and settings. Switch users from the nav bar logout button. |
| Stock detail shows "Last Close" instead of "Live Price" | yfinance quota hit or network issue — price shown is from DB; auto-recovers within 60 s |
| Market overview shows "—" prices | yfinance rate-limited; auto-recovers in 60 s |
| Opportunities page shows 0 stocks | Run Train All to compute signals + rankings first |
| AI chat / Test Connection returns 404 | Restart the frontend container — env var baked at build time |
| AI chat shows "No AI provider" | Go to Settings → AI Assistant and configure Claude or DeepSeek |
| AI chat shows API error 401 | Your API key is invalid or expired — check it in Settings |
| News not updating after toggling sources | Hard-refresh the browser (Ctrl+Shift+R) to clear SWR cache |
| Portfolio optimizer returns 400 | One or more symbols have insufficient price history — try a shorter lookback or run ingest first |
