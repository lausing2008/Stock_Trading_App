# StockAI — AI Stock Intelligence Platform

A production-ready, multi-market stock intelligence platform built on a clean
microservices architecture with a Python/FastAPI backend, PyTorch + XGBoost ML
pipeline, and a Next.js frontend.

**Markets:** US (NYSE, NASDAQ) and Hong Kong (HKEX). Plugin architecture lets
you drop in new markets (crypto) or new data vendors without touching
downstream services.

## Features

| Area | Capability |
|------|------------|
| **Data** | yfinance / Alpha Vantage / Polygon adapters, incremental ingestion, Parquet + Postgres sinks, OHLCV validation, cron scheduler |
| **Technical Analysis** | SMA/EMA, RSI, MACD, Bollinger Bands, VWAP, Fibonacci retracement, automated trendlines, support/resistance detection |
| **Pattern Recognition** | Head & Shoulders, Double Top/Bottom, Triangles, Flag/Pennant, Cup & Handle |
| **ML Prediction** | Random Forest, Gradient Boosting, XGBoost, PyTorch LSTM — price direction, short-term trend, volatility forecast |
| **AI Signals** | BUY / SELL / HOLD with horizon (short/swing/long) and 0–100 confidence score fusing TA + ML + volume |
| **K-Score Ranking** | Composite 0–100 score across Technical / Momentum / Value / Growth / Volatility sub-scores |
| **Strategy Engine** | JSON rule DSL (e.g. `RSI<30 and Close>SMA50`), vectorized backtester with Sharpe, max drawdown, CAGR, win-rate, profit factor |
| **Portfolio Optimizer** | Mean-variance, risk parity, AI allocation (K-Score filter + MVO) |
| **Watchlist** | Per-stock notes, price alerts, signal filter tabs (BUY/HOLD/SELL), sort controls, K-Score progress bars |
| **Positions** | Multi-currency P&L tracker, allocation donut chart, trade history, best/worst performer, CSV export |
| **News & Sentiment** | Per-symbol news feed with bullish/bearish/neutral sentiment badges |
| **Frontend** | TradingView-style candlestick + overlay indicators, strategy builder UI, portfolio dashboard, rankings leaderboard |

## Repo structure

```
stock_trading_app/
├── services/
│   ├── market-data/          # Data ingestion + provider adapters
│   ├── technical-analysis/   # Indicators, patterns, trendlines
│   ├── ml-prediction/        # RF / XGB / GBM / LSTM + train_all
│   ├── ranking-engine/       # K-Score composite
│   ├── signal-engine/        # Buy/Sell/Hold + confidence + batch endpoint
│   ├── strategy-engine/      # Rule DSL + backtester
│   ├── portfolio-optimizer/  # MVO / Risk Parity / AI Allocation
│   └── api-gateway/          # Reverse proxy + aggregation
├── frontend/                 # Next.js 14 + lightweight-charts + SWR
│   └── src/
│       ├── pages/            # Dashboard, Rankings, Watchlist, Positions, Stock detail
│       ├── components/       # AddStockModal, SignalCard, NewsCard, PriceChart, DonutChart
│       └── lib/api.ts        # Typed API client
├── shared/                   # Shared ORM models, config, logging
├── infra/terraform/          # VPC, ECS Fargate, ALB, RDS, ElastiCache
├── docker/                   # docker-compose for local dev
└── docs/                     # Architecture, setup, deployment, features
```

## Quickstart (local)

```bash
cp .env.example .env           # edit as needed (API keys optional — yfinance needs no key)
make build                     # build all images
make up                        # start the full stack (10 containers)

# Seed the stock universe (~20 US + HK tickers)
curl -X POST http://localhost:8000/admin/seed

# Ingest historical prices (3 years of daily bars)
curl -X POST http://localhost:8000/admin/ingest \
     -H 'content-type: application/json' \
     -d '{"symbols":["AAPL","MSFT","NVDA","GOOGL","0700.HK","9988.HK"]}'

open http://localhost:3000
```

Or use the **Train All** button on the dashboard to ingest + train all ML models in one click.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2, Pydantic v2 |
| ML / AI | PyTorch, scikit-learn, XGBoost |
| Data | PostgreSQL 16, Redis 7, Parquet (pyarrow) |
| Frontend | Next.js 14, React 18, SWR, lightweight-charts, plotly.js |
| Infra (dev) | Docker Compose |
| Infra (prod) | Terraform + AWS ECS Fargate, RDS, ElastiCache, ALB |
| Observability | structlog JSON logs, `/health` on every service, CloudWatch (prod) |

## Pages

| Page | URL | What it does |
|------|-----|-------------|
| Dashboard | `/` | All stocks grid with live BUY/SELL/HOLD badges, K-Score, price change |
| Stock Detail | `/stock/[symbol]` | Candlestick chart, AI signal, K-Score breakdown, ML prediction panel, news feed |
| Rankings | `/rankings` | Leaderboard sorted by K-Score with sub-score breakdown |
| Watchlist | `/watchlist` | Curated list with notes, price alerts, signal filter, sort controls |
| Positions | `/positions` | Portfolio P&L tracker with allocation chart, trade history, CSV export |
| Strategies | `/strategies` | Rule DSL strategy builder + backtester |

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design, data model, service contracts
- [`docs/SETUP.md`](docs/SETUP.md) — local dev setup step-by-step
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — AWS ECS Fargate deployment
- [`docs/FEATURES.md`](docs/FEATURES.md) — full feature reference for every page and service

## License

MIT
