# Architecture

## High-level diagram

```
                    ┌──────────────────────────────────────┐
                    │            Next.js Frontend           │
                    │  Dashboard · Watchlist · Positions    │
                    │  Rankings · Stock Detail · Strategies │
                    └──────────────┬───────────────────────┘
                                   │ HTTP (SWR)
                                   ▼
                    ┌──────────────────────────────────────┐
                    │             API Gateway               │
                    │  reverse proxy + /aggregate/overview  │
                    └──┬───────┬────────┬────────┬────────┬┘
                       │       │        │        │        │
              ┌────────▼─┐ ┌───▼────┐ ┌▼──────┐ ┌▼──────┐ ┌▼──────────┐
              │market-   │ │tech-   │ │ml-    │ │ranking│ │signal-    │
              │data      │ │anal.   │ │pred.  │ │engine │ │engine     │
              └────────┬─┘ └───┬────┘ └┬──────┘ └┬──────┘ └┬──────────┘
                       │       │       │          │          │
                       ▼       ▼       ▼          ▼          ▼
              ┌────────────────────────────────────────────────────┐
              │   Postgres 16    │    Redis 7    │   Parquet (FS)  │
              └────────────────────────────────────────────────────┘
                       ▲
                       │
              ┌────────┴───────┐
              │ strategy-engine│ ──►  ┌────────────────────┐
              └────────────────┘      │ portfolio-optimizer│
                                      └────────────────────┘
```

## Service responsibilities

| Service | Responsibility | Key endpoints |
|---------|---------------|---------------|
| `api-gateway` | Reverse proxy + aggregate stock overview | `GET /aggregate/overview/{symbol}` |
| `market-data` | Provider adapters, ingestion, price API | `POST /admin/ingest`, `POST /admin/seed`, `GET /stocks`, `GET /stocks/{s}/prices` |
| `technical-analysis` | Indicators, patterns, trendlines, S/R, Fibonacci | `GET /ta/{s}/indicators`, `/ta/{s}/patterns`, `/ta/{s}/levels` |
| `ml-prediction` | RF / XGB / GBM / LSTM train & predict | `POST /ml/train`, `POST /ml/predict`, `POST /ml/train_all` |
| `ranking-engine` | K-Score composite + leaderboard | `GET /rankings`, `GET /rankings/{s}`, `POST /rankings/refresh` |
| `signal-engine` | BUY/SELL/HOLD per symbol; batch latest signals | `GET /signals/{s}`, `GET /signals`, `GET /signals/{s}?persist=true` |
| `strategy-engine` | Rule DSL CRUD + vectorized backtester | `POST /strategies`, `POST /backtest` |
| `portfolio-optimizer` | MVO / Risk Parity / AI Allocation | `POST /portfolio/optimize` |

## Frontend pages and data flow

```
Dashboard (/)
  SWR keys: stocks, watchlist, rankings-all, latest-prices, signals-all
  → /stocks            (market-data)
  → /watchlist         (market-data)
  → /rankings          (ranking-engine)
  → /stocks/prices/latest  (market-data)
  → /signals           (signal-engine — batch, DB-persisted)

Stock Detail (/stock/[symbol])
  SWR keys: overview-{symbol}, news-{symbol}
  → /aggregate/overview/{symbol}  (api-gateway — persists signal to DB)
  → /news/{symbol}                (market-data)

Watchlist (/watchlist)
  SWR keys: watchlist, rankings-all, latest-prices, signals-all
  localStorage: stockai_watch_notes, stockai_price_alerts

Positions (/positions)
  SWR keys: latest-prices, rankings-all, signals-all, watchlist
  localStorage: stockai_positions, stockai_trades
```

## Signal persistence flow

```
1. User views /stock/[symbol]
   → api-gateway calls signal-engine with ?persist=true
   → signal is computed (TA + ML fusion) and written to signals table

2. Dashboard / Watchlist / Positions
   → GET /signals (no symbol) returns latest persisted signal per active stock
   → Same TA-based values shown everywhere — no K-Score threshold mismatch
```

## Design principles

1. **Clean architecture** — each service layers into API / domain / infrastructure. `src/api/*` holds FastAPI routers; domain logic (`indicators`, `models`, `scoring`, `dsl`) has zero framework dependencies; adapter/registry pattern isolates third-party libraries.
2. **Provider abstraction** — `DataAdapter` contract + registry mean `ingest_symbol()` doesn't know whether it's talking to yfinance or Polygon. Swap providers by registering a new adapter; no downstream changes.
3. **Plugin-ready markets** — `Market` enum controls routing (US / HK / CRYPTO). Schema is market-agnostic; ingestion picks the right adapter by `supports(market, timeframe)`.
4. **Typed everywhere** — Pydantic v2 at every API boundary, SQLAlchemy 2 `Mapped[...]` for ORM, TypeScript strict on the frontend. No untyped JSON bags flow between services.
5. **Fault isolation** — per-symbol try/except in ingestion, graceful degradation in signal engine if ML service is unreachable.
6. **Observability-ready** — structlog JSON logs, `/health` endpoints on every service, CloudWatch log groups pre-wired in Terraform.

## Data model

Tables: `stocks`, `prices`, `indicators`, `signals`, `rankings`, `strategies`, `backtests`, `portfolios`, `portfolio_holdings`.

- `prices` keyed by `(stock_id, ts, timeframe)` with a covering index — hot path for charting and indicator computation.
- `indicators` stores pre-computed values for fast leaderboards; recomputable from `prices` if cleared.
- `signals` stores persisted BUY/SELL/HOLD results with `ts`, `horizon`, `confidence`, `bullish_probability`. Batch `GET /signals` returns the latest row per active stock.
- `rankings` versioned by `as_of` date — allows backtest of ranking performance over time.
- `backtests` stores equity curve + trade log as JSON — denormalized for read performance.

Client-side state (positions, trades, watchlist notes, price alerts) is stored in **localStorage** — no backend required, instant writes, works offline.

## AI Confidence Score

Signal engine fuses three sources:

- **TA score** (0–1): SMA50 trend, golden-cross, RSI in healthy range, MACD histogram sign, volume expansion.
- **ML probability** (0–1): XGBoost (default) `P(price up in H days)`.
- **Fused probability** = `0.6 × ML + 0.4 × TA` when ML is available, else pure TA.

Confidence = `|fused − 0.5| × 200`  →  0 = coin-flip, 100 = maximum conviction.

## ML models comparison

| Model | Strengths | Weaknesses | Best for |
|-------|-----------|-----------|----------|
| **XGBoost** | Fast, robust to noise, handles missing data, no feature scaling needed | Less expressive on sequential patterns | **Production default** |
| **Random Forest** | Stable, low variance, easy to interpret | Slower on large feature sets, can't extrapolate | Sanity check / ensemble |
| **Gradient Boosting** | High accuracy, handles mixed feature types | Slow to train, over-fits on noisy data | Longer-horizon predictions |
| **LSTM** | Captures sequential temporal patterns | Needs large data, slow to train, hard to tune | Trend momentum on liquid stocks |

All four models share the same 14 engineered features (RSI, MACD, SMA ratios, volume change, ATR, returns over multiple windows).

## K-Score breakdown

| Sub-score | What it measures |
|-----------|-----------------|
| Technical (0–100) | Trend alignment, indicator health (RSI, MACD, SMA cross) |
| Momentum (0–100) | Rate-of-change over 1/4/12-week windows |
| Value (0–100) | P/E, P/B, EV/EBITDA vs sector peers |
| Growth (0–100) | Revenue + earnings growth trajectory |
| Volatility (0–100) | Inverse of realized volatility (lower vol = higher score) |
| **K-Score** | Weighted composite of all five sub-scores |

Fair price is derived from the Value sub-score using a DCF-lite model seeded by yfinance fundamentals.

## Train All pipeline

```
User clicks "Train All"
  1. GET /stocks → collect all active symbols
  2. POST /admin/ingest {symbols: [...]}  → refresh latest prices (async, BackgroundTask)
  3. POST /ml/train_all                  → schedule XGBoost training for every symbol
     └─ BackgroundTasks.add_task(train_model, sym, "xgboost", 5) × N symbols
  → Models ready in ~2–5 minutes
```

## Extending the platform

- **New data vendor:** implement `DataAdapter`, register it — `ingest_symbol()` picks it up automatically.
- **New market:** add `Market` enum value + adapter support; no schema change.
- **New ML model:** extend `BaseModel`, register in `models/registry.py`; `/ml/train` and `/ml/predict` pick it up via the `model` parameter.
- **New indicator in DSL:** add column to `compute_features()` in `strategy-engine/src/dsl/evaluator.py`.

## Scaling notes

- **Horizontal:** each service scales independently; Fargate task count per service is a Terraform variable.
- **Vertical:** ML service is memory-heavy (PyTorch + XGBoost), sized at 2 GB. Everything else fits in 512 MB.
- **Data:** Parquet partitioned by `(timeframe, symbol)` keeps per-symbol reads cheap even with 5000+ tickers. Postgres `prices` table uses covering index `(stock_id, timeframe, ts)` — range queries stay fast into millions of rows.
- **Future:** swap Parquet storage from local FS to S3 + DuckDB for cross-symbol analytics at scale.
