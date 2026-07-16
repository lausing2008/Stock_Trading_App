# CLAUDE.md — Persistent Session Notes for Claude Code

This file is read at the start of every session. It documents recurring issues, deployment patterns,
and constraints that must be applied every time.

---

## Recurring Issue: Login Redirect Loop After Deployment

**Symptom:** After deploying (docker restart), users are redirected to /login. Even after entering
valid credentials, they get redirected back to /login. This happens consistently after every
container restart.

**Root cause:** `frontend/src/lib/api.ts` `request()` function had a 401 handler that
unconditionally deleted the JWT from localStorage and redirected to `/login` on ANY 401 response
— including transient 401s from background calls (like `api.dataFreshness()`) that fire during
container startup. This deleted a still-valid JWT, forcing re-login even when credentials are fine.

**Fix applied (2026-06-15, updated 2026-06-16):**

Two changes were made to solve this permanently:

1. **`_app.tsx` — lazy state init (prevents blank flash and early redirect):**
   Auth state is now read synchronously from localStorage at first render. If a valid session exists,
   `checked = true` and `username` is populated immediately — no async gap where doCheck() can run
   before finding a session.

2. **`api.ts` — smart 401 handler (prevents token deletion on transient 401s):**
   The handler now decodes the local JWT first. If the token has actually expired, it removes it
   and redirects. If the token is still locally valid, it throws `'Unauthorized'` WITHOUT removing
   the token or redirecting — so transient 401s during container startup don't log out valid users.

3. **`_app.tsx` — freshness poll guarded by `username`:**
   The `api.dataFreshness()` poll now only fires when `username` is set (user is logged in).
   Previously it fired immediately on every page load, causing 401s from unauthenticated pages.

```
// api.ts — only clear the token if it's actually expired
const raw = localStorage.getItem('stockai_jwt');
let expired = true;
if (raw) { try { const p = JSON.parse(atob(raw.split('.')[1]...)); expired = p.exp < Date.now()/1000; } catch {} }
if (expired) { localStorage.removeItem('stockai_jwt'); window.location.href = '/login'; throw ...; }
throw new Error('Unauthorized'); // locally valid but server rejected — don't log out
```

**What to check if this recurs:**
1. Check api-gateway logs: `docker logs stockai-api-gateway-1 --since 2m | grep '401'`
2. Check market-data login: `docker logs stockai-market-data-1 --since 2m | grep 'login'`
3. If `POST /auth/login` returns 200 but user still gets redirected: check `_app.tsx` `doCheck()` —
   `getSession()` must be returning null. Check if decodeJWT is failing (base64 padding issue?).
4. If `POST /auth/login` returns 401: check the credentials and DB (bcrypt hash in users table).
5. After any auth.py or market-data change, always test login end-to-end before deploying.

**After deployment, if users can't log in:**
- Ask them to do a hard refresh (Ctrl+Shift+R / Cmd+Shift+R) first
- If still broken, check that market-data container started cleanly: `docker logs stockai-market-data-1 | head -30`
- NEVER add a "smart 401 redirect" that preserves the token AND redirects to /login — this causes
  a loop: login.tsx sees valid token → redirects to / → API returns 401 → redirects to /login → loop.

---

## Deployment Pattern

**Standard deployment (git-based, preferred):**
1. Commit changes locally on `prod` branch
2. `git push origin prod`
3. SSH to EC2: `ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71`
4. On EC2: `cd /home/ec2-user/Stock_Trading_App && git pull origin prod`
   - If there are local changes on EC2 blocking the pull: `git stash && git pull origin prod`
   - If there are untracked files blocking: move them to /tmp first, then pull
5. **Frontend:** needs rebuild — use the legacy builder to bypass the BuildKit stale-cache bug,
   but do NOT pass `--no-cache` (see "Recurring Issue: Slow Frontend Builds" below — `--no-cache`
   was fixed 2026-07-07 to be unnecessary overhead, not a required safety measure):
   ```
   DOCKER_BUILDKIT=0 docker build -f frontend/Dockerfile -t stockai-frontend:latest . && \
   docker compose -f docker/docker-compose.yml up -d --force-recreate frontend
   ```
   **WARNING:** `docker compose build frontend` (i.e. via `docker compose`, not `docker build`
   directly) uses BuildKit which silently serves cached layers even with `--no-cache`, producing a
   stale image. Always invoke `docker build` directly with `DOCKER_BUILDKIT=0` for frontend builds
   to guarantee the latest source is compiled — this is the part that matters, not `--no-cache`.
6. **Backend services:** `docker cp` changed files to `/app/shared/` (for shared/) and `/app/src/` (for service-specific files), then `docker restart <container>`
   - **IMPORTANT:** `shared/db/models.py` and `shared/common/` must be copied to `/app/shared/db/` and `/app/shared/common/` (NOT `/app/src/db/`!)
   - Use: `docker cp shared/db/__init__.py <container>:/app/shared/db/__init__.py`

Container names: `stockai-market-data-1`, `stockai-signal-engine-1`, `stockai-frontend-1`,
`stockai-api-gateway-1`, `stockai-ml-prediction-1`, `stockai-research-engine-1`,
`stockai-ranking-engine-1`, `stockai-strategy-engine-1`, `stockai-technical-analysis-1`,
`stockai-portfolio-optimizer-1`

Key file paths inside containers:
- market-data Python source: `/app/src/` (service-specific) and `/app/shared/` (shared models)
- signal-engine Python source: `/app/src/` and `/app/shared/`
- frontend Next.js build: `/app/.next/` (built into image during `docker compose build`)

Frontend requires `frontend/.env.production` with `API_GATEWAY_URL=http://api-gateway:8000`
before building. This file is gitignored — never commit it.

---

## Security Constraints

- `.env.production` is gitignored — NEVER commit it
- Never embed real credential values literally in SSH command strings or tool calls
- EC2 SSH: `18.205.121.71`, key: `~/Documents/Stock_AI/lausing.pem`, user: `ec2-user`
- EC2 production domain: `lausing.com`
- JWT secret and DB credentials are in EC2 `.env` file only

---

## Auth Architecture

- JWTs signed with HS256 using `jwt_secret` from env (shared across all services)
- Tokens expire after `JWT_EXPIRE_DAYS` days (typically 1)
- Token blacklist: Redis `auth:blacklist:{jti}` (set on logout) + in-memory fallback dict
- `shared/common/jwt_auth.py` is the canonical verifier (used by api-gateway proxy)
- `services/market-data/src/api/auth.py` handles login/logout/user management
- api-gateway `proxy.py` `_require_auth()` validates every non-public request

---

## Recurring Issue: Signal Refresh 401 — jose Library Missing from signal-engine

**Symptom:** HK (and potentially US) stock signals go stale — DB signals table has entries that are
days old even though the scheduler appears to be running. Users may receive BUY email alerts for a
stock that shows SELL in Signal Filter, or the top "AI Signal" badge on the stock detail page
disagrees with the 4-horizon tab signals. `POST /signals/refresh?market=HK` logs show 401.

**Root cause:** `python-jose` was missing from the `stockai-signal-engine-1` container despite
being listed in `requirements.txt`. The `shared/common/jwt_auth.py` `get_current_username()`
dependency does `from jose import JWTError, jwt` at call time — if the import fails, the generic
`except Exception` handler raises HTTP 401. This silently broke every authenticated endpoint on
the signal engine, including `POST /signals/refresh`. The scheduled `_bulk_persist` background task
was never registered so no signals were ever written.

HK stocks appeared most affected because individual US stock page visits trigger auto-persist via
the unauthenticated `GET /signals/{symbol}` endpoint, keeping US signals fresher. HK stocks with
fewer page views sat stale.

**Why the badge and tabs disagreed:** The top "AI Signal" badge comes from the aggregate overview
endpoint (`/aggregate/overview/{symbol}`) which calls `GET /signals/{symbol}?persist=true` —
unauthenticated, forces live computation. The 4 horizon tabs call `api.signal(symbol, style, false)`
which reads stored DB signals (`live=false`). When DB signals are stale, these diverge.

**Fix applied (2026-06-17):**
1. Installed `python-jose[cryptography]==3.3.0` directly in running container (immediate).
2. Rebuilt `stockai-signal-engine-1` image so it persists through future restarts.
3. Triggered manual HK + US refresh to backfill stale signals.

**What to check if signals go stale again:**
```bash
# Check if signal engine refresh is being rejected
docker logs stockai-signal-engine-1 --since 2h | grep 'refresh.*401\|401.*refresh'

# Verify jose is installed in the container
docker exec stockai-signal-engine-1 python3 -c 'from jose import jwt; print("jose OK")'

# If jose is missing, install it and rebuild:
docker exec stockai-signal-engine-1 pip install 'python-jose[cryptography]==3.3.0'
docker compose -f docker/docker-compose.yml build signal-engine && docker compose -f docker/docker-compose.yml up -d signal-engine

# Check last signal timestamp across markets
docker exec stockai-market-data-1 python3 -c "
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
rows = s.execute(text(\"SELECT market, MAX(sig.ts) FROM signals sig JOIN stocks st ON sig.stock_id=st.id GROUP BY market\")).fetchall()
print(rows); s.close()"
```

**After fix — trigger manual refresh:**
```bash
# Run from market-data container to trigger bulk signal refresh
docker exec stockai-market-data-1 python3 -c "
import sys, uuid; sys.path.insert(0,'/app/src'); sys.path.insert(0,'/app')
from common.config import get_settings; from datetime import datetime, timezone, timedelta
import httpx; from jose import jwt as _jwt; settings = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':datetime.now(timezone.utc)+timedelta(days=365)}, settings.jwt_secret, algorithm='HS256')
for mkt in ['HK','US']:
    r = httpx.post(f'http://signal-engine:8005/signals/refresh?market={mkt}', headers={'Authorization':f'Bearer {tok}'}, timeout=15)
    print(mkt, r.status_code, r.text[:60])
"
```

**Deployment note:** After any rebuild of `stockai-signal-engine-1`, verify `jose` is installed
before the next market open. The image build step must run `pip install -r requirements.txt`
including `python-jose[cryptography]`.

---

## Recurring Issue: tune_all 401 — jose Library Missing from ml-prediction

**Symptom:** `POST /ml/tune_all` returns 401 even with a valid JWT token. Optuna re-tune fails
silently. Models remain trained with stale hyperparameters.

**Root cause (found 2026-06-19):** `python-jose` was missing from the running `stockai-ml-prediction-1`
container even though it's in `requirements.txt`. The image was built before `python-jose` was added
to requirements.txt, so the installed package is absent. `shared/common/jwt_auth.py` does
`from jose import JWTError, jwt` — if the import fails, the generic `except Exception` block
raises HTTP 401, same as signal-engine's bug.

**Fix:**
```bash
docker exec stockai-ml-prediction-1 pip install 'python-jose[cryptography]==3.3.0'
# Verify:
docker exec stockai-ml-prediction-1 python3 -c 'from jose import jwt; print("jose OK")'
```

**Trigger tune_all after fix** (run from market-data container, ml-prediction is on port 8003):
```bash
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from common.config import get_settings
from jose import jwt as _jwt
import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400*365}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://ml-prediction:8003/ml/tune_all?n_trials=60', headers={'Authorization': f'Bearer {tok}'}, timeout=20)
print(r.status_code, r.text[:300])
"
```

**Permanent fix:** Rebuild the ml-prediction image (after tune_all completes — restarting kills it):
```bash
docker compose -f docker/docker-compose.yml build ml-prediction && docker compose -f docker/docker-compose.yml up -d ml-prediction
# Then re-install jose and re-trigger tune_all (rebuild wipes the pip-install)
docker exec stockai-ml-prediction-1 pip install 'python-jose[cryptography]==3.3.0'
```

---

## Recurring Issue: INT-7 Signal-Engine Research Divergence — Missing Auth Header

**Symptom:** Research divergence log entries (`signal.research_divergence`) never appear in
signal-engine logs even when a BUY signal conflicts with an AVOID/SELL research report.

**Root cause:** `signal-engine/src/api/routes.py` `_bulk_persist()` calls
`GET /research/{symbol}/summary` without an Authorization header. The research engine requires
a JWT on that endpoint. The call silently returns 401 (swallowed by `except Exception: pass`).

**Fix applied (2026-06-17):**
Added `_service_token()` function at module level (same pattern as market-data scheduler):
```python
_service_token_cache: str = ""
def _service_token() -> str:
    global _service_token_cache
    if _service_token_cache:
        return _service_token_cache
    import time
    from jose import jwt as _jwt
    payload = {"sub": "signal-engine", "exp": int(time.time()) + 365 * 86400, "jti": "signal-engine-service"}
    _service_token_cache = _jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")
    return _service_token_cache
```

The research summary call now passes `headers={"Authorization": f"Bearer {_service_token()}"}`.
Deploy: `docker cp routes.py stockai-signal-engine-1:/app/src/api/routes.py && docker restart stockai-signal-engine-1`

---

## Connectivity Audit Invariants (2026-06-17)

After the full system connectivity review, these are the rules to maintain:

1. **Any endpoint that uses `Depends(get_current_username)` must receive an Authorization header**
   when called from another service. All scheduler → service calls use `_service_token()`.
   Signal-engine → research-engine calls now also use `_service_token()`. Add the same pattern
   to any new service-to-service call against an auth-protected endpoint.

2. **The `/research/{symbol}/trigger` endpoint is intentionally unauthenticated** — do not add
   auth to it. It is only reachable from the internal Docker network.

3. **The `/stocks/conviction` endpoint is intentionally open** — it reads from Redis only
   (no sensitive data), and signal-engine calls it without auth.

4. **Dead component files — cleaned up 2026-06-18 (Tier 36-F):**
   Deleted: `board.tsx`, `forecast.tsx`, `screener.tsx`, `StrategyBuilder.tsx` — no imports anywhere.
   Retained: `DonutChart.tsx` (used by positions.tsx), `PriceChart.tsx` (used by stock/[symbol].tsx).

---

## Recurring Issue: Signal Alert Email Spam — BUY→HOLD→BUY Oscillation

**Symptom:** User receives many signal change emails for the same stock within 1–2 hours,
cycling BUY→HOLD→BUY→HOLD repeatedly. Happens for stocks sitting right at the buy_threshold.

**Root cause (fixed 2026-06-18):** Two bugs compounded:

1. **`check_signal_alerts()` in `scheduler.py` called `GET /signals/{sym}` without `live=False`.**
   The signal endpoint defaults to `live=True` — it recomputes the signal fresh from current
   intraday prices on every call. Since the alert checker runs every minute and the signal
   endpoint recomputed live each time, a stock at the threshold boundary (e.g. 0981.HK) would
   flip BUY↔HOLD on every minute tick, firing an email on each flip.

2. **No same-direction cooldown.** Once a BUY email fired, if the signal dropped to HOLD and
   then recovered to BUY within minutes, a second BUY email fired immediately.

**Fix applied:**
1. Pass `live=False` in the signal fetch: `params={"style": style, "live": "false"}`. Alert
   checker now reads the stored DB signal — consistent with what the Signal Filter page shows.
   DB signals only change when scheduled refreshes run (5×/day), eliminating intraday oscillation.
2. Added 2-hour same-direction cooldown on `last_sent_at`. Even if DB signals oscillate between
   scheduled refreshes, no more than one email per 2 hours per symbol+horizon. Full BUY↔SELL
   reversals bypass the cooldown.

**File:** `services/market-data/src/services/scheduler.py`, function `check_signal_alerts()`

**What to check if oscillation recurs:**
```bash
# Check what signal the alert checker is actually reading
docker logs stockai-market-data-1 --since 2h | grep 'signal_alert'
# Confirm live=False is being passed (grep signal fetch in scheduler)
docker exec stockai-market-data-1 grep -n 'live.*false' /app/src/services/scheduler.py
```

**Design invariant:** `check_signal_alerts()` must always read DB signals (`live=False`), not
live-computed signals. The DB signal is the source of truth for the Signal Filter page — alerts
and the filter must agree on what the current signal is.

---

## Recurring Issue: Improvements Page Not Showing New Tiers

**Symptom:** After adding a new tier to `improvements.tsx` (items, TIER_LABEL, TIER_COLOR, Tier type union),
the new tier items do not appear on the improvements page even after a frontend rebuild.

**Root cause (fixed 2026-06-20):** `frontend/src/pages/improvements.tsx` line ~6890 had a hardcoded
tier list `[1, 2, 3, ..., 54]` that controlled which tier sections are rendered on the page.
Adding a new tier to `TIER_LABEL`/`TIER_COLOR` and the items array had no effect because the
render loop only iterated this hardcoded list.

**Fix applied:** Replaced the hardcoded array with:
```js
const tiers = (Object.keys(TIER_LABEL).map(Number).sort((a, b) => a - b) as Tier[])
  .filter(t => filterTier === 0 || t === filterTier);
```
Now the render loop is driven by TIER_LABEL — any tier added there automatically appears.

**What to do when adding a new tier:**
1. Add `N` to the `type Tier` union
2. Add items with `tier: N`
3. Add `N: 'Tier N — ...'` to `TIER_LABEL`
4. Add `N: '#hexcolor'` to `TIER_COLOR`
5. The render loop (`tiers` variable) is now automatic — no manual update needed.
6. Rebuild frontend using the legacy (non-BuildKit) build to guarantee fresh content:
   ```
   DOCKER_BUILDKIT=0 docker build -f frontend/Dockerfile -t stockai-frontend:latest . && \
   docker compose -f docker/docker-compose.yml up -d --force-recreate frontend
   ```
   **Do NOT use** `docker compose build frontend` — BuildKit silently serves cached layers.
   `--no-cache` is NOT needed (see "Recurring Issue: Slow Frontend Builds" below).

---

## Recurring Issue: SQLAlchemy text() Named Params with PostgreSQL ::type Casts (BUG-6)

**Symptom:** Signal writes silently fail — no exception logged, no rows written. DB signals table
has entries that are days old even though the scheduler appears to be running normally.

**Root cause:** SQLAlchemy `text()` named parameter binding fails when a parameter is immediately
followed by a PostgreSQL `::type` cast shorthand. For example:

```sql
-- BROKEN: SQLAlchemy binds :sid but leaves :sig unbound (sees :sig::signaltype as ambiguous)
INSERT INTO signals VALUES (:sid, :sig::signaltype, :hor::signalhorizon, :rsns::jsonb)
```

The compiled SQL shows `%(sid)s, :sig::signaltype` — `sid` is bound but `sig` is not. psycopg2
receives a literal `:sig::signaltype` string and raises `psycopg2.errors.SyntaxError`. If the
`except Exception` block swallows this, zero rows are written with no visible error.

**Fix (applied 2026-06-21):**
Always use `CAST(:param AS type)` instead of `:param::type` in SQLAlchemy text() queries:

```sql
-- CORRECT: CAST() syntax avoids the :: ambiguity
INSERT INTO signals VALUES (:sid, CAST(:sig AS signaltype), CAST(:hor AS signalhorizon), CAST(:rsns AS jsonb))
```

**Rule:** Never use PostgreSQL `::` cast shorthand with SQLAlchemy `text()` named parameters in
the same expression. This applies to any service using raw SQL with SQLAlchemy.

**What to check if signals go stale:**
```bash
docker logs stockai-signal-engine-1 --since 2h | grep -i 'error\|syntax\|invalid'
# Look for: psycopg2.errors.SyntaxError or "syntax error at or near ":"
```

---

## Recurring Issue: Alert Email Suppression — market:refresh_failed Flag (BUG-8)

**Symptom:** All email alerts are silently suppressed for up to 6 hours. `check_signal_alerts()` logs
`signal_alert.suppressed_refresh_failed` on every run and returns early without checking any alerts.

**Root cause (found 2026-07-01):** `_post()` in `scheduler.py` sets the Redis key `market:refresh_failed`
whenever ANY downstream POST call fails all 3 retries. This includes the EDGAR 8-K sync endpoint
(`event-intelligence:8010/events/sync/8k`), which can legitimately time out when there's a large batch
of 8-K filings. A single EDGAR timeout suppresses ALL signal alerts for 6 hours.

The key value is the URL that failed (not a boolean). `check_signal_alerts()` checks `exists()` on the
key — if the key exists for ANY reason, all alerts are blocked.

**Fix applied (2026-07-01):** Removed the `setex` call from `_post()`. The function now logs the HTTP
failure but does NOT set the global flag. The per-symbol price freshness check inside `check_signal_alerts()`
(stale_cutoff = 4 days) is the correct safety net for stale data.

**Immediate fix if alerts are suppressed:**
```bash
docker exec stockai-redis-1 redis-cli exists market:refresh_failed   # 1 = flag is set
docker exec stockai-redis-1 redis-cli get market:refresh_failed      # shows which URL failed
docker exec stockai-redis-1 redis-cli del market:refresh_failed      # clears it
```

**What to check:**
1. `docker logs stockai-market-data-1 --since 6h | grep 'suppressed_refresh_failed'` — confirms suppression
2. `docker logs stockai-market-data-1 --since 6h | grep 'http_failed'` — shows which URL triggered it
3. If `event-intelligence:8010/events/sync/8k` keeps timing out: check event-intelligence container health
   and whether the EDGAR API is rate-limiting or timing out

**Design invariant:** The `market:refresh_failed` flag MUST NOT be set by ancillary service calls
(EDGAR 8-K, calibration, research triggers). It should only be set by code that directly indicates
price data is stale. Currently the flag is effectively deprecated — price freshness is checked per-symbol.

---

## Recurring Issue: hk_connect_flows Logging TypeError (BUG-9)

**Symptom:** `hk_connect_flows` scheduler job shows `Error` status. Log entry: `Logger._log() got an
unexpected keyword argument 'processed'`. Job runs for ~5m 45s (processing all HK stocks) then fails
at the final log.info() call.

**Root cause (found 2026-06-30):** The module-level `log` proxy in `hk_connect.py` is a structlog
`BoundLoggerLazyProxy`. With `cache_logger_on_first_use=True`, the proxy doesn't cache until its first
real method call. All `log.debug()` calls inside the loop are no-ops (filtered at INFO level), so the
proxy hasn't cached before line 181. The proxy then caches at the final `log.info()` call in the
APScheduler thread context. In some production conditions, the logger resolves to a stdlib Logger
instead of structlog's PrintLogger, and `Logger._log()` rejects keyword args.

**Fix applied (2026-07-01):**
1. `hk_connect.py`: Added `configure_logging()` at the top of `ingest_southbound_flows()`. This ensures
   structlog is configured with `PrintLoggerFactory` before the first real log call at line 181.
2. `common/logging.py`: Added explicit `logger_factory=structlog.PrintLoggerFactory()` and
   `context_class=dict` to `structlog.configure()`, making the configuration complete.

**What to check if hk_connect_flows shows error:**
```bash
docker logs stockai-market-data-1 --since 24h | grep 'hk_connect'
# Confirm configure_logging present in hk_connect.py:
docker exec stockai-market-data-1 grep 'configure_logging' /app/src/services/hk_connect.py
```

**Recovery:** After a failed run, trigger manual HK data refresh. The hk_connect_flows table will
backfill on next successful run (job runs Mon-Fri 17:00 HKT = 09:00 UTC).

---

## Recurring Issue: Stale Rankings — jose Missing from ranking-engine (BUG-10)

**Symptom:** Rankings are 7+ days old even though scheduler appears to be running. `POST /rankings/refresh?market=US` returns 401. Paper trading engine uses stale K-scores for `min_kscore` gate.

**Root cause (found 2026-07-01):** ranking-engine image was built before `python-jose` was added to `requirements.txt`. The same jose-missing-from-container pattern as signal-engine (Jun-17) and ml-prediction (Jun-19). `shared/common/jwt_auth.py` does `from jose import JWTError, jwt` — if that fails, all auth-protected endpoints return 401.

**Fix:**
```bash
docker exec stockai-ranking-engine-1 pip install 'python-jose[cryptography]==3.3.0'
# Verify:
docker exec stockai-ranking-engine-1 python3 -c 'from jose import jwt; print("jose OK")'
# Rebuild image so it persists:
docker compose -f docker/docker-compose.yml build ranking-engine && docker compose -f docker/docker-compose.yml up -d ranking-engine
# Trigger manual refresh:
docker exec stockai-market-data-1 python3 /tmp/rank_refresh.py  # or use inline token script
```

**Trigger manual ranking refresh:**
```bash
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
for mkt in ['US','HK']:
    r = httpx.post(f'http://ranking-engine:8004/rankings/refresh?market={mkt}', headers={'Authorization':f'Bearer {tok}'}, timeout=10)
    print(mkt, r.status_code, r.text[:80])
"
```

**Also found (same audit):** portfolio-optimizer missing jose → `/optimize` returning 401 for all users. Same fix: `pip install jose` + rebuild portfolio-optimizer image.

**What to check if rankings go stale:**
```bash
docker logs stockai-market-data-1 --since 2h | grep 'rankings.*401\|401.*rankings'
docker exec stockai-ranking-engine-1 python3 -c 'from jose import jwt; print("OK")'
# Check last ranking update:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
r = s.execute(text('SELECT COUNT(*), MAX(as_of)::date FROM rankings')).fetchone()
print('rankings:', r); s.close()"
```

---

## System Port Map (Verified 2026-07-01 from Dockerfiles)

Previous documentation had wrong ports. Correct internal Docker network ports:

| Service | Port |
|---|---|
| api-gateway | 8000 |
| market-data | 8001 |
| technical-analysis | 8002 |
| ml-prediction | 8003 |
| ranking-engine | 8004 |
| signal-engine | 8005 |
| strategy-engine | 8006 |
| portfolio-optimizer | 8007 |
| research-engine | 8008 |
| decision-engine | 8009 |
| event-intelligence | 8010 |

**Note:** Only api-gateway (8000) is exposed externally. All others are Docker-internal only. Nginx proxies `lausing.com` → `localhost:8000`.

---

## Known Ongoing Limitations

- Broker commission: `commission_per_share` defaults to 0.0 (user's broker is commission-free)
- Survivorship bias in ML training data (delisted stocks not included) — requires external data source
- Walk-forward backtest deferred (2+ weeks of work)
- Forward return tracking (INT-8) not yet implemented

---

## Recurring Issue: EC2 Disk Fills Up from Dangling Docker Images

**Symptom:** `docker build` fails mid-copy with `no space left on device`, even though the
image being built is a normal size. `df -h /` shows the root volume nearly 100% full.

**Root cause (found 2026-07-03):** Every `DOCKER_BUILDKIT=0 docker build --no-cache` for the
frontend (the required pattern per this file's Deployment Pattern section) leaves the
previous image's layers behind as dangling `<none>:<none>` images once the tag moves to the
new build. These accumulate silently across repeated deploys — one session's worth of
frontend rebuilds alone consumed 77GB of reclaimable, unused image layers.

**Fix:** `docker image prune -f` — safe, only removes dangling/untagged images, never touches
anything currently running or tagged. Freed 460MB → 76GB available in the 2026-07-03 incident
with zero container disruption (all services stayed healthy throughout).

**What to check before any frontend rebuild:**
```bash
df -h /                    # if root volume is >90% full, prune first
docker system df           # shows reclaimable space by type
docker image prune -f      # safe cleanup — dangling images only
```

**Consider:** a periodic (weekly) `docker image prune -f` cron job on EC2 so this doesn't
require noticing a failed build first.

---

## Recurring Issue: Slow Frontend Builds (24–47 min) — `--no-cache` Was Unnecessary

**Symptom:** `docker build -f frontend/Dockerfile` (with `DOCKER_BUILDKIT=0`, per the Deployment
Pattern section) routinely took 24–47 minutes on the EC2 t3.medium, even for tiny changes (a few
lines in one file). Build time trended upward across a session (24 → 28 → 40 → 47 min across four
consecutive deploys on 2026-07-07), which looked like — but was not — EC2 resource degradation.

**Root cause (found 2026-07-07):** The deployment pattern included `--no-cache`, which disables
ALL Docker layer caching, not just the specific BuildKit cache bug it was meant to guard against.
`frontend/Dockerfile` has a multi-stage build where `RUN npm install --legacy-peer-deps` is its own
early layer, keyed only on `package.json`/`package-lock.json` (see `COPY frontend/package.json
frontend/package-lock.json* ./` before the install line) — this layer is safe to cache and almost
never needs to be invalidated across normal deploys, since dependencies change far less often than
application source. `--no-cache` forced a full `npm install` from the registry on every single
deploy regardless, which is the actual reason builds took as long as they did — not `improvements.tsx`'s
size as initially (incorrectly) suspected mid-investigation, and not EC2 hardware degrading.

**The original justification for `--no-cache` doesn't hold up:** the CLAUDE.md warning that
motivated it was about `docker compose build --no-cache frontend` silently serving stale layers —
that bug is specific to BuildKit's cache, not Docker's classic (non-BuildKit) cache. Once
`DOCKER_BUILDKIT=0` is set and `docker build` is invoked directly (not via `docker compose build`),
the classic builder's normal layer caching is safe — cached layers are correctly invalidated when
their `COPY`'d inputs change, which is exactly the guarantee needed.

**Verification before trusting this (important — don't just take the theory on faith):** built
with `DOCKER_BUILDKIT=0 docker build -f frontend/Dockerfile -t stockai-frontend:cache-test .` (no
`--no-cache`, separate test tag so `latest`/prod traffic was never at risk) and confirmed BOTH: (1)
build time — **~6 minutes**, vs. 24–47 minutes with `--no-cache`; (2) freshness — grepped the built
image's compiled JS chunks for two strings that only existed in that session's latest, uncommitted-
until-then source (`'Unusual Vol Today'`, `'Min RVOL'`) and confirmed both were present in
`screener-*.js` and `improvements-*.js`, proving the cached build correctly picked up the latest
source rather than serving something stale.

**Investigation mistake worth noting for next time:** while monitoring the test build, `ps aux |
grep docker build` kept showing a process as "still running" long after the actual image had
finished (confirmed later via `docker images ... --format '{{.CreatedAt}}'`, which showed the real
6-minute completion time). A lingering shell/SSH pipeline process, not the build itself, was still
alive. **Always check the image's actual `CreatedAt` timestamp to determine whether a build
finished — `ps aux` can show a stale process long after `docker build` itself has completed.**

**Fix:** Deployment Pattern (above) updated to drop `--no-cache` — `DOCKER_BUILDKIT=0 docker build
-f frontend/Dockerfile ...` (no `--no-cache`) is now the standard. `docker compose build frontend`
(going through docker compose) must still never be used, regardless of cache flags — that's the
part of the original warning that remains true.

**What to check if builds are slow again:**
```bash
# Confirm which build path is being used — must be a direct `docker build`, not `docker compose build`
# Confirm --no-cache is NOT present (it shouldn't be, per this fix)
# Check actual completion via image timestamp, not `ps aux`:
docker images stockai-frontend:latest --format '{{.CreatedAt}}'
```
If builds are still slow with `--no-cache` correctly removed, the next suspect is `npm run build`
itself (Next.js compiling/statically-generating every page) — `improvements.tsx` is 13,700+ lines
and growing every session; splitting it up or trimming its content would be the next lever to pull,
but wasn't needed once `--no-cache` was correctly identified as the actual cause here.

---

## Recurring Issue: Research Generation "NetworkError" in Browser Despite Server Success

**Symptom:** Clicking "Generate Report" (or the research page auto-triggering a report) shows
"NetworkError when attempting to fetch resource" in the browser, but refreshing the page shows
the report loaded fine — the generation actually succeeded server-side, only the client-side
fetch that triggered it failed.

**Root cause (found 2026-07-06):** `/api/research/*` was still proxied browser → Nginx →
Next.js (port 3000) → api-gateway (port 8000) → research-engine — a "double hop" through the
Next.js rewrite layer. Research report generation legitimately takes 2-3 minutes (LLM call),
and long-lived connections through the extra Next.js hop are fragile — this is the EXACT same
failure mode that was already fixed for AI chat (`/api/ai/`) on an earlier date, per the comment
already in `stockai.conf`: "AI chat routes directly to the API gateway — bypasses Next.js proxy
to eliminate the double-hop that caused NetworkError in Firefox". The 2026-06-14 fix
(`e419775`) only raised timeouts for research (`proxy_read_timeout 200s` + Next.js
`proxyTimeout: 200000`) — it did NOT apply the same direct-bypass fix later used for chat, so
research kept the fragile extra hop even after chat was fixed.

**Fix applied (2026-07-06):** Changed `/etc/nginx/conf.d/stockai.conf`'s `location
/api/research/` block to `proxy_pass http://127.0.0.1:8000/research/;` (was
`http://127.0.0.1:3000;`), with the same header-forwarding lines as the `/api/ai/` block
(`Host`, `X-Real-IP`, `Authorization`, `Content-Type`). This is an EC2-only config file, not
tracked in git — there is no local copy of `stockai.conf` in the repo, so this fix must be
re-applied by hand if the EC2 instance is ever rebuilt. A backup of the pre-fix config was left
at `/etc/nginx/conf.d/stockai.conf.bak-<date>` on the instance.

**What to check if this recurs (or a similar NetworkError shows up on a new long-running
endpoint):**
```bash
# On EC2 — confirm the research block bypasses Next.js directly
sudo grep -A6 "location /api/research/" /etc/nginx/conf.d/stockai.conf
# Should show proxy_pass http://127.0.0.1:8000/research/ (NOT :3000)

# Test it responds through the direct path (401 without a token is expected/correct):
curl -s -D - -o /dev/null https://lausing.com/api/research/AAPL | head -5
```

**Design invariant:** Any endpoint whose real work can run longer than ~30-60s (LLM calls,
batch backtests, tuning sweeps) should get its own Nginx `location` block that proxies straight
to `api-gateway:8000`, bypassing the Next.js rewrite hop entirely — matching the `/api/ai/` and
now `/api/research/` pattern. Do not just raise timeouts on the existing Next.js-hop block;
that was tried once for research and the underlying double-hop fragility remained.

---

## Recurring Issue: Adding a Column to an EXISTING Table Doesn't Auto-Apply — `create_all()` Only Creates Missing Tables

**Symptom:** Adding a new field to an existing SQLAlchemy model (e.g. a new column on `User`,
which already has rows in production) breaks EVERY query against that model in production
immediately after deploy — `psycopg2.errors.UndefinedColumn: column users.new_field does not
exist`. This is different from (and easy to confuse with) the "stale `shared/db/` in a
container" issue below — this happens even with a perfectly fresh, freshly-rebuilt container.

**Root cause (found 2026-07-10):** This repo has no active Alembic migrations (`alembic.ini`
exists but zero real migration files do) — the only schema-application mechanism is
`Base.metadata.create_all()` in `shared/db/session.py`, run on every service startup.
`create_all()` only creates tables that don't exist yet; it does **not** `ALTER TABLE` an
existing table to add a newly-declared column. Adding a brand-new table's model (e.g.
`PushSubscription`, same session) works fine via this mechanism — but adding a field to an
existing, already-populated table (e.g. `User.notification_webhook`) silently does nothing to
the real schema, and the gap isn't visible until the first request that queries that column.

**Fix applied (2026-07-10):** Manually ran `ALTER TABLE users ADD COLUMN IF NOT EXISTS
notification_webhook VARCHAR(2048);` directly against production Postgres to add the missing
column. Login (`GET /auth/me`) recovered immediately once the column existed.

**What to check before adding ANY field to an EXISTING model (not a new one):**
```bash
# Does the table already exist and have rows? If yes, create_all() will NOT add the new column.
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\d table_name"
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "SELECT COUNT(*) FROM table_name"
```
If the table already exists, a manual `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...` must run
against production (and any local dev Postgres) BEFORE or immediately after deploying the code
change — plan this as an explicit deploy step, not something the deploy pipeline does for you.

**Design invariant:** `create_all()` is only sufficient for adding a brand-new table. Any new
column on an existing table needs its own manual `ALTER TABLE`, run against every environment
(production, local dev) separately — there is no migration system doing this automatically in
this repo today. Consider this a standing gap until real Alembic migrations are adopted.

---

## Recurring Issue: Local Dev Containers Run Stale `shared/db/` — AttributeError on Recently Added Model Fields

**Symptom:** A backend endpoint that reads a recently-added SQLAlchemy model field crashes with
`AttributeError: 'ModelName' object has no attribute 'field_name'` on **local dev only** — the
same endpoint works fine in production. Confirmed instances: `TuneHistory` missing from
signal-engine's `shared/db/__init__.py` (2026-07-06), `Stock.index_membership` missing from
ranking-engine's `shared/db/models.py` (2026-07-07, crashed `GET /rankings` — which also broke
the Screener page and its RVOL feature, since both read from that endpoint).

**Root cause:** `shared/db/` is baked into every service's Docker image at build time (see
Deployment Pattern above — it is NOT one of the directories `docker cp` normally targets for
day-to-day code changes). When a new field is added to `shared/db/models.py` and deployed via
`docker cp` to the ONE service that immediately needs it (e.g. signal-engine for `TuneHistory`),
every OTHER local dev container keeps running its old, pre-existing `shared/db/` copy from
whenever its image was last built — silently, with no error, until something finally tries to
read the new field through that stale container's ORM class.

**Fix pattern (apply to any container showing this error):**
```bash
docker cp shared/db/__init__.py stockai-<service>-1:/app/shared/db/__init__.py
docker cp shared/db/models.py stockai-<service>-1:/app/shared/db/models.py
docker exec stockai-<service>-1 rm -rf /app/shared/db/__pycache__
docker restart stockai-<service>-1
```

**Check ALL local dev containers proactively, not just the one that errored** — this bug is
systemic, not isolated to one service. On 2026-07-07, checking every container after fixing
ranking-engine found 4 MORE containers with the exact same staleness (technical-analysis,
event-intelligence, strategy-engine, portfolio-optimizer, api-gateway all missing
`index_membership` too) that had not yet crashed only because nothing had exercised that
specific field on them yet:
```bash
for c in market-data signal-engine ranking-engine technical-analysis event-intelligence \
         research-engine api-gateway ml-prediction decision-engine strategy-engine portfolio-optimizer; do
  echo -n "$c: "; docker exec stockai-$c-1 grep -c '<newest_field_name>' /app/shared/db/models.py
done
```
(A `0` or a non-fatal `grep` exit code with `0` output — not a real error — both mean "stale,
missing the field.")

---

## Recurring Issue: PRODUCTION Container Ran Stale Service-Local Files Despite Git Being Up To Date

**Symptom:** `docker restart` on a production container crashes on startup with
`ImportError: cannot import name 'X' from 'module'`, even though `git log`/`git status` on the
EC2 host show the repo checkout is fully up to date and does NOT reference `X` anywhere.

**Root cause (found 2026-07-07):** An earlier fix (TA-D1, removing the dead `vwap()` indicator)
was committed, pushed, and deployed to **local dev** — but the corresponding `docker cp` step to
the **production** `stockai-technical-analysis-1` container was never actually run at the time.
`git pull` on the EC2 host updates the host's checkout, not the running container's `/app/src/`
files — those only change via an explicit `docker cp`. Production kept running its old
`routes.py`/`indicators/__init__.py` (both still importing the now-deleted `vwap`) completely
unnoticed, because nothing had restarted that container since the gap was introduced — routine
`docker restart`s only became necessary again once a later, unrelated fix (T237, ATR/pattern
fixes) needed deploying to the same container, which is what finally surfaced the crash.

**This means "deployed to production" was previously asserted for TA-D1 without actually being
true** — a gap between the deployment checklist being followed in spirit (committed, pushed,
`git pull`'d on EC2) and in fact (the specific `docker cp` + restart for that specific service
never happening, or being silently skipped/forgotten).

**Fix applied:** Synced the current (correct, git-matching) `routes.py` and
`indicators/__init__.py` from the EC2 checkout into the container via `docker cp`, cleared
`__pycache__`, restarted. Confirmed via `grep vwap` inside the container (empty result) and a
successful `/health` check plus a real `GET /ta/{symbol}/patterns` call.

**What to check after ANY deploy that touches a service's Python files:**
```bash
# Immediately after docker restart, tail logs for an ImportError/crash-loop —
# don't just assume "docker restart" succeeding means the app booted:
docker logs stockai-<service>-1 --tail 20
# A clean boot ends in "Application startup complete." / "Uvicorn running on ...".
# If you see a traceback instead, the container's /app files disagree with the
# current git checkout — diff them directly:
docker exec stockai-<service>-1 grep -n '<symbol_removed_or_added_by_the_last_fix>' /app/src/<file>.py
```

**Design invariant:** Never assume a past "deploy to production" step actually completed just
because it's described as done in a tracker entry or prior session summary — after any
`docker restart` in production, always tail logs and confirm a clean startup message before
considering the deploy verified. A container that "looks running" (`docker ps` shows `Up`) can
still be serving requests from **before** a crash-and-silent-fallback, or — as here — simply
never picked up the intended change at all until the next unrelated restart exposes it.

**CORRECTED 2026-07-08 — production CAN also be affected, this claim was wrong:** this section
previously claimed "production is NOT usually affected" based on two prior checks
(TuneHistory, index_membership) that happened to find production current. On 2026-07-08, a
routine signal-engine deploy (unrelated congress-score fix, T237-EI1) crashed on restart with
the EXACT same `ImportError: cannot import name 'TuneHistory' from 'db'` on **production** —
proving production's `shared/db/` had silently drifted too, the same way local dev containers
do. The original theory (production always copies `shared/db/` explicitly per the Deployment
Pattern) is only true when someone actually remembers to run that step for every affected
container on every relevant deploy — exactly the kind of manual step that gets silently skipped,
per the pattern already documented above ("PRODUCTION Container Ran Stale Service-Local Files").
Fixed by syncing `shared/db/__init__.py` + `models.py` to `stockai-signal-engine-1` and
restarting — same fix pattern as the local-dev case. **Do not assume production's `shared/db/`
is current just because it "usually" was in the past** — always verify with a clean-startup log
check after any restart, the same discipline required for service-local files.

**Consider:** after any `shared/db/models.py` change, proactively sync `shared/db/` to every
local dev container in the same pass, rather than waiting for each one to surface its own crash
on a different field weeks later.

---

## Paper Portfolio Badges Are Two Independent Layers — Don't Expect Them to Always Match Across Portfolios

**Context (2026-07-07/08):** User asked why HK SWING Portfolio and HK GROWTH Portfolio — same
market, both HK — showed different badges on the Paper Portfolio card grid. This surfaced a real
bug (below) but also a conceptual point worth documenting so it isn't re-investigated as a bug
every time it looks like this again: **the two portfolios are only supposed to agree on layer 1,
never necessarily on layer 2.**

**Layer 1 — portfolio-level / market-level gates** (`_write_gate_block()` in
`paper_trading_engine.py`, read by `/paper-portfolio/list`'s `entry_gate_block` field). One of 11
gates: `drawdown`, `daily_loss`, `weekly_loss`, `weekly_gain_lock`, `consecutive_losses`,
`daily_entry_cap`, `regime_bear`, `regime_risk_off`, `regime_suspension`, `entry_throttle`,
`heat_brake`, `index_trend`, `market_cluster_cap`. Most of these are genuinely per-portfolio
(drawdown, consecutive losses, etc.), but the three `regime_*` gates are **market-wide** — every
portfolio in the same market (`cfg["market"]`) reads the identical cached regime dict from
`GET /stocks/regime?market=HK` (see `get_last_hk_regime()` / `get_last_regime()`, the single
canonical classifier, T232-DL-REGIME5X). **Two portfolios in the same market showing DIFFERENT
regime-gate badges at the same moment is always a bug, not expected behavior.**

**Layer 2 — per-candidate "why no entry" summary** (`_write_no_entry_summary()`, read as a
separate Redis key `paper:no_entry_summary:{portfolio_id}`, shown as e.g. "Not trading: Volume
below..."). This fires when every gate in layer 1 is clear but every individual BUY candidate that
portfolio scanned still failed its own per-symbol check (K-Score, volume_z, TA score, cooldown,
etc.). This is **inherently portfolio/style-specific** — SWING and GROWTH read from different
watchlists (`Watchlist.trading_style`), so they are frequently scanning entirely different symbols
on the same tick, each with their own thresholds. **Two portfolios in the same market showing
DIFFERENT layer-2 badges is normal and expected**, not a sign anything is broken — it means their
respective candidate lists happened to fail different (or no) per-symbol checks that cycle.

**The bug found this session (T237-GATE1, fixed 2026-07-07):** layer-1's `_write_gate_block()`
Redis key only self-expired after a 4h TTL — nothing cleared it early once a portfolio actually
passed all its gates again in a later scan. HK GROWTH kept showing a "Risk-Off Regime" badge for
~2 hours after HK's regime had already recovered to `choppy`, while HK SWING (whose key had
already expired/cleared) showed nothing — this LOOKED like the two portfolios disagreeing on
regime, but was actually just one stale Redis key. Fixed by adding an unconditional
`_clear_gate_block(portfolio.id)` call once a portfolio passes the last layer-1 gate
(`market_cluster_cap`) in `_scan_for_entries()`, so the badge clears immediately instead of
waiting out the TTL. This fix is gate-agnostic — it protects against staleness on all 11 layer-1
gates, all markets, all portfolios, not just the HK regime case that surfaced it.

**What to check if this looks wrong again:**
```bash
# Confirm both portfolios in the same market are reading the identical regime:
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/regime?market=HK'
# Check each portfolio's actual layer-1 gate Redis state directly (bypass the UI):
docker exec stockai-redis-1 redis-cli get paper:gate_block:<portfolio_id>
docker exec stockai-redis-1 redis-cli ttl paper:gate_block:<portfolio_id>
# If two same-market portfolios show DIFFERENT regime_* gate reasons — that's the bug class
# above; if they show different non-regime reasons (volume, K-score) — that's layer 2, expected.
```

---

## Recurring Issue: `docker compose up -d --force-recreate <one-service>` Can Recreate EVERY Service — And Recreation Silently Reverts `docker cp`-Patched Files

**Symptom:** Running `docker compose -f docker/docker-compose.yml up -d --force-recreate frontend`
(the standard, documented frontend deploy step) unexpectedly recreates every other service too —
market-data, ml-prediction, signal-engine, decision-engine, etc. — not just frontend. Any
in-progress background work in one of those other containers (e.g. a long-running model retrain
started via `docker exec ... python3 -c "..."`) gets killed when its container is destroyed and
rebuilt. Separately — and more dangerously — any file previously deployed via `docker cp` (the
standard "hotfix without a full image rebuild" pattern used throughout this file) is **silently
reverted** to whatever was baked into the image at its last build, because recreation destroys
the container's writable layer entirely and starts fresh from the image.

**Root cause (found 2026-07-08):** An `.env` change (SMTP_PASSWORD) earlier in the same session
apparently altered docker-compose's computed config hash for other services too (likely because
they share `.env` as their env_file), making compose consider them "changed" and eligible for
recreation on the next `up -d`, even though only `frontend` was named. This surfaced in two ways
in the same incident: (1) a production meta-model retrain running inside `stockai-ml-prediction-1`
was silently killed mid-run when that container was swept into the same recreate; (2) after
restarting the retrain, it *appeared* to succeed (wrote a new artifact, real AUC) but actually
trained against a **stale, reverted** `builder.py` — the recreate had silently undone an earlier
`docker cp` of a real code fix (removing a feature column), so the retrain used the OLD feature
set while live inference was already using the NEW one, causing a real shape-mismatch error
("index 66 is out of bounds for axis 1 with size 66") that looked like a fresh bug but was
actually stale-file poisoning of the retrain itself.

**What to check before AND after any `docker compose up -d --force-recreate <service>`:**
```bash
# Before: note which containers are currently running which images/uptimes, so you can tell
# afterward if anything you didn't name also got recreated:
docker ps --format '{{.Names}}: {{.Status}}'

# After: re-run the same command and diff — any container with a suspiciously fresh "Up X
# seconds" that you didn't intend to touch was swept in too:
docker ps --format '{{.Names}}: {{.Status}}'

# If ANY service besides the one you named got recreated, re-verify every docker cp-patched
# file in that service is still current — recreation reverts to the baked-in image silently,
# with no error, no warning:
docker exec stockai-<service>-1 md5sum /app/<path/to/file.py>
md5sum services/<service>/src/<path/to/file.py>   # compare against the git checkout
# If they differ, re-run the docker cp + restart for that file before trusting anything that
# depends on it (a retrain, a manual verification, etc.) — a mismatch here means the container
# is running an older version of the code than what's actually committed.
```

**Design invariant:** After ANY `docker compose up -d --force-recreate`, treat every currently
running container as a candidate for having reverted, not just the one you named — check `docker
ps` before and after, and re-verify file checksums on anything you'd previously hotfixed via
`docker cp` in a container that got swept in. Never assume a long-running background job (a
retrain, a bulk backfill) survived a `docker compose up` on an unrelated service without checking
`docker ps`/process state directly afterward.

---

## Recurring Issue: Congress Trading Data Silently Empty — Free Source Domains Permanently Dead

**Symptom:** `/congress/trades` (market-data) returns an empty list with no error to every real
user; `congress.tsx`/`insider.tsx` show a permanently empty page with zero indication anything is
broken. `congress_trades` table (shared, written by event-intelligence) has 0 rows no matter how
long the scheduler has been running. Catalyst scoring's congress component
(`compute_congress_score()`, `_compute_risk_score()`'s congress-selling check) silently operates
on zero real data — not fail-open-with-a-flag, just quietly always-zero.

**Root cause (found 2026-07-09):** Both free congress-trading data sources this app depended on —
`housestockwatcher.com/api/transactions` and `senatestockwatcher.com/api/transactions` — are
**permanently dead**: the domains fail to resolve via DNS at all (not a 403/301/timeout on a live
host — confirmed via direct `curl`/`nslookup` from inside the running market-data container). The
underlying project's maintainer has been inactive since March 2021 and never responded to a 2024
GitHub issue asking about a shutdown. This affected TWO independent call sites that both silently
degraded to empty results on fetch failure with no alerting: event-intelligence's
`sync_congress_trades()` (writes the shared `congress_trades` table) and market-data's
`/congress/trades` endpoint (`_fetch_house`/`_fetch_senate`, since replaced by `_fetch_kadoa`).

**Fix applied (2026-07-09):** Repointed both call sites to
`https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json`
— a live, unauthenticated, MIT-licensed GitHub JSON feed that updates via daily automated commits.
Covers House Clerk + Senate eFD + OGE executive-branch filings in one combined response (a rolling
~5000-row window, not full history — fine for keeping the feed current going forward, not a
substitute for deep historical backfill). Both call sites now filter to congress-only records
(`branch == "congress"` in event-intelligence; `chamber in ("house", "senate")` in market-data) —
executive-branch OGE filings are ~85% of the feed's rolling window and are NOT congress trades.
Verified live in production: triggered a real sync via `POST /events/sync/congress`, confirmed
441 real rows upserted into `congress_trades` with correct politician names, tickers, transaction
types, and dates.

**What to check if this recurs (either this source dies too, or a similar silent-empty-fetch
pattern shows up elsewhere):**
```bash
# Confirm the current source is actually reachable — DNS failure looks different from a 4xx/5xx:
docker exec stockai-market-data-1 curl -sv 'https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/trades.json' --max-time 15 2>&1 | head -20
docker exec stockai-market-data-1 nslookup raw.githubusercontent.com

# Check current row count / staleness in the shared table:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
print(s.execute(text('SELECT COUNT(*), MAX(trade_date) FROM congress_trades')).fetchone())
s.close()"

# Manually trigger a resync (uses the same _service_token() pattern as other scheduler jobs):
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://event-intelligence:8010/events/sync/congress', headers={'Authorization': f'Bearer {tok}'}, timeout=30)
print(r.status_code, r.text[:200])"
```

**Design invariant:** Any external free-tier data source this app depends on should have its
fetch failures surfaced somewhere visible (a log line grep, a staleness check) rather than
silently degrading to an empty result — the original bug went undetected for an unknown period
specifically because both call sites' `except: return []` pattern is indistinguishable from
"genuinely no trades today" at the API response level. When adding a new free external data
source, prefer one with committed, checkable update activity (this fix's replacement source
updates via visible daily commits) over an opaque scraped API with no way to verify liveness
without actually calling it.

---

## Feature Reference: Congress Trading Data (Two Independent Implementations)

There are TWO separate, non-wire-compatible congress-trading code paths — this is intentional
duplication tracked as architectural debt (see `T233-ARCH-CONGRESS-DEDUP` in
`frontend/src/pages/improvements.tsx`), not a bug, but worth knowing both exist:

1. **`services/market-data/src/api/congress.py`** — `GET /congress/trades`. No DB persistence;
   live-fetches on every request from `_fetch_kadoa()` (or Quiver Quantitative if
   `quiver_api_key` is configured in Settings — richer metadata, $30/mo). Response is
   PascalCase (`Ticker`, `Date`, `Politician`, `Transaction`, `Min`, `Max`, `Party`, `State`,
   `Chamber`, `ReportDate`), binary `Purchase`/`Sale`/`Exchange` transaction type. Consumed by
   `frontend/src/pages/congress.tsx` and `frontend/src/pages/insider.tsx`.

2. **`services/event-intelligence/src/services/congress.py`** — `POST /events/sync/congress`
   (scheduled job) writes to the shared `congress_trades` DB table via
   `sync_congress_trades()`; `GET /events/congress/*` reads from it. Response is snake_case
   (`transaction_type`, `politician_name`, etc.), 4-state transaction type
   (purchase/sale/exchange/unknown), and feeds `compute_congress_score()` for catalyst scoring.
   Consumed by `frontend/src/pages/intelligence.tsx` and the catalyst-scoring pipeline.

Both now source from the same kadoa-org feed (see the Recurring Issue section above) but keep
independent parsing/schema — a fix to one's data source does NOT automatically fix the other;
they must each be checked/fixed separately, exactly as happened when the previous free source
died for both simultaneously.

---

## Recurring Issue: "It's Reachable" ≠ "It's Current" — Always Check Last-Modified, Not Just HTTP 200

**Symptom:** A recommended external data source returns `HTTP 200` and looks like a solid,
official choice, but is actually not being maintained anymore — the page/file is still served,
just frozen at some point in the past. Reachability alone gave false confidence.

**Root cause (found 2026-07-14, while sourcing data for the CAPE/AI-bubble-warning feature):**
An initial research pass recommended Robert Shiller's own Yale dataset
(`econ.yale.edu/~shiller/data/ie_data.xls`) as the primary CAPE data source, citing that it
returned `HTTP 200` as proof it was "verified live." A direct re-check before committing to
that architecture found the file's `Last-Modified` header was **October 2023** — ~2.75 years
stale at investigation time — and Shiller's own site had migrated to a new Yale SOM page with
no working direct CAPE download found there either. The file being downloadable said nothing
about whether its *contents* were still being updated.

**What to check before trusting any "the data source is live" claim** (from an agent, a web
search summary, or your own quick check):
```bash
curl -sI "<candidate-url>" -A "Mozilla/5.0" --max-time 15
# Look at Last-Modified, not just the status code. A 200 with a Last-Modified from
# months/years ago means the URL still resolves but the DATA behind it is frozen.
```
Also directly inspect a few of the most recent rows/values in the actual payload and compare
against today's date — a `.csv`/`.xls` ending "2 years ago" is a hard stop, not a caveat.

**Fix pattern applied:** Re-researched and found `multpl.com` publishes a genuine Atom feed per
indicator (`multpl.com/{indicator}/atom`) — confirmed as a real, intentional, site-wide feature
(identical structure across `shiller-pe`, `s-p-500-pe-ratio`, `s-p-500-dividend-yield`, not a
one-off scrape) and verified via its own `<updated>` timestamp matching the current date, not
just a `200` on the URL. See the CAPE feature reference below for the full source used.

**Design invariant:** Before adopting ANY new external data source (especially one an agent or
a web-search summary recommends), verify current-ness directly — `Last-Modified` header, or the
payload's own embedded timestamp/most-recent-row — not just that the URL responds. An
"official" or "authoritative" source that has gone stale is worse than a well-verified
secondary source, because it looks trustworthy while silently serving frozen data.

---

## Process Note: Background Agents Can Drift Scope — Re-Confirm Before Deploying

**Observed 2026-07-14, while re-deriving 6 audit findings lost to an earlier spend-limit
interruption.** The user's instruction was narrow: recover those 6 specific candidates. The
background agents dispatched for this instead ran an open-ended fresh bug hunt across
untouched services (technical-analysis, signal-engine, market-data/strategy-engine) — a
reasonable-sounding interpretation, but broader than what was actually asked, and one agent got
stuck spawning further sub-agents and reporting a non-answer ("I'll wait for the other
agents...") instead of concrete findings.

Separately, once 2 of 3 resulting findings had been explicitly approved for fixing, a 3rd
finding arrived from a still-running background agent AFTER that approval — and very nearly
got bundled into the same deploy as the 2 approved ones, which would have shipped an
unapproved change to production under cover of an approved one.

**What to check going forward when using background/multi-agent workflows on this repo:**
1. If a background agent's report describes doing something broader than what was literally
   asked (e.g. "I also checked X and Y for good measure"), treat that extra output as
   candidate findings requiring their own explicit go-ahead — not as pre-approved just because
   they arrived attached to a task that WAS approved.
2. Before any deploy, re-list exactly which changes are being shipped and cross-check that
   list against what was actually approved in the conversation — especially if multiple
   findings/fixes accumulated across several turns or background completions.
3. If an agent's own final message describes waiting on other agents or otherwise doesn't
   contain a real, substantive answer, treat that as a failed/incomplete run and resume or
   re-dispatch it directly rather than assuming "no findings" or moving on.

---

## Feature Reference: CAPE (Shiller PE) — AI Bubble Warning Indicator

**Added 2026-07-14.** A macro valuation indicator (CAPE, the cyclically-adjusted P/E ratio for
the S&P 500) surfaced as a "Bubble Warning" tab on `frontend/src/pages/intelligence.tsx`.
Historically elevated CAPE readings have preceded major market corrections, but CAPE is a
slow-moving signal — it can stay "elevated"/"extreme" for years before any correction, so this
is framed as macro context, not a trade trigger.

**Data source:** `multpl.com`, NOT Yale's own `ie_data.xls` (see the Recurring Issue above for
why that source was rejected — found stale, 2.75 years old, at investigation time). Two
multpl.com endpoints are used:
- `multpl.com/shiller-pe/atom` — daily-updated Atom feed, current value. Confirmed as a
  genuine, site-wide feed pattern (same structure across every multpl indicator page).
- `multpl.com/shiller-pe/table/by-month` — stable `id="datatable"` HTML table, full history
  back to 1871, used for backfill/refresh of recent months.

Still an **unofficial third-party source** — same fragility class as the dead
housestockwatcher/senatestockwatcher congress-data incident, just a more stable access pattern
(a real Atom feed + a stable table ID, vs. an arbitrary scraped `<div>`). Monitor staleness the
same way as every other external feed in this app — see below.

**Architecture:**
- `shared/db/models.py` — `CapeReading` model, `cape_readings` table (new table; `create_all()`
  handles this automatically, no manual migration needed — see the `create_all()`-gap Recurring
  Issue above for when that ISN'T true).
- `services/event-intelligence/src/services/valuation.py` — `sync_cape_current()` (Atom feed),
  `sync_cape_history()` (by-month table), `cape_band()` (threshold classifier),
  `get_latest_cape()`/`get_cape_history()` (read side). `_parse_atom()`/`_parse_table()` are
  pure functions extracted specifically so they're testable against real captured fixture data
  without needing live network access in tests.
- `GET /events/valuation/cape` / `POST /events/sync/cape` in
  `services/event-intelligence/src/api/routes.py`.
- Scheduled job `sync_cape` at 08:45 UTC daily in `services/event-intelligence/src/scheduler.py`.
- `dq_check:cape_reading` entry in market-data's `_DQ_CHECKS` (`scheduler.py`) — 1080h/45-day
  staleness threshold, matching `valuation.py`'s own `stale` flag on the read side.

**Warning bands** (sourced from real historical CAPE peaks, not guessed):

| Band | CAPE range | Basis |
|---|---|---|
| Normal | < 30 | Long-run mean/median (1871–present) is ~16-17 |
| Elevated | 30–35 | Above historical norm |
| High | 35–40 | 1929 pre-crash peak was ~32-33 |
| Extreme | ≥ 40 | 2021 post-COVID peak ~38.6; Dec 1999 dot-com peak (all-time high) 44.19 |

**A real parsing bug this caught before production:** the by-month table's value cells contain
a leading `&#x2002;` (Unicode en-space) HTML entity before the actual number. A naive
`float(cells[1])` on the stripped cell text raises `ValueError`, which the per-row
`except (ValueError, IndexError): continue` swallows — silently producing **zero** synced rows
on every history-backfill run, with no error surfaced anywhere. Caught because
`tests/test_valuation.py` was written against real fixture data captured directly from
`multpl.com` (not hand-authored idealized HTML), which reproduced the bug immediately. Fixed by
stripping to `[^\d.]` before calling `float()`. **Lesson:** when writing a parser test for a
scraped/fed external source, capture and use a REAL response as the fixture — a hand-written
"clean" HTML sample will not surface the actual whitespace/entity quirks the real site emits.

**What to check if this goes stale or breaks:**
```bash
# Confirm both multpl endpoints are still live and current (check the date in the response, not
# just the status code — see the Recurring Issue above):
curl -sI "https://www.multpl.com/shiller-pe/atom" -A "Mozilla/5.0" --max-time 15
curl -s "https://www.multpl.com/shiller-pe/atom" -A "Mozilla/5.0" --max-time 15 | grep -o '<updated>[^<]*'

# Check current row count / staleness in the DB:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT COUNT(*), MAX(reading_date) FROM cape_readings;"

# Check the dq_check Redis key:
docker exec stockai-redis-1 redis-cli get dq_check:cape_reading

# Manually trigger a resync:
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from common.config import get_settings
from jose import jwt as _jwt
import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://event-intelligence:8010/events/sync/cape', headers={'Authorization': f'Bearer {tok}'}, timeout=30)
print(r.status_code, r.text[:400])
"
```

---

## Feature Reference: Tier 249 — Market-Mover Monitoring (P0/P1/P2)

**Built 2026-07-14/15.** User's original ask: "monitor the news or any information that would
make the market go up or down. Get current earning reports or CPI/FOMC before market starts,
analyze the impact. Or get the results from CPI/FOMC after they announce it ASAP and predict
the trend. Same for earnings and news." A Fable 5 consult broke this into 5 slices (P0–P4);
P0–P2 are built and live as of this writing. P3 (pre-market brief) and P4 (news pulse card)
are still `todo` in the tracker.

### The foundational bug this whole tier fixes: reference-period vs. release-date

`economic.py`'s original `sync_fred()` stores `event_date` as the observation's **reference
period** — e.g. `event_date="2026-06-01"` for June's CPI data — not the date BLS actually
**published** that number (July 14). These are two different axes wearing the same column
name. Any "alert me when CPI is released" feature needs the release-date axis; the reference-
period axis is for asking "what was June's CPI," which nothing in this tier needed. This gap
existed silently because `FRED_API_KEY` wasn't even set in production until this tier's work
started — `sync_fred()` had been no-op'ing (`fred_skip`) the whole time.

### P0 — Real release-date calendar (done)

- **FRED_API_KEY** set in production `.env` (get one free at
  fred.stlouisfed.org/docs/api/api_key.html). Rotated once already — see the log-leak section
  below for why.
- `economic.py`'s new `sync_fred_release_dates()` calls FRED's `fred/release/dates` endpoint
  (NOT `fred/series/observations`, which is what `sync_fred()` uses) per release_id in
  `_FRED_RELEASES`, with `include_release_dates_with_no_data=true` (required to see FUTURE
  scheduled dates — without it FRED only returns dates that already have data). Writes
  `{event_type}_release` rows (e.g. `cpi_release`) — a distinct event_type family from
  `sync_fred()`'s plain `cpi`/`nfp`/etc., so the two paths' rows never collide under
  `uq_economic_event(event_type, country, event_date)`.
- Scheduled daily at 06:15 UTC (`job_sync_fred_release_dates`) plus once at startup
  (`asyncio.create_task`, so a fresh deploy isn't empty until tomorrow's cron).
- market-data's `events_calendar()` now calls new `_macro_events_from_db()` first (reads the
  real `*_release` rows), and only falls back to the hand-maintained `_MACRO_2026` list for
  `(type, date-range)` combos the DB has no row for yet — `_MACRO_2026` is a safety net during
  rollout, not deleted.
- **Why BLS's own API was rejected as a data source** (relevant background for P2 below too):
  live research found BLS's own documentation states data is available via their v2 API
  ~1 day after the real release — disqualifying for same-day detection. FRED itself was
  confirmed live to have same-day availability (June 2026 CPI's `realtime_start` exactly
  equals its real July 14 release date).
- **Not built**: `EconomicEvent.expected_value` nowcast (Cleveland Fed proxy) — investigated
  and explicitly rejected. Cleveland Fed's inflation nowcast has no FRED series and no public
  API; the only live data is an internal FusionCharts JSON
  (`clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_{month,quarter,year}.json`)
  meant for their own chart widget. Fetched it directly and found real numbers, but the
  date-axis semantics were genuinely ambiguous (MM/DD labels with no year, all three files
  starting at the identical `08/20` regardless of month/quarter/year window) — could not
  confirm what a label actually means without rendering the real chart. Decided not to ship a
  data field whose correctness can't be verified. If revisited, the next step would be
  rendering the actual chart in a headless browser or comparing against an archived snapshot
  to pin down the axis, not re-guessing from the raw JSON.

### P1 — Earnings day-of alerts (done)

Two halves, both in `market-data/src/services/scheduler.py`, both scoped to `PriceAlert`-
subscribed users (not full watchlist/portfolio membership — a deliberate v1 scope-narrowing,
matching the existing `T230-ALERTING-EARNINGS-PROXIMITY` reminder's own audience rather than
introducing a wider join).

1. **Pre-market**: enriched the *existing* `T230-ALERTING-EARNINGS-PROXIMITY` day-of reminder
   (previously a generic "review your position" line) via new `_earnings_reminder_body()`,
   using `forward_eps`/`eps_beat_rate`/`eps_avg_surprise_pct` — all three already computed by
   `GET /stocks/{symbol}/fundamentals`, so this was pure wiring, not a new data source.
2. **Post-release**: genuinely new `check_earnings_reactions()`, a 1-minute-interval job (same
   cadence/lock pattern as `check_price_alerts`) reading event-intelligence's shared
   `earnings_events` table directly (same cross-service shared-table-read convention already
   used for `Ranking`/`Signal` elsewhere in this file) for symbols with `eps_actual` populated
   in the last 2 days. Fires one alert per `(user, symbol, report_date)` via a 7-day Redis
   dedup key, using the already-computed `surprise_pct`/`earnings_strength_score` — no LLM.

### P2 — Macro post-announcement fast reaction (done)

The literal "get the results ASAP and predict the trend" ask. The honest, buildable version:
fast detection of the real released number + an LLM reaction read — not an actual direction
prediction, which nobody can honestly deliver for an unreleased number.

**Detection — two independent, release-day-armed polls, both cheap no-ops on non-release days:**

- `services/event-intelligence/src/services/macro_reaction.py`'s
  `check_release_day_fast_poll()` — armed only 8:30–9:59am ET on weekdays
  (`CronTrigger(minute="*/2", hour="8-9", day_of_week="mon-fri", timezone="America/New_York")`
  — `America/New_York` handles DST correctly without manual UTC-offset math). Polls FRED's
  `series/observations` for CPI/PPI/GDP/NFP against `economic_events` rows still missing
  `actual_value` for today.
- `check_fomc_statement_poll()` — armed only 2:00–2:59pm ET, and only on real FOMC dates from
  `economic.py`'s `_FOMC_DATES`. Polls the Fed's own `press_monetary.xml` RSS feed directly
  (confirmed live: `federalreserve.gov/feeds/press_monetary.xml` — real entries, real dates)
  via `feedparser`, the same library already used in market-data's `news.py`. FRED's own rate
  series lag a day and have no "statement just posted" signal — hence the direct RSS poll.

**LLM reaction**: `generate_reaction()` calls Claude Haiku via raw `httpx` (same pattern as
decision-engine's `llm_scorer.py` — API key from Redis `stockai:admin:claude_api_key`),
fail-open (returns `None` on any error, never raises) — a missing reaction just means no email
fires that cycle, not a broken page.

**Delivery split** (same pattern as P1): event-intelligence detects + generates, writing
`reaction_text`/`reaction_generated_at` into `economic_events`; market-data's new
`check_macro_reaction_alerts()` (1-minute interval) polls for generated-but-unsent rows
(`reaction_sent_at IS NULL`) and emails the same `PriceAlert`-subscribed audience. `reaction_sent_at`
only advances inside an `if any_sent:` gate — a failed send cycle must retry next minute, not
get silently marked done (adversarially verified: removing this gate was caught by a dedicated test).

**New DB columns** (manual `ALTER TABLE` required — `create_all()` doesn't add columns to an
existing table): `economic_events.reaction_text` (TEXT), `.reaction_generated_at` (TIMESTAMP),
`.reaction_sent_at` (TIMESTAMP).

**New UI**: `GET /events/overview` gained a `latest_macro_reaction` field; a "Latest Macro
Reaction" card was added to `intelligence.tsx`'s Overview tab.

**Not built (deferred, not silently dropped)**: `sectors_helped`/`sectors_hurt` watchlist-join
personalization ("you hold/watch 4 rate-sensitive names") from the original design — the
current reaction is a general market-impact paragraph, not yet joined against the user's
specific holdings/sectors. Also not built: the per-user "macro alerts on/off" preference from
the original design (v1 reuses the `PriceAlert`-subscriber audience instead, per explicit
user choice to keep scope bounded).

### Recurring Issue: httpx Logs Full Request URLs (Including API Keys) at INFO Level

**Found 2026-07-15, while reviewing P2's deploy logs.** `httpx`'s own internal logger prints
`HTTP Request: GET https://api.stlouisfed.org/...?api_key=<real key>...` at INFO level on every
outbound call. Since `shared/common/logging.py`'s `configure_logging()` sets the stdlib root
logger to INFO (and `httpx`'s logger propagates to it), **every service that calls an external
API with a key as a query parameter had that key appear in plaintext in Docker logs** — this
had been happening since P0's `sync_fred_release_dates()` first shipped, invisible until
someone actually read the logs closely (42+ occurrences by the time it was caught).

**Fix applied**: added `logging.getLogger("httpx").setLevel(logging.WARNING)` to
`configure_logging()` in `shared/common/logging.py` — one shared fix covers every service.
WARNING still surfaces real connection/timeout errors, just not routine request lines.
Deployed by syncing `shared/common/logging.py` to all 11 backend containers and restarting
all of them (confirmed via `docker ps` diff that recreation was intentional and total, and via
a post-restart log grep that zero new `HTTP Request:` lines appeared).

**The exposed FRED key was rotated** as a precaution (get a new one free, instant, at
fred.stlouisfed.org/docs/api/api_key.html) — same "never embed real credential values in SSH
command strings" discipline applied throughout: the rotation was done by piping the key line
over SSH stdin to a remote Python script that rewrote `.env` in place, never as a `sed -i
's/.../<key>/'`-style command-line argument (which the permission system correctly blocked on
the first attempt) and never written to an intermediate file on either host (a `scp`-based
attempt was also correctly blocked for leaving a persistent plaintext artifact).

**A stray terminal escape sequence corrupted EC2's `.env` during this same edit** — line 2
became `61;7600;1cPOSTGRES_USER=stockai` instead of `POSTGRES_USER=stockai` (a leftover
cursor-position response terminal escape code, likely from an interactive editing session on
that file), which broke `docker compose` entirely (`unexpected character ";" in variable
name`). Fixed by stripping the garbage prefix (confirmed via `cat -A` before AND after the
fix, and confirmed no other line in the file had the same corruption) — **always run `docker
compose ... config` after any manual `.env` edit** to catch this class of corruption before it
blocks a real deploy.

**What to check if a future API key needs adding**: confirm `configure_logging()` still sets
`httpx`'s logger to WARNING (`docker exec <container> python3 -c "import logging;
print(logging.getLogger('httpx').level)"` should print `30`) before assuming a new key-bearing
API call is safe to add.

---

## Feature Reference: Volume Profile (Tier 250) — How to Read It

**Built 2026-07-16.** User asked for a TradingView-style footprint chart on the stock detail
page. True footprint charts (buy/sell volume split per price level) need tick/quote data no
current data source (yfinance, Alpha Vantage, the current Polygon aggregates-only
integration) provides without a paid Polygon upgrade — deferred as a separate, larger project.
What's built instead is a **volume profile**: POC/VAH/VAL/HVN using the standard
price-bucketing approximation (each bar's volume spread across its high-low range, bucketed
by price), forked from TradingView's own official `lightweight-charts` plugin-examples
volume-profile primitive.

**How to read it** (this exact explanation is also in the UI as hover tooltips on the
POC/VAH/VAL/HVN readout row and the Session/Range dropdown options — added after a user asked
"how do I read this?" with no in-app explanation available):

- **The blue horizontal bars are NOT tied to any single candle.** Each bar represents a
  **price level**, and its length is the total volume summed across every bar in the profiled
  range whose high-low span touched that price level — a sideways aggregation across time,
  projected onto the price (y) axis. If 20 different candles all had prices passing through
  $650-$660, all of their volume adds together into the one bucket at that price level. This
  is exactly why the profile is drawn to the left of the price axis rather than aligned under
  any particular candle: it collapses the time dimension entirely and only answers "how much
  total volume traded at each price," not "when."
- **POC (Point of Control, orange)** — the single price level with the most volume traded.
  Usually the most important line on the profile; acts like a magnet/support-resistance level
  since it's the price the market most agreed was "fair" for that period.
- **VAH / VAL (Value Area High/Low, blue)** — together bracket the price range containing
  70% of total volume (the standard value-area percentage, matching TradingView's own
  default). Price outside this band sat in comparatively under-traded, "thin" territory.
- **HVN (High Volume Nodes)** — specific price levels with locally peaking volume (real
  interior peaks in the bucket histogram, not just the single POC). These tend to act as
  support/resistance on revisit, same reasoning as POC but at a finer granularity.
- **Low Volume Nodes (LVN)** are computed (`VolumeProfileResult.lvn`) but not currently shown
  in the readout row — they mark price zones the market moved through quickly, which tend to
  get moved through fast again on a revisit (the opposite behavior of HVN/POC).

**Three modes** (Volume Profile dropdown in the chart toolbar):
- **Session VP** — profiles only the current trading session's bars. Useful for intraday
  support/resistance.
- **Range VP** — profiles the entire currently-visible chart window (whatever date range is
  currently selected/zoomed).
- **Fixed Range VP** (added 2026-07-16, after a user asked how to use POC as an entry point
  anchored to a specific swing high/low) — click a start point on the chart, then an end
  point, and the profile computes for exactly that bar range. `lightweight-charts` has no
  native drag-select gesture, so this uses the standard two-sequential-clicks pattern instead
  (same approach TradingView's own drawing tools and most community plugins use), reading
  `param.logical` (a bar index, not a pixel coordinate) from `chart.subscribeClick()` so the
  selection is always bar-aligned. Implemented as a separate, lightweight `useEffect` from
  the main chart-rebuild effect — subscribing on the existing chart instance via `chartRef`
  rather than recreating the whole chart on the first of the two picking clicks, which would
  otherwise flash/reset zoom on every click. A `chartInstanceVersion` counter guards the edge
  case where the user starts picking a range, then also toggles an unrelated overlay before
  finishing — without it, the click effect could stay subscribed to a since-replaced chart.

  **How to redo or clear a selection** (a user asked this directly after first using the
  feature — worth documenting since it's not obvious from the UI alone):
  - **To pick a new range**: once a selection exists, a **"Re-pick range"** button appears in
    the toolbar next to the Volume Profile dropdown (only visible when
    `fixedRangePickState === 'idle' && fixedRangeSelection` is set). Clicking it re-arms
    `picking-start` without touching `volumeProfileMode` — the old selection/profile stays
    visible on the chart until the two new clicks land and replace it.
  - **To turn it off entirely**: open the Volume Profile dropdown and uncheck "Fixed Range
    VP" — this resets `volumeProfileMode` to `'off'` and clears both `fixedRangePickState`
    and `fixedRangeSelection`. Unchecking-then-rechecking also works (same reset path) but
    isn't necessary just to redo a range — "Re-pick range" is the one-click way to do that.

**What to check if this looks wrong**: `src/lib/volumeProfile.ts`'s `computeVolumeProfile()`
is the only place this math lives — 10 tests in `volumeProfile.test.ts` cover POC placement,
VAH/VAL bracketing at exactly 70% volume, HVN detection, and edge cases (degenerate/zero-
volume bars). If a specific stock's profile looks implausible, the first thing to check is
whether `numBuckets` (currently hardcoded to 24 in `PriceChart.tsx`) is too coarse for that
stock's price range — a stock with a very wide 52-week range bucketed into only 24 buckets
will show chunkier, less precise bars than a narrower-range stock.

---

## Feature Reference: Chart Toolbar Redesign + Intraday Indicators (Tier 250 follow-up)

**Built 2026-07-16**, same day as Volume Profile above, after live user feedback found the
toolbar had become overcrowded (~15 flat SMA/EMA/BB/VWAP/Sig/RSI/MACD buttons + the new VP
buttons, all on one wrapping row).

**Toolbar**: redesigned into `frontend/src/components/ToolbarDropdown.tsx` — a reusable
checkbox-list dropdown (open/close/outside-click pattern matches `_app.tsx`'s existing
`NavGroup` nav dropdown). Three groups now: **Indicators** (SMA/EMA/BB/Sig), **Panels**
(RSI/MACD), **Volume Profile** (Session/Range). Vol/VWAP stay as quick single-click toggles
since they're the most frequently used.

**Page width**: `.container-xl` in `globals.css` widened 1200px → 1700px — the whole app
(every page, not just stock detail) was capped well below typical monitor widths.

**Chart height**: main candlestick chart 420px → 600px, ahead of a future drawing-tools
(trendline) feature the user flagged wanting next.

**Intraday indicators fix**: SMA/EMA/BB/RSI/MACD previously disappeared entirely on
intraday timeframes (5m/15m/1h/4h) because the technical-analysis service only computes
indicator series for daily bars — the intraday API response has no `indicators` field at
all. Fixed with new `frontend/src/lib/indicators.ts`, computing these client-side from the
already-fetched intraday bars (same local-computation approach already used in
`PriceChart.tsx` for VWAP/EMA200), hand-translating `shared/common/indicators.py`'s exact
pandas formulas.

**A real bug caught before shipping**: the first version of `indicators.ts` wrongly assumed
pandas' `ewm(adjust=False, min_periods=window)` seeds its recursion with an SMA of the first
`window` values. Cross-checked directly against a real `pandas.Series(...).ewm(...)` call
(not just re-derived from the JS implementation's own output) and found `adjust=False`
actually seeds at the FIRST value unconditionally (`y[0] = x[0]`) and recurses from there —
`min_periods` only masks early output as null, it does not change the seed. This would have
silently produced wrong EMA/RSI/MACD values on every intraday chart (e.g. `EMA[2]` of
`[10,20,30,40,50]` with window=3: the shipped-and-caught-wrong answer was 20, the
pandas-verified correct answer is 22.5). Rewrote all 11 `indicators.test.ts` assertions to
check exact values captured from real pandas runs rather than internally-consistent-but-
unverified expectations.

**Design invariant**: any future hand-translated formula (pandas, numpy, or otherwise) that
"looks right" and produces plausible-looking numbers should still be cross-checked against a
real run of the reference implementation on a fixed, hand-picked input — a test suite that
only re-derives its expected values from the same (possibly wrong) implementation under test
will never catch this class of bug, no matter how many tests it has.

**Test infrastructure**: this is also the first time Vitest was added to this repo (zero
JS/TS test tooling existed before 2026-07-16) — pinned to v1.6.1 rather than the latest v4.x
after discovering v4 requires a Node `styleText` export the local dev environment's Node
18.19.1 doesn't have (production's Docker build uses `node:20-alpine`, where v4 would have
worked, but v1.x was kept for local-dev compatibility). Run via `npm test` in `frontend/`.

---

## Design Reference: Why a BUY Signal Can Show Low Confidence

**A user asked this directly (2026-07-16)** after seeing a stock (6682.HK) show `AI Signal:
BUY` with only `13% Confidence` — worth documenting since it looks contradictory but is
working as designed, and the same question will come up again for other stocks.

**Confidence and the BUY/SELL/HOLD decision are two entirely independent calculations:**

- **Confidence** = `abs(fused_probability - 0.5) * 200`
  (`services/signal-engine/src/generators/signals.py:2118`, also duplicated at
  `services/signal-engine/src/api/routes.py:556` and `:5666`). This is purely "how far from a
  50/50 coin-flip is the model's probability" — a `fused_probability` of 56% bullish is barely
  above a toss-up, so confidence is mechanically forced to `abs(0.56-0.5)*200 = 12%` no matter
  what else is true about the stock. **Confidence measures conviction in the probability
  estimate itself, not trade quality.**
- **BUY/SELL/HOLD** is decided separately by `_decide_style()`
  (`services/signal-engine/src/generators/signals.py:1556`) — whether that same
  `fused_probability` clears a **threshold** (`buy_threshold`, `_STYLE_PROFILES`, varies by
  style + market regime, e.g. SWING/bull ≈ 0.60-0.63) that can itself be self-tuned over time
  by the watchdog/calibration jobs (see "Tier 85-86" in the tier-history section above —
  `_get_dynamic_buy_threshold()` reads a Redis-cached, empirically-tuned value before falling
  back to the hardcoded default).

**The practical read**: a BUY signal with low confidence means the probability barely cleared
the bar to be called BUY at all — a marginal, low-conviction call, not a strong one. **This is
exactly what the other panels on the stock detail page are for** — they're deliberately more
reliable signals of "should I actually enter" than the top-line BUY/SELL label alone:
- **Confluence Score** (weighted blend of AI signal + K-Score + technical + momentum,
  `frontend/src/lib/confluence.ts`) — a low/"Weak" score with "signals conflict" is a stronger
  real-world signal to heed than the BUY label.
- **Conviction Gate** (`_is_conviction_buy()` in `paper_trading_engine.py`, 7-layer check:
  K-Score, Uptrend, RSI, MACD, OBV, ADX, ML — see the existing Conviction Gate documentation
  elsewhere in this file) — "✗ Gate not met" with multiple failed layers means the paper
  trading engine itself would NOT have entered this position even though the top-line label
  says BUY. The gate exists specifically to catch cases like this one.

**Design invariant**: never treat the top-line AI Signal label (BUY/SELL/HOLD) as sufficient
justification to enter a real position on its own — always cross-check the Confluence Score
and Conviction Gate panels on the same page, which are deliberately independent, stricter
checks that can (and are meant to) disagree with the headline label.

---

## Recurring Issue: Stale Tracker Entries Can Point Either Direction — Verify Before Trusting Severity/Status

**Found 2026-07-16, while looking for "the next critical improvement to build."** A tracker
survey flagged `SE-F2-SAME-DAY-CLOSE-LOOKAHEAD` (tier 147, `severity: 'critical'`, no
`defaultStatus` field, `implementedNote: 'Deferred'`) as the top candidate — signal outcome
evaluation allegedly still used the same-day close as entry price, corrupting every
accuracy/calibration metric. Before building anything, checked the actual code first: the fix
had already shipped 2026-06-30 (`services/signal-engine/src/api/routes.py:5056-5059`,
explicit "T+1 entry... avoid same-day look-ahead bias" comment) as part of an unrelated
broader audit commit, and was confirmed byte-identical between the local checkout and the
live production container. The tracker entry itself was simply never updated to reflect it.

**This is the mirror image of the T203 incident** documented earlier in this file (T203 was
marked `done` but was actually never wired up/functional) — here, the entry was marked
effectively `todo`/deferred but the fix was actually live. **Both directions of staleness are
real and both have occurred in this tracker** — a tracker entry's `severity`/`defaultStatus`
tags are a starting hint for where to look, never a substitute for reading the actual current
code before deciding what to build or report as still-broken.

**A second real issue was found underneath the stale tracker entry**: 3,808
`signal_outcomes` rows (`signal_date < 2026-06-30`) still carried the pre-fix same-day-close
bias and were still feeding the self-tuning watchdog/calibration thresholds even after the
code fix landed — the code fix only affects evaluation going forward, it does not retroactively
correct already-written rows. Fixed by backing up the 3,808 rows to
`signal_outcomes_prefix_backup_20260716`, deleting them from the live table (explicit user
confirmation obtained naming the specific table before the DELETE), and re-running
`POST /signals/outcomes/evaluate` to regenerate them with the corrected T+1 entry price —
verified `COUNT(*) FILTER (WHERE entry_date = signal_date) = 0` across all 4,742 resulting
rows, and spot-checked several regenerated rows against their pre-fix backups to confirm
materially different (and correct) entry prices.

**Design invariant**: a code fix for a data-integrity bug (lookahead bias, wrong formula,
etc.) fixes future writes only — always check whether historical rows written before the fix
need a separate backfill/re-evaluation pass, and don't assume "the code is fixed" means "the
data is fixed." When surveying this tracker for "what's the next critical thing," always
verify a candidate's actual current code state directly before trusting its severity/status
tags in either direction — an entry can be wrong by claiming something is still broken
(costing you nothing but a wasted verification pass) or by claiming something is fixed when
it silently isn't (costing real time debugging a "mysterious" recurrence of an already-known
bug). Verify first, in both directions.
