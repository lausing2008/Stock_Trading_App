# StockAI — Domain Knowledge, Analysis Style & Coding Standards

Teaches Claude the trading domain, system architecture, analysis mental models, and coding standards
for the StockAI platform. Read this before working on any part of the codebase. Each service also
has its own `skill.md` with deeper service-specific knowledge.

---

## Trading Domain

### Signal Styles and Horizons

Signals are computed per-stock, per-style, per-horizon. Direction: BUY / HOLD / SELL. Confidence: 0–100.

| Style | Hold window | Personality |
|---|---|---|
| SHORT | 1–5 days | Intraday-momentum sensitive; selective; best for fast-moving HK stocks |
| SWING | 5–15 days | Balanced; reference style for most portfolios |
| GROWTH | 15–60 days | Relaxed thresholds; fires BUY more often than SWING by design |
| LONG | 60+ days | Requires sustained trend confirmation |

**Horizons** — signals are computed for `1d`, `5d`, `20d`, `60d` windows. The 4-horizon tabs on
the stock detail page show these directly.

**DB signal vs live signal — the most important distinction in the system:**
- **DB signal**: stored in `signals` table; written by `POST /signals/refresh`; refreshed 5×/day.
  Source of truth for Signal Filter page, alert emails, and paper trading entry gates.
- **Live signal**: recomputed fresh from current intraday price on every request.
  Used only by the AI badge on the stock detail page (`GET /signals/{symbol}?persist=true`).
- When badge ≠ tab: DB is stale. Fix: trigger refresh. Never switch the trading loop to live signals.

### Signal Quality Metrics

| Metric | Meaning | Used in |
|---|---|---|
| `confidence` (0–100) | Model conviction in the signal direction | DE scoring, alert threshold |
| `confidence_delta` | Change since previous refresh; negative = losing conviction | T202 gate |
| `volume_z` | z-score vs 20-day avg volume; stored in `sig.reasons` | T200 gate, DE scoring |
| `score` (0–12) | DE score across 9 dimensions; -99 = hard rejected before scoring | decide.tsx display |
| signal age (hours) | Hours since last refresh; >96h = stale for entry | T195 gate |

### Market Regime

Four regimes: **bull → neutral → choppy → bear/risk_off**. Computed from VIX + SPY momentum.
Stored in Redis. Affects paper trading via:
- `regime_risk_off_gate: true` → hard block all new entries
- `position_size_pct` scaling down in choppy/bear

### Decision Engine Pipeline

```
Signal candidate
    ↓
Hard rejects (hard_rejects.py)  ← cheap, run first; any BLOCK → score = -99
    ↓
9-dimension numerical scoring
    ↓
Score / 12 → ENTER or SKIP
```

Hard reject gates (as of T202): open exposure cap, signal staleness, price drift, volume z-score,
confidence decline, equity floor, regime gate, stop cooldown.

### Paper Trading Exit Taxonomy

| Exit reason | Meaning | Re-entry cooldown |
|---|---|---|
| `stop_hit` | Real loss stop | 24h |
| `breakeven_stop` | Stop at ≈entry price (±0.5%) | 2h |
| `target_reached` | Take profit hit | none |
| `signal_exit` | Signal flipped SELL/HOLD | none |
| `time_stop` | Max hold days exceeded | none |
| `hold_stall` | Position not moving | none |
| `trailing_stop` | Trailing stop after 3% gain | none |

### HK vs US Differences

- **Timezone**: HK bars use HKT (UTC+8). `2026-06-17 00:00:00+08:00` = HK trading day.
- **Symbol format**: `NNNN.HK` (e.g., `0981.HK`, `0700.HK`)
- **Staleness asymmetry**: US stocks auto-persist on every detail page visit (unauthenticated GET).
  HK stocks with fewer page views go stale faster — stale signal bias skews HK.
- **Stock Connect**: Southbound (mainland→HK) flows are documented alpha signals (planned T209).

---

## System Architecture

### Service Topology

```
Frontend (Next.js :3000)
    → API Gateway (:8000)  — single entry, JWT validation, transparent proxy
        → Market Data (:8001)     — prices, scheduler, paper trading, auth, email
        → Signal Engine (:8005)   — signal computation + persistence
        → Decision Engine (:8009) — hard rejects + scoring
        → ML Prediction (:8003)   — XGBoost/LightGBM/LSTM training + Optuna tuning
        → Research Engine (:8008) — Claude AI research reports
        → Ranking Engine (:8004)  — K-score rankings + leaderboards
        → Technical Analysis (:8002) — RSI/MACD/BB/patterns/trendlines/S&R
        → Strategy Engine (:8006) — DSL strategy rules + backtesting
        → Portfolio Optimizer (:8007) — mean-variance, risk parity, HRP
        → Event Intelligence (:8010) — earnings, insider, congress, macro, catalyst
```

### Data Flow for a Trade

```
1. Signal engine computes signal → upserts to signals table (stock+style+horizon)
2. Scheduler (market-data) runs paper_trading_step 5×/day
3. _scan_for_entries reads DB signals → runs hard rejects → calls DE /decide
4. DE returns score → if ENTER: _open_position writes PaperTrade row
5. _monitor_positions checks each cycle → applies stop/target/trailing
6. On exit: writes exit_reason, pnl, closed_at to PaperTrade
7. signal_outcomes tracks signal→outcome for ML feedback loop (planned T206)
```

### Scheduler as System Heartbeat

The market-data scheduler (`scheduler.py`) is the system's timing backbone:
- Every 1 min: `check_signal_alerts()` — reads DB signals (`live=False`)
- 5× per market day: price ingest → signal refresh → rankings update
- At market close: ML retrain trigger
- Uses long-lived `_service_token()` JWTs for service-to-service auth calls

**Invariant:** `check_signal_alerts()` MUST read DB signals (`live=False`). `live=True` causes
BUY↔HOLD email oscillation every minute for stocks at the threshold boundary.

### Auth Architecture

- JWTs signed with HS256, shared `jwt_secret` across all services
- `shared/common/jwt_auth.py` is the canonical verifier — uses `python-jose`
- **jose is the #1 silent failure**: if missing from a container, `from jose import JWTError, jwt`
  fails at call time, the generic `except Exception` raises HTTP 401, and ALL authenticated
  endpoints silently break. Check jose first on any unexpected 401.
- Service-to-service calls: `_service_token()` pattern — long-lived JWT with `sub="service-name"`

### Shared Module Layout

```
shared/
├── db/
│   ├── models.py     — SQLAlchemy ORM (Stock, Price, Signal, PaperTrade, Strategy, ...)
│   ├── session.py    — DB session management
│   └── __init__.py   — SessionLocal export
└── common/
    ├── config.py     — Settings from env
    ├── service.py    — FastAPI app factory (create_app)
    ├── jwt_auth.py   — JWT verify + get_current_username dependency
    ├── redis_client.py — Redis helpers
    └── logging.py    — Structured logger setup
```

**Shared module deploy path:** `shared/db/` and `shared/common/` → `/app/shared/` in containers,
NOT `/app/src/`. This is a common copy-paste error that silently deploys to the wrong location.

---

## Analysis Style — How to Diagnose Issues

### Signal Staleness (check in order)
1. Is `jose` installed? → `docker exec stockai-signal-engine-1 python3 -c 'from jose import jwt'`
2. 401s in signal-engine logs? → `docker logs ... | grep '401\|refresh'`
3. SQLAlchemy CAST syntax error swallowed by except? → `grep -i 'syntax\|invalid'`
4. DISTINCT ON ORDER BY wrong? → first ORDER BY key must match the DISTINCT ON expression
5. Scheduler missed runs? → check market-data logs for scheduler heartbeat

### Login Redirect Loop (check in order)
1. Is the JWT locally expired? (decode base64 middle segment, check `exp` vs now)
2. Is `api.ts` clearing a valid JWT on any 401? (should only clear on locally-expired tokens)
3. Is `_app.tsx` calling `doCheck()` before localStorage is initialized? (lazy init fix)
4. Is `dataFreshness()` polling before user is logged in? (must gate on `username` being set)

### Paper Trading Not Entering (check in order)
1. Is the portfolio `is_active`?
2. Is the equity floor triggered? (`equity / initial_capital < equity_floor_pct`)
3. Is the regime gate blocking? (`regime_risk_off_gate` + current regime)
4. Is the signal stale? (`signal_age_hours > max_signal_age_hours`)
5. Check logs for `paper.skip_*` structured entries

### When AI Badge ≠ Signal Filter Tab
Expected behavior when DB signals are stale. Fix: trigger manual signal refresh.
Never fix the disagreement by wiring the trading loop to live signals.

---

## Coding Standards

### Python — SQLAlchemy text() SQL

```python
# CORRECT — use CAST() syntax
sql = text("INSERT INTO signals VALUES (:sid, CAST(:sig AS signaltype), CAST(:hor AS signalhorizon))")

# BROKEN — SQLAlchemy cannot bind :param::type (BUG-6, causes silent data loss)
sql = text("INSERT INTO signals VALUES (:sid, :sig::signaltype, :hor::signalhorizon)")
```
**Rule:** Never use PostgreSQL `::type` cast immediately after a named parameter in `text()` queries.

### Python — DISTINCT ON with ORDER BY

```python
# CORRECT — DISTINCT ON key must be first ORDER BY expression
.order_by(Stock.id, Signal.ts.desc()).distinct(Stock.id)

# BROKEN — psycopg2: "SELECT DISTINCT ON expressions must match initial ORDER BY expressions"
.order_by(Signal.ts.desc()).distinct(Stock.id)
```

### Python — Gate Implementation Pattern

```python
# 1. Read threshold from config (with sensible default)
_threshold = float(cfg.get("config_key_name", default_value))

# 2. Compute value — fail-open if data missing
_value = float((sig.reasons or {}).get("volume_z", 0)) if sig.reasons else 0.0

# 3. Check and skip with structured log
if _value < _threshold:
    log.info("paper.skip_descriptive_name",
             symbol=stock.symbol, actual=round(_value, 2), threshold=_threshold)
    continue  # or return, depending on scope
```

### Python — Service-to-Service Auth Token

```python
_service_token_cache: str = ""
def _service_token() -> str:
    global _service_token_cache
    if _service_token_cache:
        return _service_token_cache
    import time
    from jose import jwt as _jwt
    payload = {"sub": "service-name", "exp": int(time.time()) + 365 * 86400, "jti": "service-name-svc"}
    _service_token_cache = _jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")
    return _service_token_cache
```

### Python — Structured Logging

```python
# Good — structured, grep-friendly, machine-parseable
log.info("paper.skip_stale_signal", symbol=stock.symbol, age_hours=round(age_h, 1), max_age=max_age)

# Bad — interpolated string, not grep-friendly
log.info(f"Skipping {stock.symbol}: signal too old ({age_h:.1f}h)")
```

### TypeScript — Improvements Tracker Type Safety

All four must include every Tier member, or TypeScript errors:
1. `type Tier = 1 | 2 | ... | N` union
2. `TIER_LABEL: Record<Tier, string>` — add `N: 'label'`
3. `TIER_COLOR: Record<Tier, string>` — add `N: '#hexcolor'`
4. Item objects with `tier: N as const`

Render loop is automatic (driven by TIER_LABEL keys) — no hardcoded tier array to update.

### TypeScript — Auth Safety (login redirect loop prevention)

- Never delete the JWT on any 401 — only delete when token is locally expired (`exp < Date.now()/1000`)
- Never add a handler that preserves the token AND redirects to /login — causes infinite loop
- `dataFreshness()` poll must be gated on `username` being set — never poll unauthenticated

### Deployment — Backend

```bash
git add <file> && git commit -m "..." && git push origin prod
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "cd /home/ec2-user/Stock_Trading_App && git pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/<host-path> <container>:<container-path> && \
   docker restart <container>"
```

### Deployment — Frontend (critical rules)

```bash
# ALWAYS use DOCKER_BUILDKIT=0 — BuildKit silently serves cached layers even with --no-cache
DOCKER_BUILDKIT=0 docker build --no-cache -f frontend/Dockerfile -t stockai-frontend:latest .
docker compose -f docker/docker-compose.yml up -d --force-recreate frontend

# NEVER use this — BuildKit = stale image guaranteed
docker compose build --no-cache frontend
```

- Run synchronously — no `run_in_background: true` (SSH timeout = unknown container state)
- `frontend/.env.production` must exist on EC2 before building (gitignored, never commit)

### Comments Policy

No comments by default. Add only when the WHY is non-obvious: a hidden constraint, a workaround
for a specific bug, a subtle invariant. Never: what the code does, who calls it, which ticket.

---

## Container Reference

| Service | Container | Internal port |
|---|---|---|
| market-data | `stockai-market-data-1` | 8001 |
| signal-engine | `stockai-signal-engine-1` | 8005 |
| decision-engine | `stockai-decision-engine-1` | 8009 |
| ml-prediction | `stockai-ml-prediction-1` | 8003 |
| research-engine | `stockai-research-engine-1` | 8008 |
| api-gateway | `stockai-api-gateway-1` | 8000 |
| ranking-engine | `stockai-ranking-engine-1` | 8004 |
| technical-analysis | `stockai-technical-analysis-1` | 8002 |
| strategy-engine | `stockai-strategy-engine-1` | 8006 |
| portfolio-optimizer | `stockai-portfolio-optimizer-1` | 8007 |
| event-intelligence | `stockai-event-intelligence-1` | 8010 |

File paths inside containers: service-specific → `/app/src/`, shared modules → `/app/shared/`

**Port table source of truth:** ports above are read from each service's Dockerfile/`main.py`
`uvicorn.run(..., port=N)` — verify with `grep -rh "EXPOSE\|uvicorn.run" services/<svc>/Dockerfile
services/<svc>/src/main.py` if this table is ever suspected stale. CLAUDE.md carries the same
table under "System Port Map" — keep both in sync if either changes.
