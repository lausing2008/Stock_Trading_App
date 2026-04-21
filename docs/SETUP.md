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
| market-data | 8001 | Price data, ingestion |
| technical-analysis | 8002 | Indicators, patterns |
| ml-prediction | 8003 | ML models |
| ranking-engine | 8004 | K-Score leaderboard |
| signal-engine | 8005 | BUY/SELL/HOLD signals |
| strategy-engine | 8006 | Strategy DSL + backtester |
| portfolio-optimizer | 8007 | MVO / optimization |

## 3. Seed and ingest data

```bash
# Seed ~20 blue-chip US + HK tickers into the stocks table
curl -X POST http://localhost:8000/admin/seed

# Ingest 3 years of daily bars (incremental — safe to re-run)
curl -X POST http://localhost:8000/admin/ingest \
  -H 'content-type: application/json' \
  -d '{"symbols":["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","0700.HK","0005.HK","9988.HK"]}'
```

Or open http://localhost:3000 and click **Train All** on the dashboard — it runs ingest + ML training for every stock in one click.

## 4. Explore the frontend

Open http://localhost:3000. Six pages are available:

- **Dashboard** — stock grid with BUY/SELL/HOLD badges and K-Scores
- **Rankings** — leaderboard sorted by K-Score
- **Watchlist** — curated watchlist with notes and price alerts
- **Positions** — portfolio P&L tracker with allocation chart
- **Stock Detail** — click any stock card; shows chart, signals, ML predictions, news
- **Strategies** — build and backtest rule-based strategies

## 5. Train ML models

**Option A — from the UI:** Click **Train All** on the dashboard or any stock detail page.

**Option B — via API:**
```bash
# Train one stock
curl -X POST http://localhost:8000/ml/train \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","model":"xgboost"}'

# Train all active stocks (background tasks)
curl -X POST http://localhost:8000/ml/train_all

# Get a prediction
curl -X POST http://localhost:8000/ml/predict \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","model":"xgboost"}'
```

Available models: `xgboost` (default), `random_forest`, `gradient_boosting`, `lstm`.
Training takes ~30 seconds per symbol. After training, run predict to get a direction + confidence.

## 6. Generate signals

Signals are generated and persisted automatically when you view a stock detail page.
To generate them in bulk via the API:

```bash
# Single symbol — saves to DB
curl http://localhost:8000/signals/AAPL?persist=true

# All active stocks — latest persisted signal per stock
curl http://localhost:8000/signals
```

## 7. Run a backtest

```bash
# Create a strategy using the rule DSL
curl -X POST http://localhost:8000/strategies \
  -H 'content-type: application/json' \
  -d '{"name":"RSI dip","rule_dsl":{"entry":{"op":"<","left":"rsi_14","right":30},"exit":{"op":">","left":"rsi_14","right":70}}}'
# → {"id": 1, ...}

# Run backtest
curl -X POST http://localhost:8000/backtest \
  -H 'content-type: application/json' \
  -d '{"strategy_id":1,"symbol":"AAPL"}'
```

## 8. Use the Watchlist

- Click **☆ Watch** on any stock detail page or add from the dashboard
- On the Watchlist page: filter by BUY/HOLD/SELL, sort by K-Score or price change
- Click 📝 to add private notes (stored in your browser)
- Click 🔔 to set a price alert — a yellow banner appears when the price crosses your target
- Click **+ POS** to add to your Positions portfolio

## 9. Track Positions

- Navigate to `/positions` or click **+ POS** from the watchlist
- Add positions with symbol, number of shares, average cost, and currency
- Currencies supported: USD, HKD, CAD, GBP, EUR, AUD
- View live P&L, today's change, allocation donut chart
- Click BUY/SELL buttons to log trades and update your average cost
- Sort by symbol, value, P&L$, P&L%, today's change, or K-Score
- Click **Export CSV** to download a spreadsheet of your portfolio

## 10. Tests

```bash
make test   # runs pytest across all services
```

## Rebuilding after code changes

```bash
# Rebuild a single service (e.g. frontend)
docker compose -f docker/docker-compose.yml build frontend
docker compose -f docker/docker-compose.yml up -d --force-recreate frontend

# Rebuild all
make build && make up
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "No price data for X" | Run `POST /admin/ingest` with that symbol |
| "Model not trained yet" | Click Train on the stock detail page or run `POST /ml/train` |
| Signal shows K-Score fallback (not real TA) | View the stock detail page once — this persists the TA signal to DB |
| yfinance HTTP 429 (rate limit) | Wait and retry; adapters use exponential backoff automatically |
| Port collision on 5432/6379/3000/8000-8007 | Stop the conflicting process or edit `docker/docker-compose.yml` |
| Positions/notes disappeared | These are stored in browser localStorage; clearing browser data removes them |
