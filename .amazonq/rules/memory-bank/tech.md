# StockAI — Technology Stack

## Languages & Versions

| Component | Technology | Version |
|-----------|------------|---------|
| Backend | Python | 3.11+ |
| Frontend | TypeScript/JavaScript | ES2022 |
| Database | PostgreSQL | 16 |
| Cache | Redis | 7 |

## Backend Stack

### Framework & Libraries
- **FastAPI** — async web framework
- **SQLAlchemy 2** — ORM with async support
- **Pydantic v2** — data validation and settings
- **structlog** — structured JSON logging
- **alembic** — database migrations

### ML/AI Libraries
- **PyTorch** — LSTM neural networks
- **scikit-learn** — Random Forest, Gradient Boosting, preprocessing
- **XGBoost** — gradient boosting models
- **scipy** — optimization (SLSQP for portfolio)
- **pandas/numpy** — data manipulation

### Data Sources
- **yfinance** — free market data (default)
- **Alpha Vantage** — premium market data
- **Polygon.io** — premium market data

### AI Providers
- **Anthropic Claude** — AI chat and research reports
- **DeepSeek** — alternative AI provider

## Frontend Stack

- **Next.js 14** — React framework with App Router
- **React 18** — UI library
- **TypeScript 5.5** — type safety
- **SWR 2.2** — data fetching with caching
- **Zustand 4.5** — state management
- **lightweight-charts 4.2** — TradingView candlestick charts
- **Plotly.js 2.35** — interactive visualizations
- **Vitest** — unit testing

## Infrastructure

### Local Development
- **Docker Compose** — 10-container stack
- **PostgreSQL** — primary database
- **Redis** — caching (live prices, news)

### Production (AWS)
- **ECS Fargate** — container orchestration
- **RDS** — managed PostgreSQL
- **ElastiCache** — managed Redis
- **ALB** — load balancing
- **Terraform** — infrastructure as code

## Development Commands

```bash
# Build & Run
make build          # Build all Docker images
make up             # Start full stack (10 containers)
make down           # Stop stack
make logs           # Tail logs

# Testing
make test           # Run all unit tests
cd services/<svc> && python -m pytest  # Test single service
cd frontend && npm test                 # Frontend tests

# Code Quality
make fmt            # Format Python (ruff)
cd frontend && npm run lint            # Lint frontend
cd frontend && npm run typecheck       # TypeScript check

# Database
make migrate        # Run DB migrations
make seed           # Seed stock universe

# API Endpoints
curl -X POST http://localhost:8000/admin/seed     # Seed stocks
curl -X POST http://localhost:8000/admin/ingest   # Ingest prices
```

## Environment Variables

Key variables in `.env`:
```bash
DATABASE_URL=postgresql://user:pass@localhost:5432/stockai
REDIS_URL=redis://localhost:6379
JWT_SECRET=<secret>
ANTHROPIC_API_KEY=<key>      # Optional: AI chat
ALPHA_VANTAGE_KEY=<key>      # Optional: premium data
POLYGON_API_KEY=<key>        # Optional: premium data
```

## Service Ports

| Service | Port |
|---------|------|
| api-gateway | 8000 |
| market-data | 8001 |
| technical-analysis | 8002 |
| ml-prediction | 8003 |
| ranking-engine | 8004 |
| signal-engine | 8005 |
| strategy-engine | 8006 |
| portfolio-optimizer | 8007 |
| research-engine | 8008 |
| frontend | 3000 |
| PostgreSQL | 5432 |
| Redis | 6379 |

## Dependencies

### Backend (per service)
See `services/<service>/requirements.txt`

Common dependencies:
- fastapi, uvicorn
- sqlalchemy, psycopg2-binary
- pydantic, pydantic-settings
- redis, httpx
- pandas, numpy

### Frontend
See `frontend/package.json`

### Shared Library
See `shared/pyproject.toml`
