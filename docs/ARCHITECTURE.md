# Architecture

## High-level diagram

```
                    ┌──────────────────┐
                    │    Next.js FE    │  lightweight-charts + strategy builder
                    └────────┬─────────┘
                             │ HTTPS
                             ▼
                    ┌──────────────────┐
                    │   API Gateway    │  reverse proxy + aggregation
                    └────────┬─────────┘
          ┌────────────┬─────┴─────┬────────────┬────────────┐
          ▼            ▼           ▼            ▼            ▼
   ┌───────────┐ ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐
   │market-data│ │tech anal.│ │ml-pred. │ │ranking   │ │signal    │
   └──────┬────┘ └────┬─────┘ └────┬────┘ └────┬─────┘ └────┬─────┘
          │           │            │           │            │
          ▼           ▼            ▼           ▼            ▼
     ┌─────────────────────────────────────────────────────────┐
     │   Postgres 16    │    Redis 7    │    Parquet (FS/S3)   │
     └─────────────────────────────────────────────────────────┘
          ▲
          │
   ┌──────┴────────┐
   │ strategy eng. │  ──── ┐
   └───────────────┘       │
                           ▼
                   ┌───────────────┐
                   │ portfolio opt.│
                   └───────────────┘
```

## Service responsibilities

| Service | Responsibility | Key endpoints |
|---------|---------------|---------------|
| `api-gateway` | Reverse proxy + aggregate endpoints | `/aggregate/overview/{symbol}` |
| `market-data` | Provider adapters, ingestion, prices API | `/stocks`, `/stocks/{s}/prices`, `/admin/ingest`, `/admin/seed` |
| `technical-analysis` | Indicators, patterns, trendlines, S/R | `/ta/{s}/indicators`, `/ta/{s}/patterns`, `/ta/{s}/levels` |
| `ml-prediction` | RF / XGB / GBM / LSTM train & predict | `/ml/models`, `/ml/train`, `/ml/predict` |
| `ranking-engine` | K-Score composite + leaderboard | `/rankings`, `/rankings/{s}`, `/rankings/refresh` |
| `signal-engine` | BUY/SELL/HOLD + AI confidence | `/signals/{s}` |
| `strategy-engine` | Rule DSL CRUD + vectorized backtester | `/strategies`, `/backtest` |
| `portfolio-optimizer` | MVO / Risk Parity / AI Allocation | `/portfolio/optimize` |

## Design principles

1. **Clean architecture** — every service layers cleanly into API / domain / infrastructure. `src/api/*` holds FastAPI routers, `src/*/` (indicators, models, scoring, dsl) holds pure domain logic with zero framework dependencies, and the adapter/registry pattern isolates third-party libraries.
2. **Provider abstraction** — `DataAdapter` contract + registry mean `ingest_symbol()` doesn't know whether it's talking to yfinance or Polygon. Swap free→paid providers by registering a new adapter; no downstream changes.
3. **Plugin-ready for new markets** — `Market` enum controls routing (US / HK / CRYPTO). Schema is market-agnostic; ingestion picks the right adapter by `supports(market, timeframe)`.
4. **Typed everywhere** — Pydantic v2 at every API boundary, SQLAlchemy 2 `Mapped[...]` for ORM, TypeScript on the FE. No untyped JSON bags flow between services.
5. **Fault isolation** — per-symbol try/except in ingestion, graceful degradation in signal engine if ML service is unreachable.
6. **Observability-ready** — structlog JSON logs, `/health` endpoints on every service, CloudWatch log groups pre-wired in Terraform.

## Data model

Tables: `stocks`, `prices`, `indicators`, `signals`, `rankings`, `strategies`, `backtests`, `portfolios`, `portfolio_holdings`.

- `prices` is keyed by `(stock_id, ts, timeframe)` with a covering index for range queries — the hot path for charting and indicator computation.
- `indicators` stores pre-computed values for fast leaderboards; recomputable from `prices` if cleared.
- `rankings` is versioned by `as_of` date so you can backtest ranking performance.
- `backtests` stores equity curve + trade log as JSON — denormalized for read performance.

Indicator-series inputs for the DSL are computed in-memory in the strategy engine (not stored) — cheap enough at single-asset scale, and avoids cache-invalidation bugs.

## AI Confidence Score

Signal-engine fuses three sources:
- **TA score** (0-1): SMA50 trend, golden-cross, RSI in healthy range, MACD hist sign, volume expansion.
- **ML probability** (0-1): XGBoost (default) `P(up in H days)`.
- **Fused probability** = 0.6 × ML + 0.4 × TA when ML is available, else pure TA.

Confidence = `|fused - 0.5| * 200` (0 = flip-coin, 100 = maximum directional conviction).

## Extending the platform

- **New data vendor:** implement `DataAdapter`, register it, done.
- **New market:** add `Market` enum value + adapter support; no schema change.
- **New model:** extend `BaseModel`, register in `models/registry.py`; `/ml/train` and `/ml/predict` pick it up via `model` parameter.
- **New indicator in DSL:** add column to `compute_features()` in `strategy-engine/src/dsl/evaluator.py`.

## Scaling notes

- Horizontal: each service scales independently; Fargate task count per service is a Terraform variable.
- Vertical: ML service is memory-heavy (PyTorch + XGBoost), sized at 2GB. Everything else fits in 512 MB.
- Data: Parquet partitioned by `(timeframe, symbol)` allows per-symbol reads to remain cheap even with 5000+ tickers. Postgres `prices` table uses covering index `(stock_id, timeframe, ts)` — range queries stay fast into the millions of rows.
- Future: swap Parquet storage from local FS to S3 + DuckDB for cross-symbol analytics.
