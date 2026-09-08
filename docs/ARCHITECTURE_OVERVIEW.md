# StockAI — Architecture Overview

*Regenerated 2026-07-27 — every value below was verified directly against source code
(a prior 2026-07-23 version of this doc was found to have significant errors: wrong ports,
3 omitted services, and several wrong formulas — see the "What changed from the last version"
note at the end).*

---

## 1. High-Level System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Browser / PWA                               │
│                    Next.js 14  (port 3000)                          │
│  Dashboard · Watchlist · Stock Detail · Trade Board · Paper Port    │
│  Rankings · Opportunities · Screener · Alerts · Research · Settings │
└────────────────────────────┬────────────────────────────────────────┘
                             │ HTTP / SWR polling
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       API Gateway  (port 8000)                      │
│   Only externally-exposed service. Reverse proxy, JWT auth,         │
│   admin-role enforcement. Routes ~20 path prefixes to 10 backends   │
│   (see Section 9 for the full table).                               │
└──┬─────────┬─────────┬─────────┬─────────┬─────────┬─────────┬─────┬┘
   │         │         │         │         │         │         │     │
   ▼         ▼         ▼         ▼         ▼         ▼         ▼     ▼
┌──────┐ ┌──────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌──────┐┌──────┐
│Market│ │ Tech.│ │  ML   │ │Ranking│ │Signal │ │Strateg│ │Portf.││Decis.│
│ Data │ │Analys│ │Predict│ │Engine │ │Engine │ │ Engine│ │ Optim││Engine│
│:8001 │ │:8002 │ │:8003  │ │:8004  │ │:8005  │ │:8006  │ │:8007 ││:8009 │
└──┬───┘ └──┬───┘ └───┬───┘ └───┬───┘ └───┬───┘ └───┬───┘ └──┬───┘└──┬───┘
   │        │         │         │         │         │        │       │
   │        │         │         │    ┌────┴─────┐   │        │       │
   │        │         │         │    │Research  │   │        │       │
   │        │         │         │    │Engine    │   │        │       │
   │        │         │         │    │:8008     │   │        │       │
   │        │         │         │    └──────────┘   │        │       │
   │        │         │         │    ┌──────────┐   │        │       │
   │        │         │         │    │Event     │   │        │       │
   │        │         │         │    │Intel.    │   │        │       │
   │        │         │         │    │:8010     │   │        │       │
   │        │         │         │    └──────────┘   │        │       │
   ▼        ▼         ▼         ▼         ▼         ▼        ▼       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Shared Infrastructure                            │
│   PostgreSQL 16 (port 5432)  ·  Redis 7 (port 6379)                │
│   Model files (/data/models/)                                       │
└─────────────────────────────────────────────────────────────────────┘
```

All 11 backend services are peers reached directly by the gateway — none of them sit
"downstream" of another in a pipeline sense at the network level. The signal-generation
*data* flow (market-data → signal-engine → ml-prediction → event-intelligence enrichment) is
a real dependency chain, but it happens via direct service-to-service HTTP calls, not through
a shared queue or a fixed pipeline order enforced by the gateway.

---

## 2. Service Inventory

| Service | Port | Key Responsibility |
|---------|------|--------------------|
| api-gateway | 8000 | Reverse proxy, JWT auth, admin-role enforcement. Only service exposed externally. |
| market-data | 8001 | OHLCV ingestion, live prices, alerts (12 types), paper trading engine, broker integration, scheduler (56 jobs) |
| technical-analysis | 8002 | Support/resistance, trendlines, Fair Value Gaps, chart patterns, accumulation/distribution |
| ml-prediction | 8003 | XGBoost/LightGBM/RandomForest ensemble training, Optuna tuning, cross-symbol meta-model |
| ranking-engine | 8004 | K-Score composite (6 weighted sub-scores), screener |
| signal-engine | 8005 | BUY/HOLD/WAIT/SELL signal generation, self-tuning calibration, outcome tracking |
| strategy-engine | 8006 | Rule-DSL backtester, custom strategy builder |
| portfolio-optimizer | 8007 | Mean-Variance / Risk Parity / HRP / AI portfolio allocation |
| research-engine | 8008 | AI research reports (Claude/DeepSeek), AI chat proxy |
| decision-engine | 8009 | 13-branch entry scoring gate for paper trading (see Section 5 of `FUNCTIONAL_OVERVIEW.md`) |
| event-intelligence | 8010 | Insider trades, congress trades, earnings, macro (FRED/FOMC) events, catalyst scoring |
| frontend | 3000 | All UI pages (46 real pages), SWR data fetching, chart rendering |

Port map verified directly against each service's Dockerfile `EXPOSE`/uvicorn `--port`
argument — this is the single source of truth if this doc and any container config ever
disagree again.

---

## 3. Data Layer

### PostgreSQL 16 — Real Tables (41 total)

Every table below is a real `class X(Base): __tablename__ = "..."` in `shared/db/models.py`,
confirmed by direct grep — not a curated/approximate list.

```
users                    prices                   signal_alerts
stocks                   indicators               push_subscriptions
signals                  rankings                 user_positions
strategies               backtests                position_trades
portfolios               portfolio_holdings       user_cash
watchlists               watchlist_items          app_notifications
price_alerts             trade_journal            signal_outcomes
trade_plans              broker_connections       paper_portfolios
paper_trades             paper_equity_curve       fundamentals
economic_events          earnings_events          insider_transactions
congress_trades          institutional_holdings   institutional_transactions
political_events         stock_connect_flows      catalyst_scores
sec_filings              hk_connect_flows         fundamentals_snapshot
tune_history             cape_readings            volume_area_levels
sector_rotation_snapshots
```

### Redis 7 — Key Cache Patterns (with real TTLs)

| Key Pattern | TTL | Content |
|-------------|-----|---------|
| `stockai:live_prices` | 90s | Bulk live price array |
| `stockai:avg_volume` | 6h | 20-day avg volume per symbol |
| `conv_gate:{symbol}:{style}` | 24h | Conviction gate pass/fail state |
| `scheduler:job:{name}` | 14d | Job health status (last_run, duration, error) |
| `stockai:sector_rotation` | 3d | Sector K-Score momentum map |
| `stockai:watchdog:{STYLE}:threshold` | 7d | Emergency-tightened threshold override |
| `stockai:premarket_gappers` | short (const) | Cached premarket gapper list |
| `stockai:overnight_futures` | short (const) | Cached overnight futures snapshot |
| `stockai:morning_digest:*` / `stockai:premarket_brief:{uid}:{market}:{date}` | 20h | Per-user digest send-dedup |
| `stockai:earnings_remind:*` / value-area alert dedup | 26h | Per-symbol alert dedup |
| position-drawdown alert dedup | 24h | Per-position alert dedup |
| `stockai:lock:{job_name}` | varies | Distributed execution locks (one per scheduled job that can't safely overlap) |
| `stockai:admin:claude_api_key` / `deepseek_api_key` | — | Admin-configured AI provider keys |
| `stockai:fear_greed`, `stockai:market_pulse`, `stockai:market_overview`, `stockai:market_breadth` | varies | Market-wide snapshot caches |
| `signals:cache:*` | scanned/invalidated | Expensive signal-engine endpoint results |
| `ps:shadow:pending` / `ps:shadow:resolved` | list, no TTL | Position-scaling shadow-mode verdict log |
| `auth:blacklist:{jti}` | keyed by logout | JWT revocation list |

---

## 4. Signal Generation Pipeline

```
Every 5 min (market hours, both US and HK) or on-demand:

  yfinance OHLCV
       │
       ▼
  market-data/ingestion.py
  → validate_ohlcv() (zero-volume filter, adj_close)
  → DB: prices table
       │
       ▼
  signal-engine/generators/signals.py  generate_all_signals(symbol)
  │
  ├─ _ta_score()          4-pillar TA score: TREND / MOMENTUM / VOLUME / STRUCTURE
  ├─ _weekly_technicals() Weekly RSI/trend/MACD alignment gate (SWING/LONG only; skipped for GROWTH)
  ├─ _sr_context()        Support/resistance zone awareness (delegates to technical-analysis)
  ├─ _fetch_relative_strength()  RS vs sector ETF
  ├─ _pullback_recovery() Dip + volume-confirmed reversal detector
  ├─ _fetch_patterns_from_ta()   Cup/handle, double-bottom, flag, triangle (from technical-analysis)
  ├─ _fetch_analyst_momentum()   Upgrade/downgrade 7d momentum
  ├─ _fetch_news_sentiment()     Claude Haiku (or VADER fallback) via market-data's
  │                              /stocks/{symbol}/news/sentiment (sources: Yahoo Finance
  │                              News, Google News RSS — 15-60min typical latency)
  │
  ├─ ml-prediction ensemble (per symbol, per horizon)
  │   ├─ XGBoost      (0.30 nominal weight)
  │   ├─ LightGBM     (0.45 nominal weight)
  │   ├─ RandomForest (0.25 nominal weight)
  │   │   (weights renormalize over whichever models are available/not OOS-suppressed;
  │   │    each individually calibrated via LogisticRegression or IsotonicRegression)
  │   └─ Cross-symbol meta-model (0.15 blend: prob*0.85 + meta_prob*0.15), AUC-gated,
  │       retrained monthly
  │
  ├─ fused = ml_w * ml_prob + (1 - ml_w) * ta_prob
  │   └─ ml_w derivation (NOT a simple linear formula):
  │        AUC < 0.50           → raw_w = 0.0  (near-random model, TA-only)
  │        0.50 ≤ AUC < 0.55    → raw_w ramps 0.0 → 0.20
  │        AUC ≥ 0.55           → raw_w = clip(0.20 + (AUC-0.55)/0.15 * 0.55, 0.20, 0.75)
  │        Then: capped by per-style ml_weight_cap, floored by an AUC-scaled
  │        ml_weight_floor, and discounted 25-50% further if ML and TA disagree
  │        by more than 0.25-0.35 (ml_ta_conflict)
  │
  ├─ Regime-tiered buy_threshold (per style, per regime — see Section 3 of
  │   FUNCTIONAL_OVERVIEW.md for the full 4-style x 4-regime table; e.g. SWING bull=0.72,
  │   NOT a flat/simple value — this is the single most consequential threshold in the system)
  ├─ Earnings compression (per-style dict keyed by days-to-earnings, e.g. SWING:
  │   {2:0.65, 5:0.85, 10:0.95}; SHORT/LONG have NO earnings compression at all)
  ├─ News sentiment compression (per-style dict, e.g. SWING: {25:0.75, 35:0.85} i.e.
  │   -25%/-15%; SHORT/LONG have none)
  ├─ Options flow adjustment (C/P ratio-based sentiment nudge)
  ├─ Breadth compression (style-specific: SHORT/SWING 0.90, LONG 0.92, GROWTH 0.95 —
  │   applied only when fused > 0.5, i.e. bullish-direction-only)
  │
  ├─ _apply_style_signal()  → BUY / HOLD / WAIT / SELL
  │   └─ SELL threshold is a FLAT 0.35 for all styles/regimes (no regime tiers exist
  │      for SELL, unlike BUY — a known, documented asymmetry, not an oversight)
  │
  └─ Catalyst scores (insider_score, congress_score) do NOT adjust fused_prob at all
      in signal-engine — they only feed decision-engine's separate conviction score
      (see Section 5 of FUNCTIONAL_OVERVIEW.md, Layer "catalyst_insider"/"catalyst_congress")

  → DB upsert: signals table (ON CONFLICT DO UPDATE, one row per stock/horizon/day)
  → Redis: signals:cache invalidated
```

---

## 5. Paper Trading Engine

```
paper_trading_step()  [runs every 5 min, distributed Redis lock per portfolio]
│
├─ _fetch_market_regime()   5-state: bull/neutral/choppy/risk_off/bear
│   └─ SPY/QQQ EMA + VIX thresholds
│
├─ _monitor_positions()     For each open paper trade:
│   ├─ Fetch live price
│   ├─ Check hard stop / take-profit / time stop / signal exit
│   ├─ Update trailing stop (highest_price × regime_trail_adj)
│   └─ Position scaling gate (SHADOW MODE ONLY — logs a verdict, never places a real
│       scale order; all 6 build phases are done but no "live" mode exists yet)
│
└─ _scan_for_entries()      [skipped in bear regime or daily loss limit hit]
    ├─ Query BUY signals (confidence ≥ min_confidence × 0.9)
    ├─ Filter: max_positions, sector cap, open risk cap, daily entry cap
    ├─ _should_enter() — a large, many-dimension scoring function (NOT a small fixed
    │   9-factor list — it has grown to ~30 dimensions across R:R, ML confidence, RSI,
    │   MACD, regime, K-Score, volume, extended-move guards, market-hours/time-of-day
    │   gates, correlation-with-open-positions, and more — see
    │   services/market-data/src/services/paper_trading_engine.py directly, this
    │   function is too large to summarize accurately in a fixed table)
    │
    ├─ decision-engine /decide/{symbol}  [external gate, fail-open if unreachable]
    │   └─ 13-branch additive scorer — see Section 5 of FUNCTIONAL_OVERVIEW.md for
    │      the complete, exact breakdown (NOT a clean "7-layer" model despite the
    │      code's own docstring label)
    │
    └─ Position sizing:
        shares = (equity × risk_per_trade_pct × [multiple regime/confidence/research
                  multipliers]) / (live_price - stop_price)
        (paper_trading_engine.py's real sizing formula and decision-engine's own
         sizer.py are DELIBERATELY DIFFERENT and not meant to be read as equivalent —
         sizer.py's own module docstring states it is preview/display-only)
```

---

## 6. Self-Improvement Loop

```
Weekly (Sunday 14:00 America/Los_Angeles):
  _weekly_full_refresh()
  │
  ├─ Force re-ingest 3 years daily bars (all symbols)
  ├─ Refresh fundamentals batch
  ├─ POST /ml/tune_all                        → Optuna, background (~2-4h)
  ├─ POST /signals/calibrate_ta_weights        → logistic regression on outcomes
  ├─ POST /signals/calibrate_conviction_weights
  ├─ POST /signals/calibrate_ml_weight
  ├─ POST /signals/outcomes/calibrate/apply    → dynamic buy thresholds
  ├─ POST /signals/tune_style_profiles         → per-style gate params
  ├─ POST /signals/tune_strategy               → joint buy_threshold x ml_weight_cap
  │                                              sweep per horizon (added to this
  │                                              weekly job 2026-07-27 — previously
  │                                              built but never scheduled)
  ├─ calibrate_entry_weights()                 → paper trade logistic regression
  ├─ calibrate_min_rr_ratio()
  └─ promotion_gate.evaluate_and_record()      → writes tune_history rows

Separate weekly jobs (own schedule, not part of _weekly_full_refresh):
  Sun 15:00 PT   db_purge_weekly
  Sun 16:00 ET   sector_rotation_weekly
  Sun 16:30 ET   fundamentals_snapshot_weekly
  Sun 17:00 ET   watchlist_auto_rotation_weekly
  Sun 04:00 UTC  position_scaling_gate_weekly_retrain
  Sun 04:30 UTC  position_scaling_gate_weekly_drift_check

Monthly (1st Sunday of month):
  03:00 UTC   meta_model_monthly_retrain   → cross-symbol XGBoost meta-model (AUC-gated)
  04:00 UTC   backfill_realized_ev_monthly → did a promoted change actually help live?

Promotion Gate (every tuning attempt, every mechanism above):
  new_validation_ev > baseline_ev  AND  worst_trade_pct acceptable
  → promoted=True  → write tune_history row
  → promoted=False → keep old params, still write tune_history row (never silently discarded)
```

---

## 7. K-Score Composite

```
K-Score (0-100) = weighted average of 6 sub-scores (NOT 5 — a relative-strength factor
exists alongside the other 5):

  Technical         (22%)  SMA50/SMA200 cross state (1/3 weight), RSI(14) asymmetric
                            "optimal zone 50-70" curve (0.4 weight), ADX-based ±10 boost.
                            No MACD, OBV, or Bollinger Bands anywhere in this formula.
  Momentum          (23%)  0.5×(3-month return) + 0.3×(6-month return) + 0.2×(1-month
                            return), mapped to a 0-100 score. No 12-month return term;
                            relative-strength-vs-sector is its own separate factor below,
                            not part of Momentum.
  Value             (13%)  Passed in from an EXTERNAL caller (sector-relative PE/PB/
                            EV-EBITDA percentiles) — not computed inside kscore.py at all.
  Growth            (14%)  Also passed in externally (revenue growth, earnings growth,
                            ROE, EPS beat rate) — same as Value, no formula lives in
                            kscore.py itself.
  Volatility        (18%)  60-day realized volatility of daily returns only. No ATR%,
                            beta, max-drawdown, or Sharpe-proxy term exists in this file.
  Relative Strength (10%)  Also passed in externally — stock's own return vs. its
                            sector ETF's return over the same window.

  Any factor that's None (insufficient data) is EXCLUDED from the composite entirely
  and the remaining weights are renormalized — never proxied or defaulted to neutral.
```

---

## 8. Event Intelligence Catalyst Score

```
catalyst_score = 0.35 × insider_score
               + 0.30 × earnings_score
               + 0.25 × congress_score
               + 0.10 × economic_score

insider_score  ∈ [-100, 100]  (SEC Form 4 — net buy $ weighted by role)
congress_score ∈ [-100, 100]  (STOCK Act — net buy $ weighted by chamber)
earnings_score ∈ [0, 100]     (EPS surprise %, beat rate, strength score)
economic_score ∈ [0, 100]     (FRED/FOMC macro event impact)

catalyst_score is clamped to [0, 100] before storage. insider_score and congress_score
are ALSO stored separately (signed, unclamped) — these signed values are what
decision-engine's scorer.py actually reads (catalyst_insider/catalyst_congress layers),
NOT the blended, clamped catalyst_score. Signal-engine's own fused_prob never reads
either the blended score or the signed sub-scores directly.
```

---

## 9. API Gateway Route Table

Only `api-gateway` (port 8000) is exposed externally; Nginx proxies `lausing.com` →
`localhost:8000`. Every other service is Docker-internal only. Full `_ROUTES` prefix map
(`services/api-gateway/src/api/proxy.py`):

| Prefix(es) | Target service |
|---|---|
| `stocks`, `admin`, `watchlist(s)`, `auth`, `alerts`, `signal-alerts`, `journal`, `positions`, `app-notifications`, `board`, `paper-portfolio`, `broker`, `push`, `rl-agent` | market-data |
| `ta` | technical-analysis |
| `ml` | ml-prediction |
| `rankings` | ranking-engine |
| `signals` | signal-engine |
| `strategies`, `backtest(s)` | strategy-engine |
| `portfolio`, `portfolio-risk` | portfolio-optimizer |
| `research`, `ai` | research-engine |
| `decide` | decision-engine |
| `events`, `catalyst` | event-intelligence |

Only `auth` is public (no JWT required); everything else requires a valid JWT, and the
`admin` prefix additionally requires the admin role, enforced at the gateway itself before
proxying. POST requests to `research`/`ai` get a 240s proxy timeout (long-running LLM
calls); everything else gets 120s.

---

## 10. Authentication & Multi-Tenancy

```
JWT (HS256)
  → every service validates via shared common/jwt_auth.py
  → service-to-service calls use a long-lived scheduler JWT (365d, auto-refresh)
  → logout blacklist: Redis auth:blacklist:{jti} + in-memory fallback dict

User isolation:
  watchlist_items.user_id  → each user sees only their stocks
  signal_alerts.user_id    → per-user alert subscriptions
  trade_plans.user_id      → per-user Trade Board
  paper_portfolios         → per-user (admin creates, user manages)

Roles:
  ADMIN  → user management, engine controls, calibration triggers, impersonation
  USER   → full trading features, own watchlist/positions/alerts
```

---

## 11. Infrastructure (Production)

Single EC2 t3.medium instance running all 11 backend services + frontend + Postgres +
Redis as Docker containers (`docker-compose.yml`), NOT AWS ECS Fargate/RDS/ElastiCache —
this is a much simpler single-host deployment than a prior version of this doc claimed.
Nginx on the host handles HTTPS termination (Let's Encrypt) and reverse-proxies to
`api-gateway` on `localhost:8000`. Deploy is git-based: `git pull` + either `docker cp` +
`restart` (backend hotfix) or a full `docker build` (frontend, since Next.js bakes its
build into the image).

---

## 12. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| One signal row per (stock, horizon, day) with ON CONFLICT DO UPDATE | Prevents table bloat; intraday refreshes overwrite rather than append |
| Redis distributed locks on every scheduled job that touches shared state | Prevents double-processing races between concurrent scheduler runs |
| Catalyst scores stored signed (insider/congress) separately from blended score | Blended score is clamped [0,100] for display; signed sub-scores needed for decision-engine's own additive scoring, which can go negative |
| Promotion gate on every calibration | No parameter change ships without beating baseline on a walk-forward validation slice |
| tune_history table for every attempt | "We tried X and it didn't help" is always auditable without log archaeology |
| adj_close for all feature computation | Prevents 50% apparent drops on splits corrupting momentum/SMA features |
| Point-in-time fundamentals join (merge_asof) | Prevents lookahead bias from broadcasting today's fundamentals to historical training rows |
| Atomic model file writes (tempfile + os.replace) | Prevents serving a half-written model bundle during weekly retrain |
| AUC-gated meta-model promotion | New meta-model only replaces deployed bundle if AUC is not strictly worse |
| decision-engine and paper_trading_engine's `_should_enter()` are two INDEPENDENT scoring implementations | decision-engine acts as an external, fail-open second opinion; paper_trading_engine's own gate is the ground truth and never blocked by decision-engine's unavailability. They are being incrementally reconciled (T232-DL-DUALSCORER-DEBT) but are not, and were never meant to be, identical |

---

## What changed from the last version of this doc (2026-07-23)

A review pass found the previous version had: 4 of 9 listed ports wrong; 3 entire backend
services omitted (technical-analysis, strategy-engine, portfolio-optimizer); event-
intelligence misplaced in the system diagram as if downstream of shared infra rather than a
peer service; a linear `ml_weight` formula that doesn't match the real piecewise ramp;
SWING's bull buy_threshold claimed as 0.62 when it's actually 0.72; ML ensemble weights
claimed 40/35/25 when the real code uses 30/45/25; K-Score claimed as 5 factors at
25/25/20/15/15 when it's actually 6 factors at 22/23/13/14/18/10; several K-Score inputs
(MACD, OBV, Bollinger Bands, 12-month return, a "falling-knife gate") that don't exist in
the real file at all; catalyst scores claimed to nudge `fused_prob` directly when they
never do; and an AWS ECS/RDS/ElastiCache infrastructure description that doesn't match the
actual single-EC2-instance Docker Compose deployment. This version was rewritten with every
number pulled directly from the executing code path rather than paraphrased from a
docstring or a prior summary — treat any future discrepancy as a bug in this doc to
re-verify, not in the code.
