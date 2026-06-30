# StockAI — Domain Knowledge, Analysis Style & Coding Standards

This file teaches Claude the trading domain, system architecture mental models, and project-specific
coding standards for the StockAI platform. Read this to understand how to think about the codebase,
not just what the code does.

---

## Trading Domain Knowledge

### Signal Styles and Horizons

Signals are computed per-stock, per-style, per-horizon. A signal has a direction (BUY/HOLD/SELL)
and a confidence score (0–100).

**Styles** — define the trading timeframe and threshold profile:
| Style | Hold window | Personality |
|---|---|---|
| SHORT | 1–5 days | Sensitive to intraday momentum; fires BUY more selectively; best for fast-moving HK stocks |
| SWING | 5–15 days | Balanced; the reference style for most portfolio configs |
| GROWTH | 15–60 days | Relaxed thresholds; fires BUY more often than SWING by design; high-volatility momentum |
| LONG | 60+ days | Slow-moving; requires sustained trend confirmation |

**Horizons** — within a style, signals are computed for multiple time windows:
- `1d`, `5d`, `20d`, `60d` — the 4-horizon tab UI on the stock detail page

**Key distinction — DB signal vs live signal:**
- **DB signal**: stored in `signals` table; written by `POST /signals/refresh`; refreshed 5×/day on market days. Source of truth for Signal Filter page, alert emails, and paper trading entry gates.
- **Live signal**: recomputed fresh from current intraday price data on every request. Used by the AI badge on the stock detail page (`GET /signals/{symbol}?persist=true`).
- When these disagree, DB is stale. Fix: trigger manual refresh. Never "fix" the disagreement by making the DB path live — it would recompute on every page load.

### Signal Quality Metrics
- **confidence** (0–100): how strongly the model believes in the signal direction
- **confidence_delta**: change in confidence since the previous signal computation — negative = losing conviction
- **volume_z**: z-score of today's volume vs 20-day average, stored in `sig.reasons` — negative = thin market
- **score**: Decision Engine score out of 12 (9 dimensions); -99 = hard rejected before scoring
- **signal age**: hours since signal was last computed; >96h = stale for entry purposes

### Regime Detection
The system classifies the market into four regimes based on VIX + SPY momentum:
- **bull**: VIX low, SPY trending up — full position sizing
- **neutral**: normal conditions — normal sizing
- **choppy**: VIX elevated or SPY range-bound — reduced sizing
- **bear** / **risk_off**: VIX spike or sustained SPY decline — optional hard block on new entries

Regime affects paper trading through `regime_risk_off_gate` (hard block) and `position_size_pct` scaling.
The regime is read from a Redis key updated by the market-data scheduler.

### Decision Engine (DE) Pipeline
Hard rejects → numerical scoring → decision. Order matters: hard rejects are cheap and run first.

```
Signal candidate
    ↓
Hard rejects (hard_rejects.py) — any BLOCKED → short-circuit, score = -99
    ↓
9-dimension numerical scoring (DE service)
    ↓
Final score / 12 → ENTER or SKIP
```

**Hard reject gates (as of T202):**
1. Open exposure cap — total open notional > 40% of initial capital
2. Signal staleness — signal age > 96h
3. Price drift — stock moved > 3% from signal date close
4. Volume gate — volume_z < -1.5 (abnormally thin market)
5. Confidence decline — confidence_delta < -8pts since last refresh
6. Equity floor — equity < 80% of initial capital (circuit breaker)
7. Regime gate — risk_off and regime_risk_off_gate=True
8. Recent stop cooldown — same stock stopped out in last 24h (2h for break-even stops)

### Paper Trading Exit Taxonomy
Exits are not generic "stop hit" — the reason matters for cooldown logic:
| Exit reason | Meaning | Re-entry cooldown |
|---|---|---|
| `stop_hit` | Real loss — stop triggered below entry | 24h |
| `breakeven_stop` | Stock ran then came back to entry (±0.5%) | 2h |
| `target_reached` | Take profit hit | No cooldown |
| `signal_exit` | Underlying signal flipped | No cooldown |
| `time_stop` | Max hold days exceeded | No cooldown |
| `hold_stall` | Position not moving — capital redeployment | No cooldown |
| `trailing_stop` | Trailing stop triggered after 3% gain | No cooldown |

### HK vs US Market Differences
- **Timezone**: HK trades in HKT (UTC+8). Daily bars must be stored with correct UTC offset. A bar labeled `2026-06-17 00:00:00+08:00` is a HK trading day bar, not a UTC midnight bar.
- **HK stock symbols**: format is `NNNN.HK` (e.g., `0981.HK`, `0700.HK`)
- **Page visit refresh bias**: US stocks get auto-persisted on every stock detail page visit (unauthenticated GET). HK stocks with fewer page views go stale faster — so stale signals skew HK.
- **Stock Connect**: Southbound (mainland → HK) and Northbound (HK → mainland) daily flows are documented alpha signals for HK stocks (planned T209).

---

## System Architecture Mental Models

### Service Topology
```
Frontend (Next.js :3000)
    → API Gateway (:8000) — all external traffic, JWT validation
        → Market Data (:8001) — prices, watchlists, alerts, paper trading, scheduler
        → Signal Engine (:8005) — signal computation and storage
        → Decision Engine (:8006) — hard rejects + scoring
        → ML Prediction (:8003) — XGBoost models, Optuna tuning
        → Research Engine (:8008) — AI research reports
        → Ranking Engine (:8007) — stock rankings
        → Technical Analysis (:8009) — TA indicators
        → Strategy Engine (:8010) — strategy evaluation
        → Portfolio Optimizer (:8011) — portfolio-level optimization
        → Event Intelligence (:8012) — earnings, macro events
```

### Scheduler as Orchestrator
The market-data scheduler (`services/market-data/src/services/scheduler.py`) is the system's heartbeat:
- Every 1 min: `check_signal_alerts()` — reads DB signals (live=False), checks thresholds
- 5× per market day: price ingest, signal refresh (POST to signal-engine), rankings update
- At market close: ML retrain trigger
- The scheduler authenticates to other services using a long-lived `_service_token()` JWT

**Critical invariant:** `check_signal_alerts()` MUST read DB signals (`live=False`). Using live=True causes
BUY↔HOLD oscillation every minute for stocks sitting at the threshold boundary.

### Auth Flow
- JWTs signed with HS256, shared secret across all services
- `shared/common/jwt_auth.py` is the canonical verifier — uses `python-jose`
- If `jose` is missing from a container, `from jose import JWTError, jwt` fails at call time,
  the generic `except Exception` raises HTTP 401, and ALL authenticated endpoints silently break
- Service-to-service calls use `_service_token()` — a long-lived JWT with `sub="scheduler"` or service name
- **jose is the #1 recurring silent failure** — check it first when any endpoint returns unexpected 401s

### Data Flow for a Trade
```
1. Signal engine computes signal → writes to signals table (upsert by stock+style+horizon)
2. Scheduler runs paper_trading_step → calls _scan_for_entries
3. _scan_for_entries reads signals from DB (not live) → runs hard rejects → calls DE /decide
4. DE returns score → if ENTER: _open_position writes PaperTrade row
5. _monitor_positions checks open trades each cycle → applies stop/target/trailing logic
6. On exit: writes exit_reason, pnl, closed_at to PaperTrade
7. signal_outcomes table tracks signal→outcome for future ML training feedback
```

---

## Analysis Style

### Diagnosing Production Issues — Mental Order

**Signal staleness:**
1. Is jose installed in signal-engine? (`docker exec ... python3 -c 'from jose import jwt'`)
2. Are there 401s in signal-engine logs? (`docker logs ... | grep '401\|refresh'`)
3. Is there a SQLAlchemy CAST syntax error swallowed by except? (`grep -i 'syntax\|invalid'`)
4. Is the DISTINCT ON ORDER BY correct? (stock_id first, then ts.desc())
5. Then check scheduler logs for missed runs

**Login redirect loop:**
1. Is the JWT locally expired or still valid? (decode base64 middle segment, check `exp`)
2. Is api.ts clearing a valid JWT on any 401? (it should only clear on locally-expired tokens)
3. Is `_app.tsx` firing doCheck() before localStorage is initialized? (lazy init fix)
4. Is dataFreshness() polling before user is logged in? (must gate on `username` being set)

**Paper trading not entering:**
1. Is the portfolio `is_active`?
2. Is the equity floor triggered?
3. Is the regime gate blocking?
4. Is the signal stale?
5. Check paper_trading_engine logs for `paper.skip_*` structured log entries

### When the AI Badge Disagrees with the Signal Tabs
- Badge = live computation (always fresh, from current price)
- Tabs = DB signal (from last refresh, up to 96h old)
- This is expected behavior when DB signals are stale
- Fix: trigger refresh, not code change

### Gate Design Philosophy
- **Fail open on missing data**: if volume_z is None, allow entry — don't block on uncertainty
- **Config-driven thresholds**: every gate threshold is a `portfolio.config` key with a sensible default
- **Log on every skip**: `log.info("paper.skip_<reason>", symbol=..., threshold=..., actual=...)`
- **No magic numbers**: thresholds come from config, not hardcoded constants in the loop

---

## Coding Standards

### Python — Signal & Trading Engine

**SQL with SQLAlchemy text():**
```python
# CORRECT — use CAST() syntax
INSERT INTO signals VALUES (:sid, CAST(:sig AS signaltype), CAST(:hor AS signalhorizon))

# BROKEN — SQLAlchemy cannot bind :param::type (the :: is ambiguous)
INSERT INTO signals VALUES (:sid, :sig::signaltype, :hor::signalhorizon)
```
**Rule:** Never use PostgreSQL `::` cast shorthand immediately after a named parameter in SQLAlchemy `text()` queries.

**DISTINCT ON with ORDER BY:**
```python
# CORRECT — DISTINCT ON key must be first in ORDER BY
.order_by(Stock.id, Signal.ts.desc()).distinct(Stock.id)

# BROKEN — psycopg2 error: SELECT DISTINCT ON expressions must match initial ORDER BY
.order_by(Signal.ts.desc()).distinct(Stock.id)
```

**Gate implementation pattern:**
```python
# Read from config with default
_threshold = float(cfg.get("config_key_name", default_value))

# Compute the value (fail-open: use safe default if data missing)
_value = float((sig.reasons or {}).get("volume_z", 0)) if sig.reasons else 0.0

# Check threshold
if _value < _threshold:
    log.info("paper.skip_descriptive_name",
             symbol=stock.symbol, actual=round(_value, 2), threshold=_threshold)
    continue  # or return, depending on scope
```

**Service token pattern (service-to-service auth):**
```python
_service_token_cache: str = ""
def _service_token() -> str:
    global _service_token_cache
    if _service_token_cache:
        return _service_token_cache
    import time
    from jose import jwt as _jwt
    payload = {"sub": "service-name", "exp": int(time.time()) + 365 * 86400, "jti": "service-name-service"}
    _service_token_cache = _jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")
    return _service_token_cache
```

**Structured logging style:**
```python
# Good — structured, grep-friendly
log.info("paper.skip_stale_signal", symbol=stock.symbol, age_hours=round(age_h, 1), max_age=max_age)

# Bad — unstructured
log.info(f"Skipping {stock.symbol}: signal too old ({age_h:.1f}h)")
```

### TypeScript — Frontend

**Improvements tracker type safety:**
Every new tier number must appear in ALL FOUR places or TypeScript errors with `Type 'N' is not assignable to type 'Tier'`:
1. `type Tier = 1 | 2 | ... | N` union
2. `TIER_LABEL: Record<Tier, string>` entry
3. `TIER_COLOR: Record<Tier, string>` entry
4. Item objects with `tier: N as const`

**API client pattern:**
```typescript
// All API calls go through api.ts request() — never fetch() directly
const result = await api.someEndpoint(params)

// New endpoint definition in api.ts:
newEndpoint: (param: string) =>
  request<ResponseType>(`/endpoint/${param}`, { method: 'POST', body: JSON.stringify(data) })
```

**Auth safety rules (from login redirect loop fix):**
- Never delete JWT on ANY 401 — only delete when the token is locally expired (check `exp` claim)
- Never add a handler that preserves the token AND redirects to /login — causes infinite loop
- dataFreshness() poll must be gated on `username` being set — never poll when unauthenticated

### Comments Policy
Default: no comments. Add a comment ONLY when the WHY is non-obvious:
- A hidden constraint, a workaround for a specific bug, a subtle invariant
- Recurring bug pattern references (e.g., `# BUG-6: ::type cast syntax fails with SQLAlchemy text()`)
- Never: what the code does, who calls it, which ticket it came from

### Deployment Discipline
- Backend: `docker cp <file> <container>:<path> && docker restart <container>` — always on EC2, after git pull
- Frontend: `DOCKER_BUILDKIT=0 docker build --no-cache -f frontend/Dockerfile -t stockai-frontend:latest .` — NEVER `docker compose build`
- Shared modules: `shared/db/` and `shared/common/` → `/app/shared/` in container, NOT `/app/src/`
- Never commit `.env.production` — it is gitignored and must be created manually on EC2
- Run frontend builds synchronously — never background them (SSH timeout = unknown state)
