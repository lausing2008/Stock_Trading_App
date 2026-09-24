# StockAI — Project Structure

## Repository Layout

```
Stock_Trading_App/
├── services/                    # Backend microservices (Python/FastAPI)
│   ├── api-gateway/             # Reverse proxy + aggregation + AI proxy (port 8000)
│   ├── market-data/             # Data ingestion + live quotes + provider adapters (port 8001)
│   ├── technical-analysis/      # Indicators, patterns, trendlines (port 8002)
│   ├── ml-prediction/           # RF/XGB/GBM/LSTM models + training (port 8003)
│   ├── ranking-engine/          # K-Score composite scoring (port 8004)
│   ├── signal-engine/           # Buy/Sell/Hold signals + confidence (port 8005)
│   ├── strategy-engine/         # Rule DSL + backtester (port 8006)
│   ├── portfolio-optimizer/     # MVO/Risk Parity/HRP/AI allocation (port 8007)
│   ├── research-engine/         # AI research report generation (port 8008)
│   ├── decision-engine/         # Trade decision logic + hard rejects
│   ├── event-intelligence/      # Institutional flow + event detection
│   └── news-intelligence/       # News aggregation + sentiment
├── frontend/                    # Next.js 14 web application
│   └── src/
│       ├── pages/               # Next.js pages (dashboard, stock detail, etc.)
│       ├── components/          # React components (charts, cards, modals)
│       ├── lib/                 # API client, auth, utilities
│       └── styles/              # CSS styles
├── shared/                      # Shared Python library
│   ├── common/                  # Config, logging, JWT auth, Redis client
│   └── db/                      # SQLAlchemy ORM models, session management
├── docker/                      # Docker Compose for local development
├── infra/terraform/             # AWS infrastructure (ECS Fargate, RDS, ElastiCache)
├── scripts/                     # DB migrations, backup, bootstrap scripts
├── docs/                        # Architecture, setup, deployment, features docs
└── Improvements/                # Feature design documents
```

## Service Architecture

Each microservice follows the same structure:
```
services/<service-name>/
├── src/
│   ├── api/
│   │   └── routes.py            # FastAPI route handlers
│   ├── services/                # Business logic
│   └── main.py                  # FastAPI app entry point
├── tests/                       # pytest unit tests
├── Dockerfile
├── requirements.txt
├── agent.md                     # AI agent context
└── skill.md                     # Service capabilities
```

## Core Components

### Backend Services
- **api-gateway**: Entry point, routes requests to downstream services, proxies AI calls
- **market-data**: Fetches prices from yfinance/Alpha Vantage/Polygon, manages stock universe
- **technical-analysis**: Computes indicators (RSI, MACD, etc.) and detects patterns
- **ml-prediction**: Trains and serves ML models for price prediction
- **signal-engine**: Generates trading signals by fusing TA + ML + volume analysis
- **ranking-engine**: Computes K-Score composite rankings
- **decision-engine**: Applies hard reject rules and trade filters
- **research-engine**: Generates AI research reports via Claude

### Frontend
- **Next.js 14** with App Router
- **SWR** for data fetching with caching
- **lightweight-charts** for candlestick charts
- **Plotly.js** for interactive visualizations
- **Zustand** for state management

### Shared Layer
- **ORM Models**: Stock, Signal, PriceHistory, User, Alert, PaperTrade, etc.
- **Config**: Environment-based configuration via pydantic-settings
- **JWT Auth**: Token-based authentication middleware
- **Redis Client**: Caching layer for live prices and news

## Data Flow

```
yfinance/Alpha Vantage → market-data → PostgreSQL
                              ↓
                    technical-analysis → indicators
                              ↓
                      ml-prediction → predictions
                              ↓
                      signal-engine → signals (BUY/HOLD/WAIT/SELL)
                              ↓
                      api-gateway → frontend
```

## Key Files

| File | Purpose |
|------|---------|
| `docker/docker-compose.yml` | Local dev stack (10 containers) |
| `shared/db/models.py` | SQLAlchemy ORM models |
| `shared/common/config.py` | Environment configuration |
| `frontend/src/lib/api.ts` | Frontend API client |
| `Makefile` | Build, test, deploy commands |
