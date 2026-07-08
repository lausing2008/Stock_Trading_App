# Market Data Service — Domain Knowledge & Coding Standards

The largest and most complex service. Central hub for price ingestion, paper trading, scheduling,
authentication, email alerts, and broker integration. Line counts below are approximate and grow
every session — if precision matters, `wc -l` the actual file rather than trusting this doc.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Price ingestion (yfinance, Polygon, Alpha Vantage) | `services/ingestion.py`, `adapters/` |
| Paper trading engine — entry/exit/monitoring | `services/paper_trading_engine.py` |
| System scheduler — heartbeat for all services | `services/scheduler.py` |
| Paper portfolio CRUD + toggle | `api/paper_portfolio.py` |
| Signal alert emails | `services/email_service.py`, `api/signal_alerts.py` |
| JWT auth — login/logout/user management | `api/auth.py` |
| Watchlists, journals, positions | `api/watchlist.py`, `api/journal.py`, `api/positions.py` |
| News feed aggregation | `api/news.py` |
| Broker integration (E*Trade, manual) | `api/broker.py`, `services/broker/etrade_broker.py` |
| Admin endpoints | `api/admin.py` |
| Reinforcement learning agent | `services/rl_agent.py` |
| Market regime (canonical source of truth, since 2026-07-04) | `GET /stocks/regime?market=US\|HK` in `api/routes.py`, wrapping `services/paper_trading_engine.py`'s `get_last_regime()`/`get_last_hk_regime()` |

---

## Paper Trading Engine Architecture (`paper_trading_engine.py`, ~3,760 lines)

### Execution loop
```
paper_trading_step()  — top-level, iterates ALL active portfolios per cycle
    per portfolio:
        ├── _monitor_positions() → exits via stop/target/trailing/signal/time/stall/momentum-exhaustion/wait-decay
        │       (ALWAYS runs, even if portfolio is paused — commits immediately after)
        └── _scan_for_entries()  — only if not paused AND within market hours
                ├── ~15 portfolio-level circuit breakers (drawdown, daily/weekly loss,
                │     consecutive losses, regime bear/risk_off, regime suspension streak,
                │     entry throttle, heat brake, index trend, market cluster cap — ANY of
                │     these `return`s immediately, skipping the whole candidate loop)
                ├── pre-fetch: _prefetched_open, _sig_ref_prices, _recently_stopped, ATR batch
                ├── per-candidate loop (~17 gates, each `continue`s past just that symbol):
                │     stop cooldown, global symbol cap, watchlist membership, K-score floor,
                │     signal staleness, SHORT confluence, price drift, volume_z, HK flow gate,
                │     TA score, declining confidence, conviction-gate Redis check, research
                │     AVOID/SELL hard gate, min position value, open-risk cap, sector caps
                ├── entry qualifier: DE `/decide/{symbol}` (primary mode, default) — falls back
                │     to the independent `_should_enter()` scorer if DE is unreachable (these
                │     two scorers have drifted apart — see T232-DL-DUALSCORER)
                └── _open_position() if approved
    if entries_made == 0 this cycle: _write_no_entry_summary() — per-candidate skip-reason
        tally surfaced in the UI as "ⓘ Not trading: {reason}" (T232-WHYNOTRADE, added 2026-07-03)
```

See `docs/AUDIT_REPORT_TIER232_2026-07-02.md` Part 7.1 (full gate audit table with every
threshold, its config-key source, and hardcoded-vs-configurable status) for the complete,
current picture — this summary is necessarily incomplete and will drift faster than that report.

### Key pre-fetched state (built once per step, used across candidate loop)
- `_prefetched_open: list[tuple[PaperTrade, Stock]]` — all open positions for this portfolio
- `_sig_ref_prices: dict[int, float]` — daily close at signal date per stock_id (price drift gate)
- `_recently_stopped: set[int]` — stock IDs on stop cooldown; merged from `stop_hit` (24h) and `breakeven_stop` (2h)
- `_open_sector_counts: dict[str, int]` — open position count per sector
- `_open_exposure: float` — total open notional / initial_capital

### Portfolio config keys (all accessed via `cfg = {**_DEFAULT_CONFIG, **(portfolio.config or {})}`)

Corrected 2026-07-04 — this list previously named a `max_drawdown_pct` key that doesn't exist
(the real key is `max_portfolio_drawdown_pct`, default 0.20 not 0.15) and was missing most of the
actual gate keys. **Always cross-check against `_DEFAULT_CONFIG` in `paper_trading_engine.py`
directly** — this list is a curated subset, not exhaustive, and several keys are configurable via
`POST /paper-portfolio/{id}/configure` but have NO entry in `_DEFAULT_CONFIG` (meaning a fresh
portfolio silently uses a hardcoded `.get()` fallback until someone sets it explicitly — see
T232-DL2/DL3 for what happens when those fallbacks drift from the intended default):
```
max_positions:              6      # max concurrent open positions
max_open_exposure_pct:      0.40   # total open notional cap (T194) — NOT in _DEFAULT_CONFIG
max_signal_age_hours:       72     # staleness gate (T195) — actual default is 72, not 96
max_price_drift_pct:        3.0    # % drift from signal date close (T196) — NOT in _DEFAULT_CONFIG;
                                    # NOTE: this one is percentage-POINTS not a 0-1 fraction, unlike
                                    # every other *_pct key — a likely source of operator error
breakeven_cooldown_hours:   2      # cooldown for breakeven_stop exits (T197) — NOT in _DEFAULT_CONFIG
stop_cooldown_hours:        120    # cooldown for stop_hit exits (5 days, not 24h)
min_volume_z:               -1.5   # volume z-score gate (T200) — NOT in _DEFAULT_CONFIG
equity_floor_pct:           0.80   # equity / initial_capital floor (T201) — NOT in _DEFAULT_CONFIG
max_confidence_decline:     -8.0   # confidence_delta gate (T202) — NOT in _DEFAULT_CONFIG
max_portfolio_drawdown_pct: 0.20   # max drawdown from equity curve peak (NOT "max_drawdown_pct")
max_daily_loss_pct:         0.04   # daily loss circuit breaker
max_weekly_loss_pct:        0.08   # weekly loss circuit breaker — NOT in _DEFAULT_CONFIG
max_weekly_gain_pct:        0.06   # weekly gain-lock — NOT in _DEFAULT_CONFIG, NOT even configurable via API
max_consecutive_losses:     3      # consecutive-loss circuit breaker
max_entries_per_day:        3      # daily entry cap (module docstring previously said 5 — wrong, fixed)
regime_risk_off_gate:       true   # hard block in risk_off regime (default TRUE, not false)
regime_risk_off_override_until: null  # time-boxed override timestamp, set via POST /risk-off-override
min_kscore:                 48.0   # Ranking.score floor; style overrides GROWTH 48/SWING 52/LONG 50
min_entry_score:            4      # _should_enter()/DE score threshold; SWING override 5, HK override 6
min_confidence:              45.0  # Signal.confidence floor; per-style + HK overrides up to 65.0
min_ta_score:                0.0   # off by default; SWING override 0.65, HK market override 0.65
decision_engine_mode:        "primary"  # primary | shadow | legacy — controls whether DE or the
                                          # independent _should_enter() actually gates entries
position_size_pct:           0.10   # default position as % of equity
breakeven_trigger_pct:       0.03   # gain at which stop moves to entry; per-style overrides
max_sector_positions:        3      # per-sector position cap
max_sector_pct:               0.25   # per-sector $ cap
```

### Exit reason taxonomy
- `stop_hit` — real loss; 24h cooldown; `|stop - entry| > 0.5% of entry`
- `breakeven_stop` — stop at ≈entry; 2h cooldown; `|stop - entry| ≤ 0.5% of entry`
- `target_reached`, `signal_exit`, `time_stop`, `hold_stall`, `trailing_stop` — no cooldown

### Critical model facts
- `PaperTrade.pnl` is the P&L field — `realized_pnl` does NOT exist
- `PaperPortfolio.is_active` — already on model; `paper_trading_step` filters by it
- `PaperPortfolio.initial_capital` — baseline for equity floor; does not change on drawdown

---

### HK vs US: gates are asymmetric, not just the regime source

`_HK_MARKET_OVERRIDES` tightens several gates specifically for `market=="HK"` (applied only when
the portfolio hasn't explicitly overridden the key): `regime_suspension_days` 7 (vs US 3),
`min_entry_score` 6 (vs US 4), `min_confidence` 65.0 (vs US 45.0), `trail_atr_mult` 1.5 (vs US
2.0), `max_position_pct` 0.07 (vs US 0.10), `risk_per_trade_pct` 0.007 (vs US 0.01). HK also gets
an extra `hk_flow_gate` (mainland Stock-Connect southbound flow) with no US equivalent, while US
gets VIX-gradient sizing and HMM bear-pressure sizing that are silent no-ops for HK (HK's regime
dict never populates `vix`, and the HMM endpoint is simply never called for HK). If a change
"works in testing" against US data, re-verify it against HK — the two markets are gated by
materially different rulesets, not just different index inputs.

## Scheduler Architecture (`scheduler.py`, ~3,440 lines)

The scheduler is the system heartbeat. It coordinates all other services.

### Schedule
| Interval | Job | Key invariant |
|---|---|---|
| Every 1 min | `check_signal_alerts()` | Must use `live=False` — live causes oscillation |
| 5× market day | Price ingest → signal refresh → rankings | Uses `_service_token()` for auth |
| Market close | ML retrain trigger | POST to ml-prediction:8003 |
| Ad hoc | Paper trading step | Called by scheduler per active portfolio |

### Service token pattern
```python
# Long-lived JWT for scheduler → other services. Cache after first creation.
_service_token_cache: str = ""
def _service_token() -> str:
    global _service_token_cache
    if _service_token_cache:
        return _service_token_cache
    import time; from jose import jwt as _jwt
    payload = {"sub": "scheduler", "exp": int(time.time()) + 365 * 86400, "jti": "scheduler-svc"}
    _service_token_cache = _jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")
    return _service_token_cache
```

---

## Auth Service (`api/auth.py`)

- Login: `POST /auth/login` — bcrypt verify → issue JWT
- Logout: `POST /auth/logout` — add jti to Redis blacklist (`auth:blacklist:{jti}`)
- JWT blacklist: Redis `auth:blacklist:{jti}` with TTL + in-memory fallback dict
- After any change here: test full login flow before deploying

---

## Email Service (`services/email_service.py`)

- Transport: Gmail SMTP or AWS SES (configured by env)
- Alert emails are triggered by `check_signal_alerts()` in scheduler
- Rate limit: 2h same-direction cooldown per symbol+horizon (stored in signal_alerts table)
- Full reversal (BUY→SELL): bypasses cooldown

---

## Common Failure Modes

1. **jose missing** → all auth-protected endpoints return 401 silently
2. **SQLAlchemy `::type` cast** (BUG-6) → signal writes silently fail; rows not inserted
3. **`live=False` missing** in alert checker → BUY↔HOLD email oscillation every minute
4. **Wrong cooldown bucket** → breakeven exits get 2h cooldown, stop_hit gets 120h (5d) — not 24h
5. **Shared module deployed to wrong path** → `shared/db/` must go to `/app/shared/db/`, not `/app/src/db/`
6. **`BackgroundTasks` swallows exceptions with zero visibility** (T232-RANKSTALE pattern, found
   in ranking-engine but the SAME risk applies to any fire-and-forget task in this service) — a
   naked exception inside a background callback is NEVER surfaced to the HTTP caller. Any new
   background task must wrap its entire body in try/except with a log line on both success and
   failure, or it can silently stop working for days with the endpoint still reporting 200.
7. **Redis distributed lock fails open on Redis outage** (fixed 2026-07-04, T232-DL-OBSERVABILITY)
   — `_run_paper_trading_step`'s lock now fails CLOSED (skips the cycle, logs ERROR) rather than
   letting a Redis hiccup silently re-enable the double-execution race the lock exists to prevent
   (double cash-credit on the same exit). `check_signal_alerts`' lock still fails open by design
   (worst case is a duplicate email, and there's a DB-level dedup fallback) but now logs a WARNING.
8. **Data Quality Checks framework** (added 2026-07-03, `_DQ_CHECKS` in `scheduler.py`) now
   catches staleness in rankings/signals/prices/outcomes/equity-curve automatically every 2h and
   emails on failure — check `GET /admin/dq-status` or the admin-health.tsx DQ section FIRST when
   diagnosing any "X seems stale" report, before manually grepping logs.

---

## Deployment

```bash
# Backend files
docker cp services/market-data/src/<file> stockai-market-data-1:/app/src/<path>
docker restart stockai-market-data-1

# Shared DB models
docker cp shared/db/models.py stockai-market-data-1:/app/shared/db/models.py
docker restart stockai-market-data-1
```
