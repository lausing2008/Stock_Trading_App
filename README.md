# AI Stock Intelligence Platform

A production-ready, multi-market stock intelligence platform — comparable in
scope to TradingView, TrendSpider, Tickeron and Kavout — built on a clean
microservices architecture with a Python/FastAPI backend, PyTorch + XGBoost
ML pipeline, and a Next.js TradingView-style frontend.

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
| **K-Score Ranking** | Composite 0–100 score across Technical / Momentum / Value / Growth / Volatility |
| **Strategy Engine** | JSON rule DSL (e.g. `RSI<30 and Close>SMA50`), vectorized backtester with Sharpe, max drawdown, CAGR, win-rate, profit factor |
| **Portfolio Optimizer** | Mean-variance, risk parity, AI allocation (K-Score filter + MVO) |
| **Frontend** | TradingView-style candlestick + overlay indicators, strategy builder UI, portfolio dashboard, rankings leaderboard |

## Repo structure

```
stock_trading_app/
├── services/
│   ├── market-data/          # Data ingestion + provider adapters
│   ├── technical-analysis/   # Indicators, patterns, trendlines
│   ├── ml-prediction/        # RF / XGB / GBM / LSTM
│   ├── ranking-engine/       # K-Score composite
│   ├── signal-engine/        # Buy/Sell/Hold + confidence
│   ├── strategy-engine/      # Rule DSL + backtester
│   ├── portfolio-optimizer/  # MVO / Risk Parity / AI Allocation
│   └── api-gateway/          # Reverse proxy + aggregation
├── frontend/                 # Next.js + lightweight-charts
├── shared/                   # Shared ORM models, config, logging
├── infra/terraform/          # VPC, ECS Fargate, ALB, RDS, ElastiCache
├── docker/                   # docker-compose for local dev
└── docs/                     # Architecture, setup, deployment
```

## Quickstart (local)

```bash
cp .env.example .env           # edit as needed (API keys optional for yfinance)
make build                     # build all images
make up                        # start the full stack (8 backend svcs + FE + PG + Redis)
curl http://localhost:8000/admin/seed -X POST         # seed universe
curl http://localhost:8000/admin/ingest -X POST \
     -H 'content-type: application/json' \
     -d '{"symbols":["AAPL","MSFT","NVDA","0700.HK"]}'
open http://localhost:3000
```

## Tech stack

- **Backend:** Python 3.11, FastAPI, SQLAlchemy 2, Pydantic v2
- **ML/AI:** PyTorch, scikit-learn, XGBoost
- **Data:** PostgreSQL 16, Redis 7, Parquet (pyarrow)
- **Frontend:** Next.js 14, React 18, lightweight-charts, SWR
- **Infra:** Docker Compose (dev), Terraform + AWS ECS Fargate (prod)
- **Observability:** structlog JSON logs, CloudWatch (prod)

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design, data model, service contracts
- [`docs/SETUP.md`](docs/SETUP.md) — local dev setup
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — AWS ECS Fargate deployment

## License

MIT
