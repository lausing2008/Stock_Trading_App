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

## 2. Build & launch

```bash
make build
make up
```

All 10 containers come up (postgres, redis, 8 backend services, frontend). Check:
```bash
make ps
make logs
```

## 3. Seed + ingest initial universe

```bash
# Seed ~20 blue-chip US + HK tickers into the stocks table
curl -X POST http://localhost:8000/admin/seed

# Ingest 3 years of daily bars for the whole seeded universe
curl -X POST http://localhost:8000/admin/ingest \
  -H 'content-type: application/json' \
  -d '{"symbols":["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","0700.HK","0005.HK","9988.HK"]}'
```

Ingestion is incremental and idempotent — re-running only pulls new bars.

## 4. Explore

- **Frontend:** http://localhost:3000
- **API Gateway (OpenAPI):** http://localhost:8000/docs
- **Individual services:** 8001–8007 (see `docker/docker-compose.yml`)

## 5. Train an ML model

```bash
curl -X POST http://localhost:8000/ml/train \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","model":"xgboost"}'

# Check prediction
curl -X POST http://localhost:8000/ml/predict \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","model":"xgboost"}'
```

## 6. Run a backtest

```bash
# Create strategy
curl -X POST http://localhost:8000/strategies \
  -H 'content-type: application/json' \
  -d '{"name":"RSI dip","rule_dsl":{"entry":{"op":"<","left":"rsi_14","right":30},"exit":{"op":">","left":"rsi_14","right":70}}}'
# → {"id": 1, ...}

curl -X POST http://localhost:8000/backtest \
  -H 'content-type: application/json' \
  -d '{"strategy_id":1,"symbol":"AAPL"}'
```

## Tests

```bash
make test   # Runs pytest across all services
```

## Troubleshooting

- **"No price data for X"** — run `/admin/ingest` first.
- **"No trained model at…"** — run `/ml/train` for that symbol.
- **yfinance HTTP 429** — rate-limited; wait and retry (adapters use exponential backoff).
- **Port collision (5432/6379/3000/8000-8007)** — stop the conflicting process or edit `docker/docker-compose.yml`.
