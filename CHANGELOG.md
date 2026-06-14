# Changelog

All notable changes are documented here, newest first.

---

## [2026-06-14] — Morning Digest Email; Pattern Signals + Email Alerts; Research NetworkError Fix

### Features
- **Daily morning digest email**: Automated email at 9:00 AM ET every weekday to all users with email configured. Contains: market regime (SPY/VIX state), top 5 ranked opportunities by K-Score with signal + ML%, open paper positions with yesterday's close P&L and stop distance, and any pattern alerts fired in the last 28 hours. Admin can trigger manually via `POST /admin/send-morning-digest`. Registered as APScheduler job `morning_digest` (job count now 14).
- **Live Pattern Signals widget on stock detail**: "Live Pattern Signals" badge strip appears automatically when one or more bullish technical patterns are detected in the last 3–5 sessions. Patterns detected: Golden Cross, MACD Bullish Cross, RSI Oversold Bounce, Double Bottom (W-pattern), and Volume Breakout. Data comes from a new `GET /signals/{symbol}/patterns` endpoint in the signal-engine.
- **Pattern alert email subscriptions**: Four new alert conditions added to the stock detail alert dropdown under "Pattern Signals": MACD Bullish Crossover, RSI Oversold Bounce (crosses 30), Double Bottom, and Volume Breakout. These fire a one-shot email the day the pattern is first detected, using the same Gmail/SES infrastructure as price alerts.
- **DB migration**: `alertcondition` PostgreSQL enum extended with `macd_bullish_cross`, `rsi_oversold_bounce`, `double_bottom`, `breakout` values. Run `scripts/migrations/add_pattern_alert_conditions.sql` on existing deployments.

### Bug Fixes
- **Research report NetworkError on EC2**: Nginx `proxy_read_timeout` for `/api/research/` was 150s (shared with all API routes). Claude can take up to 90s; combined with data-gathering latency this could exceed the limit. Added a dedicated `/api/research/` location block with `proxy_read_timeout 200s` in `docs/DEPLOY_EC2.md`. Apply to live server with: `sudo tee /etc/nginx/conf.d/stockai.conf` (see DEPLOY_EC2.md) then `sudo nginx -t && sudo systemctl reload nginx`.
- **Research NetworkError local (Next.js proxy)**: `experimental.proxyTimeout` in `next.config.js` raised from 120,000ms to 200,000ms so the built-in rewrite proxy doesn't cut off long-running research requests.

### Schema / API
- `shared/db/models.py`: Added 4 new `AlertCondition` enum values: `MACD_BULLISH_CROSS`, `RSI_OVERSOLD_BOUNCE`, `DOUBLE_BOTTOM`, `BREAKOUT`
- `services/signal-engine/src/api/routes.py`: New `GET /signals/{symbol}/patterns` endpoint — detects active patterns from 260 days of daily OHLCV price history; cached by SWR for 5 min in the frontend
- `services/market-data/src/services/scheduler.py`: `check_technical_alerts()` extended with 4 new pattern branches; volume data now fetched alongside close prices for breakout detection
- `scripts/migrations/add_pattern_alert_conditions.sql`: One-time migration for existing databases

### Files Changed
- `shared/db/models.py`
- `services/signal-engine/src/api/routes.py`
- `services/market-data/src/services/scheduler.py`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/stock/[symbol].tsx`
- `frontend/next.config.js`
- `docs/DEPLOY_EC2.md`
- `scripts/migrations/add_pattern_alert_conditions.sql` (new)
- `services/market-data/src/services/email_service.py` (morning digest renderer)
- `services/market-data/src/services/paper_trading_engine.py` (get_last_regime())
- `services/market-data/src/services/scheduler.py` (morning digest job)
- `services/market-data/src/api/admin.py` (manual trigger endpoint)

---

## [2026-06-10] — Per-horizon signal alerts, consensus indicator, admin health, Add to Radar

### Features
- **Per-horizon signal alerts**: `signal_alerts` table now stores a `horizon` column (SHORT/SWING/LONG/GROWTH); unique constraint changed from `(user_id, symbol)` to `(user_id, symbol, horizon)` so users can subscribe to each timeframe independently
- **Require consensus setting**: New `require_consensus` Boolean on `SignalAlert`; when enabled, an alert only fires if ≥2 of 4 horizons agree on the same new signal direction
- **4-horizon consensus indicator on stock detail**: Stock detail page fetches all 4 horizons concurrently and displays a 2×2 grid (signal + confidence per horizon) plus a consensus label (Strong bullish / Moderately bullish / Mixed / etc.)
- **Per-horizon alert rows on alerts page**: Alert subscription list replaced with 4 per-symbol rows; each row shows its horizon badge, mode toggle (All/Buy/Sell), and a ⚡ Consensus / Any toggle
- **Add to Radar button on Opportunities**: Each stock card has a 📡 button that adds the stock to a "Radar" watchlist (creates it automatically on first use); already-added stocks show as checked
- **Admin health — SIGNAL REFRESH HEALTH section**: BUY/SELL/WAIT/HOLD distribution from SWING signals, bull/bear ratio, progress bar, fresh/stale counts, last US/HK refresh timestamps
- **Admin health — ML TRAINING HEALTH section**: Model quality card (Avg AUC, good/weak/overfit counts), Last Retrain card (US/HK post-close timestamps + pass/fail badge)

### Bug Fixes
- **AUC showing 0.000 in admin health**: ML trainer bundles store metrics as `"auc"` and `"cv_auc_mean"` but the `/ml/metrics` endpoint was reading `"test_auc"` and `"cv_auc"` — all 119 models returned null causing every row to be filtered out; fixed key names in `ml-prediction/src/api/routes.py`
- **SWR race condition in Opportunities**: `radarList` fetcher used `() => api.listWatchlist(radarList!.id)` (non-null assertion); if `radarList` was undefined at mount the call would throw; replaced with guard `() => radarList ? api.listWatchlist(radarList.id) : Promise.resolve([])`

### Schema / API
- `shared/db/models.py`: Added `horizon: Mapped[str]` and `require_consensus: Mapped[bool]` to `SignalAlert`
- `shared/db/session.py`: `_run_migrations()` adds both columns; drops old `signal_alerts_user_id_symbol_key` unique constraint; creates `idx_signal_alerts_user_symbol_horizon` unique index
- `services/market-data/src/api/signal_alerts.py`: New `SignalAlertCreate` (horizon, require_consensus), `SignalAlertUpdate` (partial), `SignalAlertOut`; `create_signal_alert` checks uniqueness on (user_id, symbol, horizon)
- `services/market-data/src/services/scheduler.py`: `check_signal_alerts()` now reads `alert.horizon` directly; fetches signals for all 4 horizons when `require_consensus=True`; consensus gate skips alert if <2 horizons agree

### Files Changed
- `shared/db/models.py`
- `shared/db/session.py`
- `services/market-data/src/api/signal_alerts.py`
- `services/market-data/src/services/scheduler.py`
- `services/ml-prediction/src/api/routes.py`
- `frontend/src/lib/api.ts`
- `frontend/src/pages/stock/[symbol].tsx`
- `frontend/src/pages/alerts.tsx`
- `frontend/src/pages/opportunities.tsx`
- `frontend/src/pages/admin-health.tsx`

---

## [2026-06-10] — Security hardening, P1 audit fixes, login improvements

### Security
- **Open redirect prevention**: `?next=` parameter in login and gate flows now validated with `startsWith('/')` guard; external redirect attempts silently fall back to `/`
- **Admin password out of source**: `_seed_admin()` in `shared/db/session.py` now reads from `ADMIN_PASSWORD` env var instead of literal plaintext; skips seeding entirely if env var is empty
- **CORS restriction**: All services now read `CORS_ORIGINS` env var (comma-separated) instead of hardcoding `allow_origins=["*"]`; defaults to `*` in dev

### Data Integrity
- **`alert_mode` migration**: `signal_alerts.alert_mode` column added via `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in `_run_migrations()` — existing prod DBs no longer crash on queries that reference this column
- **ORM model drift fixed**: Removed stale `UniqueConstraint("user_id", "stock_id")` from `WatchlistItem.__table_args__` that contradicted the actual DB schema and produced false alembic drift

### API / Routing
- **FastAPI path ordering**: `GET /outcomes/summary` moved before `GET /{symbol}` in signal-engine routes — was previously shadowed, returning a signal lookup for symbol `"outcomes"` instead of the summary endpoint
- **HTTP status codes**: `GET /stocks/{symbol}/dividends` and `GET /stocks/{symbol}/institutional` now raise `HTTPException(502)` on upstream failure instead of returning `200 OK` with an `"error"` key
- **Short squeeze dedup**: Added `ORDER BY stock_id, as_of ASC` to the short squeeze query; dict comprehension now reliably keeps the latest ranking per stock rather than whichever row happens to come last

### ML / Signal Engine
- **`calibrate_ta_weights` cross-validation**: Replaced in-sample accuracy metric with `cross_val_score(TimeSeriesSplit(n_splits=5))` to prevent reporting trivially overfitting accuracy; model still fits on full data for production weights
- **`calibrate_ml_weight` temporal split**: Weight selection now uses the older 70% of observations as calibration set and the newer 30% as validation; prevents picking weights that overfit a recent bullish window
- **Optuna deterministic seed**: `optuna.create_study` now passes `sampler=TPESampler(seed=42)` — ML weight tuning runs are now reproducible
- **`days_active` streak fix**: `prev_ts = ts` now assigned inside the streak loop; gap-break added (`if (prev_ts - ts).days > 2: reset active`) — streak count was previously inflated across scheduler outages and holidays

### Scheduler
- **`_weekly_full_refresh` error handling**: `_symbols_for('US') + _symbols_for('HK')` moved inside the `try` block so a DB failure records the job failure via `_record_job_status` rather than raising unhandled and silently skipping the weekly ingest

### Docker
- **Redis healthcheck**: Added `test: redis-cli ping, interval: 5s, retries: 5` so dependent services can use `condition: service_healthy`
- **Ordered startup**: `api-gateway depends_on` now uses `condition: service_healthy` for all 10 dependencies (postgres, redis, 8 backend microservices) — eliminates cold-boot 502s and proxy failures

### Frontend
- **Earnings panel states**: `earningsCalendar` SWR call now destructures `isLoading` and `error`; panel shows a loading row while fetching and a muted error note on failure instead of silently vanishing
- **Dead state removed**: `rConfirm` and `resetMsg` states removed from `login.tsx` (leftover from removed reset-password UI)
- **`useEffect` deps corrected**: `router.query` added to dependency array in login redirect effect

### Configuration
- `shared/common/config.py`: Added `admin_password: str = ""` and `cors_origins: str = ""` settings fields
- `.env.production` (gitignored): Add `ADMIN_PASSWORD=<value>` and `CORS_ORIGINS=https://lausing.com` on the EC2 server manually

### Commits
- `2453a40` — fix: P1 audit fixes batch — security, routing, ML, scheduler, Docker
- `e96d791` — Merge branch 'dev' into prod

---

## [2026-06-09] — Research Intelligence Engine, signal pipeline audit fixes

### Features
- Research Engine service (port 8008) — AI-generated research reports per symbol
- `/research/[symbol]` page + Board Research button on stock detail page
- Short interest tracker on stock detail page + signal squeeze boost (RES-1)
- Earnings This Week panel on Opportunities page (SCR-2)
- ML weight auto-calibration endpoint + UI apply button (UI-10 backend)

### Fixes
- Freshness indicator improvements, research cache quality, intraday stall fix

---

## [2026-06-04] — Improvements batch (Tier 2/3 complete)

Completed all Tier 2 and Tier 3 items across 6 sessions:
Support/Resistance context, ATR-based position sizing, drift detection, peer comparison panel, portfolio risk metrics, DCF valuation model. Walk-forward backtest deferred (~2 weeks).

---

## [2026-05-30] — ML training overhaul

- 22-feature set, Optuna hyperparameter tuning, TimeSeriesSplit CV metrics, early stopping
- `POST /ml/tune_all` for weekend batch jobs

---

## [2026-05-20] — Email price alerts

- Per-user price alerts via Gmail SMTP or AWS SES
- Alert checker runs every minute via scheduler
- UI on stock detail page

---

## [2026-05-06] — Watchlist improvements

- Watchlist picker, move-between-lists, rankings prices, full refresh with force ingest

---

## [2026-04-15] — Multi-user system

- JWT auth, user management, bcrypt password hashing
- Namespaced localStorage per user, admin settings section

---

## [2026-03-01] — HK timezone fix

- HK daily bars corrected to store with proper UTC offset
- Fix applied in `base.py`, `routes.py`, and DB migration

---
