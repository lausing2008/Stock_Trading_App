# StockAI — AI Stock Intelligence Platform

A production-ready, multi-market stock intelligence platform built on a clean
microservices architecture with a Python/FastAPI backend, PyTorch + XGBoost ML
pipeline, Next.js frontend, and integrated AI chat (Claude / DeepSeek).

**Markets:** US (NYSE, NASDAQ) and Hong Kong (HKEX). Plugin architecture lets
you drop in new markets (crypto) or new data vendors without touching
downstream services.

## Features

| Area | Capability |
|------|------------|
| **Data Sources** | yfinance (default, free), Alpha Vantage, Polygon.io — toggle per source in Settings with API key management |
| **Live Prices** | Real-time quotes via yfinance `fast_info`, Redis-cached (60 s TTL), auto-refreshes every 60 s in UI |
| **News Sources** | Yahoo Finance News + Google News RSS — each toggleable in Settings; essential for HK stocks |
| **Technical Analysis** | SMA/EMA, RSI, MACD, Bollinger Bands, VWAP, Fibonacci retracement, automated trendlines, support/resistance |
| **Pattern Recognition** | Head & Shoulders, Double Top/Bottom, Triangles, Flag/Pennant, Cup & Handle |
| **ML Prediction** | Random Forest, Gradient Boosting, XGBoost, PyTorch LSTM — price direction, confidence score |
| **AI Signals** | BUY / SELL / HOLD with horizon (short/swing/long) and 0–100 confidence fusing TA + ML + volume |
| **K-Score Ranking** | Composite 0–100 score across Technical / Momentum / Value / Growth / Volatility |
| **Strategy Engine** | JSON rule DSL (e.g. `RSI<30 and Close>SMA50`), vectorized backtester with Sharpe, max drawdown, CAGR |
| **Portfolio Optimizer** | Sharpe-maximizing MVO, Risk Parity, Hierarchical Risk Parity (HRP), AI Allocation — all with Ledoit-Wolf covariance shrinkage |
| **Alerts** | Rule-based alerts on price, % change, signal, or K-Score — with cooldown, notification history, and sound |
| **AI Chat** | Ask Claude or DeepSeek about any stock on the detail page — context-aware (price, signal, news, K-Score) |
| **Watchlist** | Per-stock notes, price alerts, signal filter tabs, sort controls, K-Score progress bars |
| **Positions** | Multi-currency P&L tracker, allocation donut chart, trade history, best/worst performer, CSV export |
| **Company Financials** | Valuation, income statement, balance sheet, margins, returns, 52-week range, analyst consensus |
| **Settings** | Data source toggles, AI provider config, news source toggles, notification preferences, ML defaults |
| **Auth** | Login page with session management; password reset; pre-created account (`lausing`) |

## Repo structure

```
stock_trading_app/
├── services/
│   ├── market-data/          # Data ingestion + live price quotes + provider adapters
│   ├── technical-analysis/   # Indicators, patterns, trendlines
│   ├── ml-prediction/        # RF / XGB / GBM / LSTM + train_all
│   ├── ranking-engine/       # K-Score composite
│   ├── signal-engine/        # Buy/Sell/Hold + confidence + batch endpoint
│   ├── strategy-engine/      # Rule DSL + backtester
│   ├── portfolio-optimizer/  # Sharpe MVO / Risk Parity / HRP / AI Allocation
│   └── api-gateway/          # Reverse proxy + aggregation + AI proxy
├── frontend/                 # Next.js 14 + lightweight-charts + SWR
│   └── src/
│       ├── pages/            # Login, Dashboard, Rankings, Watchlist, Positions,
│       │                     # Stock Detail, Strategies, Portfolio, Alerts, Settings
│       ├── components/       # AddStockModal, SignalCard, NewsCard, PriceChart,
│       │                     # DonutChart, NotificationBell
│       └── lib/              # api.ts, auth.ts, alerts.ts, settings.ts, ai.ts
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

# Ingest historical prices (runs in parallel, ~5-10 s for 20 symbols)
curl -X POST http://localhost:8000/admin/ingest \
     -H 'content-type: application/json' \
     -d '{"symbols":["AAPL","MSFT","NVDA","GOOGL","0700.HK","9988.HK"]}'

open http://localhost:3000
# Login: username lausing / password 120402
```

Or use the **⚡ Train All** button on the dashboard to ingest + train all ML models in one click.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2, Pydantic v2 |
| ML / AI | PyTorch, scikit-learn, XGBoost |
| Optimization | scipy (SLSQP), scikit-learn (Ledoit-Wolf), Ward clustering |
| Data | PostgreSQL 16, Redis 7 (live price cache + news cache), Parquet (pyarrow) |
| Frontend | Next.js 14, React 18, SWR, lightweight-charts, plotly.js |
| AI Chat | Anthropic Claude API + DeepSeek API (proxied via api-gateway, keys in browser) |
| Auth | localStorage session, password stored per-browser |
| Infra (dev) | Docker Compose |
| Infra (prod) | Terraform + AWS ECS Fargate, RDS, ElastiCache, ALB |
| Observability | structlog JSON logs, `/health` on every service, CloudWatch (prod) |

## Pages

| Page | URL | What it does |
|------|-----|-------------|
| Login | `/login` | Sign-in gate; password reset tab |
| Dashboard | `/` | All stocks grid with live BUY/SELL/HOLD badges, K-Score, real-time prices |
| Stock Detail | `/stock/[symbol]` | Candlestick chart, AI signal, K-Score, ML prediction, financials, AI chat, news |
| Rankings | `/rankings` | Leaderboard sorted by K-Score with sub-score breakdown |
| Watchlist | `/watchlist` | Curated list with notes, price alerts, signal filter, sort controls |
| Positions | `/positions` | Portfolio P&L tracker with allocation chart, trade history, CSV export |
| Strategies | `/strategies` | Rule DSL strategy builder + backtester |
| Portfolio | `/portfolio` | Quantitative portfolio optimizer (MVO / Risk Parity / HRP / AI) |
| Alerts | `/alerts` | Create and manage stock alerts; notification history |
| Settings | `/settings` | Data sources, news sources, AI provider, notifications, ML defaults |

## Default account

| Field | Value |
|-------|-------|
| Username | `lausing` |
| Password | `120402` |

Password can be reset on the Login page → **Reset Password** tab.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system design, data model, service contracts
- [`docs/SETUP.md`](docs/SETUP.md) — local dev setup step-by-step
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — AWS ECS Fargate deployment
- [`docs/FEATURES.md`](docs/FEATURES.md) — full feature reference for every page and service

## License

MIT
