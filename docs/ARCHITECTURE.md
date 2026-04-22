# Architecture

## High-level diagram

```
                    ┌──────────────────────────────────────┐
                    │            Next.js Frontend           │
                    │  Login · Dashboard · Watchlist        │
                    │  Positions · Stock Detail · Strategies│
                    └──────────────┬───────────────────────┘
                                   │ HTTP (SWR, 60 s auto-refresh)
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
| `market-data` | Live price quotes, company fundamentals, ingestion, price history | `GET /stocks/latest_prices`, `GET /stocks/{s}/fundamentals`, `POST /admin/ingest`, `POST /admin/seed`, `GET /stocks/{s}/prices` |
| `technical-analysis` | Indicators, patterns, trendlines, S/R, Fibonacci | `GET /ta/{s}/indicators`, `/ta/{s}/patterns`, `/ta/{s}/levels` |
| `ml-prediction` | RF / XGB / GBM / LSTM train & predict | `POST /ml/train`, `POST /ml/predict`, `POST /ml/train_all` |
| `ranking-engine` | K-Score composite + leaderboard | `GET /rankings`, `GET /rankings/{s}`, `POST /rankings/refresh` |
| `signal-engine` | BUY/SELL/HOLD per symbol; batch latest signals | `GET /signals/{s}`, `GET /signals`, `GET /signals/{s}?persist=true` |
| `strategy-engine` | Rule DSL CRUD + vectorized backtester | `POST /strategies`, `POST /backtest` |
| `portfolio-optimizer` | MVO / Risk Parity / AI Allocation | `POST /portfolio/optimize` |

## Authentication

Auth is client-side only — suitable for a personal/single-user deployment.

```
Browser localStorage
  stockai_auth_users  — {username: password} map (overrides defaults)
  stockai_auth_session — {username} set on successful login

Default credentials (hardcoded fallback in auth.ts):
  lausing / 120402

Flow:
  1. User visits any page → _app.tsx checks localStorage for session
  2. No session → redirect to /login
  3. Login page verifies credentials → sets session → redirect to /
  4. Logout → clears session → redirect to /login
  5. Reset password → updates stockai_auth_users in localStorage
```

For production with multiple users, replace `auth.ts` with a proper JWT-based backend service.

## Live price flow

```
GET /stocks/latest_prices
  1. Check Redis key "stockai:live_prices" (TTL 60 s)
     → HIT:  return cached JSON instantly
     → MISS: fetch live from yfinance (step 2)
  2. ThreadPoolExecutor (6 workers)
     → yf.Ticker(symbol).fast_info.last_price  per symbol in parallel
     → ~3–5 s for 20 symbols
  3. Write result to Redis (TTL 60 s)
  4. Return live prices

Fallback:
  If yfinance fails for all symbols → fall back to latest DB close

Cache bust:
  ingest_universe() deletes "stockai:live_prices" after writing new bars
  → next /latest_prices call fetches fresh post-ingest prices

Frontend:
  SWR refreshInterval: 60_000 on Dashboard, Watchlist, Positions
  → prices update in background every 60 s without user action
```

## Fundamentals flow

```
GET /stocks/{symbol}/fundamentals
  1. Check Redis key "stockai:fundamentals:{SYMBOL}" (TTL 24 h)
     → HIT:  return cached JSON instantly
     → MISS: fetch from yfinance (step 2)
  2. yf.Ticker(symbol).info  — ~1-2 s, returns 100+ fields
  3. Map to FundamentalsOut (30 curated fields, None for missing)
  4. Write to Redis (TTL 86400 s)
  5. Return

Fields returned:
  Valuation:     market_cap, enterprise_value, trailing_pe, forward_pe,
                 price_to_book, ev_to_ebitda
  Income (TTM):  total_revenue, gross_profit, net_income, ebitda
  Margins:       profit_margin, operating_margin, gross_margin
  Cash flow:     free_cashflow, operating_cashflow
  Balance sheet: total_cash, total_debt
  Per share:     trailing_eps, forward_eps, book_value,
                 dividend_yield, dividend_rate
  Returns/risk:  return_on_equity, return_on_assets,
                 revenue_growth, earnings_growth, beta
  Range/volume:  week_52_high, week_52_low,
                 average_volume, shares_outstanding
  Analyst:       target_price, recommendation, number_of_analysts

Included in /aggregate/overview/{symbol} — fetched in parallel with the
other 7 upstream calls, adding zero extra latency to the stock detail page load.
```

## Signal persistence flow

```
1. User views /stock/[symbol]
   → api-gateway calls signal-engine with ?persist=true
   → signal computed (TA + ML fusion) and written to signals table

2. Dashboard / Watchlist / Positions
   → GET /signals returns latest persisted signal per active stock
   → Same TA-based values shown everywhere — no K-Score mismatch
```

## Train All pipeline

```
User clicks "⚡ Train All"
  1. GET /stocks → collect all active symbols
  2. POST /admin/ingest {symbols: [...]}
     → ingest_universe() with ThreadPoolExecutor (6 workers, parallel)
     → waits for ALL symbols to complete (synchronous response)
     → busts live price Redis cache
  3. Frontend: await mutatePrices() + mutateRankings()
     → UI shows fresh prices immediately
  4. POST /ml/train_all
     → BackgroundTasks.add_task(train_model, sym, "xgboost") × N
     → returns immediately; models ready in ~2–5 min
  5. Frontend: setTimeout(() => mutateSignals(), 5000)
     → picks up refreshed signals after models settle
```

## Frontend pages and data flow

```
Login (/login)
  No API calls — pure localStorage auth

Dashboard (/)
  SWR keys: stocks, watchlist, rankings-all, latest-prices (60s), signals-all
  → /stocks            (market-data)
  → /watchlist         (market-data)
  → /rankings          (ranking-engine)
  → /stocks/latest_prices  (market-data — live yfinance, Redis-cached)
  → /signals           (signal-engine — batch, DB-persisted)

Stock Detail (/stock/[symbol])
  SWR keys: overview-{symbol}, news-{symbol}
  → /aggregate/overview/{symbol}  (api-gateway — fans out to 8 upstreams in parallel)
      ├─ price, prices, indicators, patterns, levels  (market-data / technical-analysis)
      ├─ signal (persist=true)                        (signal-engine)
      ├─ ranking                                      (ranking-engine)
      └─ fundamentals (Redis 24 h cache)              (market-data → yfinance .info)
  → /news/{symbol}                (market-data)
  ← Back button: router.back()

Watchlist (/watchlist)
  SWR keys: watchlist, rankings-all, latest-prices (60s), signals-all
  localStorage: stockai_watch_notes, stockai_price_alerts

Positions (/positions)
  SWR keys: latest-prices (60s), rankings-all, signals-all, watchlist
  localStorage: stockai_positions, stockai_trades
```

## Design principles

1. **Clean architecture** — each service layers into API / domain / infrastructure. `src/api/*` holds FastAPI routers; domain logic has zero framework dependencies; adapter/registry pattern isolates third-party libraries.
2. **Provider abstraction** — `DataAdapter` contract + registry mean `ingest_symbol()` doesn't know whether it's talking to yfinance or Polygon.
3. **Plugin-ready markets** — `Market` enum controls routing (US / HK / CRYPTO). Schema is market-agnostic.
4. **Typed everywhere** — Pydantic v2 at every API boundary, SQLAlchemy 2 `Mapped[...]` for ORM, TypeScript strict on the frontend.
5. **Fault isolation** — per-symbol try/except in ingestion; live price endpoint falls back to DB if yfinance fails; graceful degradation in signal engine if ML service is unreachable.
6. **Observability-ready** — structlog JSON logs, `/health` endpoints on every service, CloudWatch log groups pre-wired in Terraform.

## Data model

Tables: `stocks`, `prices`, `indicators`, `signals`, `rankings`, `strategies`, `backtests`, `portfolios`, `portfolio_holdings`.

- `prices` keyed by `(stock_id, ts, timeframe)` — used for chart history and indicator computation. **Not** the source for dashboard prices (live yfinance is used instead).
- `signals` stores persisted BUY/SELL/HOLD with `ts`, `horizon`, `confidence`, `bullish_probability`. Batch `GET /signals` returns latest row per active stock.
- `rankings` versioned by `as_of` date — allows backtest of ranking performance over time.

**Fundamentals are not stored in Postgres.** They are fetched live from yfinance `.info` on demand and cached in Redis for 24 hours. This avoids schema migrations when adding new fields and keeps the data fresh each quarter without a scheduled job.

Client-side state (positions, trades, notes, alerts, auth session) is stored in **localStorage** — no backend required, instant writes, works offline.

## AI Confidence Score

Signal engine fuses three sources:

- **TA score** (0–1): SMA50 trend, golden-cross, RSI in healthy range, MACD histogram sign, volume expansion.
- **ML probability** (0–1): XGBoost `P(price up in H days)`.
- **Fused probability** = `0.6 × ML + 0.4 × TA` when ML is available, else pure TA.

Confidence = `|fused − 0.5| × 200` → 0 = coin-flip, 100 = maximum conviction.

## ML models comparison

| Model | Strengths | Weaknesses | Best for |
|-------|-----------|-----------|----------|
| **XGBoost** | Fast, robust to noise, handles missing data | Less expressive on sequential patterns | **Production default** |
| **Random Forest** | Stable, low variance, interpretable | Slower on large feature sets | Sanity check / ensemble |
| **Gradient Boosting** | High accuracy, mixed feature types | Slow to train, over-fits noisy data | Longer-horizon predictions |
| **LSTM** | Captures sequential temporal patterns | Needs large data, slow to train | Trend momentum on liquid stocks |

All four share the same 14 engineered features (RSI, MACD, SMA ratios, volume change, ATR, returns over multiple windows).

## K-Score breakdown

| Sub-score | What it measures |
|-----------|-----------------|
| Technical | Trend alignment, RSI, MACD, SMA cross |
| Momentum | Rate-of-change over 1/4/12-week windows |
| Value | P/E, P/B, EV/EBITDA vs sector peers; DCF-lite fair price |
| Growth | Revenue + earnings growth trajectory |
| Volatility | Inverse of realized volatility |
| **K-Score** | Weighted composite of all five |

## Extending the platform

- **New data vendor:** implement `DataAdapter`, register it — ingestion picks it up automatically.
- **New market:** add `Market` enum value + adapter support; no schema change.
- **New ML model:** extend `BaseModel`, register in `models/registry.py`.
- **New indicator in DSL:** add column to `compute_features()` in `strategy-engine/src/dsl/evaluator.py`.
- **Production auth:** replace `frontend/src/lib/auth.ts` with a JWT endpoint backed by a users table.

## Scaling notes

- **Horizontal:** each service scales independently; Fargate task count per service is a Terraform variable.
- **Vertical:** ML service is memory-heavy (PyTorch + XGBoost), sized at 2 GB. Everything else fits in 512 MB.
- **Live prices at scale:** for >200 symbols, consider replacing `fast_info` per-symbol with a bulk quote API (Polygon or Alpha Vantage) and increasing the Redis TTL to reduce upstream calls.
- **Data:** Parquet partitioned by `(timeframe, symbol)` keeps per-symbol reads cheap even with 5000+ tickers.
