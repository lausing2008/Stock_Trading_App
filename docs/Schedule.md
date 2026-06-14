# Scheduler Reference

All scheduled jobs are registered in
`services/market-data/src/services/scheduler.py` via APScheduler.
Times are local to each market's timezone unless noted otherwise.
DST transitions are handled automatically by APScheduler's `timezone` parameter.

---

## Job Summary (15 jobs total)

| Job ID | Trigger | Frequency | Purpose |
|--------|---------|-----------|---------|
| `us_open_burst` | Mon–Fri 09:25–09:45 ET | Every 5 min (5 fires) | US open — prices + rankings + signals |
| `us_intra` | Mon–Fri 10:00–15:00 ET | Every 5 min | US regular hours — prices + rankings + signals |
| `us_close_burst` | Mon–Fri 15:30–16:15 ET | Every 5 min (10 fires) | US close — prices + rankings + signals |
| `us_post_close` | Mon–Fri 16:30 ET | Once daily | US post-close — final bar + ML retrain |
| `hk_open_burst` | Mon–Fri 09:25–09:45 HKT | Every 5 min (5 fires) | HK open — prices + rankings + signals |
| `hk_intra` | Mon–Fri 10:00–11:55 + 13:00–15:00 HKT | Every 5 min (skip 12:00–13:00 lunch) | HK regular hours — prices + rankings + signals |
| `hk_close_burst` | Mon–Fri 15:30–16:15 HKT | Every 5 min (10 fires) | HK close — prices + rankings + signals |
| `hk_post_close` | Mon–Fri 16:30 HKT | Once daily | HK post-close — final bar + ML retrain |
| `us_5m_intraday` | Mon–Fri 09:30–15:25 ET | Every 5 min | US intraday 5-min bars + paper trade monitor |
| `hk_5m_intraday` | Mon–Fri 09:30–15:25 HKT (skip 12:00–13:00) | Every 5 min | HK intraday 5-min bars + paper trade monitor |
| `morning_digest_us` | Mon–Fri 09:00 ET | Once daily | US morning digest email |
| `morning_digest_hk` | Mon–Fri 08:55 HKT | Once daily | HK morning digest email |
| `weekly_full_refresh` | Sunday 14:00 PST | Weekly | Force re-ingest 3 years + tune_all + calibrate weights |
| `price_alert_check` | Every 1 minute | 1440×/day | Check price threshold alerts + pattern alerts |
| `db_purge_weekly` | Sunday 15:00 PST | Weekly | Delete 5m bars >90 days + signal_outcomes >1 year |

---

## Detailed Job Descriptions

---

### Market Refresh Jobs (`us_open_burst`, `us_intra`, `us_close_burst`, `hk_*` equivalents)

Each call to `_refresh_market(market, post_close=False)` runs four stages in order:

**Stage 1 — Ingest** (`ingest_universe`)
- Downloads latest daily OHLCV bars from yfinance using a single batch call
- Writes/updates `prices` table (timeframe `D1`)
- Failure is isolated — stages 2–4 still run using the last good bar

**Stage 2 — Rankings + Signals**
- `POST /rankings/refresh?market={market}` — ranking-engine recomputes K-Scores for all active stocks in this market
- `POST /signals/refresh?market={market}` — signal-engine regenerates BUY/HOLD/WAIT/SELL signals for all 4 horizons (SHORT / SWING / LONG / GROWTH) for this market
- Post-close additionally fires:
  - `POST /ml/train_all` — ml-prediction retrains XGBoost models on the day's data (nightly)
  - `POST /signals/outcomes/evaluate` — evaluates BUY/SELL signals whose hold window has expired

**Stage 3 — Alerts**
- `check_signal_alerts()` — checks all `SignalAlert` subscriptions; sends email when signal transitions match subscribed direction
- `check_technical_alerts()` — checks price threshold alerts + 4 new pattern conditions (MACD cross, RSI bounce, double bottom, breakout)

**Stage 4 — Paper Trading** (US only)
- `paper_trading_step()` — scans for new entries, monitors open positions (trailing stops, profit targets, HOLD stall exits)
- Post-close additionally: `snapshot_equity_curve()` — writes daily equity point to `paper_equity_curve`

**Effective US firing schedule:**

| Phase | Times (ET) | Count |
|-------|-----------|-------|
| Open burst | 09:25 09:30 09:35 09:40 09:45 | 5 |
| Regular hours | 10:00 10:05 … 14:55 15:00 | 61 |
| Close burst | 15:30 15:35 … 16:10 16:15 | 10 |
| Post-close | 16:30 | 1 |
| **Total per day** | | **77** |

**Effective HK firing schedule:**

| Phase | Times (HKT) | Count |
|-------|------------|-------|
| Open burst | 09:25 09:30 09:35 09:40 09:45 | 5 |
| Regular hours (AM) | 10:00 10:05 … 11:55 | 24 |
| Lunch break | 12:00–13:00 | skipped |
| Regular hours (PM) | 13:00 13:05 … 15:00 | 25 |
| Close burst | 15:30 15:35 … 16:10 16:15 | 10 |
| Post-close | 16:30 | 1 |
| **Total per day** | | **65** |

---

### 5-Minute Intraday Jobs (`us_5m_intraday`, `hk_5m_intraday`)

Runs `_refresh_5m(market)` every 5 minutes during market hours.

**What it does:**
1. `ingest_universe(symbols, "5m")` — downloads 5-minute OHLCV bars from yfinance → `prices` table (`M5` timeframe)
2. `paper_trading_step()` — monitors open paper positions with fresh intraday prices so stops, trailing stops, and exit conditions are checked every 5 minutes (not just at daily-bar refresh points)

**Note:** Rankings and signals are NOT updated here — they use daily bars only.

**US window:** 09:30–15:25 ET (6.5 hours × 12 bars/hour = 78 fires/day)
**HK window:** 09:30–11:55 + 13:00–15:25 HKT (5.5 hours × 12 = 66 fires/day, lunch excluded)

---

### Morning Digest Jobs (`morning_digest_us`, `morning_digest_hk`)

Runs `send_morning_digest(market)` once per market day, ~30 minutes before each market opens.

**Timing:**
- **US:** 09:00 ET (30 min before NYSE open at 09:30)
- **HK:** 08:55 HKT (30 min before HKEX open at 09:25)

**Email contents:**

| Section | Details |
|---------|---------|
| Market Regime | SPY price, VIX level, bull/bear/choppy/risk-off/neutral state (from last paper trading step) |
| Top 5 SWING | Highest K-Score stocks for the current market with SWING signal + ML bullish% — BUY-signal stocks shown first |
| Top 5 GROWTH | Same but for GROWTH horizon — higher-volatility momentum picks |
| Open Positions | All open paper trades in this market's stocks — entry vs yesterday's close, P&L%, stop distance, days held |
| Pattern Alerts | Pattern conditions fired in the last 28 hours: golden cross, MACD cross, RSI bounce, double bottom, breakout |

**Recipients:** All `User` records where `email` is not null.

**Manual trigger:** `POST /admin/send-morning-digest?market=US` or `?market=HK` (admin only).

---

### Weekly Full Refresh (`weekly_full_refresh`)

**Trigger:** Every Sunday at 14:00 PST (= 22:00 UTC, ~11 hours before HK Monday open at 09:25 HKT = 01:25 UTC Monday)

**What it does (sequentially):**
1. `ingest_universe(all_symbols, "1d", force=True)` — deletes all daily bars and re-fetches 3 years for every active US + HK stock; ensures no yfinance data drift from the week
2. `POST /rankings/refresh` (no market filter — all stocks)
3. `POST /signals/refresh` (no market filter)
4. `POST /ml/tune_all` (fire-and-forget) — Optuna hyperparameter tuning, 60 trials/symbol; runs ~2–4 hours inside ml-prediction container; best params saved to per-symbol JSON for Monday's daily retrains
5. `POST /signals/calibrate_ta_weights` (fire-and-forget, ~30s) — fits logistic regression on TA features vs signal correctness; writes `ta_weights.json`
6. `POST /signals/calibrate_conviction_weights` (fire-and-forget, ~30s) — fits logistic regression on conviction layer boolean flags; writes `conviction_weights.json`

**Why Sunday?** Markets are closed; no interference with live refresh jobs. The 14:00 PST slot gives ~11 hours of margin before HK Monday open, enough for tune_all to complete.

---

### Price Alert Checker (`price_alert_check`)

**Trigger:** Every 1 minute, all day every day (no market-hours restriction).

**What it does:**
- Reads all active `PriceAlert` records from DB
- Fetches live prices via yfinance for all alert symbols (batched)
- Evaluates each alert condition:
  - **Price above/below threshold** — fires when current price crosses the user-set target
  - **New 52-week high/low** — fires when price exceeds the rolling 52-week high or falls below the low
  - **Golden cross / Death cross** — fires when EMA50 crosses EMA200 (detected from last 5 daily bars)
  - **MACD bullish cross** — fires when MACD line crosses above signal line (last 3 bars)
  - **RSI oversold bounce** — fires when RSI(14) crosses above 30 from below (last 3 bars)
  - **Double bottom** — fires when W-pattern detected in last 60 bars (two troughs within 3%, 5%+ peak between)
  - **Volume breakout** — fires when close > 20-day high AND volume ≥ 1.4× 20-day average volume
- Sends email via Gmail SMTP or AWS SES (configured via `EMAIL_PROVIDER` env var)
- Marks alert as `triggered=True` after firing (one-shot — no repeat)

**Rate-limiting:** Consecutive email failures per alert are counted (max 3 retries); after the limit the alert state advances to prevent an infinite retry loop.

---

### Database Purge (`db_purge_weekly`)

**Trigger:** Every Sunday at 15:00 PST (= 23:00 UTC, 1 hour after weekly full refresh starts)

**What it deletes:**
- `prices WHERE timeframe='M5' AND ts < NOW() - INTERVAL '90 days'` — 5-minute intraday bars older than 90 days (~3.5M rows/year growth rate)
- `signal_outcomes WHERE ts_evaluated < NOW() - INTERVAL '365 days'` — signal outcome records older than 1 year

**Why these tables?** 5m bars accumulate fast and have no analytical value after 90 days (signals use daily bars only). Signal outcomes rolling window of 1 year is sufficient for accuracy tracking and factor analysis.

---

### One-Shot Startup Job (`signal_alert_startup`)

**Trigger:** 60 seconds after process startup (one-shot, does not repeat)

**What it does:** Runs `check_signal_alerts()` once to repopulate Redis conviction data after a container restart. Signal alert state is cached in Redis; a fresh container has an empty cache and the first alert check may mis-fire until Redis is warm.

---

## Signal Alert Email Conditions

Emails are sent when a stock's signal transitions between states and the user has a `SignalAlert` subscription for that stock + horizon.

| Transition | Email type |
|-----------|-----------|
| Any → BUY | Signal Alert (bullish) |
| BUY → HOLD, BUY → WAIT, BUY → SELL | Signal Weakening (bearish) |
| HOLD → SELL, WAIT → SELL | Signal Alert (bearish) |

Emails include: signal confidence, ML probability, key TA indicators, earnings proximity, insider activity, conviction layer checklist, and a game plan (entry levels, stop, target) for BUY transitions.

---

## Potential Improvements / Known Issues

| # | Issue | Status |
|---|-------|--------|
| 1 | DB purge at 15:00 PST fires 1 hour into the weekly full refresh (started 14:00 PST). The purge deletes `prices M5` and `signal_outcomes` — not `prices D1` — so there is no data race, but moving purge to 13:00 PST (before refresh) would be cleaner. | Low priority |
| 2 | `check_signal_alerts` runs every time `_refresh_market` fires (up to 77 times/day for US). Each call queries all `SignalAlert` rows. With many users this is fine; at scale a Redis-based dirty flag would avoid redundant full scans between signal refreshes. | Future |
| 3 | HK public holiday detection uses a hardcoded list in `_is_hk_holiday()`. This list needs updating each year. | Annual maintenance |
| 4 | Paper trading `paper_trading_step()` runs on every `_refresh_market` (US only) AND every `_refresh_5m` (every 5 min). This is intentional — the daily-bar path does entry scans, the 5m path does position monitoring only. The logs distinguish them via `paper_trading` vs `paper_trading_5m_us` job keys. | By design |
