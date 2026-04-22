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
| market-data | 8001 | Live prices, ingestion, price history |
| technical-analysis | 8002 | Indicators, patterns |
| ml-prediction | 8003 | ML models |
| ranking-engine | 8004 | K-Score leaderboard |
| signal-engine | 8005 | BUY/SELL/HOLD signals |
| strategy-engine | 8006 | Strategy DSL + backtester |
| portfolio-optimizer | 8007 | MVO / optimization |

## 3. Login

Open http://localhost:3000. You will be redirected to the login page.

**Default account:**
- Username: `lausing`
- Password: `120402`

To reset your password, go to the Login page and click the **Reset Password** tab.

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

Dashboard, Watchlist, and Positions pages all show **real-time prices** fetched from yfinance on every load. Prices auto-refresh in the background every 60 seconds — no manual action needed.

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

## 7. Generate signals

Signals are computed and persisted automatically when you view a stock detail page.
To generate in bulk via API:

```bash
# Single symbol — saves to DB
curl http://localhost:8000/signals/AAPL?persist=true

# Latest signal for all active stocks
curl http://localhost:8000/signals
```

## 8. Run a backtest

```bash
# Create a strategy
curl -X POST http://localhost:8000/strategies \
  -H 'content-type: application/json' \
  -d '{"name":"RSI dip","rule_dsl":{"entry":{"op":"<","left":"rsi_14","right":30},"exit":{"op":">","left":"rsi_14","right":70}}}'
# → {"id": 1, ...}

# Run backtest
curl -X POST http://localhost:8000/backtest \
  -H 'content-type: application/json' \
  -d '{"strategy_id":1,"symbol":"AAPL"}'
```

## 9. Use the Watchlist

- Click **☆ Watch** on any stock detail page, or add from the dashboard
- On the Watchlist page: filter by BUY/HOLD/SELL, sort by K-Score or price change
- Click 📝 to add private notes (stored in browser localStorage)
- Click 🔔 to set a price alert — yellow banner appears when price crosses target
- Click **+ POS** to add to your Positions portfolio

## 10. Track Positions

- Navigate to `/positions` or click **+ POS** from the watchlist
- Add positions: symbol, shares, average cost, currency (USD/HKD/CAD/GBP/EUR/AUD)
- View live P&L, today's change, allocation donut chart, best/worst performer
- Click BUY/SELL to log trades and update average cost
- Click **Export CSV** to download your portfolio as a spreadsheet

## 11. Tests

```bash
make test   # runs pytest across all services
```

## 12. Rebuilding after code changes

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
| Can't log in | Default credentials: `lausing` / `120402`. Use Reset Password tab to change. |
| Positions/notes disappeared | Stored in browser localStorage — clearing browser data removes them |
