# Market Data Service — Domain Knowledge & Coding Standards

The largest and most complex service (15k lines). Central hub for price ingestion, paper trading,
scheduling, authentication, email alerts, and broker integration.

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

---

## Paper Trading Engine Architecture (`paper_trading_engine.py`, ~2,957 lines)

### Execution loop
```
paper_trading_step(portfolio)
    ├── _compute_equity()
    ├── circuit breaker checks (max_drawdown, equity_floor, regime gate)
    ├── _monitor_positions() → exits via stop/target/trailing/signal/time/stall
    └── _scan_for_entries()
            ├── pre-fetch: _prefetched_open, _sig_ref_prices, _recently_stopped
            ├── per-candidate: hard rejects (age, drift, volume_z, confidence_delta)
            ├── call DE /decide endpoint
            └── _open_position() if ENTER
```

### Key pre-fetched state (built once per step, used across candidate loop)
- `_prefetched_open: list[tuple[PaperTrade, Stock]]` — all open positions for this portfolio
- `_sig_ref_prices: dict[int, float]` — daily close at signal date per stock_id (price drift gate)
- `_recently_stopped: set[int]` — stock IDs on stop cooldown; merged from `stop_hit` (24h) and `breakeven_stop` (2h)
- `_open_sector_counts: dict[str, int]` — open position count per sector
- `_open_exposure: float` — total open notional / initial_capital

### Portfolio config keys (all accessed via `cfg = portfolio.config or {}`)
```
max_open_exposure_pct:    0.40   # total open notional cap (T194)
max_signal_age_hours:     96     # staleness gate (T195)
max_price_drift_pct:      3.0    # % drift from signal date close (T196)
breakeven_cooldown_hours: 2      # cooldown for breakeven_stop exits (T197)
stop_cooldown_hours:      24     # cooldown for stop_hit exits
min_volume_z:            -1.5    # volume z-score gate (T200)
equity_floor_pct:         0.80   # equity / initial_capital floor (T201)
max_confidence_decline:  -8.0    # confidence_delta gate (T202)
max_drawdown_pct:         0.15   # max drawdown from equity curve peak
regime_risk_off_gate:     false  # hard block in risk_off regime
position_size_pct:        0.10   # default position as % of equity
breakeven_trigger_pct:    0.03   # gain at which stop moves to entry
max_sector_positions:     3      # per-sector position cap
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

## Scheduler Architecture (`scheduler.py`, ~2,628 lines)

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
4. **Wrong cooldown bucket** → breakeven exits get 24h cooldown instead of 2h
5. **Shared module deployed to wrong path** → `shared/db/` must go to `/app/shared/db/`, not `/app/src/db/`

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
