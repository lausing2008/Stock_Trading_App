# Architecture

## High-level diagram

```
                    ┌──────────────────────────────────────────────┐
                    │              Next.js Frontend                  │
                    │  Login · Dashboard · Watchlist · Positions     │
                    │  Stock Detail · Portfolio · Alerts · Settings  │
                    └──────────────────┬───────────────────────────┘
                                       │ HTTP (SWR, 60 s auto-refresh)
                                       ▼
                    ┌──────────────────────────────────────────────┐
                    │                API Gateway                    │
                    │  /ai/chat · /aggregate/overview · proxy       │
                    └──┬───────┬────────┬────────┬────────┬────────┘
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
              ┌────────┴───────┐
              │ strategy-engine│ ──►  ┌────────────────────┐
              └────────────────┘      │ portfolio-optimizer│
                                      └────────────────────┘

                    API gateway also proxies to:
                    ┌─────────────────────┐  ┌───────────────┐
                    │ api.anthropic.com   │  │ api.deepseek  │
                    │ (Claude API)        │  │ .com (DS API) │
                    └─────────────────────┘  └───────────────┘
```

## Service responsibilities

| Service | Responsibility | Key endpoints |
|---------|---------------|---------------|
| `api-gateway` | Reverse proxy + aggregate overview + AI proxy | `GET /aggregate/overview/{symbol}`, `POST /ai/chat` |
| `market-data` | Live prices, fundamentals, ingestion, news, price history | `GET /stocks/latest_prices`, `GET /stocks/{s}/fundamentals`, `GET /stocks/{s}/news`, `POST /admin/ingest` |
| `technical-analysis` | Indicators, patterns, trendlines, S/R, Fibonacci | `GET /ta/{s}/indicators`, `/ta/{s}/patterns`, `/ta/{s}/levels` |
| `ml-prediction` | RF / XGB / GBM / LSTM train & predict | `POST /ml/train`, `POST /ml/predict`, `POST /ml/train_all` |
| `ranking-engine` | K-Score composite + leaderboard | `GET /rankings`, `GET /rankings/{s}`, `POST /rankings/refresh` |
| `signal-engine` | BUY/SELL/HOLD per symbol; batch latest signals | `GET /signals/{s}`, `GET /signals`, `GET /signals/{s}?persist=true` |
| `strategy-engine` | Rule DSL CRUD + vectorized backtester | `POST /strategies`, `POST /backtest` |
| `portfolio-optimizer` | Sharpe MVO / Risk Parity / HRP / AI Allocation | `POST /portfolio/optimize` |

## Authentication

Auth is client-side only — suitable for a personal/single-user deployment.

```
Browser localStorage
  stockai_auth_users   — {username: password} map (overrides defaults)
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

## AI Chat flow

```
User types a question on /stock/[symbol]
  1. Frontend reads AI provider + model + API key from localStorage (settings)
  2. Builds context string:
       - Stock symbol + company name
       - Current price + day change
       - Signal (BUY/SELL/HOLD + confidence + bullish %)
       - K-Score + fair value
       - Last 5 news headlines
  3. POST /ai/chat to api-gateway with:
       { provider, model, api_key, messages, system (context) }
  4. api-gateway ai_proxy.py routes to:
       provider=claude   → POST https://api.anthropic.com/v1/messages
       provider=deepseek → POST https://api.deepseek.com/v1/chat/completions
  5. AI response returned as { content, model, provider }
  6. Frontend renders assistant reply in the chat panel

Security notes:
  - API keys are stored in browser localStorage only
  - Keys are passed in the request body (not stored on the server)
  - The gateway acts as a transparent proxy — it reads and discards the key
  - No conversation history is logged server-side
```

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

Frontend:
  SWR refreshInterval: 60_000 on Dashboard, Watchlist, Positions
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

Included in /aggregate/overview/{symbol} — fetched in parallel with the
other 7 upstream calls, adding zero extra latency to the stock detail page load.
```

## News flow

```
GET /stocks/{symbol}/news?sources=yfinance,google
  1. Parse "sources" param → enabled = {yfinance, google} set
  2. Cache key = "stockai:news:{SYMBOL}:{sources}" (TTL 30 min)
     → Unique per source combination, so toggling a source bypasses stale cache
  3. Check Redis
     → HIT: return cached JSON
     → MISS: fetch fresh (step 4)
  4. If yfinance in enabled:
       _yfinance_news(symbol) — filters articles older than 7 days
  5. If google in enabled AND (HK stock OR yfinance returned < 3 articles OR yfinance disabled):
       _google_news(company_name) — Google News RSS via feedparser
  6. Merge + deduplicate (by title[:60]) → sort newest-first
  7. VADER sentiment scoring on each headline
  8. Write to Redis (TTL 1800 s)
  9. Return

Source toggles controlled by Settings → News Sources (stored in localStorage).
The frontend passes the active sources as the ?sources= query parameter.
```

## Alert checking flow

```
Global 60 s interval in _app.tsx (runs while user is logged in)
  1. Parallel fetch:
       GET /stocks/latest_prices  → price + change_pct per symbol
       GET /signals               → latest signal + confidence per symbol
       GET /rankings              → K-Score per symbol
  2. Build maps: prices, signals, scores
  3. checkAlerts(prices, signals, scores) in lib/alerts.ts:
       - Load enabled alert rules from localStorage
       - For each rule: check condition against current data
       - Respect cooldown (skip if < cooldownMinutes since lastTriggered)
       - Collect triggered: { id, alertId, symbol, message, triggeredAt }
  4. If triggered.length > 0:
       - Save to stockai_notifications (localStorage, max 100)
       - Update lastTriggered on alert rules
       - Dispatch CustomEvent('stockai:notifications')
       - Play notification sound if settings.notificationSound = true
  5. NotificationBell component listens to 'stockai:notifications'
     → re-renders badge count
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

## Portfolio Optimizer internals

```
POST /portfolio/optimize
  1. _fetch_closes(symbols, lookback_days)
     → sequential HTTP to market-data /stocks/{s}/prices
     → builds pandas DataFrame of daily closes
     → forward-fill gaps, drop columns with no data
  2. returns = closes.pct_change().dropna()
  3. Route by method:

     mean_variance:
       mu  = James-Stein(returns.mean() * 252)       # shrink toward grand mean
       cov = LedoitWolf(returns).covariance_ * 252    # analytical shrinkage
       Maximize Sharpe: argmax_w (w·mu - Rf) / √(w·Σ·w)
       Constraints: Σw = 1, 0 ≤ w ≤ 0.40
       Solver: scipy SLSQP

     risk_parity:
       Same mu, cov as above
       Minimize Σ(RC_i - mean(RC))² where RC_i = w_i(Σw)_i / σ_p
       Constraints: Σw = 1, 0 ≤ w ≤ 0.60

     hierarchical_risk_parity:
       1. corr = cov / outer(vols, vols)
       2. dist = √((1 - corr) / 2)
       3. Ward linkage on squareform(dist)
       4. Quasi-diagonalization: leaf order from dendrogram
       5. Recursive bisection: weight by inverse cluster variance
       No matrix inversion required — numerically stable

     ai_allocation:
       1. _fetch_scores(symbols) → K-Score per symbol from ranking-engine
       2. Filter: keep symbols with score ≥ min_score
       3. Normalize scores to [0,1], map to return range [mu-5%, mu+15%]
       4. Blend: 60% historical (shrunk) + 40% K-Score views
       5. Maximize Sharpe on blended returns
       6. Scale down by (1 - cash_floor) to maintain 5% cash buffer

  4. Compute metrics: Sharpe, max_drawdown, diversification (1-HHI)
  5. Return PortfolioWeights dataclass
```

## Frontend pages and data flow

```
Login (/login)
  No API calls — pure localStorage auth

Dashboard (/)
  SWR keys: stocks, watchlist, rankings-all, latest-prices (60s), signals-all
  → /stocks, /watchlist, /rankings, /stocks/latest_prices, /signals

Stock Detail (/stock/[symbol])
  SWR keys: overview-{symbol}, news-{symbol}-{sources}
  → /aggregate/overview/{symbol}  (fans out to 8 upstreams in parallel)
      ├─ price, prices, indicators, patterns, levels  (market-data / technical-analysis)
      ├─ signal (persist=true)                        (signal-engine)
      ├─ ranking                                      (ranking-engine)
      └─ fundamentals (Redis 24 h cache)              (market-data → yfinance .info)
  → /stocks/{symbol}/news?sources={activeNewsSources}  (market-data)
  AI Chat: POST /ai/chat  (api-gateway → Claude/DeepSeek API)

Portfolio (/portfolio)
  On-demand: POST /portfolio/optimize

Alerts (/alerts)
  SWR: /stocks  (for stock selector)
  localStorage: stockai_alert_rules, stockai_notifications

Settings (/settings)
  No API calls — reads/writes localStorage only via lib/settings.ts
  Test Connection: POST /ai/chat  (API key validation)
```

## Design principles

1. **Clean architecture** — each service layers into API / domain / infrastructure. `src/api/*` holds FastAPI routers; domain logic has zero framework dependencies.
2. **Provider abstraction** — `DataAdapter` contract + registry mean `ingest_symbol()` doesn't know whether it's talking to yfinance or Polygon.
3. **User-controlled data sources** — data source toggles and AI provider selection live in the frontend settings. The backend respects `sources=` query params and passes API keys through.
4. **Plugin-ready markets** — `Market` enum controls routing (US / HK / CRYPTO). Schema is market-agnostic.
5. **Typed everywhere** — Pydantic v2 at every API boundary, SQLAlchemy 2 `Mapped[...]` for ORM, TypeScript strict on the frontend.
6. **Fault isolation** — per-symbol try/except in ingestion; live price endpoint falls back to DB if yfinance fails; graceful degradation in signal engine if ML is unreachable.
7. **Observability-ready** — structlog JSON logs, `/health` endpoints on every service, CloudWatch pre-wired in Terraform.

## Data model

Tables: `stocks`, `prices`, `indicators`, `signals`, `rankings`, `strategies`, `backtests`, `portfolios`, `portfolio_holdings`.

- `prices` keyed by `(stock_id, ts, timeframe)` — for chart history and indicator computation
- `signals` stores persisted BUY/SELL/HOLD with `ts`, `horizon`, `confidence`, `bullish_probability`
- `rankings` versioned by `as_of` date — allows backtest of ranking performance over time

**Fundamentals and news are not stored in Postgres.** They are fetched live and cached in Redis.

Client-side state (positions, trades, notes, alerts, settings, AI keys) is stored in **localStorage**.

## AI Confidence Score (Signal Engine)

Signal engine fuses three sources:

- **TA score** (0–1): SMA50 trend, golden-cross, RSI in healthy range, MACD histogram sign, volume expansion
- **ML probability** (0–1): XGBoost `P(price up in H days)`
- **Fused probability** = `0.6 × ML + 0.4 × TA` when ML available, else pure TA

Confidence = `|fused − 0.5| × 200` → 0 = coin-flip, 100 = maximum conviction.

## ML models comparison

| Model | Strengths | Weaknesses | Best for |
|-------|-----------|-----------|----------|
| **XGBoost** | Fast, robust to noise, handles missing data | Less expressive on sequential patterns | **Production default** |
| **Random Forest** | Stable, low variance, interpretable | Slower on large feature sets | Sanity check / ensemble |
| **Gradient Boosting** | High accuracy, mixed feature types | Slow to train, over-fits noisy data | Longer-horizon predictions |
| **LSTM** | Captures sequential temporal patterns | Needs large data, slow to train | Trend momentum on liquid stocks |

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

- **New data vendor:** implement `DataAdapter`, register it — ingestion picks it up automatically
- **New market:** add `Market` enum value + adapter support; no schema change
- **New ML model:** extend `BaseModel`, register in `models/registry.py`
- **New alert condition:** add to `ConditionType` union in `lib/alerts.ts` and `checkAlerts()` switch
- **New AI provider:** add a branch in `ai_proxy.py` and a provider option in `settings.tsx`
- **Production auth:** replace `frontend/src/lib/auth.ts` with a JWT endpoint backed by a users table

## Scaling notes

- **Horizontal:** each service scales independently; Fargate task count per service is a Terraform variable
- **Vertical:** ML service is memory-heavy (PyTorch + XGBoost), sized at 2 GB; everything else fits in 512 MB
- **Live prices at scale:** for >200 symbols, consider replacing `fast_info` per-symbol with a bulk quote API (Polygon or Alpha Vantage) and increasing the Redis TTL
- **AI proxy at scale:** the gateway is stateless; multiple tasks share no state — scales horizontally without change
- **Data:** Parquet partitioned by `(timeframe, symbol)` keeps per-symbol reads cheap even with 5000+ tickers
