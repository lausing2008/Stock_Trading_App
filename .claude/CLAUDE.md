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

**Also surfaced on the Reports page (2026-07-17):** `frontend/src/pages/reports.tsx` has its
own dedicated "CAPE / Bubble Warning" tab (`?tab=cape`), promoted from a card that had
originally been buried inside the Trend tab — a user asked "where is the CAPE tab?" expecting
a distinct tab like `intelligence.tsx`'s, not a card nested inside another tab. The Reports
version adds a warning-bands reference table (Normal/Elevated/High/Extreme with the same
thresholds documented below) alongside the live reading. Both pages read the same
`api.eventsCape()` endpoint; there is no second CAPE data path.

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

**Built 2026-07-14/15, P3 added 2026-07-17.** User's original ask: "monitor the news or any
information that would make the market go up or down. Get current earning reports or CPI/FOMC
before market starts, analyze the impact. Or get the results from CPI/FOMC after they announce
it ASAP and predict the trend. Same for earnings and news." A Fable 5 consult broke this into 5
slices (P0–P4); P0–P3 are built and live as of this writing. P4 (news pulse card) is still
`todo` in the tracker.

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

### P3 — Pre-market brief (done 2026-07-17)

The "before market starts" half of the original ask, generalized once P0–P2 existed. New
`send_premarket_brief()` job in `services/market-data/src/services/scheduler.py`, registered
as `premarket_brief_us`/`premarket_brief_hk` at 8:00 local (50 min ahead of the existing
`morning_digest_us`/`_hk` at 8:50, so catalyst context arrives before the opportunities digest).
`send_premarket_brief_email()` builder in `email_service.py` matches
`send_morning_digest_email()`'s section-composition HTML style.

**Deliberate scope narrowing from the original design doc**: no new LLM call. The original P3
fix note proposed generating a fresh conditional-scenario paragraph per brief ("if CPI prints
above X: historically pressures rate-sensitive names...") for an event that hasn't happened
yet. Built instead as pure composition of three already-computed sources, zero new LLM cost/
latency/hallucination risk per send:
1. Today's high/critical-importance macro releases — reuses P0's own `_macro_events_from_db()`
   (imported directly from `routes.py`, not re-queried).
2. Which of the recipient's own watched symbols report earnings today — `EarningsEvent.report_date
   == today` (the day-of window, vs. `check_earnings_reactions()`'s post-release `>=today-2d,
   eps_actual IS NOT NULL` window), same `user_symbols` construction pattern as P1.
3. Macro reactions generated in the last 18h — reuses P2's own already-LLM-generated
   `reaction_text` on real releases that already happened. This is the section that actually
   satisfies the "historically reacted" framing goal, and is more honest than a hypothetical
   pre-release scenario paragraph would have been — it reports what really happened, not what
   might. This required a genuinely new query (`reaction_generated_at >= now - 18h`); no
   existing helper covered this shape (`check_macro_reaction_alerts()` only tracks
   sent-vs-unsent, a queue, not a time window).

Audience: same `PriceAlert`-subscribed recipients as P1/P2 (`check_earnings_reactions()`/
`check_macro_reaction_alerts()`), deliberately narrower than `send_morning_digest()`'s all-`User`
audience, for consistency within the T249 alert family rather than introducing a third audience
model.

**Testing constraint hit again**: `send_premarket_brief()` itself can't be imported under the
local pytest harness — `scheduler.py`'s import chain pulls in `apscheduler` (and
`ingestion.py`/`paper_trading_engine.py`/`api/routes.py`), none of which `conftest.py` stubs,
matching the same constraint already documented in `test_price_alert_price_check.py` and
`test_earnings_alert_bodies.py`. `send_premarket_brief_email()` has no such problematic imports
(only `smtplib`/`common.config`/`common.logging`, all stubbed or stdlib) so it's tested directly
with real inputs — 9 tests covering empty-state notes in every section, impact-color
distinctness between critical/high, None-safe EPS-estimate formatting (adversarially verified:
temporarily removed the `is not None` guard and confirmed the resulting `TypeError` was caught
before restoring it), a 5-item cap on rendered reactions, and disclaimer presence. The job
function itself gets 5 source-text regression checks (matching `test_scheduler_static_names.py`'s
established pattern for the exact "MagicMock masks a real NameError" risk this repo has hit
before) plus a genuine live-verification call against the real deployed container:
```python
# Run inside stockai-market-data-1 with send_email monkeypatched to a no-op logger —
# calling the real function unpatched would email every real PriceAlert-subscribed user.
import sys; sys.path.insert(0, '/app')
import src.services.email_service as es
es.send_email = lambda *a, **kw: (print('WOULD SEND to', a[0], '| subject:', a[1]) or True)
from src.services.scheduler import send_premarket_brief
send_premarket_brief(['US'])
```
Ran clean on the real deployed container immediately after the `docker cp` + restart deploy:
no exceptions, real DB queries executed (P0/P1/P2 tables), logged
`premarket_brief.nothing_to_report` (a legitimate state — no high/critical macro releases
scheduled and no matching earnings/reactions at verification time), and
`scheduler:job:premarket_brief_us` recorded `{"status": "ok", "error": null}` in Redis —
confirming `_record_job_status()` wiring is correct too, not just the absence of a crash.

**Design invariant reinforced by this feature**: when a new scheduler.py function would send
real emails/pushes to real users, verify it live by monkeypatching the SEND function to a no-op
logger, never by calling the real function unpatched against production data — this is a
stricter version of the "verify against live state, not just tests" discipline already
documented elsewhere in this file, adapted for the case where the live verification itself has
a real-world side effect that must be neutralized first.

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

**How to trade it — breakouts and direction** (a user asked this directly on 2026-07-16,
separately from "how do I read this" — worth keeping distinct since reading the levels and
trading them are different questions):

- **Breakout above VAH** — price has left the "accepted"/fair-value range into thin,
  low-volume territory above it. Thin territory means less resistance overhead, so price can
  move fast — read as bullish continuation, especially if price holds above VAH on a retest
  (old resistance flipping to new support is the confirming signal, not the initial break
  itself).
- **Breakdown below VAL** — the mirror case, bearish. Price rejected the value area from
  below and is now in thin air below it — commonly used as an exit/reduce-position trigger
  (this is exactly what `T252-VALUE-AREA-BREAKDOWN-ALERT`, still `todo` in the tracker, would
  automate as a real alert instead of a manual chart read).
- **Failed breakout (rejection back into the value area)** — if price pokes above VAH or
  below VAL and then closes back inside, that's often a false breakout / reversal signal —
  the market "tested" outside fair value and the market rejected it. Treat a poke-and-reject
  as the opposite signal from a genuine breakout, not a weaker version of the same one.
- **POC as a magnet** — price far from POC often gets pulled back toward it. A stock trading
  well above POC can be extended/due for a pullback to POC before continuing, rather than an
  immediate reversal signal on its own.
- **HVN vs LVN as a roadmap** — HVNs (thick bars) act like speed bumps: price tends to slow
  down, consolidate, or reverse there. LVNs (thin bars/gaps) are zones the market moved
  through fast the first time — expect a quick move back through them too if price revisits
  (much less "friction" than an HVN revisit).
- **Practical entry read**: a higher-quality long setup is often price pulling back toward
  POC or an HVN from above (acting as support), holding there, with volume drying up on the
  pullback itself (thin selling pressure) — generally a better-quality entry than chasing a
  breakout with no pullback at all.
- **Which mode fits which read**: Session VP for intraday direction (where today's volume
  actually concentrated); Range VP for the current visible swing's context; Fixed Range VP
  for judging whether a SPECIFIC prior rally/decline had "real" volume support underneath it
  (HVN-heavy = well-supported move; LVN-heavy = thin/fragile move, more likely to fully
  retrace).
- **Standing caveat**: this is still the bucketing approximation described above, not a true
  buy/sell-split tick footprint — it tells you WHERE volume concentrated, not whether that
  volume was aggressive buying or selling at each level. Directional reads above lean on
  price behavior AROUND the profile (holds vs. rejects a level), not on the profile's volume
  alone distinguishing buyers from sellers.

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

## Design Reference: The ↑/↓ Percentage Arrows on the Daily Chart

**A user asked "what's the percentage on the graph like 50%" (2026-07-17)** after seeing small
green ↑ and red ↓ arrows above/below certain candles on the daily chart, each labeled with a
number like `46%`, `47%`, or `50%` — worth documenting since it's easy to confuse with the
sidebar's live `Confidence`/`Bullish` percentages, but it's a completely different, historical
signal.

**What they are**: `frontend/src/components/PriceChart.tsx:353-373` — these arrows mark **AI
Signal transition points** in the SWING horizon's stored signal history, daily timeframe only
(`!isIntraday`, line 353). The code takes every stored `signalMarkers` point, keeps only the
last entry per calendar date (signals fire every 5 min while stable — line 355-359), then
filters down to just the **transitions**: the first day a new signal direction appears,
compared to the previous day's stored signal (line 364,
`sorted.filter((m, i) => i === 0 || m.signal !== sorted[i-1].signal)`). Every day the signal
just *held* its existing direction is skipped — only the day it *flipped* gets a marker.

**What the percentage means**: `text: `${Math.round(m.confidence ?? 0)}%`` (line 370) — the
label is that stored signal's own **confidence at the moment it flipped**, using the exact
same `confidence = abs(fused_probability - 0.5) * 200` formula documented above. It is NOT
today's live confidence (shown separately in the sidebar) — it's a frozen historical value
from whichever day that specific transition happened.

**Visual encoding**: green `arrowUp` below the bar for a flip to BUY, red `arrowDown` above the
bar for a flip to SELL (line 367-369) — color and shape indicate direction, the number
indicates how confident that particular flip was.

**Practical read**: a marker with a low percentage (e.g. a red ↓ at "50%") means the signal
flipped to SELL on that day, but only barely cleared the bar to be called SELL at all —
matching the same "confidence measures conviction in the probability estimate, not trade
quality" caveat as the design reference above. A cluster of low-confidence flip markers close
together often reflects a choppy period where the signal was oscillating near its decision
threshold, not a series of strong directional calls.

**What to check if this looks wrong**: the transition-filtering logic is the only place this
renders — if arrows are missing entirely, confirm `!isIntraday` (daily timeframe only) and
`showSignals` is enabled; if a percentage looks inconsistent with the sidebar's current
reading, that's expected — they're deliberately different values (historical flip-moment vs.
live-today).

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

---

## AUD250 — Deep Audit of the 2026-07-11 to 2026-07-16 Work Window (73 Commits, 11 Services)

**What this was:** a full multi-agent review (one agent per touched service, reviewing that
service's REAL git diff against a fixed base commit, not a generic fresh bug hunt) of every
logic/code change made in a 5-day window: the Tier 247 full 11-service audit, Tier 248/249/250
features (CAPE valuation, Market-Mover Monitoring, Volume Profile), and this session's own
SE-F2/retro-feedback/premarket work. 16 findings surfaced; all 16 survived independent
adversarial verification. Two (a `TuneHistory` column and 3 `EconomicEvent` columns) were
refuted immediately by checking live production Postgres directly — the audit agents could only
reason from git history and commit-message language, and in both cases a commit's OWN language
was internally inconsistent about whether a required `ALTER TABLE` had actually been run. This
reinforces the "verify against live state, not just git history" discipline documented
elsewhere in this file — a subagent without SSH access will get this exact class of finding
wrong in either direction, and the fix is always to check the actual running system.

### CRITICAL (fixed same-day, before its first scheduled run) — Watchlist Auto-Rotation Never Actually Ran

**Symptom:** none visible yet — this was caught BEFORE it could produce a symptom. The
`watchlist_auto_rotation_weekly` job (Sunday 17:00 ET, `services/market-data/src/services/scheduler.py`)
had run zero times successfully since being merged 2026-07-13; the coming Sunday would have
been its first live-scheduled fire.

**Root cause:** `_run_watchlist_auto_rotation()` had TWO independent `NameError`-causing bugs in
the same function: (1) `Market.US` used for a market tie-break with `Market` never imported
anywhere in `scheduler.py`'s ~4,700 lines; (2) `desc(Ranking.score)` used with `desc` never
imported (the function's own local `from sqlalchemy import func as _func, case as _case, delete
as _delete` didn't include it). Both are caught by the function's own top-level
`except Exception`, which logs `watchlist_auto_rotation_failed` and records job status "error" —
silent from the outside, no adds, no drops, indistinguishable from "ran fine, found nothing to
do" unless someone actually reads the error log. **Zero test coverage existed for this
function** — and critically, `conftest.py` stubs `sqlalchemy`/`db` as `MagicMock()` for local
tests, and a `MagicMock()` attribute access never raises `NameError`/`AttributeError` — so even
importing and exercising the real function under the existing test harness would NOT have
caught either bug. This is the same "stub a whole module, mask a real missing-import bug" gap
already documented for `services/signal-engine/tests/conftest.py` elsewhere in this repo's
history — worth checking any OTHER heavily-stubbed test suite in this repo for the same blind
spot before trusting "all tests pass" as proof a new function is wired correctly.

**Fix applied (2026-07-16):** Added `Market` to the module-level `from db import ...` line and
`desc` to the function's local `from sqlalchemy import ...` line. Verified by actually CALLING
`_run_watchlist_auto_rotation()` live in the production container immediately after deploying —
not just re-running the (still-stubbed) pytest suite — and confirmed a real completion log line
(`watchlist_auto_rotation_complete`, dropped=8, no exception) where before it would have logged
`_failed` every time. Added `services/market-data/tests/test_scheduler_static_names.py` — two
narrow, source-text regression checks (not a general "does every name resolve" static
analyzer, which was attempted and abandoned: nested closures/lambdas/comprehensions made a
hand-rolled scope resolver produce more false positives than real signal in this file).

**What to check if this or a similar function looks silently broken again:**
```bash
# Confirm the job's real completion status, not just "container is Up":
docker logs stockai-market-data-1 --since 24h | grep 'watchlist_auto_rotation'
# Should show watchlist_auto_rotation_complete, not _failed, after every Sunday 17:00 ET run.

# To directly re-verify a scheduler function actually runs (don't trust stubbed pytest alone):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app')
from src.services.scheduler import _run_watchlist_auto_rotation
_run_watchlist_auto_rotation()
"
```

**Design invariant:** any test suite that stubs a whole module as `MagicMock()` (common in this
repo's conftest.py files, needed to avoid real Docker-only dependencies like psycopg2/redis at
import time) provides ZERO protection against a missing import or undefined name inside code
that only runs through that stubbed module — `MagicMock()` silently accepts any attribute
access. A brand-new function added to a heavily-stubbed file needs either (a) a direct live
call against a real deployed container before trusting it's wired correctly, or (b) a narrow
source-text regression test for its specific imports/names, matching the pattern in
`test_scheduler_static_names.py` — "all pytest passed" alone does not prove the function can
actually execute.

### HIGH — event-intelligence's Macro-Reaction Poll Blocked Its Own Event Loop

**Symptom:** none reported yet (release-day-armed, so only exposed during the ~90-minute
CPI/PPI/GDP/NFP window or FOMC statement windows) — caught by the audit before it could cause a
real incident.

**Root cause:** `check_release_day_fast_poll()`, `check_fomc_statement_poll()`, and
`generate_reaction()` in `services/event-intelligence/src/services/macro_reaction.py` are all
`async def` (registered on `AsyncIOScheduler`, running on the SAME event loop as the FastAPI app
serving real-time HTTP requests to this service — confirmed via `main.py`'s
`on_startup=start_scheduler`), but called `httpx.get()` and `feedparser.parse()` — both
blocking, synchronous I/O — directly. Any concurrent request to event-intelligence during a
poll would hang for up to that call's own timeout (8-10s per FRED/regime call). This is the
exact same bug class already fixed once in this repo for decision-engine's `regime.py`
(`_regime_executor`) — the fix wasn't ported to this newer service when it was built.

**Fix applied (2026-07-16):** Added a dedicated `_macro_reaction_executor =
ThreadPoolExecutor(max_workers=2)` and routed all three blocking calls through
`loop.run_in_executor(...)`, matching `regime.py`'s established pattern exactly. New
`test_macro_reaction_not_blocking.py` (4 source-text checks) confirms the executor exists and
each of the three call sites actually uses it — adversarially verified by reverting one call to
its direct blocking form and confirming the test caught it before restoring.

**Design invariant:** any new `AsyncIOScheduler` job in a service whose FastAPI app shares the
same event loop (true for every service in this repo except market-data, which uses
`BackgroundScheduler` — a separate thread pool by design) must route ALL blocking I/O (HTTP
libraries without an async variant used correctly, `feedparser`, file I/O, etc.) through a
dedicated `ThreadPoolExecutor` + `run_in_executor()`. Grep for `httpx.get(` / `httpx.post(`
(not `AsyncClient`) and any third-party library without a documented async mode inside any
`async def` in a service using `AsyncIOScheduler` before considering a new job "done."

### MEDIUM — research-engine's In-Flight Dedup Silently Lost Tracking on a Mismatch Fallthrough

**Root cause:** `generate_research()`'s concurrent-request dedup (`_inflight_research: dict[str,
asyncio.Event]`) has the first caller `pop()` its own entry right before firing its completion
event. A second, concurrent caller that was waiting on that event, then found the finished
report's baked-in portfolio params didn't match its own request (T247-RESEARCHENGINE-CACHEKEY's
own fix, working as intended), fell through to compute its own report — but never re-registered
itself in `_inflight_research`. A third concurrent request arriving in that exact window would
see the symbol as not-in-flight and start its OWN duplicate LLM generation instead of deduping
against caller #2's now-in-progress work — a real, if narrow-window, dead-code-defeating-its-
own-purpose bug.

**Fix applied (2026-07-16):** Re-register `_inflight_research[sym] = asyncio.Event()` on the
mismatch-fallthrough path, exactly matching the `else` branch's own registration. Not covered
by a new automated test — `generate_research()` has heavy runtime dependencies (httpx gather
across 6+ services, DB, LLM call) that make a real behavioral test for this specific race
expensive to build safely; documented here instead, matching the same effort-vs-risk judgment
already applied elsewhere in this file (e.g. the signal-engine rollback finding below).

### MEDIUM (documented, not fixed this pass) — Two Real Findings Requiring Larger, Riskier Changes

**1. `evaluate_signal_outcomes()`'s per-signal `try/except` calls `session.rollback()` mid-loop**
(`services/signal-engine/src/api/routes.py`, function starts ~line 5006). SQLAlchemy's
`Session.rollback()` expires every ORM object in the session's identity map by default —
including every already-bulk-loaded `Signal` row in `pending_signals`, a list iterated across
potentially thousands of loop iterations. After any single signal's exception, every
SUBSEQUENT iteration's `sig.xxx` attribute access silently triggers a fresh per-attribute SELECT
against the DB (a real N+1 performance regression on every failure, not silent data
corruption — Signal rows aren't otherwise concurrently mutated, so the re-fetched values should
still be correct, just expensive to re-fetch). The textbook-correct fix is `session.begin_nested()`
(a SAVEPOINT) around each signal's own processing, rolling back only that one signal's `add()`
without expiring the whole identity map — deliberately NOT applied this pass given the function's
size (250+ lines, multiple exit paths, existing counters) and the real risk of a rushed
structural change to a function this delicate. Revisit as its own focused task.

**2. `_macro_events_from_db()`'s per-type (not per-`(type, date-range)`) fallback tracking**
(`services/market-data/src/api/routes.py`, ~line 1634). `types_with_db_rows` is a plain
`set[str]` — if the DB has even ONE release-date row for a macro type anywhere in the requested
window, ALL `_MACRO_2026` hardcoded fallback entries for that type are skipped across the
ENTIRE window, including date ranges the DB sync never actually reached.
`sync_fred_release_dates()` only syncs 180 days ahead by default; `GET
/stocks/events/calendar?days_ahead=365` is a valid, allowed request (`Query(90, ..., le=365)`) —
a caller requesting >180 days ahead can see a real DB row for a near-term release, which then
silently suppresses fallback coverage for months 181-365 that the DB genuinely has no data for.
**Low real-world exposure today**: the frontend only ever requests the 90-day default (under
the 180-day sync window), so this has not yet produced a visible gap. Fix requires tracking
covered date-ranges per type, not just a type-level boolean — real but deliberately deferred as
its own scoped task rather than rushed.

### LOW severity, fixed

- **research-engine**: a genuinely verified `institutional_ownership.pct == 0.0` (real data,
  0% institutional ownership) rendered as "Unknown" in the fundamentals checklist — a classic
  falsy-zero bug, now newly REACHABLE now that this field carries real precisely-scaled data
  (T247-RESEARCHENGINE-INSTOWNERSHIP-SCALE) instead of an LLM free-text guess that almost never
  landed on an exact `0`. Fixed by tracking whether real fundamentals data was available
  separately from the numeric value itself.
- **frontend `research/[symbol].tsx`**: removed the `pct * 100 > 1 ? pct : pct * 100` scale-
  detection heuristic — dead weight now that the backend fix above guarantees `pct` is always a
  real, pre-scaled percent; the heuristic existed specifically to compensate for the OLD
  unscaled behavior and was never removed when that was fixed.
- **event-intelligence `congress.py`**: `on_conflict_do_update`'s `SET` clause included
  `stock_id`, which could overwrite a previously-resolved `stock_id` with `NULL` if a later
  re-sync's ticker-to-stock lookup failed transiently. Fixed with `COALESCE(excluded.stock_id,
  congress_trades.stock_id)` so a failed re-resolution can't regress an already-correct link.
  (Caught myself nearly reintroducing this exact NameError bug class while writing this fix —
  `sqlalchemy.func` wasn't imported in this file either; added it alongside the coalesce fix.)

### LOW severity, documented only (real but low-impact / needs larger scoped work)

- **decision-engine**: `abuild_game_plan()` shares the same 4-worker `_yf_executor` thread pool
  with the unrelated yfinance-price-fallback path, instead of its own dedicated executor like
  `regime.py`'s `_regime_executor` — undercuts (but doesn't defeat) the parallelism a batch
  `/decide/batch` request is supposed to get; tasks queue behind each other rather than
  stalling the event loop.
- **signal-engine**: `signal_watchdog()`'s cross-mechanism-coupling note is written into
  `TuneHistory.gate_failures` while `promoted=True` — every other of ~15 call sites treats
  `gate_failures` as exclusively a rejection reason paired with `promoted=False`. This IS an
  intentional, already-documented design choice (see `SELFIMPROVE-CROSS-MECHANISM-BLINDNESS`'s
  own implementedNote elsewhere in this file, and `admin.py`'s pre-existing "reverted" marker
  reusing the same field on a `promoted=True` row) — a real field-overloading code smell, not
  an accidental bug, left as-is rather than a schema change chasing a smell with no functional
  impact.
- **portfolio-optimizer**: a user-supplied `constraints.max_weight` that makes the mean-
  variance/risk-parity SLSQP optimization infeasible falls back to flat equal-weight with an
  HTTP 200 and no field in the `PortfolioWeights` response indicating this happened — only
  visible via a `log.warning`. A real, previously-unaddressed gap; fixing it means adding a new
  field to the response schema (and the frontend consuming it), a genuine small API-contract
  change deliberately not rushed into this pass.
- **ranking-engine test quality**: `test_rank_symbol_market_scoping.py`'s regression test for
  the CROSSMARKET fix hand-duplicates the query construction rather than extracting and
  exercising `rank_symbol()`'s REAL source (the source-text-extraction pattern already
  established elsewhere in this repo, e.g. `test_backfill_realized_ev.py`,
  `test_price_alert_price_check.py`) — it can pass even if the real `routes.py` regresses. The
  underlying PRODUCTION fix is real and already verified working; this is a test-infrastructure
  quality gap, not a live bug. Worth converting to source-extraction as a follow-up.

### Refuted — Confirmed Live in Production, Not Actually Missing

Two findings claimed a required `ALTER TABLE` for an existing-table column addition was never
actually applied to production, based on git-history/commit-message language alone (the audit
agents had no way to SSH and check the real database). Both were checked directly against
production Postgres and found to already be live:
```
\d signal_outcomes  → research_rec is already varchar(32) (widened, not the original 16)
\d economic_events   → reaction_text / reaction_generated_at / reaction_sent_at all already present
```
Filed here as a reminder of the "verify against live state, not git history" discipline
documented elsewhere in this file — a commit's own language can be internally contradictory
(one part says "pending," another part of the SAME commit says "verified live") and only a
direct check of the real running system resolves which half was actually true.

---

## Feature Reference: Fair Value Gap (FVG) — What It Is and How to Use It

**Built 2026-07-16.** User asked for Fair Value Gap zones specifically to help set entry,
target, and stop — the same underlying goal as Volume Profile (real structural price levels
instead of eyeballing a chart), but a different pattern with a sharper, more mechanical
entry/stop read than POC/VAH/VAL.

**What it is**: a standard ICT / smart-money-concepts 3-candle pattern. Look at any 3
consecutive candles — call them bar 1, bar 2, bar 3:
- **Bullish FVG**: bar 1's high is BELOW bar 3's low. Bar 2 (the middle candle) moved up so
  decisively that bars 1 and 3 never overlap its range at all — there's a real price zone,
  bounded by bar 1's high (bottom) and bar 3's low (top), that NO candle actually traded
  through. That's the "gap" / "imbalance."
- **Bearish FVG**: the mirror — bar 1's low is ABOVE bar 3's high, leaving an untraded zone
  between bar 3's high (bottom) and bar 1's low (top).
- **Important**: the gap boundary is bar 1 and bar 3's edges, NOT bar 2's own high/low. Bar 2
  is the candle whose move CREATED the gap, but its own range is not the gap itself.

**Why it matters**: an untraded price zone is considered "unfair" — the market moved through
it too fast for real two-sided trading to happen there. Price frequently comes back to
"rebalance" (retrace into) that zone before continuing in the original direction — this makes
the gap a plausible pullback entry zone, not just a curiosity.

**How to read it on the chart**: toggle "Fair Value Gaps" in the chart toolbar's Indicators
dropdown (on by default). Each gap is drawn as a pair of horizontal lines (top edge + bottom
edge of the zone) — solid/dashed and bold green (▲) for an unfilled bullish gap, bold red (▼)
for an unfilled bearish gap. Once a later candle has traded all the way through a gap (fully
closing it, not just dipping partway in), the pair dims to a thin dotted line and its label
disappears — the zone already "did its job" as support/resistance on that revisit and is no
longer an open, actionable target for a NEW entry.

**How to use it for entry/stop/target** — a new "Fair Value Gap Trade Plan" card on the stock
detail page (below Position Sizer) does this automatically for the single most relevant gap
right now:
- **Which gap it picks**: only a bullish gap that sits BELOW the current price (room to
  retrace down into it — a long setup) or a bearish gap ABOVE current price (room to retrace
  up into it — a short setup). A bullish gap already above price, or a bearish gap already
  below it, has nothing left to retrace into from here and is skipped. Among the remaining
  candidates, the NEAREST one to the current price is used — the one most likely to actually
  get touched next.
- **LONG vs. SHORT is not fixed — it's derived from whichever gap wins the pick above** (a
  user asked this directly, since the card only ever seemed to show one direction for a given
  stock at a given moment). If the nearest actionable gap is bullish, the card shows a LONG
  plan; if it's bearish, SHORT. It can flip for the same stock at a different time simply
  because price moved and a different gap became the nearest actionable one. It is NOT tied to
  the SHORT/SWING/LONG/GROWTH signal-horizon tabs elsewhere on the page — FVG is a daily-bar
  chart structure, not a per-horizon signal, so switching horizon tabs does not change which
  gap this card picks.
- **Entry** = the gap's midpoint (not its exact edge — edges are rarely touched with pixel
  precision; the midpoint is the standard, more realistic fill assumption).
- **Stop** = just past the gap's FAR edge (the bottom for a long, the top for a short) — the
  reasoning: if price fully closes the entire gap and keeps going past it, the "unfair, will
  get rebalanced" thesis has failed and the setup is invalidated, not just pulled back further
  than expected.
- **Target** = a configurable reward:risk floor (1.5:1 by default) measured off the gap's own
  real size, not an arbitrary fixed dollar/percent distance — the target scales naturally with
  how big the actual imbalance is.
- **This is shown as its own separate card, not merged into Position Sizer's numbers** —
  Position Sizer's own entry/stop/target (ATR-based stop, nearest support, analyst target
  price) stays exactly as it was; the FVG plan is an independent, comparable alternative a user
  can weigh against it, not a silent override of one system by the other.
- **No candidate gap** = the card simply doesn't render (no error, no placeholder) — this
  happens whenever there's no unfilled gap positioned to be retraced into from the current
  price, which is a normal, common state, not a bug.

**Architecture**: `services/technical-analysis/src/indicators/trendlines.py`'s
`detect_fair_value_gaps()` (same module and `@dataclass` convention as the existing `Level`/
`Trendline` detectors) scans the last 200 bars, filters out near-zero noise-level gaps, and
tracks `filled`/`filled_idx` by checking every later bar for a FULL cover of `[bottom, top]`
(a bar that only partially dips into the zone does not count as filled). Folded into the
existing `GET /ta/{symbol}/levels` endpoint as a new `fair_value_gaps` field, alongside
`support_resistance`/`trendlines`/`fibonacci` — not a new route, since FVG is conceptually
just another kind of level. `frontend/src/components/PriceChart.tsx` renders it via the exact
same `createPriceLine`-per-level pattern already used for S/R and `gamePlanLevels` — no new
chart primitive was introduced. `frontend/src/lib/fvgTradePlan.ts`'s `nearestActionableFvg()`
is a small, pure, independently-testable function (9 Python detection tests + 10 TypeScript
trade-plan tests, both adversarially verified) — the entry/stop/target math has no server
round-trip of its own; it runs entirely off the same `levels.fair_value_gaps` array already
being fetched for the chart.

**What to check if this looks wrong**: `detect_fair_value_gaps()` in `trendlines.py` is the
only place the detection math lives; `nearestActionableFvg()` in `fvgTradePlan.ts` is the only
place the trade-plan math lives. If a gap looks like it should be marked filled but isn't (or
vice versa), check whether a later bar's range genuinely covers the FULL `[bottom, top]` span
— a bar that pokes partway into the zone and reverses does NOT count as a fill by design.

**Game Plan vs. FVG Trade Plan vs. T252 Risk/Reward lines — three DIFFERENT systems, not
duplicates** (a user asked directly whether Game Plan and FVG are "the same or similar," after
finding the chart cluttered with multiple sets of entry/stop/target lines at once):
- **Game Plan** — on-demand, LLM-generated (Claude writes a specific plan with catalysts/risk
  narrative in prose). `null` until a user explicitly clicks to request one.
- **T252 Risk/Reward lines** (`riskRewardLevels` prop) — always-computed, ATR/nearest-support/
  analyst-target-derived, the same numbers already shown as text in Position Sizer, just drawn
  on the chart. No LLM call.
- **Fair Value Gap Trade Plan** — always-computed, purely mechanical (3-candle imbalance
  pattern), completely independent math from the other two, shown as its own separate card.

**On-chart collision handling**: Game Plan and the T252 Risk/Reward lines are mutually
exclusive on the chart itself — `riskRewardLevels` only renders `when !gamePlanLevels`, so
opening a Game Plan hides the ATR-based lines rather than stacking both. The FVG Trade Plan
card is NOT gated by either of these — it always shows independently whenever an actionable
gap exists, since it lives in its own card below Position Sizer, not on the chart's price-line
layer. This means a user can still see, at the same time: FVG's chart lines (toggle-controlled,
see above) + either Game Plan's OR the T252 lines (never both) + the separate FVG Trade Plan
card's own numbers — three distinct sources of "where's my entry" that are deliberately not
merged into one, so a user can compare independent reads rather than have one silently pick a
winner.

**Chart decluttering (2026-07-16)**: a user reported the chart as too cluttered to read once
S/R levels + 52-week High/Low + FVG lines + the new Risk/Reward lines + SMA/EMA curves were
all stacking up with no way to turn any group off. Support/Resistance and 52-Week High/Low
were changed from always-on to togglable (off by default) in the Indicators dropdown, matching
the pattern already used for Fair Value Gaps — a user now opts into extra context instead of
seeing everything at once unasked.

**Follow-up same day — FVG itself was still the real culprit.** After the S/R/52W fix, the
user reported the chart looked identical and still cluttered. The dense stack of thin
horizontal lines across the whole chart turned out to be FVG, not S/R — `detect_fair_value_gaps()`
can return up to 20 gaps (its own `max_gaps` default), rendered as 2 `createPriceLine()` calls
each = up to 40 lines, and FVG's own toggle had shipped defaulting to **on**, unlike every
other opt-in overlay added in the same decluttering pass. Two fixes: (1) `showFVG` now
defaults to `false`, matching S/R/52W's just-added off-by-default convention instead of being
the one exception; (2) even when a user does turn FVG on, `PriceChart.tsx` now caps rendering
to the 6 most relevant gaps — all unfilled ones (up to 6, since those are the only ones
actionable for a NEW entry) plus the most recent filled ones if there's room left in the cap,
never all 20 at once. The backend's own `fair_value_gaps` array is unchanged (still returns up
to 20 — useful for the "FVG Trade Plan" card's `nearestActionableFvg()`, which only ever picks
one gap anyway and isn't affected by this cap); this is purely a chart-rendering-density fix.

---

## Research: Per-Horizon AI Signal Strategy Tuning (2026-07-16)

**Ask:** tune and find the best strategy for AI Signal, per horizon (SHORT/SWING/LONG/GROWTH).
Research-only pass (no code written yet) — documents current state, gaps, and a phased plan.

### Current per-horizon strategy (`_STYLE_PROFILES`, `services/signal-engine/src/generators/signals.py:1278`)

| Param (hardcoded fallback) | SHORT | SWING | LONG | GROWTH |
|---|---|---|---|---|
| buy_threshold (bull/high_vol/bear/unknown regime) | .63/.65/.68/.62 | .72/.74/.76/.72 | .60/.65/.70/.62 | .60/.65/.68/.60 |
| hold_threshold (bull regime) | .46 | .50 | .46 | .45 |
| ml_weight_cap / ml_weight_floor | .30 / .10 | .65 / .15 | .45 / .12 | .60 / .20 |
| adx_min | 27 | 15 | None | 12 |
| min_pillars_for_buy | — | 3 | 3 | — |
| max_compress_ratio | .70 | .55 | .65 | .60 |
| BUY hold_days (`_OUTCOME_HOLD_DAYS`, routes.py:4958) | 7 | 14 | 28 | 14 |
| SELL hold_days (`_SELL_OUTCOME_HOLD_DAYS`) | 5 | 7 | 10 | 7 |

SELL threshold is a flat `_SELL_THRESHOLD_FALLBACK = 0.35` — no regime tiers (unlike BUY).
Live values are Redis overlays with 30-day TTLs, priority order: `stockai:watchdog:{STYLE}:threshold`
→ `stockai:signal_thresholds:{STYLE}` (+ `:SELL:{STYLE}`) → hardcoded fallback above; separately
`stockai:style_tune:{STYLE}:{param}` for ml_weight_cap/adx_min/high_vol_compression/breadth_compression.
All Redis-written values silently revert to the hardcoded table on TTL expiry with no alert.

### What's scheduled vs. manual-only

**Weekly (Sun 14:00 PT, `market-data/scheduler.py` `_weekly_full_refresh`):** `/ml/tune_all`
(AUC-only, not P&L), `calibrate_ta_weights`, `calibrate_conviction_weights`,
`outcomes/calibrate/apply` (per-horizon BUY+SELL threshold sweep, 0.55–0.85, routes.py:3614),
`tune_style_profiles` (ml_weight_cap 0.15–0.75, adx_min 10–40, compression on/off; routes.py:4031),
`calibrate_entry_weights` (paper-trading), RL training, `calibrate_min_rr_ratio` (see the
SELFIMPROVE-NEVER-CALIBRATED-PARAMS section elsewhere in this file).

**Daily:** `signal_watchdog` (06:10 ET — emergency ±0.02–0.03 nudge, 7-day TTL, max 3 tightenings
before flagging for manual review).

**Manual-only (never scheduled):** `calibrate_ml_weight`, the gate harness
(`GET /paper-portfolio/backtest/min-entry-score` + `/promote`), `gate_backtest`,
`backfill_realized_ev`.

**Never tuned anywhere, permanently hardcoded:** `hold_threshold`, SELL's regime tiers (SELL has
none at all — BUY does), earnings/news/RS/weekly compression maps, `max_compress_ratio`,
`min_pillars_for_buy`, `ml_weight_floor`, the `hold_days` windows themselves, and every regime
tier is applied as a flat delta off the bull baseline (`_get_dynamic_buy_threshold()`) — the
calibration mechanisms themselves are regime-agnostic.

### Data volume — a real constraint

Per `docs/SELF_IMPROVEMENT_LOOP.md` (2026-07-06 snapshot), resolved `is_correct_10d` outcome
rows: SHORT ≈120, SWING ≈115, LONG ≈77, GROWTH ≈94. **LONG and GROWTH fall below
`outcomes/calibrate/apply`'s 100-sample floor and are silently skipped every single week** —
this has presumably been true continuously since that snapshot; a fresher count needs a live DB
query, not available in a read-only research pass.

### Established conventions every existing sweep already follows (any new tuner must too)

Chronological 70/30 split (never random — avoids look-ahead leakage), a per-slice minimum
sample floor, candidate must beat the CURRENT LIVE baseline's own validation-slice EV (never a
fixed number — repeated tuning runs compare against the truth, not a stale target),
`EV = mean(pct_return)` (never `avg_return × win_rate` — T232-OC4, a documented double-counting
bug fixed elsewhere), unconditional rejection of negative EV lift (a real past incident applied
a worse SELL:GROWTH threshold before this gate existed), one `TuneHistory` row per attempt via
`_record_tune_history()` regardless of outcome (promoted or rejected), and Redis reads always
clamp to sane bounds in case of a corrupted/stale cached value.

### Design-doc delta

`docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md` (248 lines) plus the living
`docs/SELF_IMPROVEMENT_LOOP.md` — Phases 1–3 are done (walk-forward everywhere, the
`min_entry_score` gate harness, the promotion gate + `tune_history` table). NOT done: Phase 2b
(equity-curve replay for `min_kscore`/`min_ta_score`/`min_volume_z` — needed to test LOOSENING a
parameter, not just tightening), Phase 2c (decision-engine path), Phase 4 (ML-hyperparameter
P&L gate, position sizing), Phase 5 (scheduling the harness itself). **The key gap vs. "find the
best strategy per horizon": every existing mechanism tunes ONE parameter at a time in
isolation — there is no joint per-horizon sweep, no hold_days tuning, and `calibrate_ml_weight` +
the gate harness aren't scheduled or fully `TuneHistory`-integrated.**

### Phased plan (not yet built)

**Phase 1 (one session)** — `POST /signals/tune_strategy` in signal-engine `routes.py`: per
horizon, a joint grid sweep over **(buy_threshold × ml_weight_cap)** — the two highest-leverage
parameters, both re-derivable from already-stored `SignalOutcome.fused_prob` +
`Signal.reasons["ml_weight"]` with NO signal regeneration needed (a real speed advantage — this
is pure re-filtering of history that already happened, not a re-simulation). Keep the grid small
(~31×13 candidates) to limit multiple-comparison overfit risk against an n≈100-120 sample
baseline; require min_samples=15 per slice, validation-beats-current-live-baseline, unconditional
negative-lift rejection — all matching the conventions above exactly. Apply through the EXISTING
Redis keys (`stockai:signal_thresholds:{H}`, `stockai:style_tune:{H}:ml_weight_cap`) so the READ
side (`_decide_style()`, `_get_style_tuned_param()`) needs zero changes. One `TuneHistory` row
per horizon per run. Companion `GET /signals/strategy_status` reporting live-vs-hardcoded values
per horizon side by side. LONG/GROWTH will skip until enough data accumulates — surface that
explicitly in the response rather than silently.

**Phase 2** — sweep `hold_days` per horizon using the ALREADY-POPULATED `return_5d/10d/20d`
columns as three candidate exit windows (vs. today's single hardcoded `_OUTCOME_HOLD_DAYS`
value) — same no-regeneration speed advantage as Phase 1.

**Phase 3** — once a few manual cycles look sane, add to the Sunday scheduler (replacing/
augmenting the existing calibrate/apply + tune_style_profiles steps), and fold in
`calibrate_ml_weight` (currently manual-only) into the same run.

**Phase 4 (honest limitation, not silently glossed over)**: stored-outcome sweeps can only ever
evaluate TIGHTENING an existing parameter (re-filtering signals that already fired under the
CURRENT threshold) — simulating a LOOSER threshold or a different compression-map value would
require actually regenerating signals against historical price data, which is exactly what the
design doc's own deferred Phase 2b (equity-curve replay) is for. Phase 1-3 above are real,
buildable, and valuable, but they are fundamentally a re-filtering exercise, not a full backtest.

**Key files for implementation**: `services/signal-engine/src/api/routes.py` (existing sweep
functions at :3614, :4031, :4302, :4958 — the new tuner should sit alongside these, following
their exact structure), `generators/signals.py:1278-1577` (`_STYLE_PROFILES`, the read side),
`docs/SELF_IMPROVEMENT_LOOP.md`, `docs/DESIGN_SELF_IMPROVEMENT_LOOP_2026-07-04.md`,
`services/market-data/src/backtest/gate_harness.py` (the Phase 2b equity-replay precedent).

---

## Research: Reports Tab — Per-Market (US/HK) Report Aggregation (2026-07-16)

**Ask:** a Reports tab covering, per market: market trend, key asset performance, top-performing
stocks, money-flow-by-sector + recommended best stocks in that sector, news-sentiment
monitoring, and self-tuning/backtesting reports. Research-only pass — documents what already
exists (to maximize reuse) vs. what needs new backend work.

**User clarification (important, changes report #4's scope):** "best stocks in the sector"
means discovery across the WHOLE MARKET, not just symbols already in this app's ~150-stock
universe — with a one-click "add to my system" action once a good candidate is found. This is
a genuinely new capability (market-wide screening), not just aggregating existing per-symbol
data, and is the one part of this feature that can't be pure reuse.

### Per-report-type inventory (build-vs-reuse verdict)

| # | Report type | Verdict | Key existing endpoints/tables |
|---|---|---|---|
| 1 | Market trend | **Reuse** (near-complete) | `GET /stocks/regime?market=`, `/stocks/market_overview`, `/stocks/fear_greed` (includes `sp500_regime`/`sp500_vs_ma200_pct`), `/stocks/market_breadth` (US only — gap), `/stocks/regime-state` (HMM), `/events/valuation/cape` |
| 2 | Key asset performance | **Reuse** | `market_overview`'s `_INDICES` (^GSPC/^IXIC/^DJI/VIX/HSI), `GET /stocks/sector_rotation` (US sector ETFs vs SPY, 1w/1m/3m, leading/lagging) — gap: no HK sector-ETF equivalent |
| 3 | Top performing stocks | **Reuse** | `GET /rankings?market=` (K-Score), `/stocks/sector_performance` (per-sector day-change), `/rankings/screen`, `/admin/watchlist-performance` |
| 4 | Money-flow-by-sector + best stocks | **Reuse + 1 new endpoint + NEW market-wide screener** | `GET /stocks/sector-rotation` (Redis-cached K-Score momentum per sector, written weekly by `_compute_sector_rotation()`), `/stocks/hk-connect-flow/{symbol}` (per-symbol only — gap: no market-level top-N aggregation), `/{symbol}/options-flow`, `/{symbol}/institutional`, event-intelligence's insider/congress/institutional leaderboards, `/catalyst/leaderboard`. **NEW (per user clarification): a whole-market screener + "add to my system" action — see below.** |
| 5 | News sentiment (market-level) | **Mostly build** | Today's `news.py` (`_google_news`/`_claude_sentiment`) is per-symbol only. `T249-MARKETMOVER-P4-MARKET-PULSE-NEWS-CARD` (tracker, `todo`, effort S) is exactly this design — market-level queries through the existing pipeline, 30-min cache. `GET /events/overview`'s `latest_macro_reaction` field is already live and reusable now. |
| 6 | Self-tuning/backtest reports | **Reuse** (rich, already built) | `GET /signals/tune_status` (already rendered by `signal-tuning.tsx`), `/signals/outcomes/summary`, `/signals/accuracy`, `/signals/rolling_accuracy`, `/signals/gate_backtest`, `/admin/promotion-history`, `/admin/watchlist-rotation-history`, `/admin/scheduler-status`, `/paper-portfolio/entry_factors`, `/paper-portfolio/min_rr_calibration` |

### New capability needed for report #4 (per user's "whole market" clarification)

A market-wide stock screener is needed — NOT limited to this app's existing ~150-symbol
universe. yfinance itself has a screening capability (`yf.screen()` / predefined + custom
screener queries against Yahoo's own screener backend, still free-tier) that could surface
candidates by sector + performance without needing a paid screener API. Design: once
sector-rotation identifies a leading sector, run a market-wide screen scoped to that sector,
rank candidates by a simple momentum/volume heuristic (full K-Score requires data this app
doesn't have for a symbol not yet in the universe), and surface each with an **"Add to my
system"** button — reusing this app's EXISTING add-stock/ingest pipeline (the same one driving
manual symbol additions today) to seed the new symbol, trigger initial ingestion, and optionally
add it to a chosen watchlist in one action.

### Page structure precedent

`frontend/src/pages/intelligence.tsx` is the model to follow: a `type Tab` union + a `TABS`
array + `useState<Tab>` + one component per tab, backed by a single aggregate fetch
(`eventsOverview()`). Nav: add a `Reports` entry to the `Markets` group in `_app.tsx`'s
`NAV_GROUPS`.

### Phased plan (not yet built)

**Phase 1 — frontend-only, composing existing endpoints** (covers report types 1, 2, 3,
4-partial, 5-partial via the macro-reaction field, and 6 in full): new
`frontend/src/pages/reports.tsx` with a US/HK market toggle + tabs (Trend / Assets / Top Stocks
/ Money Flow / News & Macro / Self-Tuning), composing `regime`, `marketOverview`, `fearGreed`,
`marketBreadth`, CAPE, `sectorRotationEtf`, `sectorRotation` (K-Score momentum), `rankings`,
`sectorPerformance`, `eventsOverview`, `signalTuneStatus`, `outcomesSummary`,
`promotionHistory`, `schedulerStatus`, `minRrCalibration`, `entryFactors`. Touches:
`frontend/src/pages/reports.tsx` (new), `frontend/src/pages/_app.tsx` (nav entry),
`frontend/src/lib/api.ts` (a few missing wrappers — `hkConnectFlow`, `gateBacktest`,
insider/congress/institutional leaderboards if not already present).

**Phase 2 — new backend, ranked by effort:**
1. HK southbound money-flow top-N endpoint (simple SQL over the already-existing
   `hk_connect_flows` table) — S.
2. HK market breadth (extend `market_breadth` with a `market` param) — S.
3. T249-P4 market-level news-pulse endpoint (design already written in the tracker) — S/M.
4. Whole-market sector screener + "Add to my system" action (per the user's clarification
   above — the one genuinely new discovery capability, not just aggregation) — M.
5. HK sector-ETF rotation equivalent to the existing US one — M.
6. `/stocks/top_movers?market=` N-day gainers/losers convenience endpoint (optional — largely
   already composable from rankings + sector_performance client-side) — S/M.

---

## Recurring Issue: `/events/overview`'s Nested `top_buys` Is a DIFFERENT Shape Than the Standalone Leaderboard Endpoints — Reused the Wrong Type

**Symptom (found 2026-07-17):** After the Reports tab (`reports.tsx`) shipped, the News & Macro
tab threw a runtime crash — reported by the user as "News and Macro not working." Separately,
`intelligence.tsx`'s Overview tab silently showed blank/dash values for insider top-buy scores,
with no visible error at all.

**Root cause:** `GET /events/overview`'s `insider.top_buys` field is populated server-side by
`get_insider_leaderboard()` (`services/event-intelligence/src/api/routes.py`), which returns
`{stock_id, symbol, company, purchases, sales, net_value}` — confirmed directly against the
real live response. This is a genuinely DIFFERENT shape than `InsiderLeaderItem`
(`{symbol, score, buy_count, sell_count, net_value}`), the type used by the STANDALONE
`GET /events/insider/leaderboard` endpoint. `frontend/src/lib/api.ts`'s `EventIntelOverview`
type wrongly reused `InsiderLeaderItem` for the nested `/events/overview` field, even though
the two endpoints are backed by different code and return different fields. The congress side
happened to escape detection the same way — `CongressLeaderItem` has `net_amount`/
`unique_politicians` which don't exist on `/events/overview`'s actual congress rows either,
it just wasn't hit as hard because `intelligence.tsx`'s congress rendering only read the one
field (`net_amount`) that happens to also exist on the real congress shape by coincidence.

`reports.tsx`'s `NewsTab` called `b.score.toFixed(0)` directly — since the real data has no
`score` field, this threw `TypeError: Cannot read properties of undefined (reading 'toFixed')`
and crashed the whole tab. `intelligence.tsx`'s Overview tab called the same nonexistent
`item.score` but routed it through a null-safe `fmt()` helper first (`fmt(item.score)` returns
`'—'` for `undefined`) — same underlying type bug, but it degraded to a silently-wrong display
instead of a hard crash, which is why it went unnoticed until the Reports tab's less-defensive
code hit the exact same bug and actually crashed.

**Fix applied (2026-07-17):** Added distinct `OverviewInsiderTopBuy`/`OverviewCongressTopBuy`
types to `api.ts` matching the REAL `/events/overview` response shape, and corrected both
`reports.tsx` and `intelligence.tsx` to read the real fields (`purchases`/`net_value`/`company`)
instead of the wrong borrowed type's fields (`score`/`buy_count`).

**Design invariant:** never assume two endpoints that return "the same kind of data" (here,
"insider top buys") share a wire type just because the field names sound similar — a nested
field on an aggregate/overview endpoint is frequently built by different backend code than the
dedicated single-purpose endpoint for that same concept, and can have a genuinely different
shape. Verify the ACTUAL response shape (a live curl/query) before reusing an existing
TypeScript type for a new call site, especially for aggregate endpoints like `/events/overview`
that pull from multiple internal helper functions. Also: prefer failing loudly (direct field
access) over silently-safe helpers (`fmt()`-style null coalescing) when wiring up a NEW field
for the first time — the silent version can mask a real type mismatch for a long time, exactly
as it did here in `intelligence.tsx`.

**What to check if a similar "endpoint X and Y look like they return the same shape" bug is
suspected:**
```bash
# Query the real live response directly rather than trusting the TypeScript type on file:
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://api-gateway:8000/events/overview', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.json()['insider']['top_buys'][0])
"
```

---

## Recurring Issue: A Full EC2 Instance Reboot Reverts EVERY `docker cp` Hotfix Across ALL Containers At Once

**Symptom (2026-07-17):** the EC2 instance became completely unreachable (no SSH, no HTTPS,
100% ping loss) for an unknown external reason (not caused by anything deployed this session —
last confirmed action before the outage was a routine frontend image build). On recovery, every
container had been recreated fresh — `api-gateway` crash-looped immediately
(`ModuleNotFoundError: No module named 'numpy'`, a real, separately-documented bug below), and
a systematic check found **12 service-local files across 5 services, plus `shared/db/models.py`
and `shared/common/logging.py` across all 10 other backend containers, had silently reverted**
to whatever was baked into each image at its last real build — every fix applied via `docker cp`
during this entire session (T230-CHARTING-PREMARKET's ingestion.py/yfinance_adapter.py,
AUD250's scheduler.py/routes.py fixes across 4 services, SELFIMPROVE-NEVER-CALIBRATED-PARAMS'
paper_portfolio.py/paper_trading_engine.py, T254's trendlines.py FVG detector, and both
`shared/` files) was gone. One file (`event-intelligence`'s `macro_reaction.py`) didn't exist
in the image AT ALL — that container's image predates the file's creation and it had only ever
lived via `docker cp`, never a real rebuild.

**Root cause:** this is the exact risk already documented elsewhere in this file under
"`docker compose up -d --force-recreate <one-service>` Can Recreate EVERY Service — And
Recreation Silently Reverts `docker cp`-Patched Files" — except at MAXIMUM scale. That entry
was about ONE `docker compose up` sweeping in unintended sibling services. A full instance
reboot recreates **literally every container**, all at once, with no warning and no way to
`docker ps`-diff "before" against "after" the way that entry's own mitigation describes — there
is no "before" snapshot when the whole machine went down external to any action taken here.

**Fix applied:** systematically diffed every `docker cp`-patched file this session had touched,
service-by-service, against the git checkout (`diff <(docker exec ... cat ...) <local path>`)
for all 11 backend services — not just the ones that crashed. Found and re-`docker cp`'d 12
service-local files + `shared/db/models.py` + `shared/common/logging.py` to every affected
container, cleared `__pycache__`, restarted, verified clean startup logs and a live functional
check (confirmed `fair_value_gaps` still returns real data from technical-analysis post-restart).

**What to check after ANY event that force-restarts the whole instance (reboot, host
maintenance, an EC2 status check failure, `docker compose down && up` at the compose-file
level) — not just after a single-service `--force-recreate`:**
```bash
# For EVERY service you've ever docker cp'd a fix into this session (check your own session
# history, not just what crashed) — diff the running container against the git checkout:
for f in <list of every file you docker cp'd this session>; do
  diff <(docker exec stockai-<service>-1 cat /app/<path>) <local repo path>
done
# Also check shared/db/ and shared/common/ across ALL 11 containers, not just the ones you
# personally touched — a shared file synced to container A during today's work is just as
# reverted as one synced to container B.
for c in market-data signal-engine ranking-engine technical-analysis event-intelligence \
         research-engine api-gateway ml-prediction decision-engine strategy-engine portfolio-optimizer; do
  diff <(docker exec stockai-$c-1 cat /app/shared/db/models.py) shared/db/models.py
  diff <(docker exec stockai-$c-1 cat /app/shared/common/logging.py) shared/common/logging.py
done
```

**Design invariant, stated more strongly than the earlier single-service version of this
entry:** `docker cp` is fundamentally a SESSION-SCOPED hotfix, not a deployment. ANY event that
recreates a container — a targeted `--force-recreate`, a full `docker compose down/up`, or an
entire instance reboot outside anyone's control — reverts it back to whatever the image was
built with. The only way a fix survives across an unplanned full-instance event is if it was
baked into a real image via `docker compose build` / `docker build` at some point. Every
`docker cp` fix applied in a session should be treated as "still owed a real image rebuild"
until that rebuild actually happens — this incident is the proof that the gap between
"hotfixed" and "durably deployed" is not hypothetical.

**CORRECTED same day — the first recovery pass above was itself incomplete.** It only diffed
files this session specifically remembered `docker cp`'ing (the ones tied to fixes made
earlier in the same conversation), not an exhaustive sweep of every `.py` file in every
service. The user then reported two NEW-looking bugs in the just-built Reports page — CAPE
stuck loading forever, News & Macro not working — which traced back to the SAME root cause,
just in files the first pass never checked: `event-intelligence`'s `routes.py`, `scheduler.py`,
`economic.py`, and `services/valuation.py` had ALSO reverted. `valuation.py` (the entire CAPE
feature, Tier 249) was missing from the container **entirely** — its image predates the file's
creation, meaning `docker cp` had been the ONLY way that feature was ever deployed, the whole
time since it was built. A genuinely exhaustive re-sweep (every `.py` file under every
`services/*/src/`, not a remembered subset) found **31 reverted files across 9 services**
total: `event-intelligence` (7 files + 1 missing entirely — worst offender), `ranking-engine`
(2), `ml-prediction` (5), `decision-engine` (6 — its whole core scoring pipeline), `strategy-
engine` (3), `portfolio-optimizer` (2), `market-data`'s `admin.py` + 2 more, `signal-engine`'s
`generators/signals.py`, `technical-analysis`'s `indicators/core.py`. Only `research-engine`
and `api-gateway` were genuinely fully clean. All 31 re-synced, verified byte-identical,
restarted, and confirmed live (`GET /events/valuation/cape` returns real data; `GET
/events/overview` includes `latest_macro_reaction`).

**The lesson under the lesson:** after an incident like this, a "fixed" claim based on
checking only the files you personally remember touching is itself unverified — the same
"verify in both directions, don't trust a status claim" discipline this file already applies
to stale tracker entries (see the SE-F2 section) applies just as much to your OWN prior
"done" claim within the same session. The only reliable check is exhaustive: every file, every
service, not a remembered subset. A dedicated sweep agent doing `find services/<svc>/src -name
'*.py'` then diffing each one against the container is cheap enough to just always do fully,
rather than trying to reconstruct "which files did I touch this session" from memory.

---

## Recurring Issue: api-gateway Crash-Loops on `ModuleNotFoundError: No module named 'numpy'`

**Symptom (found during the reboot-recovery above):** `stockai-api-gateway-1` crash-looped
immediately on every fresh start with `ModuleNotFoundError: No module named 'numpy'`, traced
through `shared/common/__init__.py` → `from .indicators import ...` → `shared/common/indicators.py`'s
`import numpy as np`.

**Root cause:** `T233-ARCH-INDICATOR-DEDUP` (2026-07-09, commit `6a6de85`) added
`shared/common/indicators.py` and wired it into `shared/common/__init__.py`'s unconditional
top-level imports — meaning EVERY service that does `from common import ...` (or transitively
triggers `common/__init__.py`, which is essentially all of them) now requires `numpy` and
`pandas`, whether that service actually uses indicators or not. Every other service's
`requirements.txt` already had `numpy` (it's a common transitive need for a data-heavy trading
app), but `api-gateway` — originally a thin auth/routing proxy with no data-science
dependencies by design — never did. This was a DORMANT bug for over a week: the running
`api-gateway` process had already successfully imported everything before the fix landed (or
survived on a build predating it), so nothing crashed until this session's unrelated instance
reboot forced a genuinely fresh container start and the import ran for real for the first time.

**Fix applied (2026-07-17):** added `numpy==1.26.4` and `pandas>=2.0.0` to
`services/api-gateway/requirements.txt` (matching every sibling service's exact numpy pin) and
rebuilt the image via `docker compose -f docker/docker-compose.yml build api-gateway` (a real
rebuild was required here — this is a dependency addition, not a code hotfix, so `docker cp`
cannot fix it). Verified clean startup and live traffic post-recreate.

**Systemic risk not fixed here (documented, not silently dropped):** `shared/common/__init__.py`
importing `indicators.py` unconditionally means ANY future service added to this repo, or any
existing thin service, could hit this same class of bug the moment it touches `shared/common/`
at all — the dependency is invisible until a cold start actually exercises the import chain.
The more robust fix would be making the `indicators` import lazy (deferred until a caller
actually requests `sma`/`ema`/etc.) so services that never touch indicators never pay the
numpy/pandas cost — not done here under incident-recovery time pressure, since it touches a
file imported by all 11 services and deserves its own careful, non-incident-driven change.

**What to check if a similar crash-loop appears in a different service:**
```bash
docker logs stockai-<service>-1 --tail 40 | grep -A3 "ModuleNotFoundError"
# If the traceback bottoms out in shared/common/__init__.py -> indicators.py -> numpy/pandas,
# check that services/<service>/requirements.txt actually has numpy + pandas pinned — compare
# against a sibling service's requirements.txt (they should all match on these two).
```

---

## Feature Reference: Mobile Nav Drawer (T251-MOBILE-RESPONSIVE-DESIGN, Phase 1)

**Built 2026-07-17.** A 2026-07-16 audit found the whole app effectively desktop-only — zero
`isMobile`/`useMediaQuery`/`matchMedia` usage anywhere, and the shared nav bar in `_app.tsx`
(logo + up to 6 dropdown groups + search box + user controls, one non-wrapping flex row) is the
single worst offender: it will visibly clip/overflow on any phone-width screen with no fallback
at all, unlike most page bodies which at least degrade to horizontally-scrollable tables.

**What shipped (Phase 1 only — the nav bar, the one component every page shares):**
- `frontend/src/styles/globals.css` — a `.desktop-nav-row`/`.mobile-nav-toggle` CSS pair, swapped
  by a single `@media (max-width: 767px)` block. Above 768px this is a no-op (`.desktop-nav-row`
  is `display:flex` unconditionally, `.mobile-nav-toggle` is `display:none`) — the desktop layout
  is pixel-identical to before this change.
- `frontend/src/pages/_app.tsx` — a new `mobileMenuOpen` state + hamburger button (☰ / ✕,
  `.mobile-nav-toggle`, only visible below 768px) and a new `MobileNavDrawer` component: a
  click-to-expand accordion (not hover — hover has no touch equivalent) over the same
  `NAV_GROUPS` data the desktop dropdowns use, so there is exactly one source of truth for nav
  structure. The drawer also repeats the search box and user controls (settings/logout) at the
  bottom, since those live in the same now-hidden desktop row. The drawer auto-closes on route
  change (a `useEffect` keyed on `router.pathname`) and on any item click, so it never lingers
  open behind a freshly-navigated page.
- Verified via a full `npx next build` (all pages compiled clean, not just the changed one) and
  by grepping the actual compiled `.next/static/css`/`.next/static/chunks` output for the new
  class names, the `max-width:767px` rule, and the hamburger's aria-label — confirming the
  change is really present in what would ship, not just correct-looking in source.

**Not yet built (Phase 2, tracked as the remaining scope on the same tracker item):** per-page
responsive breakpoints for the ~57 files using rigid fixed-pixel-width grids (stock detail's
`1fr 320px` sidebar, positions/insider's 8-column tables, strategies.tsx's `240px 1fr`, etc.).
These pages still don't collapse to single-column on a phone — most are at least wrapped in
`overflowX:auto` so they degrade to scrollable tables rather than breaking outright, which is
why the nav bar (no such fallback) was prioritized first.

**What to check if the mobile nav looks wrong:** `_app.tsx`'s `MobileNavDrawer` function and the
`.desktop-nav-row`/`.mobile-nav-toggle` rules in `globals.css` are the only two places this
logic lives — if the hamburger doesn't appear or the desktop row doesn't hide at phone width,
check the compiled CSS actually contains the `max-width:767px` block (a stale cached build could
serve pre-change CSS, same class of bug as the frontend build-cache issues documented above).

---

## Feature Reference: `_should_enter()` / decision-engine Score Parity (T232-DL-DUALSCORER-DEBT, partial)

**Built 2026-07-17.** `T232-DL-DUALSCORER-DEBT` documents ~34 dimensions where
`paper_trading_engine._should_enter()` (the fallback gate, used only when decision-engine is
unreachable — `decision_engine_mode="primary"` is the live default, so DE's `/decide/{symbol}`
verdict drives real entries whenever it responds) diverges from decision-engine's
`scorer.py`/`hard_rejects.py`. That item remains open as a whole; this was a narrow, verified
slice of it.

**Corrected assumption before writing any code:** research-recommendation gating looked like a
live divergence at first read (DE's `hard_rejects.py`/`scorer.py` accept a `research_rec`
param that `_should_enter()`'s signature doesn't have at all) — but decision-engine's `/decide`
route independently fetches research itself via `aggregator.py`'s `fetch_all()` ->
`_fetch_research()`, rather than relying on `paper_trading_engine` to forward it in the
request body. So DE's research hard-reject and research-score layer already work correctly
whenever DE is reachable — not a real gap, despite how it read on first pass.

**Three genuinely-open gaps, all safely portable (pure functions of data `_should_enter()`
already receives), ported into `paper_trading_engine.py`'s `_should_enter()`:**
1. **Pre-regime early-warning score (F11)** — `-1` for `is_pre_choppy`/`is_pre_risk_off`.
   `_should_enter()` previously only used these flags one level up in `_scan_for_entries` (for
   `min_entry_score`/sizing), never as a direct score component the way DE's `scorer.py` does.
2. **Market regime as a direct score layer** — bull `+1` / choppy `-1` / risk_off `-2`.
   Previously `_should_enter()` only used `regime_state` to raise thresholds (`min_entry_score`,
   `min_rr`) and dampen sizing — a different mechanism from DE's direct score adjustment that
   does not necessarily land on the same pass/fail boundary for a borderline candidate.
3. **K-Score as a direct ±1 layer** — `_should_enter()` already received `kscore` (used inside
   its RL-adjustment and calibrated-logistic-bypass branches) but never scored it directly like
   DE does. A portfolio without 100+ closed trades' calibration got zero adjustment for a weak
   K-Score during exactly the DE-outage window when the fallback's quality matters most.

**Deliberately NOT ported** (per the same research pass's own recommendation): RL policy
adjustment and the calibrated-logistic-regression bypass remain `_should_enter()`-only — both
depend on `market-data`-local file state (`rl_agent.py`'s trained Q-function, `entry_weights.json`)
that decision-engine has no access to as a separate service. Porting either would mean a new
cross-service callback on DE's hot path or duplicating model-loading logic in a second service
— both worse than documenting the asymmetry. `sizer.py` also untouched — it's explicitly
illustrative-only and never consumed by real trades (its own module docstring says so).

**Tests:** `services/market-data/tests/test_should_enter_de_parity.py` (13 tests) isolates
exactly the three new layers using otherwise-neutral inputs (a candidate that clears every
hard reject and scores 0 on every pre-existing layer). Adversarially verified: temporarily
disabled the K-Score layer and confirmed 3 tests correctly failed before re-enabling it. Full
existing 174-test `market-data` suite stays green.

**A real test-writing gotcha hit along the way:** `conftest.py` stubs `SessionLocal` as a bare
`MagicMock()` — its chained `.execute().fetchone()` is truthy by default, which silently trips
`_should_enter()`'s macro-blackout hard reject in every test unless `signal_data["reasons"]`
explicitly sets `"macro_blackout": False` to hit the fast-path check before the DB fallback
query ever runs. Also: choppy/risk_off regimes raise the R:R hard-reject floor and separately
trigger the pre-existing cross-horizon-consensus score penalty — a naive "neutral baseline"
input isn't actually regime-neutral in this function, so isolating just the new regime-score
layer required bumping `take_profit` (to clear the raised R:R floor) and setting
`cross_style_buys=2` (to neutralize the unrelated pre-existing consensus layer) in those
specific tests.

---

## Deep Audit: Trading Gate / Chart / Reports (2026-07-17) — 10 Confirmed Bugs, All Fixed

**Trigger:** user asked for a full audit of everything touched recently, with explicit focus
on "paper trading, decision engine, market regime, ai signal, FVG, entry point, stop loss,
target price." Process: 6 parallel agents each independently reviewed a real `git diff`
against a fixed base commit for their area (chart/FVG/drawing tools; Reports/nav/API types;
market-data backend; docs/tracker consistency; decision-engine+paper-trading gate; AI-signal-
to-entry/stop/target pipeline), reading the actual current code, not just diff text. 3
adversarial verification agents then tried to REFUTE the highest-stakes candidates before
anything was reported — one verified the date-vs-datetime claim by querying the real
production Postgres container directly. All 10 reported findings were CONFIRMED.

**Two of the ten broke features shipped in the SAME session** — a reminder that shipping a
feature and auditing it are genuinely different activities; neither the original build nor its
own tests (which mock/stub the exact boundary the bug lived in) caught either one.

### 1. Pre-market brief's macro section could never show anything (date-vs-datetime)

`_macro_events_from_db(session, today, today)` compared `EconomicEvent.event_date`
(DateTime, rows land at e.g. 08:30 UTC) against a bare `date` cutoff. Postgres coerces the
date to midnight for the comparison — confirmed live: `'2026-07-17 08:30:00'::timestamp <=
'2026-07-17'::date` returns `false`. Invisible for `events_calendar()`'s existing 90-day-ahead
window (the exclusion only clipped the far edge); fatal for the brief's `cutoff==today` call,
which excluded literally every same-day release. **Fix:** widen the upper bound to end-of-day
via `datetime.combine(cutoff, datetime.max.time())` inside `_macro_events_from_db()` itself —
a no-op for the wide-window caller, fixes the same-day case.

### 2. US and HK pre-market briefs were near-duplicates

The recipient query (`select(PriceAlert).where(triggered.is_(False))`) had no market filter at
all — both jobs emailed the identical full subscriber list, and macro content (US-only FRED/
FOMC data) was identical in both, useless at 8am HKT. **Fix:** recipients now filtered by
`_sym_market(a.symbol) in markets` (only users watching a symbol in THIS market get THIS
market's brief); macro-releases and macro-reactions sections both gated behind `if "US" in
markets`, so the HK brief only ever contains real HK earnings data or doesn't send.

### 3. Pre-regime double-penalty (introduced by this session's own T232 DE-parity fix)

`_scan_for_entries` already raised `min_entry_score` for `is_pre_choppy`/`is_pre_risk_off`
(the pre-existing `RE-9` mechanism). Adding a score-layer subtraction for the same flags (this
session's earlier T232-DL-DUALSCORER-DEBT parity fix) meant a pre-regime window now hit twice
— raised floor AND lowered score — a discontinuous 2-point swing at the boundary with zero
backtest coverage (`gate_harness.py` replays with `live_regime=None`). Checked decision-engine's
own `min_score_for_regime()`: it only reads `regime_state`, never the pre-regime flags — DE
applies the effect exactly once, via score only. **Fix:** removed the `min_entry_score` raise
from `RE-9`'s pre-regime block, kept only its sizing tighten — the score-layer subtraction is
now the sole pre-regime effect, matching DE exactly instead of over-correcting past it.

### 4/5. Inverted-looking R:R + no currency handling (PositionSizer, PriceChart)

Both `PositionSizer.tsx` and `PriceChart.tsx`'s risk-reward label computed `Math.abs()` on
both the risk leg AND the reward leg — a take-profit on the wrong side of entry (e.g. an
analyst `target_price` below current price, a bearish signal) still produced a positive-
looking R:R, displayed as if it were a favorable long. Separately, `PositionSizer` has one
global USD account-size setting with no currency awareness at all — HK stock entry/stop/
target (HKD) were silently sized as if USD, off by the FX rate (~7.8x) with zero indication.
**Fix:** direction is now inferred from stop-vs-entry (this tool has no explicit long/short
toggle); when target lands on the wrong side for that inferred direction, the R:R figure is
suppressed entirely with a visible warning instead of showing a misleading ratio. For
currency: no FX-conversion data source exists anywhere in this app, so rather than guess an
exchange rate, a `currency` prop (from the stock detail page's live-price data) now drives a
mismatch warning banner and relabels "Account Size ($)" to the stock's real currency when it
isn't USD — surfacing the problem honestly rather than pretending to solve it.

### 6. ETF fundamentals permanently uncached (this session's OWN empty-fetch guard, over-applied)

The `fetch_looks_empty` guard added earlier this session (to fix a real null-overwrite
incident) checked `market_cap is None and trailing_pe is None and total_revenue is None` —
exactly the three fields genuinely absent from every real, successful ETF fetch (GLD/SPY/
sector ETFs report `totalAssets` instead). Every ETF request tripped the guard, permanently
skipping caching and DB persistence, re-hitting yfinance on every page view with zero cache
protection — the guard couldn't tell "yfinance failed" from "this ETF genuinely has none of
these three." **Fix:** added `quoteType in ("ETF", "MUTUALFUND") or totalAssets is not None`
as a fund-type carve-out — a genuinely failed fetch (`info == {}`) still correctly trips the
guard since neither signal would be present either.

### 7. All 7 Reports nav items highlighted as "current" simultaneously

`NavGroup`'s item-level `isCurrent` used `navPath(item.href)` (strips the query string) — the
7 Reports items differ ONLY by `?tab=`, so they all reduced to the same pathname and all
highlighted at once regardless of which tab was actually open. **Fix:** new `isItemCurrent()`
helper compares the query string too when an href actually has one (a no-op for every other
plain-path `NAV_GROUPS` entry); `currentSearch` (from `router.asPath`) threaded through both
`NavGroup` and `MobileNavDrawer`. Group-level `isActive` (the whole Reports group highlighting
regardless of tab) was already correct and untouched.

### 8/9. Fair Value Gap detection: cap ordering and single-bar fill requirement

`detect_fair_value_gaps()`'s `max_gaps` cap was a pure `gaps[-max_gaps:]` slice — the most
RECENTLY FORMED gaps by bar index, mixing filled/unfilled with no regard for actual relevance.
A genuinely nearest, still-actionable OLDER gap could be silently dropped if 20+ newer (even
already-filled, far-away) gaps had formed since. Separately, the fill check required a SINGLE
bar's range to span the entire gap — a gap traded through gradually over several bars (each
covering only part of the range) never satisfied that and stayed "unfilled" forever, rendering
a long-dead gap as a live level. **Fix (8):** gaps are now sorted by `(filled, distance-to-
last-close)` before the cap is applied — unfilled-and-nearest survives over filled-and-recent
— then restored to chronological order for stable rendering. **Fix (9):** replaced the single-
bar check with a cumulative contiguous-coverage tracker: extends a covered `[lo, hi]` sub-range
only when a new bar's overlap with the gap is itself contiguous with what's already covered.
Caught a real bug while implementing this fix — a first version incorrectly marked a gap
"filled" just because a LATER bar's high exceeded the gap's top, even though that bar's full
range sat entirely ABOVE the gap and never actually touched it; a dedicated test
(`test_a_bar_entirely_above_or_below_the_gap_does_not_falsely_mark_it_filled`) catches this
distinct failure mode from the "disjoint touches with an untraded middle strip" case.

### 10. Calibration mixing two score scales under one coefficient

This session's own T232 DE-parity fix (added 2026-07-17, earlier in the day) shifted
`_should_enter()`'s score scale for every trade entered from that point forward.
`calibrate_entry_weights()` persists `entry_score` verbatim and fits `w_score` across the FULL
closed-trade history with no distinction — pre- and post-change trades, on two different
score scales, mixed under one coefficient. Assessed as real but bounded (self-correcting once
enough post-change trades accumulate; the fit is already gated by "must beat baseline EV on a
held-out validation slice," which would catch a badly-mis-fit model, just not necessarily a
subtly-biased one). **Fix:** added `PaperTrade.entry_date >= date(2026, 7, 17)` to the query —
every future calibration run now trains on a single, internally-consistent score scale. Not a
schema change or a new `score_version` column — the existing `entry_date` column plus a fixed
cutoff constant was sufficient and far less invasive.

### Verification discipline applied throughout

Every fix with a plausible sabotage point was adversarially self-verified DURING
implementation, not just claimed: temporarily broke the FVG contiguity check (removed the
`lo <= covered_hi and hi >= covered_lo` guard) and confirmed the disjoint-gap test correctly
failed before restoring it; temporarily removed the K-Score `is not None` guard from an
earlier fix in the same file and confirmed 3 tests caught it (repeat of the same discipline
already documented above for `_should_enter_de_parity.py`). 24 new/updated test cases across
4 files, all passing; full existing suites (193 `market-data`, 26 `technical-analysis`, 42
frontend) stay green; frontend typecheck and a full `next build` both clean.

**Not fixed this pass — documented as known, lower-priority gaps, not silently dropped:**
`regime_min_rr_ratio` is never forwarded from `paper_trading_engine` to decision-engine (DE
falls back to a hardcoded 3.0, the fallback path uses the calibrated value) — a real
asymmetry, but assessed lower-impact than the 10 fixed here, deserving its own focused pass
rather than a rushed addition to an already-large batch. `_monitor_positions` can compute exit
logic against a stale cached price during a multi-cycle live-quote gap (a data-gap-driven
phantom stop/target, not a logic bug in the boundary check itself). The pre-market brief has
no send-state dedup (a restart within the 60s misfire-grace window could re-email every
recipient) and no per-user error isolation in its send loop (one bad address aborts the rest).
The mobile nav drawer doesn't lock body scroll, ignores the impersonation banner's height in
its `maxHeight` calc, and duplicates `GlobalSearch` (two keydown listeners, one of which tries
to focus a CSS-hidden desktop input). Trendline drawings can break across timeframe switches
(bar indices captured on one `activePrices` array reused against a different one). "Top Buys"
cards can display net-negative (net-selling) rows under a "buys" heading. `CapeResponse.latest`
is typed non-nullable but the backend can return `null` — currently harmless since the one call
site guards it, but a real type-safety gap for a future consumer.

**Standing exposure, unchanged by this pass:** 5 backend files remain hotfix-only in
production (deployed via `docker cp` during this session, never baked into a rebuilt image) —
`scheduler.py`, `email_service.py`, `paper_trading_engine.py`,
`technical-analysis/src/indicators/trendlines.py`, and its `routes.py`. Per the standing
"docker cp is a session-scoped hotfix, owed a real image rebuild" invariant documented
elsewhere in this file — this audit's own fixes to these same files are ALSO currently hotfix-
only until a real image build happens.

---

## Research: Tier 257 — Four Feature Designs (2026-07-17, design-only, no code yet)

User ask, verbatim intent: (1a) a per-minute abnormal-volume alert with a breakout-or-breakdown
read; (1b) a per-minute "top 3 stocks about to move, very very high confidence" buy/sell email;
(3) overnight options-flow + futures-flow analysis to read whether the market opens high/low
and lay out the day; (4) prod E*Trade "using client secret but why still login — make it more
systematic." All four researched against the actual codebase (3 parallel mapping agents,
file:line-verified) before designing. Tracker: T257-* entries.

### 1a. Abnormal-Volume Alert (T257-VOLUME-ANOMALY-ALERT)

**The data path already exists and is the ONLY viable one at 1-minute cadence:**
`stockai:live_prices` (refreshed every 1 min by `_live_price_refresh_job`, scheduler.py:4657 —
one bulk yf.download for the whole universe; carries current-day cumulative `volume` +
`change_pct`) and `stockai:avg_volume` (`_AVG_VOLUME_KEY`, 20-day mean, refreshed 4-hourly).
A new 1-min job MUST read only these two Redis keys — per-symbol yfinance or `/rvol` DB calls
at 150-symbols/minute would rate-limit or hammer the DB (yfinance was observed rate-limited
this very day). Precedent: the post-open digest's vol_surge scan (scheduler.py:3838) already
does exactly this Redis-only universe sweep, just at 5-6×/day instead of every minute.

**Abnormality math — reuse T241's session-elapsed scaling, don't invent new math:** raw
`volume/avg_volume` compares partial-day cumulative volume against a FULL-day average — at
10:00 ET even a normal day looks "low" and a slightly-busy open looks normal. The already-fixed
form (T241-AUDIT-RVOL-INTRADAY-BIAS, scheduler.py:3881-3887) scales the threshold by session
elapsed fraction: `surge_threshold = max(1.05, BASE × elapsed_frac)`. New job uses the same,
with a higher BASE (e.g. 2.5-3.0× for "abnormal/huge" vs. the digest's 1.5×) — exact value to
tune after observing a week of candidate counts.

**Direction + breakout/breakdown read (honest version):** direction from `change_pct` sign.
For the handful of symbols that actually trigger (not universe-wide): compare live price
against the stored game-plan `breakout` level (already computed into signal reasons,
scheduler.py:843) and stop level → label "pressing its breakout level ($X) on Nx volume" /
"breaking below stop/support ($Y) on Nx volume." One technical-analysis `/levels` HTTP call
per TRIGGERED symbol is acceptable (few/day); never in the universe loop. Framing per repo
discipline: the email reports the measured fact (volume ratio + which level price is testing)
and historical context — it does NOT claim "this WILL break out"; nobody can honestly deliver
that, and the repo's T249-P3 precedent explicitly rejects prediction claims.

**Job shape:** every-minute `add_job(..., "interval", minutes=1, max_instances=1,
coalesce=True)`, 55s Redis lock (`stockai:lock:...` — same pattern as check_price_alerts),
market-hours-gated (the live-price cache is only fresh during market hours anyway). Recipients:
the established PriceAlert-subscriber audience (consistent with the whole T249/T230 alert
family). Dedup: `stockai:vol_anomaly:{uid}:{sym}:{date}` with a same-day TTL, PLUS an
escalation re-fire if RVOL later doubles again from the alerted level (a 3× alert shouldn't
suppress a later 6× climax). Daily cap per user (e.g. 10) to bound spam on broad-market
high-volume days when many symbols trigger simultaneously.

**Extend, don't duplicate:** `AlertCondition.VOLUME_SPIKE` (models.py:325) exists but is
daily-bar, per-subscribed-symbol, ~5-min cadence via check_technical_alerts — a different
product (subscribed-symbol technical alert) from this universe-wide anomaly scan. Keep both;
name the new job distinctly (`check_volume_anomalies`).

### 1b. Top-3 High-Confidence Movers Alert (T257-TOP3-CONVICTION-ALERT)

**The honest version of "very very high confidence" already exists as data:** signal-engine's
confidence calibration (`_build_confidence_calibration`, signal-engine routes.py:260) buckets
REAL measured win rates from signal_outcomes by (horizon, direction, market, confidence-band),
min n=30, cached in Redis (`signal:confidence_calibration`, 1h TTL) — this is the number shown
as "Historical win rate (n=85)" on stock pages. **The design gates on MEASURED bucket win rate,
not raw model confidence**: a pick qualifies only if its bucket's tracked win rate ≥ threshold
(propose 70% to start) AND n ≥ 30 AND `conviction_tier == "full"` (the 7-layer/4-layer
`_is_conviction_buy` gate, scheduler.py:585) AND K-Score ≥ 55 AND regime not bear/risk_off for
BUYs. Rank all qualifying candidates by bucket win rate (tiebreak: confidence), hard-cap 3.

**What's genuinely new vs. today's check_signal_alerts:** (a) cross-symbol ranking + cap — the
existing alert fires per-symbol independently with no selection step; (b) wiring
calibrated_win_rate into the FIRE decision — today it's display-only; (c) cadence honesty:
signals regenerate on the 5-minute refresh bursts, so a 1-minute loop would mostly re-scan
unchanged data — run the scan every minute (cheap Redis/DB reads) but fire only when the
qualifying set CHANGES (new symbol qualifies, or direction flips), dedup per
(user, symbol, direction, day), max one email per composition change.

**Expectation to set explicitly with the user (put it in the email footer too):** on most days
ZERO picks will clear a 70%-measured-win-rate bar — an empty day means the bar is working, not
that the feature is broken. The email includes each pick's measured win rate + sample size
("this setup class won 72% over the last 41 tracked outcomes") — never an unbacked confidence
claim. If the user later wants more alerts, the threshold is one Redis-tunable knob; lowering
it trades accuracy for frequency, and the email's own printed win-rate keeps that trade-off
visible.

### 3. Overnight Options/Futures Flow → Morning Day-Plan (T257-OVERNIGHT-FLOW-BRIEF)

**Current state (mapped, verified):** ZERO futures data exists anywhere (no ES=F/NQ=F/YM=F/
RTY=F references; market_overview._INDICES is spot-only ^GSPC/^IXIC/^DJI/^VIX/^HSI).
Options-flow exists per-symbol (`GET /stocks/{symbol}/options-flow` — call/put volume,
cp_ratio, whale premiums >$500K) but is live-only via yfinance option chains, rate-limit
fragile, with NO historical persistence — nothing can currently answer "what did flow look
like yesterday/overnight." Premarket bars ARE ingested and labeled (T230-CHARTING-PREMARKET's
`_classify_session`) but only for charting. The 8:00 local `send_premarket_brief` (T249-P3) is
the natural delivery vehicle — an overnight-flow section slots in as section 4.

**Phase 1 (cheap, buildable immediately): overnight futures + premarket read.** New ~7:15 ET
job fetching ES=F, NQ=F, YM=F, RTY=F + VIX via one bulk yfinance call → overnight change vs.
prior settle; top premarket gappers in the universe from already-ingested PRE-session bars;
both added to the pre-market brief. Framing: futures ARE the market's own live expectation of
the open — "ES +0.8% overnight" is a measurement, and reporting it as "futures point to a
higher open" is honest because that's literally what futures prices mean; predicting whether
that holds through the open is not claimed. Optionally later: compute and print the tracked
historical stat "on days futures were up >0.5% overnight, SPY's open was green X% of the time
(n=...)" from our own stored data — only once actually measured, never asserted from intuition.

**Phase 2: options-flow snapshots (the "where investors put money" half).** New
`options_flow_snapshots` table + an end-of-day job persisting per-symbol cp_ratio, call/put
premium, whale_count for a bounded set (PriceAlert-subscribed + top-K by K-Score, NOT the whole
universe — yfinance option chains are the most rate-limited endpoint we touch), spread over
minutes with backoff. The morning brief can then report "yesterday's late-day flow was
call-heavy on X/Y/Z (cp_ratio 3.2, $1.4M whale premium)" — real observed positioning, which is
what "see where the investors putting money" actually asks for. True OVERNIGHT options flow
(index options trading in Globex hours) is not available from yfinance at all — that would
need a paid data source (documented as a known limitation, not silently faked with stale data).

**Phase 3: the "day layout" — an attention list, not a plan-of-trades.** Brief section listing
symbols scoring on ≥2 of: premarket gap beyond threshold, unusual prior-day options flow
(Phase 2), earnings today (P1 data), macro release today (P0 data) — "pay attention to these
today, here's why," each reason a measured fact. Explicitly NOT auto-generated buy/sell
instructions — that's what the signal pipeline + T257-TOP3 alert are for, with their tracked
accuracy; duplicating direction calls here with no outcome tracking would be the dishonest
version.

### 4. Systematic Prod E*Trade Auth (T257-ETRADE-PROD-SYSTEMATIC)

**Direct answer to "using client secret but why still login":** E*Trade uses OAuth 1.0a. The
consumer key + client secret only identify THE APP — they cannot produce an access token by
themselves (there is no client-credentials or refresh-token grant in OAuth 1.0a, by design).
The browser login + PIN (verifier) step is E*Trade's mandated way for the ACCOUNT HOLDER to
authorize the app. This step cannot be legitimately automated (scripting their login page
violates their API agreement), and E*Trade access tokens **hard-expire at midnight US Eastern
every day** — plus go inactive after ~2h of no API activity intraday (reactivatable via
renew). So some periodic re-auth is an E*Trade platform constraint, not an app bug.

**What IS ours to fix (mapped against the real code):**
1. **`renew_access_token()` exists (etrade_broker.py:115) but is NEVER scheduled** — only a
   manual "Reconnect" button calls it. Fix: an intraday keepalive cron (e.g. every 90 min,
   market hours) renewing all authorized E*Trade connections so tokens never go 2h-idle-dead
   mid-session. This is the single highest-value change.
2. **Silent intraday failure:** on a dead token, paper trading's broker calls silently no-op
   (`_get_portfolio_broker` returns None / exceptions logged as warnings) until the ONCE-DAILY
   08:30 ET `_check_broker_auth` health check notices. Fix: in-loop 401/token_rejected
   detection in `_place_broker_entry`/`_place_broker_exit`/`poll_broker_order_fills` →
   immediately mark `is_authorized=False` + fire the (already-existing)
   `send_broker_reauth_email` with a fresh authorize URL, instead of waiting for tomorrow's
   cron.
3. **One-tap morning re-auth UX:** the daily re-auth email already exists and already embeds a
   fresh authorize URL; streamline the landing so Settings auto-focuses the PIN input (and
   auto-completes on paste) — the human step shrinks to: click email link, log in, copy PIN,
   paste. That's the floor OAuth 1.0a allows.
4. **Prod switch itself is config-only:** broker_type `etrade` (vs `etrade_sandbox`) with prod
   consumer key/secret entered in Settings — OAuth endpoints already always hit the prod base
   (etrade_broker.py:73,101,118); data/order calls swap base by flag. Prerequisite is E*Trade's
   own portal approval for a prod API key.
5. **If daily-login is unacceptable for full automation:** the structural answer is
   TIER84-BROKER-ALPACA (already in the tracker) — Alpaca auths with a plain API key/secret,
   no PIN, no daily expiry. E*Trade's daily midnight expiry is a hard platform limit for
   unattended trading; document the trade-off rather than fighting it.

**Also flagged during research:** T205-ETRADE-SANDBOX's tracker text is stale (describes
"OAuth 2.0" and claims no live calls exist — the full 1.0a flow shipped in Tier 18); fold its
correction into the T257 work when built.

### T257-ETRADE-PROD-SYSTEMATIC — Built 2026-07-17

Items (1)-(3) above shipped same-day; item (4) (Alpaca) remains documented only.

**Shared helpers** (`scheduler.py`) factored out of `_check_broker_auth`'s previously-inline
logic so the new keepalive cron and in-loop detection don't duplicate (and risk drifting
from) the same checks: `_is_token_rejected_error(err)` — pure string-matching on
`token_rejected`/`401`/`unauthorized`, case-insensitive; `_mark_broker_unauthorized_and_notify
(session, conn)` — flips `is_authorized=False`, mints a fresh `start_oauth()` URL, emails via
the existing `send_broker_reauth_email`.

**New keepalive cron** `_renew_broker_tokens()`, registered at 5 fixed ET clock times spanning
the trading session — `(9,45), (11,15), (12,45), (14,15), (15,45)` — **not** a raw cron
`minute="*/90"` interval. Caught this exact mistake while implementing: APScheduler's
`CronTrigger` minute field only spans 0-59, so `*/90` would silently register a job that never
fires — a genuinely dangerous silent failure for something meant to prevent silent failures.
Calls the existing (previously never-scheduled) `renew_access_token()` on every
active+authorized `etrade`/`etrade_sandbox` connection; skips other broker types (Alpaca, when
it exists, doesn't have this OAuth 1.0a concept). On a genuine rejection (not just idle),
immediately hands off to `_mark_broker_unauthorized_and_notify` rather than waiting for the
08:30 ET check.

**In-loop detection** — new `_handle_broker_error_if_token_rejected(session, portfolio, exc)`
in `paper_trading_engine.py`, wired into all three previously-silent broker call sites
(`_place_broker_entry`, `_place_broker_exit`, `poll_broker_order_fills`). Each now distinguishes
a token rejection (immediate mark-unauthorized + reauth email) from a transient/unrelated
error (still just logged — a network timeout must NOT flip a healthy connection to
unauthorized, which would be its own new bug). **Lazily imports** `scheduler.py`'s two helpers
inside the function body, not at module top — `scheduler.py` already imports several names
from `paper_trading_engine.py` at its own module level (`get_last_regime`,
`paper_trading_step`, etc.), so a top-level import in the reverse direction would create a
circular import. A dedicated test asserts the import stays lazy; adversarially verified by
temporarily moving it to module-top and confirming the test caught it.

**Settings UX**: the PIN/verifier input auto-focuses via a callback ref (`ref={el =>
el?.focus()}`) the instant the authorize URL appears — correct here specifically because this
input only mounts once `oauthUrl[b.id]` is set, so ref-callback-on-mount fires exactly when the
field first exists, no separate ref map needed for the per-broker-row case. Enter now submits
too. Net flow: click the emailed/on-screen link → log in on E*Trade → copy PIN → switch back
(already focused) → paste → Enter. That's the floor OAuth 1.0a's mandated human-authorization
step allows — there is no way to remove it entirely without abandoning E*Trade for a
key-only broker like Alpaca (see design section above, item 5).

**Tests**: `services/market-data/tests/test_etrade_token_renewal.py`, 12 cases.
`_is_token_rejected_error` is pure/dependency-free (no DB/HTTP), loaded directly via the
exec()-from-source technique (matching `test_earnings_alert_bodies.py`) and tested with real
inputs — including the important negative case that a timeout or 500 must NOT match, which
would silently misfire the whole feature (flipping healthy connections unauthorized on any
transient error). The scheduling/wiring is source-text-checked (`scheduler.py` can't be
imported in this test environment — its import chain pulls in `apscheduler` — matching
`test_scheduler_static_names.py`'s established pattern for exactly this constraint).

---

## Feature Reference: T257-VOLUME-ANOMALY-ALERT — Abnormal Volume Detection (Built 2026-07-17)

**User ask:** "I want a volume alert, check every min on the volume, if you see some abnormal
vol or huge vol going up or down, send me the stock details and will it breakout or breakdown."

**Design constraint carried over from this repo's established rate-limit discipline:** a
1-minute universe-wide scan must NEVER call yfinance or hit per-symbol DB rows in the main
loop — this repo has hit yfinance rate-limiting before from exactly this class of tight loop.
`check_volume_anomalies()` (`services/market-data/src/services/scheduler.py`) reads only the
pre-existing Redis caches `stockai:live_prices` and `stockai:avg_volume`, both already
maintained by other jobs for other purposes. Only for the small subset of symbols that
actually trigger does it make a per-symbol HTTP call — to technical-analysis's
`GET /ta/{symbol}/levels` — to find the nearest support/resistance level in the move's
direction, for the "will it breakout or breakdown" part of the ask.

**Threshold — session-elapsed-scaled, not a flat multiple**, reusing the same principle
already documented for T241-AUDIT-RVOL-INTRADAY-BIAS elsewhere in this file: comparing a
partial trading day's cumulative volume against a full day's average volume would produce
false triggers in the first hour of trading even on a perfectly normal day. Computes separate
US/HK session-elapsed fractions via `ZoneInfo`, then `threshold = max(1.5, 2.5 * elapsed_fraction)`
— early in the session the bar is lower (in raw multiple terms) but the absolute volume
required to clear it is still proportionally reasonable for how much of the day has passed.

**Gating and delivery**: Redis lock (`_VOL_ANOMALY_LOCK_KEY`, 55s TTL) prevents overlapping runs
if one cycle runs long. Triggered symbols sort by RVOL descending. Delivery is scoped to the
`PriceAlert`-subscriber audience (same narrower v1 scope already established for P1/P2 of
Tier 249's Market-Mover Monitoring — not the full watchlist/portfolio membership). Per-recipient
dedup + a daily cap prevent spam: `stockai:vol_anomaly_cap:{uid}:{today}` caps total emails per
user per day; `stockai:vol_anomaly:{uid}:{symbol}:{today}:{int(rvol//1)}` dedups the same
symbol at materially the same RVOL magnitude within the same day (a stock climbing from RVOL 3
to RVOL 8 over the day fires again — a stock oscillating between RVOL 3.1 and 3.3 does not).

**Honesty note on "will it breakout or breakdown"**: the email includes the nearest S/R level
and which side of it price sits on, framed as context, not a prediction — matching this
repo's standing disclaimer convention (see the Top-3 Conviction Alert below for the same
principle applied more strongly). No model claims to know the outcome; it surfaces the
structural level a trader would want to know about before deciding for themselves.

**Files**: `services/market-data/src/services/scheduler.py` (`check_volume_anomalies()`,
registered `id="volume_anomaly_check"`, `"interval"`, `minutes=1`, right after
`price_alert_check`), `services/market-data/src/services/email_service.py`
(`send_volume_anomaly_email`).

**Tests**: `services/market-data/tests/test_volume_anomaly_alert.py`, 11 cases.
`send_volume_anomaly_email` is tested directly; the scan logic (Redis-only reads, no
yfinance/DB calls in the loop, threshold math, dedup/cap keys) is source-text-checked, matching
the established pattern for functions with heavy Docker-only dependencies. One false-positive
caught while writing these: an early assertion checked `"yfinance" not in body`, which failed
because the function's own docstring legitimately explains why yfinance is avoided — fixed to
check `"import yfinance" not in body` (actual usage, not word presence).

**What to check if this looks wrong**: `docker logs stockai-market-data-1 --since 1h | grep
'volume_anomaly'` for scan activity; confirm the Redis caches it reads are actually fresh
(`docker exec stockai-redis-1 redis-cli get stockai:live_prices` — if stale, the alert is
comparing against old prices, not a bug in this feature itself but in whatever job populates
that cache).

---

## Feature Reference: T257-TOP3-CONVICTION-ALERT — High-Conviction Pick Alert (Built 2026-07-17)

**User ask:** "I want to get email when you think 3 top stocks will be going up or down with
very very high confidence, I will buy or sell the stock as you recommended, I need it to be
very accurate and confident." Because the user explicitly said they'd act on these picks
directly, the gating design deliberately optimizes for honesty over pick frequency — most
1-minute cycles are expected to qualify zero picks, by design, not as a bug.

**Why measured win rate, not raw model confidence**: raw signal confidence
(`abs(fused_probability - 0.5) * 200`, see the "Why a BUY Signal Can Show Low Confidence"
design reference elsewhere in this file) measures distance from a coin-flip, not real-world
accuracy. Given the user's explicit intent to act on these directly, `check_top3_conviction()`
instead gates on signal-engine's existing confidence-calibration cache — real historical
bucket win rates keyed `"{horizon}|{direction}|{market}|{band}"`, built from actual
`signal_outcomes` rows, requiring a minimum sample count before a bucket counts at all
(`_TOP3_MIN_COUNT = 30`). **If the calibration cache is empty for any reason, the function
returns zero picks rather than silently falling back to raw confidence** — this fallback-to-
zero is deliberate and adversarially verified (temporarily replaced the guard with `pass` and
confirmed the dedicated test caught it before reverting). A default minimum win rate of 0.70
(`_TOP3_MIN_WIN_RATE`, Redis-tunable via `stockai:top3_min_win_rate` without a redeploy) is the
"very very high confidence" bar; BUY additionally requires regime not bear/risk-off and
K-Score ≥ 55 (`_TOP3_MIN_KSCORE`).

**Deliberately NOT the full 7-layer Conviction Gate**: `_is_conviction_buy()` (K-Score/Uptrend/
RSI/MACD/OBV/ADX/ML) would require per-symbol signal-detail fetches for the whole universe
every minute — reintroducing exactly the rate-limit cost problem this feature has to avoid.
Instead built a lighter gate directly from data already fetchable in bulk: `GET /signals?
style=X` for all 4 horizons, `GET /signals/confidence-calibration`, `GET /rankings` for
K-Scores — 3 bulk calls total per cycle, not N per-symbol calls.

**Regime lookup is a direct function call, not HTTP** — an earlier draft had this reaching back
into market-data's own `/stocks/regime` endpoint via a hacky URL string substitution
(`_settings.signal_engine_url.replace('signal-engine', 'market-data')`); caught and fixed to
call `get_last_regime()` / a locally-imported `get_last_hk_regime()` directly, since
`scheduler.py` already runs inside market-data itself — no HTTP round-trip needed for a
same-process call.

**Delivery**: sorts qualifying candidates by `(win_rate, confidence)` descending, caps to the
top 3. Tracks the last-sent composition (`stockai:top3_last_composition`) so an unchanged set
of 3 picks doesn't re-email every single minute — only fires again when the actual composition
changes.

**Files**: `services/market-data/src/services/scheduler.py` (`check_top3_conviction()`,
registered `id="top3_conviction_check"`, `"interval"`, `minutes=1`),
`services/market-data/src/services/email_service.py` (`send_top3_conviction_email`, subject
line explicitly says "measured win rate ≥70%" rather than implying a company-endorsed
prediction, and the body disclaimer explicitly states "not a prediction... Most cycles qualify
zero picks" so a user seeing an empty inbox for days understands that's expected, not broken).

**Tests**: `services/market-data/tests/test_top3_conviction_alert.py`, 15 cases, including
dedicated checks for the no-fallback-to-raw-confidence guard, the regime-lookup-is-a-direct-
call-not-HTTP property, and the ranked-by-win-rate-not-confidence ordering. One false positive
fixed during writing: a 300-character slice window used to isolate the calibration-empty-guard
source text cut off before the word "return" appeared — widened to 400 characters.

**What to check if this looks wrong**: `docker logs stockai-market-data-1 --since 1h | grep
'top3_conviction'`; if zero emails have fired in a long time, check
`docker exec stockai-redis-1 redis-cli get stockai:top3_min_win_rate` (confirm no stale
override) and whether `GET /signals/confidence-calibration` is actually returning populated
buckets — an empty calibration cache means this feature will correctly, silently produce zero
picks forever until enough `signal_outcomes` accumulate.

---

## Feature Reference: T257-BROKER-ORDER-HISTORY — E*Trade Sandbox/Prod Order History (Built 2026-07-17)

**User ask, surfaced mid-session while checking the E*Trade sandbox connection**: "how can I
see all the history from sandbox?" — clarified to mean E*Trade's own order/trade history (not
this app's separate paper-trading history, which already has its own dedicated UI elsewhere).

**What existed already**: `BrokerInterface.list_orders()` was already defined as an optional
method defaulting to `NotImplementedError` (the same pattern used for other broker-specific-
only capabilities), but no concrete broker implemented it, and there was no API route or UI
surface for it at all.

**Implementation**: `EtradeBroker.list_orders(account_id=None, status="open")` calls E*Trade's
real `GET /v1/accounts/{key}/orders.json` — the same endpoint `get_order()` already used with
an `orderId` filter, just called without one to get the full list. An explicit status-vocabulary
map translates this app's internal terms to E*Trade's own literal params (`open`→`OPEN`,
`filled`→`EXECUTED`, `cancelled`→`CANCELLED`, `rejected`→`REJECTED`); `status="all"` omits the
param entirely rather than passing something E*Trade wouldn't recognize (which would silently
return zero rows, not an error). Parses `OrdersResponse.Order[]` into `BrokerOrder` instances;
E*Trade's epoch-millisecond `placedTime` is converted to ISO8601 inside a try/except so a
missing or malformed timestamp degrades to `None` rather than crashing the whole call. Added a
new optional `placed_at` field to the shared `BrokerOrder` dataclass (backward-compatible,
defaults to `None` for every other broker).

**API**: new `GET /broker/connections/{id}/orders` in `services/market-data/src/api/broker.py`
— verifies the connection is authorized, calls `list_orders()`, and specifically distinguishes
`NotImplementedError` (→ HTTP 501, "this broker doesn't support this") from any other failure
(→ HTTP 502, a real error) rather than collapsing both into one generic error response.

**UI**: `frontend/src/pages/settings.tsx` gained an "Order History" button per broker
connection, next to the existing "Load Balance" button. Three distinct states are rendered,
not collapsed into one blank screen: a specific "not supported by this broker" message on a
501, an empty-state message when the account genuinely has zero orders, and a full table
(Symbol/Side/Qty/Status/Filled Price/Placed) otherwise. `frontend/src/lib/api.ts` gained
`brokerOrderHistory()` and the `BrokerOrderHistoryItem` type.

**Tests**: `services/market-data/tests/test_broker_order_history.py`, 9 cases, run directly
against the real `EtradeBroker` class with `requests.get` mocked — `EtradeBroker` only depends
on `requests`/`requests_oauthlib`, both real installed packages (not part of this repo's
`conftest.py` stub list), so no source-text-extraction workaround was needed here. Covers
multi-order parsing, status-vocabulary translation, epoch-ms-to-ISO8601 conversion, graceful
`None` on a missing `placedTime`, `status="open"` correctly mapping to `"OPEN"`, `status="all"`
omitting the param, an HTTP failure raising `RuntimeError`, an empty response returning `[]`
(not `None`), and `ManualBroker` correctly inheriting the base interface's `NotImplementedError`
rather than silently returning empty (which would look identical to "authorized but genuinely
zero orders" to a caller). Adversarially verified the status-mapping test by temporarily
passing the internal vocabulary straight through unmapped and confirming the dedicated test
failed (`'open' == 'OPEN'`) before reverting. `requests_oauthlib` needed a local `pip install`
to run these tests in this dev environment (already a real pinned dependency in
`requirements.txt`, just missing locally — not a stubbed dependency).

**What to check if this looks wrong**: a 501 response means the connected broker type doesn't
implement `list_orders()` (currently only E*Trade does — `ManualBroker`/Fidelity-manual does
not, by design, since it has no real API at all); a 502 means the E*Trade call itself failed —
check `docker logs stockai-market-data-1 --since 10m | grep 'orders'` for the underlying error.

---

## Feature Reference: T255-STRATEGY-TUNER-PER-HORIZON — Joint Buy-Threshold x ML-Weight-Cap Tuner (Phase 1, Built 2026-07-18)

**Gap this closes**: every self-tuning mechanism in signal-engine (`calibrate_ta_weights`,
`calibrate_ml_weight`, `outcomes_calibrate_apply`, `tune_style_profiles`) tunes exactly ONE
parameter at a time, against its own independent train/validation split. None had ever
searched for the best COMBINATION of `buy_threshold` + `ml_weight_cap` together — a real gap,
since a candidate that looks best for `buy_threshold` alone need not be the best pairing once
`ml_weight_cap` also shifts (a lower cap changes which outcomes even clear a given threshold,
because it changes the effective `fused_prob` population being swept).

**New endpoint**: `POST /signals/tune_strategy` in
`services/signal-engine/src/api/routes.py`, placed right after `tune_style_profiles`. For each
of SHORT/SWING/LONG/GROWTH: joins already-stored `SignalOutcome.fused_prob` to
`Signal.reasons["ml_weight"]` (same join pattern `tune_style_profiles` already uses), then
grid-searches 31 `buy_threshold` levels (0.55-0.85) x 13 `ml_weight_cap` levels (0.15-0.75) —
403 cells — on the chronological OLDER 70% of the joined rows (train), and only applies the
winning cell if it ALSO beats the CURRENT LIVE baseline's own EV on the NEWER 30% (validation)
that the search never saw. This is a **re-filtering exercise, not a re-simulation** — a grid
cell's `fused_prob` still reflects whatever `ml_weight` was ACTUALLY used when the signal was
originally generated, not a replay of what it would have been under a different cap. This
means the sweep can only ever evaluate TIGHTENING an existing threshold/cap combination, never
a looser one — the same explicit limitation the design doc's own deferred Phase 2b
(equity-curve replay) exists to eventually address.

**Reuses every existing convention exactly**, so this new mechanism can't silently violate a
safety property its siblings already enforce:
- Chronological 70/30 split (never random — avoids look-ahead leakage).
- `min_samples=15` per grid cell per slice (looser than `outcomes_calibrate_apply`'s 50,
  deliberately — a 403-cell 2D grid already spreads a smaller outcome pool thin; the
  validation-beats-baseline gate below, not this floor, is what actually protects against a
  noisy cell being promoted).
- Unconditional rejection of negative EV lift, regardless of how large the grid shift looks.
- `EV = mean(pct_return)` (never `avg_return × win_rate` — the T232-OC4 double-counting fix
  documented elsewhere in this file).
- One `TuneHistory` row per horizon per run via `_record_tune_history()`, regardless of
  promoted-or-skipped outcome (`parameter_class="joint_strategy"`,
  `parameter_name="buy_threshold+ml_weight_cap"` — a new value in that column, but it's a plain
  `String(32)`, not an enum, so no schema/migration was needed).
- Sane-bounds clamp on both dimensions before ever writing to Redis.

**Applies through the EXISTING Redis keys** — `stockai:signal_thresholds:{H}` (same key
`outcomes_calibrate_apply` already writes, read via `_get_dynamic_buy_threshold()` as a
bull-baseline-relative delta applied per-regime) and `stockai:style_tune:{H}:ml_weight_cap`
(same key `tune_style_profiles` already writes, read via `_get_style_tuned_param()` as a flat
value). **Zero changes needed anywhere on the read side** — `_decide_style()`, the signal
generator, and the existing `GET /signals/tune_status` status-reporting endpoint all already
handle these keys. Checked whether a new companion status endpoint was warranted (per the
original design doc's Phase 1 sketch, `GET /signals/strategy_status`) and did NOT build one —
`tune_status` already reports `effective`/`redis_overrides` for both `buy_threshold` and
`ml_weight_cap` per horizon, so a dedicated new endpoint would have been pure duplication.

**Tests**: `services/signal-engine/tests/test_tune_strategy.py`, 9 cases, using the
exec()-from-source extraction technique already established for functions in `routes.py` this
environment can't import directly (`conftest.py` stubs `common`/`db` wholesale) — run against a
REAL in-memory SQLite session and the REAL `shared/db/models.py`, with only `_get_redis`/
`_record_tune_history` stubbed, so these tests exercise the actual grid-search/gating logic,
not a hand-copied reimplementation that could silently drift from it.

Adversarially verified twice during implementation: (1) disabled the negative-EV-lift
rejection gate (`if ev_lift < 0:` → `if False:`) and confirmed the validation-slice-loser test
caught a wrongly-promoted candidate (`ev_lift_pct: -7.0` still applied) before reverting;
(2) disabled the min-sample-floor gate and confirmed 4 tests failed with a real `IndexError`
(an empty train/validation split crashing on `train_wr[0][0].signal_date`) before reverting.

**A real test-design trap hit while building the "genuinely better combination" fixture**: an
initial dataset alternated `fused_prob`/`ml_weight` so cleanly that BOTH the candidate's
tighter cap and the baseline's wider cap selected the IDENTICAL subset via their respective
`cap + 0.05` tolerance windows — `ev_lift_pct` came out to exactly `0.0` every time, not
because the code was wrong but because the fixture never actually exercised a cap-driven
distinction (only a threshold-driven one, which both cells shared identically). Fixed by
deliberately placing the losing rows' `ml_weight` WITHIN the baseline's tolerance window but
OUTSIDE the candidate's — only then did the sweep have a real cap-driven signal to find.
**Lesson for any future 2D-grid test fixture in this codebase**: check that each axis of the
grid actually produces a DIFFERENT selected subset between the candidate and the baseline —
two axes that happen to collapse onto the same filtered rows will always show zero lift
regardless of whether the underlying logic is correct.

**A real bug caught by triggering this live against real production data (not just tests) on
first deploy**: the initial version only had the hard `ev_lift < 0` rejection — no soft
min-lift floor like `outcomes_calibrate_apply`'s own `min_ev_lift` + shift-size convention.
Running it live against 2,782 real outcomes immediately surfaced SHORT applying a real
`(0.63->0.55, 0.30->0.25)` shift with `ev_lift_pct` EXACTLY `0.0` — a tie, not an improvement.
Fixed by adding an unconditional `ev_lift <= 0` rejection independent of shift size
(deliberately STRICTER than the sibling mechanism's own shift-size escape hatch — a large
parameter shift with a genuinely-measured zero lift against 2,782 real samples means the
tested parameters don't matter for this outcome distribution, not that measurement noise is
masking a real edge worth keeping anyway), plus the sibling's own soft `min_ev_lift`
+ trivial-shift floor for small-but-positive lifts. The bad pre-fix live write was manually
cleared (`redis-cli del stockai:signal_thresholds:SHORT stockai:style_tune:SHORT:ml_weight_cap`)
before the fixed code was deployed; SWING's write from that same initial run (a genuine
`ev_lift_pct=1.57` improvement) was left in place, confirmed against the corrected gate logic.
**Lesson reinforced**: live-verifying a new self-tuning mechanism against real production data
immediately, rather than trusting a synthetic test suite alone, caught a real gate gap within
minutes of first deploy — the same "verify against live state" discipline documented elsewhere
in this file, applied to a brand-new mechanism's very first run instead of an existing one.

**What to check if this looks wrong**:
```bash
# Confirm the endpoint exists and run it manually (needs a valid JWT — see any other
# _service_token()-style example elsewhere in this file for the pattern):
docker exec stockai-signal-engine-1 curl -s -X POST 'http://localhost:8005/signals/tune_strategy?days=180' \
  -H "Authorization: Bearer <token>" | head -c 500

# Confirm a promoted change is visible via the EXISTING status endpoint (no new endpoint to check):
docker exec stockai-signal-engine-1 curl -s 'http://localhost:8005/signals/tune_status' \
  -H "Authorization: Bearer <token>"

# Check TuneHistory rows this mechanism wrote:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT style, old_value, new_value, promoted, gate_failures FROM tune_history WHERE parameter_class='joint_strategy' ORDER BY ts DESC LIMIT 10;"
```

**Not yet built (Phases 2-4, documented not silently dropped)**: Phase 2 — sweep `hold_days`
per horizon using the already-populated `return_5d/10d/20d` columns (same no-regeneration
speed advantage). Phase 3 — schedule this weekly once a few manual cycles look sane, folding
in `calibrate_ml_weight` (currently manual-only). Phase 4 — the explicit limitation that any
stored-outcome sweep (this one included) can only evaluate TIGHTENING an existing parameter;
testing a genuinely LOOSER threshold or a different compression map needs the design doc's own
deferred Phase 2b equity-curve replay, a separate and larger project.

---

## Feature Reference: T230-PORTFOLIO-BROKER-SYNC — Automatic Broker Position Sync (Built 2026-07-18)

**Gap this closes**: `GET /connections/{id}/account` (`src/api/broker.py`) already round-trips a
real broker's live positions end-to-end — the whole OAuth + fetch + parse chain already worked.
Nothing ever PERSISTED that fetch into `UserPosition` (`positions.tsx`'s actual data source),
so every broker-linked user still had to hand-copy their real E*Trade holdings into the manual
positions tracker. This was originally tracked as a critical/XL item ("complete a broker
integration sprint") — re-scoping against the actual code before building found the hard parts
already done, shrinking it to "call the already-working fetch and persist the result."

**New function**: `sync_broker_positions()` in
`services/market-data/src/services/paper_trading_engine.py`, piggybacking on the SAME
already-scheduled/locked cycle `poll_broker_order_fills()` runs on inside
`_run_paper_trading_step()` (`scheduler.py`) — no new cron job, no new Redis lock.

**Provenance marker, not a separate table**: `UserPosition` gained two nullable columns —
`broker_connection_id` (FK to `broker_connections`, `ON DELETE SET NULL`) and
`broker_synced_at`. `NULL` = manually entered (every existing row, unchanged behavior).
Non-`NULL` = owned by that sync; the row will be silently overwritten on the next cycle if
hand-edited, which is exactly why the manual CRUD routes now reject edits to it (see below).

**Conflict semantics — the one real risk this design has to get right**: a symbol the sync
wants to write is only ever created fresh (no existing row) or updated in place (existing row
already owned by THIS connection). A manual entry (`broker_connection_id IS NULL`) or a row
owned by a DIFFERENT connection for the same symbol is left **completely untouched** and
logged as a conflict — never silently overwritten with the broker's numbers, since the user's
manually-tracked cost basis/share count could genuinely differ (e.g. a partial manual entry
made before ever linking the account). A synced row whose symbol the broker no longer reports
(sold externally, e.g. directly on E*Trade's own site) is removed — but ONLY rows this sync
itself owns; a manual row is never auto-removed just because the broker reports nothing for it.

**API + UI**: `positions.py`'s `buy`/`sell`/`remove` endpoints now return `409` on a
broker-synced row ("this position is synced from a linked broker account... manage it through
your broker instead") rather than silently accepting an edit the next sync cycle would just
revert. `positions.tsx` shows a "SYNCED" badge next to the symbol and hides the BUY/SELL/remove
controls for those rows (the ★ watch and trade-history-expand controls stay — those aren't
broker-owned state).

**Tests**: `services/market-data/tests/test_broker_position_sync.py`, 10 cases, against a real
in-memory SQLite session + the real `shared/db/models.py` — `paper_trading_engine.py` can't be
imported directly in this test environment (`conftest.py` stubs `sqlalchemy` itself as a
`MagicMock`), so the test pops the stub, builds ONE shared engine, then restores the stub
immediately. **A real test-isolation bug was caught and fixed while writing these**: an
earlier version of this technique left the real `sqlalchemy` swapped in globally for the rest
of the pytest session, silently breaking 7 OTHER test files' collection (they passed in
isolation, failed only in the full suite) — fixed by building the engine BEFORE restoring the
stub (`sqlalchemy`'s `create_engine()` does a dynamic dialect-plugin lookup at CALL time, not
just import time, so it can't be deferred past the restore point) and sharing that one engine
across all 10 tests with a per-test row cleanup instead of a fresh engine each time.

Adversarially verified by disabling BOTH conflict guards (manual-row, different-connection)
simultaneously and confirming 3 tests correctly failed (a real data-loss scenario) before
reverting — disabling just ONE guard alone was insufficient to trigger any test failure, since
the two guards turned out to be redundant-safe (a bug in one doesn't cause data loss because
the other still catches it via its own `!= conn.id` branch). That's a genuinely good defensive
property, caught only by investigating why the single-guard sabotage didn't produce the
expected failure rather than assuming the test was simply wrong.

**What to check if this looks wrong**:
```bash
docker logs stockai-market-data-1 --since 1h | grep 'broker.position_sync'
# broker.position_sync_done {synced, conflicts} on a normal cycle with active connections;
# broker.position_sync_conflict_skipped per-symbol if a manual/other-connection row blocked a write;
# broker.position_sync_error only on a genuine unexpected failure (fetch failures for one
# connection are caught per-connection and don't abort the whole sync — check
# broker.position_sync_fetch_failed for those instead).

# Check a specific user's positions and their provenance directly:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, shares, avg_cost, broker_connection_id, broker_synced_at FROM user_positions WHERE user_id = <id>;"
```

---

## Feature Reference: T233-ARCH-PORTFOLIO-CONSOLIDATE — portfolio.py Moved to portfolio-optimizer (Built 2026-07-18)

**What moved**: market-data's `api/portfolio.py` (correlation matrix, beta, parametric VaR,
sector concentration — `GET /portfolio-risk/risk`) relocated verbatim to
`services/portfolio-optimizer/src/api/risk.py`. Same route path, same response shape — the
frontend (`board.tsx`, `portfolio.tsx`, both via `api.portfolioRisk()`) needed **zero changes**.

**This does NOT consolidate the two correlation implementations into one** — portfolio.py's
simple `df.corr()` and portfolio-optimizer's own `methods.py` (Ledoit-Wolf shrinkage
covariance, used by `/portfolio/optimize`) remain two separate implementations, just now both
living in the same service. Replacing one with the other is a separate, riskier numerical-
methods change deliberately not bundled into this architectural relocation.

**The real complication this move had to solve**: portfolio-optimizer has **no direct DB
access at all** (confirmed via grep — zero `from db import` anywhere in it; it's a pure
HTTP-consumer service). market-data's original `portfolio.py` queried `Price`/`Stock` directly
via SQLAlchemy. The moved version's `_fetch_returns()`/`_fetch_stock_meta()` instead call
market-data's own `GET /stocks/{symbol}/prices` and `GET /stocks/{symbol}` over HTTP — the
SAME two endpoints this service's pre-existing `_fetch_closes()` (in `routes.py`, backing
`/portfolio/optimize`) already relies on, so this isn't a new integration pattern for this
service, just reusing an existing one for a second endpoint.

**New runtime dependency**: `yfinance>=0.2.54` added to portfolio-optimizer's
`requirements.txt` (needed for the SPY/HSI benchmark-beta fetch, wasn't there before) — this
means the deploy needs a real image rebuild (`docker compose build portfolio-optimizer`), not
just a `docker cp` hotfix, per this repo's own "new dependency needs a real rebuild" rule
(same class of gap as the api-gateway numpy incident documented elsewhere in this file).

**Routing**: api-gateway's `proxy.py` route table's `"portfolio-risk"` entry repointed from
`market_data_url` to `portfolio_optimizer_url` — one line, since the path itself didn't change.
market-data's `main.py` had `portfolio_router` removed; the old file was deleted outright
(`git rm`), not deprecated in place.

**Tests**: `services/portfolio-optimizer/tests/test_portfolio_risk.py`, 8 cases, direct function
calls with `monkeypatch` on the module's own `_fetch_returns`/`_fetch_stock_meta`/`yf` —
matching this service's existing `test_optimize_endpoint.py` convention exactly (`fastapi`/
`httpx`/`pandas`/`numpy` are all real, installed packages in this test environment per
`conftest.py`'s own docstring, so no stub workaround was needed). Covers the 2/10-symbol
bounds, mismatched-weights rejection, insufficient-history `422`, full correlation/beta/
sector-weight computation, the HK-vs-US benchmark selection rule, high-correlation/
concentration warning triggers, and a graceful `beta=1.0` fallback when the yfinance benchmark
fetch itself fails. Adversarially verified the high-correlation warning check by disabling it
and confirming the dedicated test caught it before reverting.

**What to check if this looks wrong**:
```bash
# Confirm the route resolves to portfolio-optimizer, not a stale market-data instance:
docker exec stockai-api-gateway-1 python3 -c "
from src.api.proxy import _ROUTES
print(_ROUTES['portfolio-risk'])"

# Live check against a real deployed container:
docker exec stockai-portfolio-optimizer-1 curl -s \
  'http://localhost:8007/portfolio-risk/risk?symbols=AAPL,MSFT' -H "Authorization: Bearer <token>"
```

---

## Recurring Issue: A Redundant Local `from datetime import datetime` Made Two Hard Rejects Dead Code (BUG232-DEADCODE)

**Symptom (found 2026-07-18, while writing regression tests for T232-DL-DUALSCORER-DEBT's
already-ported DE-only hard rejects):** `_should_enter()`'s AUD232-005 time-of-day gate
(blocks the first 30 min / last 15 min of the trading session) and its extended-move 6% hard
block never actually fired in production, despite the code looking correctly ported and
passing code review. No visible symptom otherwise — the fallback gate silently ran with two
fewer protections than intended, only during a decision-engine outage (its normal reachable
state never exercises this fallback path at all).

**Root cause:** the macro-blackout hard-reject block (a few lines earlier in the same
function) has `if _macro_evt is None: try: ... from datetime import datetime, timezone,
timedelta ...` — a REDUNDANT local import, since `datetime`/`timezone` are already imported at
module level (line ~34). Per normal Python scoping rules, the mere PRESENCE of a local `import`
statement anywhere in a function body makes that name local for the ENTIRE function, even on
code paths that never execute the import. Since `reasons.get("macro_blackout")` is normally an
explicit `True`/`False` (never bare `None`) thanks to signal-engine's T220-D fast path, the
`if _macro_evt is None:` block — and its local import — is SKIPPED on essentially every real
call. The LATER time-of-day-gate code's `datetime.now(timezone.utc)` call then raises
`UnboundLocalError: cannot access local variable 'datetime' where it is not associated with a
value` — silently swallowed by that block's own `except Exception: pass` (a deliberate
fail-open pattern for tz-lookup failures, which this wasn't).

**Fix applied:** deleted the redundant local `from datetime import datetime, timezone,
timedelta` — the module-level import already covers every use in the function.

**How this was caught:** NOT by code review (the code had already passed review once) — by
writing a direct behavioral test for the time-of-day gate using a custom `datetime` subclass
overriding `.now()` to return a fixed instant, which immediately surfaced the `UnboundLocalError`
in the test output when the mocked call actually executed.

**Design invariant, generalized beyond this one function:** a local `import` statement inside
an `if`/`try` block that is normally SKIPPED will silently shadow the SAME name at module level
for the rest of that function, on every call — not just the branch containing the import. This
is a real, non-obvious Python gotcha (not specific to this codebase), and it is invisible to
static review because the local import LOOKS harmless in isolation ("just re-importing
something already available") — the bug only manifests as an `UnboundLocalError` on a
DIFFERENT code path, and only if that path is reached without the import's own block having
run first. **Grep for `from datetime import` (or any local re-import of an already-module-
level name) inside conditional blocks in any function with multiple hard-reject/early-return
branches** — this exact pattern could recur anywhere a name is imported locally "just in case"
inside one conditional branch of a large function.

**What to check if a similar silently-dead-code bug is suspected:**
```bash
# Grep for local re-imports of already-module-level names inside conditional blocks:
grep -n "^from datetime import\|^import datetime" services/market-data/src/services/paper_trading_engine.py
# Then check whether any local `from datetime import ...` (or similar) exists deeper in the
# same file, inside an if/try block — that's the shape of this bug class.

# Confirm the two hard rejects actually fire when they should (needs a real live-triggered
# UnboundLocalError to have been fixed — a stale deploy would silently still no-op):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from datetime import datetime, timezone
from src.services.paper_trading_engine import _should_enter
# a candidate whose game_plan/signal_data trips the time-of-day gate at whatever the
# real current time is would confirm this live; easier to just re-run the test suite:
"
docker exec stockai-market-data-1 python3 -m pytest tests/test_should_enter_de_parity.py -q
```

## Feature Reference: T232-DL-DUALSCORER-DEBT — 4 DE-Only Hard Rejects, Test Coverage Added (2026-07-18)

**What this session found**: the 4 "decision-engine-only hard rejects" this tracker item
listed as a safe next porting step (market-hours/holiday guard, time-of-day gate, extended-
move 6% block, regime-based R:R stiffening) were ALL already present in `_should_enter()` —
ported in an earlier session, tagged `AUD232-021`/`AUD232-005`/`AUD232-060` in code comments.
The tracker text describing them as still-portable was stale in the "code doesn't exist yet"
direction — the mirror image of the SE-F2/aud14 staleness pattern already documented elsewhere
in this file (where a tracker entry claimed something was fixed that wasn't). **Always verify
against the actual current code before assuming a tracker's "todo" status is accurate — in
either direction.**

**The real remaining gap was test coverage, not code** — `test_should_enter_de_parity.py` only
had tests for the 3 score-layer ports from the 2026-07-17 partial fix; none of the 4 hard
rejects had a single dedicated test. Writing those tests is what surfaced BUG232-DEADCODE
(above) — 2 of the 4 "already-ported" hard rejects were actually silently non-functional.

**Test additions** (`services/market-data/tests/test_should_enter_de_parity.py`, 27 tests
total now, 17 new): market-hours (mocks `_is_market_hours` directly via monkeypatch, since
real wall-clock time can't be safely controlled from a test); time-of-day gate (a custom
`datetime` subclass overriding `.now()` to return a fixed instant in the target market's
timezone — this is the exact mechanism that caught BUG232-DEADCODE); extended-move 6% block
(above/at/below the threshold, plus a configurable-threshold case); regime-based R:R
stiffening (choppy/risk_off raising the floor from 2.0 to 3.0, clearable with a wider
take_profit). All adversarially verified by sabotaging each condition (`if <cond>:` →
`if False:`) one at a time and confirming exactly the expected test subset fails, then
reverting.

**Two real test-writing gotchas hit along the way** (both fixed in the final test file, worth
knowing if extending these tests further): (1) changing `live_price` to exercise the
extended-move/time-of-day checks without also re-deriving `stop`/`take_profit` for that new
price causes the EARLIER R:R hard-reject to fire first and mask the check actually under test
— every new fixture explicitly recomputes stop/take_profit to keep R:R comfortably clear at
its own live_price. (2) floating-point imprecision: `(105.999.../100.0 - 1) * 100` can compute
to `6.000000000000005`, not exactly `6.0` — a test asserting "exactly at the threshold does
not reject" is inherently flaky on an exact boundary; use a comfortably-below value instead of
chasing an exact float boundary.

**What to check if this looks wrong**: run
`docker exec stockai-market-data-1 python3 -m pytest tests/test_should_enter_de_parity.py -v`
inside the container — all 27 should pass. If any of the 4 hard-reject tests fail after a
future edit to `_should_enter()`, that's a real regression in DE parity, not a flaky test (all
4 groups were adversarially confirmed to fail correctly when their underlying condition is
disabled).

---

## Feature Reference: T258-WHATCOULDGOWRONG-AGENT — Adversarial Pre-Trade Risk Check (Built 2026-07-18)

**What this is**: the one genuinely-new agent from the "Combined Agent Catalog" fit-gap
analysis (see T258-FITGAP-AGENT-CATALOG). Before this, nothing in the codebase argued AGAINST
a proposed entry — research reports have risk sections but are slow, on-demand, per-report;
decision-engine's hard rejects block on rules but never enumerate concrete failure modes for
a trade that clears every gate.

**Implementation**: `services/decision-engine/src/api/risk_agent.py`, deliberately mirroring
`llm_scorer.py`'s exact established pattern rather than inventing a new LLM-call convention —
same `stockai:admin:claude_api_key` Redis lookup, same `httpx.AsyncClient` call to
`api.anthropic.com/v1/messages`, same fail-open-returns-None contract, same 6h Redis cache
keyed by symbol+style+date. Opt-in via `risk_check_enabled` config (default `False`, same
convention as `llm_scoring_enabled`). Called from `_decide()` in `routes.py` right after the
existing LLM-scoring step, using ONLY context `_decide()` already has in scope (game_plan,
regime, research_rec/score, `reasons` dict fields) — zero new fetches.

**Deliberately does NOT emit a probability_of_failure number.** Per the source design doc's
own honest-answer section and this repo's established "don't let a rubric that sounds right
stay in production unvalidated" discipline: an LLM narrating "73% chance of failure" is not
evidence of a 73% edge — it's evidence the model followed formatting instructions. The value
is the forced, concrete risk *enumeration* a human reads before entering, not an unvalidated
confidence number attached to it.

**Also deliberately returns `None`, not `[]`, when zero risks pass validation** — a forced-
adversarial prompt asking the model to argue against a trade will essentially always find
something to say, so an empty list is never a real "clean bill of health" finding worth
reporting; distinguishing "didn't run" from "found nothing" would invite over-trusting a rare,
likely-spurious empty response.

**Response shape**: new `RiskFlag` pydantic model (`category: macro|sector|company|technical`,
`severity: low|medium|high`, `note: str`) and a `risks: list[RiskFlag] | None` field on
`DecisionResult`. Frontend: `decide.tsx` gained a `RisksCard` component rendered only when
`risks` is a non-empty list, styled to match the existing `PositionCard`'s "illustrative only"
warning convention. `frontend/src/lib/api.ts`'s `DecisionResult` type also gained
`llm_verdict`/`llm_reasoning`/`llm_verdict_overridden_by_sizing` — these were real backend
fields the TypeScript type had been missing since T203, found while extending this type for
the new `risks` field.

**Tests**: `services/decision-engine/tests/test_risk_agent.py`, 16 cases — opt-in gate,
missing-API-key fail-open, successful parse, non-200/network-exception/malformed-JSON
fail-open, markdown-fence stripping, per-risk category/severity/note validation (invalid
entries filtered, not silently accepted), the all-invalid-degrades-to-`None` case, cache
hit/write behavior, pure prompt-construction checks. `redis` needed a local `pip install` to
run these tests (already a real pinned dependency in `requirements.txt`, just missing from
this local dev environment — same class of gap as the jose/requests_oauthlib incidents
documented elsewhere in this file).

**A real adversarial-verification gotcha worth remembering**: the first version of
`test_returns_none_when_risk_check_disabled` used `cfg={"risk_check_enabled": False}` alone
and passed — but sabotaging the opt-in gate itself (`if not cfg.get("risk_check_enabled",
False):` → `if False:`) did NOT make this test fail, because the sabotaged code path fell
through to the SEPARATE no-api-key early return (the test's cfg had no API key either), which
also returns `None`. Two different guards returning the same value can mask each other in a
naive test. Fixed by supplying a valid API key and asserting the API is never called — that
version correctly failed with the gate disabled before being fixed.

**What to check if this looks wrong**:
```bash
# Confirm the opt-in gate: risk_check_enabled must be explicitly set in portfolio config
docker exec stockai-decision-engine-1 python3 -c "
from src.api.risk_agent import check_risks
print('module loads OK')"

# Check cache state for a specific symbol/style/date:
docker exec stockai-redis-1 redis-cli get "de:risk:AAPL:SWING:2026-07-18"
```

---

## Feature Reference: T258-PORTFOLIO-CORRELATION-PREENTRY — Correlation-Aware Entry Scoring (Built 2026-07-18)

**What this is**: wires the ALREADY-EXISTING portfolio-risk correlation math
(`/portfolio-risk/risk`, portfolio-optimizer) into the pre-entry decision as an advisory score
layer — a candidate highly correlated with an already-open position now scores -1 in
`_should_enter()`, the DE-outage fallback gate. Never a hard reject, matching this repo's
established discipline of promoting a soft penalty to a hard gate only after outcome data
justifies it.

**Why the fallback gate, not decision-engine itself**: decision-engine's `scorer.py` scores
each candidate in complete isolation — `DecisionRequest.open_positions` is only a COUNT, never
a symbol list, by design. Extending decision-engine to accept and score against a real symbol
list (and the price history needed to correlate against it) would be a materially bigger,
more invasive change than "port an advisory layer" — this repo already treats `_should_enter()`
as the place to harden DE-parity behaviors (see the T232-DL-DUALSCORER-DEBT hard-reject ports
above), so the correlation layer landed there too, at the same M-effort scope as the tracker
item called for.

**Why local DB math, not an HTTP call to portfolio-optimizer**: market-data has direct DB
access to `Price`/`Stock`; portfolio-optimizer's own `/portfolio-risk/risk` endpoint fetches
prices over HTTP specifically BECAUSE it lacks that access (see that endpoint's own module
docstring). Calling out to portfolio-optimizer from `_should_enter()`'s hot path would add a
network round-trip to the single most capital-sensitive code path in the system for math this
service can already do directly — so the `df.corr()` logic was reimplemented locally instead
of reused via HTTP.

**Implementation**: two new functions in `paper_trading_engine.py`.
`_bulk_fetch_daily_closes(session, stock_ids)` — one bulk query (30-day lookback,
`Price.stock_id.in_(...)`) pivoted into a wide DataFrame, called ONCE per scan cycle for the
whole open book (not once per candidate). `_max_correlation_with_open_positions(session,
candidate_stock_id, open_stock_ids, open_closes_cache)` — fetches only the candidate's own
closes fresh, joins onto the pre-fetched open-book cache, returns the highest absolute
pairwise daily-return correlation or `None` if incomputable (no open positions, insufficient
overlapping history — matching the repo's convention that `None` and a real "no correlation"
value have different implications for the score layer, so they must be distinguishable).
`_should_enter()` gained a `max_open_corr` parameter and penalizes -1 when it exceeds `0.8` —
the SAME threshold portfolio-optimizer's own risk endpoint already uses for its "high
correlation" warning, chosen for consistency rather than picked fresh.

**Not built in this pass**: beta-weighted book exposure (the other half of the original
catalog design) — correlation was the higher-value, more tractable half for a per-candidate
score layer; beta-weighted exposure is more naturally a book-level dashboard readout than a
per-entry-decision score component, and is left as a smaller, separately-scoped follow-up.

**Tests**: 6 new cases in `test_should_enter_de_parity.py` (score-layer behavior: penalizes
`>0.8`, not at exactly `0.8`, not below, not on negative/hedge correlation, stacks
independently with the pre-existing regime/K-Score layers) plus 11 new cases in
`services/market-data/tests/test_correlation_preentry.py`, extending
`test_broker_position_sync.py`'s established real-sqlalchemy-via-stub-pop-and-restore
technique to the `Stock`/`Price` models — covers the bulk fetch, lookback-window exclusion,
high/low/insufficient-history correlation detection, and picking the highest absolute
correlation across multiple open positions.

**A real adversarial-verification finding worth remembering** (a near-miss on false test
confidence, not a shipped bug): the first version of the "candidate excluded from its own
open-position list" test built `open_closes_cache` from `[candidate_stock_id]` alone. Disabling
the actual self-exclusion filter in the source did NOT make this test fail — because with the
candidate's own column already present in `open_closes_cache`, the subsequent
`open_closes_cache.join(cand_wide[[candidate_stock_id]], how="outer")` call raises a plain
pandas `ValueError` (duplicate column name) on ANY code path, self-exclusion filter present or
not, which the function's own `except Exception` silently catches and returns `None` from —
the exact same return value the test expected, but for a completely different, coincidental
reason. Caught by disabling the filter and getting a passing test back (a red flag — the test
should have failed), then rewriting it to build a cache that does NOT contain the candidate's
column, with the candidate ID separately duplicated into `open_stock_ids` — that version
correctly produces a spurious `1.0` self-correlation and fails when the filter is removed.
**Lesson**: an adversarial-verification pass that produces "still passes" for a supposedly
protective guard is itself a finding — investigate why, don't just conclude the guard is
redundant, the way the broker-position-sync case earlier in this file genuinely was.

**A SQLite/BigInteger test-harness quirk hit again** (same class already documented for
`SignalOutcome` elsewhere in this file): `Price.id` is a `BigInteger` primary key, which
doesn't get SQLite's implicit `INTEGER PRIMARY KEY` autoincrement — test fixtures inserting
`Price` rows must assign `id` explicitly (a real Postgres sequence handles this in production;
this is a test-harness-only workaround).

**What to check if this looks wrong**:
```bash
# Confirm the correlation layer is actually computing values (not silently always None):
docker logs stockai-market-data-1 --since 1h | grep 'correlation_check_failed'
# Absence of this log line does NOT confirm success on its own — it only means no EXCEPTION
# occurred; None is also the normal, expected return for a portfolio with 0-1 open positions.

# Spot-check the bulk fetch + correlation math directly against real data:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.services.paper_trading_engine import _bulk_fetch_daily_closes
from db import SessionLocal
s = SessionLocal()
df = _bulk_fetch_daily_closes(s, [1, 2, 3])  # real stock_ids
print(df.tail())"
```

---

## Feature Reference: T258-MACRO-SECTOR-IMPACT — Structured Sector Chips on Macro Reactions

**Built 2026-07-18.** Finishes what T249-P2 (macro post-announcement fast reaction) explicitly
deferred: `generate_reaction()` (`services/event-intelligence/src/services/macro_reaction.py`)
previously returned only a narrative `reaction_text` paragraph. It now also asks the same
single Haiku call for a structured `{sectors_helped: [], sectors_hurt: []}` block (0-4
GICS-style sector names each) — no second LLM call, same fail-open contract as before.

**Validation**: new `_clean_sector_list(raw: object) -> list[str]` — non-list input becomes
`[]`, non-string/empty entries are filtered, surviving strings are whitespace-stripped, capped
at 6. `generate_reaction()`'s return type changed from `str | None` to `dict | None` (`
{"reaction_text": ..., "sectors_helped": [...], "sectors_hurt": [...]}`); both
`check_release_day_fast_poll()` and `check_fomc_statement_poll()` were updated to unpack the
new shape.

**Storage**: two new nullable `EconomicEvent` columns, `sectors_helped`/`sectors_hurt` (both
`Text`), JSON-encoded strings — matching `reaction_text`'s existing TEXT-column convention
rather than introducing a new Postgres array/JSONB type for consistency with the sibling
columns on the same table. **Requires a manual `ALTER TABLE economic_events ADD COLUMN IF NOT
EXISTS sectors_helped TEXT, ADD COLUMN IF NOT EXISTS sectors_hurt TEXT;`** in every environment
— per this file's own `create_all()`-gap invariant (new columns on an existing, already-
populated table are never auto-applied).

**Read side**: `GET /events/overview` (`services/event-intelligence/src/api/routes.py`) parses
both columns defensively via an inline `_parse_sectors()` helper (degrades to `[]` on any parse
failure) into the `latest_macro_reaction` field. `frontend/src/pages/intelligence.tsx`'s
"Latest Macro Reaction" card renders green ▲ chips for `sectors_helped` and red ▼ chips for
`sectors_hurt`, between the actual/previous value line and the reaction paragraph.

**Deliberately not built this pass**: watchlist-join personalization ("you watch 3 names in a
sector this release pressures", from the original T249-P2 design) — scoped to the structured-
data half only; the chips already let a user do that cross-reference visually without a new
per-recipient query in `check_macro_reaction_alerts()`.

**Tests**: 21 new cases in `services/event-intelligence/tests/test_macro_reaction.py` (full
suite 143 passed) — `_clean_sector_list` validation (valid list, non-list, non-string
filtering, whitespace stripping, 6-entry cap, empty list), `generate_reaction()`'s new dict
shape via a `_FakeAsyncClient` async-context-manager pattern (mirroring `risk_agent.py`'s own
test technique, since `httpx` is a `MagicMock` in this test environment), and source-text
checks confirming both poll functions write the new columns. Adversarially verified
`_clean_sector_list` by replacing its body with `return raw` — 5 of 7 tests correctly failed,
then reverted.

**What to check if this looks wrong**:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\d economic_events" | grep sectors
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://api-gateway:8000/events/overview', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.json().get('latest_macro_reaction'))
"
```

---

## Feature Reference: T258-TRADE-POSTMORTEM — Per-Closed-Trade Plan-vs-Actual Review

**Built 2026-07-18.** The aggregate learning loop already existed and is validated
(`calibrate_entry_weights` learns from closed trades, `entry_factors` does per-factor win-rate
analysis, retro-feedback backfills realized EV onto `TuneHistory`) — but there was no per-trade
review: looking at one closed trade couldn't answer "did entry match plan, was the stop
respected, was the exit early vs. the time-stop, did price run further after exit." v1 is
mechanical only (no LLM) — `PaperTrade` already stores both the plan (entry/stop/take_profit at
entry) and the actuals (exit price/reason/pnl), so this is mostly presentation over existing
data plus one new bar-data query.

**Endpoint**: `GET /paper-portfolio/trades/{trade_id}/postmortem`
(`services/market-data/src/api/paper_portfolio.py`) — 404 if the trade doesn't exist, 400 if
`trade.stage != "closed"` (post-mortems only make sense on a finished trade). Computes:
- `is_mechanical_exit` — whether `exit_reason` is in `_MECHANICAL_EXIT_REASONS = {"stop_hit",
  "breakeven_stop", "target_reached", "time_stop"}` (plan-consistent) vs. anything else
  (discretionary/manual/decay).
- `plan_adherence.exit_vs_stop_pct` / `.exit_vs_target_pct` — actual exit price vs. the stored
  plan levels, as a percent.
- `hold_window.hold_days_vs_expected` — actual `hold_days` vs. the trading style's
  `_STYLE_OVERRIDES` `max_hold_days` (SHORT=10, GROWTH=60, SWING=20, LONG=90; unknown style
  falls back to 60) — a different concept from signal-engine's `_OUTCOME_HOLD_DAYS` (that one
  labels signal outcomes; this one is the paper-trade time-stop horizon).
- `max_favorable_excursion` — the highest daily `Price.high` between `entry_time` and
  `exit_time` for the trade's linked `stock_id`, vs. the actual exit price. One indexed range
  query against the same daily `Price` table already used elsewhere in this file — not a new
  data source.
- `entry_slippage_pct` — currently a placeholder, always `0.0`. Pure paper trades fill exactly
  at the signal's live price with no separate "planned" entry to diverge from; the field is
  kept in the response shape for forward compatibility with real-broker-synced trades
  (T257-BROKER-ORDER-HISTORY), where an actual fill CAN diverge from the paper-simulated
  `entry_price`.

**UI**: `frontend/src/pages/paper-portfolio.tsx`'s `PostmortemPanel` renders as an expandable
row under each closed trade in the trade history table — click a row to toggle
(`expandedTradeId`, the same pattern already used elsewhere on this page). Shows a
plan-consistent/discretionary badge plus 5 stat cells, with a callout when price ran more than
5% above the exit price afterward ("worth reviewing whether the exit was early").

**Deliberately not built this pass**: a v2 LLM call generating `what_went_right`/
`what_went_wrong`/`lessons` prose per trade — v1's mechanical fields are what this repo's own
calibration-loop discipline says to trust first; an LLM narrative layer is a later, optional
addition, not a prerequisite.

**Two real bugs caught in my own test-writing process during adversarial verification** (not
in the shipped feature — both were self-caught before either could ship with false test
confidence):
1. An early version of the test extraction hardcoded `_MECHANICAL_EXIT_REASONS` as a literal
   dict in the test namespace instead of pulling it from real source. Sabotaging the REAL
   constant in `paper_portfolio.py` (emptying the set) still passed the test — because the test
   was reading its own hardcoded duplicate, not the sabotaged value. Fixed by extracting the
   real constant's source line via string search and `exec()`-ing it into the namespace before
   the function body runs; re-verified the sabotage is now correctly caught.
2. Separately (unrelated to this feature, discovered while running the full suite in
   isolation): a genuine pre-existing wall-clock flakiness bug in
   `test_should_enter_de_parity.py` — its autouse `_always_market_hours` fixture only patched
   `_is_market_hours()`, never the separate time-of-day gate's own `datetime.now()` call. 13
   tests failed for real when run at 9:48 AM ET (inside the "first 30 min of market open" gate
   window). Fixed by also pinning `datetime.now()` to a fixed, safe mid-session instant (noon
   ET on a Monday) inside the same fixture; confirmed the per-test time-of-day-gate tests (which
   use their own local `_mock_local_time` override) still correctly take precedence over the
   fixture default.

**Tests**: 13 new cases in `services/market-data/tests/test_trade_postmortem.py`, using the
established real-sqlalchemy-via-stub-pop-and-restore technique (same as
`test_broker_position_sync.py`/`test_correlation_preentry.py`) to load real
`PaperPortfolio`/`PaperTrade`/`Stock`/`Price` models against an in-memory SQLite engine —
covering the 404/400 guards, mechanical-vs-discretionary exit-reason classification,
exit-vs-stop/target math, hold-days-vs-expected per style (including the unknown-style
fallback), and max-favorable-excursion (highest high within the hold window, ignoring prices
outside it — the specific case the entry_time-lower-bound sabotage above targets). Full
294-test market-data suite and frontend typecheck green.

**What to check if this looks wrong**:
```bash
# Confirm the endpoint returns real data for a known closed trade:
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://api-gateway:8000/paper-portfolio/trades/<real_trade_id>/postmortem', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.status_code, r.json())
"
```

---

## Feature Reference: T249-MARKETMOVER-P4 — Market Pulse Card (Built 2026-07-18)

**The last unbuilt slice of Tier 249's original ask** ("monitor the news or any information
that would make the market go up or down") — P0-P3 covered the structured, high-signal half
(CPI/FOMC/NFP/earnings releases and reactions); this is the deliberately lower-signal,
free-headline half, framed from the start as an honest MVP rather than a real-time
breaking-news engine (that would need a paid data source — Benzinga/Polygon news tier — and
remains an explicit non-goal here).

**New endpoint**: `GET /stocks/market/pulse` (`services/market-data/src/api/news.py`), reusing
the existing per-symbol news pipeline's exact building blocks rather than a new one: three
market-level `_google_news()` queries (`"stock market"`, `"S&P 500"`, `"Federal Reserve"`),
merged/deduped via the existing `_merge()`, top ~10 headlines piped through a new
`_claude_market_themes()` — same Haiku-call shape as `_claude_sentiment()` (same model, same
fail-open contract) but additionally asks for up to 3 recurring themes, since a market-level
digest needs more than a bare score to be useful. Falls back to a plain VADER average with no
themes if Claude is unavailable or fails. Cached 30 min in Redis
(`stockai:market_pulse`), matching the per-stock news cache's own TTL.

**Deliberately NOT wired into any alert/notification path** — 30-minute cadence and unranked
headlines are too noisy to page someone about; this is a passive dashboard card only, rendered
as `MarketPulseCard` on `intelligence.tsx`'s Overview tab (above the existing Latest Macro
Reaction card).

**Test environment gap found and fixed**: `feedparser` and `vaderSentiment` are both real,
pinned `services/market-data/requirements.txt` dependencies that `news.py` imports at module
level, but neither was installed in this local dev environment nor stubbed by `conftest.py` —
attempting to import `news.py` for testing raised `ModuleNotFoundError` on both in turn. Fixed
by a local `pip install feedparser==6.0.11 vaderSentiment==3.3.2` (matching the exact pinned
versions) rather than adding them to conftest's stub list — same class of gap already
documented for `jose`/`requests_oauthlib`/`redis` elsewhere in this file, and the same
resolution: prefer running tests against the real library over stubbing it, so `_google_news()`
RSS parsing and the VADER fallback path are exercised for real, not mocked.

**A real bug found live, right after first deploy**: production returned `source: "vader"` with
empty `themes` even though the user had already set a Claude API key on the admin Settings
page. Root cause: `news.py`'s `_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")` reads a
plain environment variable set once at import time — but nothing in this app ever writes
`ANTHROPIC_API_KEY` into a container's env. The Settings page instead writes to
`stockai:admin:claude_api_key` in Redis, the SAME key `llm_scorer.py`/`risk_agent.py` already
read via their own `_get_api_key()`/`_get_claude_key()` helpers — `news.py`'s per-symbol
`_claude_sentiment()` had this identical gap the whole time, just never noticed because the
per-symbol sentiment endpoint's VADER fallback is unremarkable-looking either way. Fixed by
adding `news._get_claude_key()` (Redis-first via `_get_redis().get(_REDIS_CLAUDE_KEY)`, falling
back to the env var only if Redis has nothing or errors), matching `llm_scorer.py`'s exact
established pattern, and switching every `_ANTHROPIC_KEY` read site (`_claude_sentiment()`,
`_claude_market_themes()`, `get_news_sentiment()`) to call it instead of reading the module-level
constant directly.

**A real test-writing gotcha hit while wiring this up**: the existing tests patched
`news._ANTHROPIC_KEY` directly to simulate "no key configured" — but once the code called
`_get_claude_key()`, which itself calls `_get_redis()` first, the conftest-stubbed `MagicMock`
Redis client returned a truthy `MagicMock` from `.get(...).strip()`, silently defeating the "no
key" test case (it kept passing, but for the wrong reason — the code proceeded past the
guard into a stubbed `httpx.Client()` call that itself degraded to `None` via the non-200 path,
not via the intended early-return). Caught by adversarially disabling the real guard
(`if not api_key or not titles` → `if False or not titles`) and finding the test still passed —
the same "still passes after sabotage" red flag already documented for the correlation
self-exclusion finding elsewhere in this file. Fixed by replacing all `patch.object(news,
"_ANTHROPIC_KEY", ...)` call sites with `patch.object(news, "_get_claude_key", return_value=...)`
and adding an explicit `mock_client.assert_not_called()` to the no-key test so a regression here
fails on the right assertion instead of coincidentally landing on the same return value via a
different code path. Re-verified: the same sabotage now correctly fails this test.

**A second real bug found live, right after the first fix deployed**: with the Redis key now
correctly found, the endpoint STILL returned `source: "vader"` — live-calling
`_claude_market_themes()` directly against the real Anthropic API in the production container
showed the HTTP call itself succeeded (`200 OK`) but `json.loads(text)` raised `Expecting value:
line 1 column 1 (char 0)`, silently swallowed by the function's own `except Exception` fail-open
contract. Root cause: Claude sometimes wraps its JSON response in `` ```json ... ``` `` markdown
fences despite the system prompt explicitly saying not to — `risk_agent.py` already strips this
via `re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL)` before its own `json.loads()`,
but `news.py`'s two Claude call sites (`_claude_sentiment()`, the pre-existing per-symbol
endpoint, and the new `_claude_market_themes()`) never had this stripping — the per-symbol
endpoint's silent VADER fallback made this identical, pre-existing gap invisible until Market
Pulse's live verification actually inspected the real failure reason instead of just checking
`source: "vader"` and assuming "no key configured" was still the cause. Fixed by adding a shared
`_strip_markdown_fence()` helper (matching `risk_agent.py`'s regex exactly) and applying it at
both `json.loads(text)` call sites in `news.py`. Adversarially verified by reverting one call
site to its unstripped form and confirming the new
`test_claude_market_themes_strips_markdown_fence_before_parsing` test correctly failed
(`result is None` instead of a populated dict) before restoring it.

**Lesson reinforced**: after a fix ships, "check that it returns 200 / doesn't error" is not the
same verification bar as "check that it returns the CORRECT thing for the CORRECT reason" — the
first live check here only confirmed `source: "vader"` was still showing, which could have
several different causes, and assuming it was still the already-diagnosed Redis-key issue would
have been wrong. Calling the actual failing function directly and reading its real exception
(rather than its swallowed, logged-only failure) found the true, different root cause in under a
minute.

**Tests**: `services/market-data/tests/test_market_pulse.py`, 19 cases — Claude-available vs.
VADER-fallback scoring paths, neutral-with-no-headlines, confirming all three market-level
queries are actually issued, Redis cache write + warm-cache read (no re-fetch when cache is
warm), themes capped at 3, `_claude_market_themes()`'s own fail-open cases (missing API key —
now asserting the HTTP client is never constructed, not just that the result is `None` — non-200
response, malformed JSON), and 4 new cases for `_get_claude_key()` itself (Redis value preferred
over the env var, env-var fallback when Redis is empty, env-var fallback on a Redis connection
error, whitespace-only Redis value treated as absent). Adversarially verified four guards by
sabotage, confirmed each caught the induced failure, then reverted: removing the `[:3]` themes
cap (test caught 5 themes surviving instead of 3); disabling the warm-cache early-return in
`get_market_pulse()` (test caught 3 live re-fetch calls instead of the expected 0); appending a
4th entry to `_PULSE_QUERIES` (test caught the extra query appearing, confirming the test reads
the real module-level constant rather than a hardcoded duplicate that could silently drift from
it — the exact failure mode documented in the T258-TRADE-POSTMORTEM entry above); and the
Redis-priority order in `_get_claude_key()` (test caught the env-var value winning instead of
the Redis value); and the markdown-fence stripping (test caught a `None` result instead of a
populated dict when a call site's stripping was reverted). Full 313-test market-data suite and
frontend typecheck green.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 python3 -c 'from jose import jwt' 2>/dev/null  # sanity: jose still present
docker exec stockai-redis-1 redis-cli get stockai:market_pulse
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'lauwing2','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://api-gateway:8000/stocks/market/pulse', headers={'Authorization': f'Bearer {tok}'}, timeout=20)
print(r.status_code, r.json())
"
```

---

## Feature Reference: AUD250-PORTFOLIOOPTIMIZER-SILENT-FALLBACK-NO-FLAG — Fallback Reason Now Visible in Response (Built 2026-07-19)

**The gap**: `T247-PORTFOLIOOPTIMIZER-SLSQP-SILENT` (2026-07-07) added a `log.warning()` for
every silent equal-weight fallback in `services/portfolio-optimizer/src/optimizers/methods.py`
— SLSQP non-convergence, or `constraints.max_weight` making `sum(w)=1.0` mathematically
infeasible (`n * max_weight < 1.0`) — but `PortfolioWeights` (the response dataclass) never
gained a field for it. A caller got a flat, indistinguishable-from-genuine equal-weight result
with `HTTP 200` and zero indication their constraint was ignored, visible only by grepping
production logs.

**Fix**: added `fallback_reason: str | None = None` to `PortfolioWeights`, set at all 4
fallback call sites:
- `mean_variance()` / `risk_parity()` / `ai_allocation()` — each has two fallback branches
  (the `n * max_weight < 1.0` bypass that skips SLSQP entirely, and the `res.success == False`
  branch after a real SLSQP attempt), each now sets a distinct descriptive message.
- `hierarchical_risk_parity()` — its cap-and-redistribute helper, `_cap_and_redistribute()`,
  changed its return type from a bare `np.ndarray` to `tuple[np.ndarray, str | None]` so the
  infeasibility-bypass case (the only fallback HRP has, since it never calls SLSQP) can
  propagate a reason up to `_pack()`.

`_pack()` (the shared response-builder used by 3 of the 4 methods) gained a `fallback_reason`
parameter; `ai_allocation()` builds `PortfolioWeights` directly (doesn't go through `_pack()`)
and was updated separately. `routes.py`'s `asdict(out)` already serializes any dataclass field
automatically — no route change was needed for the field to reach the actual HTTP response.

**Frontend**: `frontend/src/lib/api.ts`'s `PortfolioWeights` type gained `fallback_reason?:
string | null`; `frontend/src/pages/portfolio.tsx` shows a warning banner
("Optimization fell back to equal weight: ...") whenever it's set, using the same `setWarning()`
state the existing `dropped_symbols` warning already uses (mutually exclusive — `dropped_symbols`
wins if both would otherwise fire, so the single banner never has to show two unrelated
warnings at once).

**A real gap surfaced while wiring this up**: `portfolio.tsx` has no `max_weight` UI control at
all today — only the SLSQP-non-convergence fallback is reachable from this page currently. The
infeasibility-bypass fallback_reason is still fully live and tested via the API directly (and
protects any future UI work that adds a `max_weight` slider/input), just not yet exercisable
end-to-end through this specific page.

**Tests**: 8 new/extended cases across `services/portfolio-optimizer/tests/test_optimizers.py`
and `test_optimize_endpoint.py` — genuine (non-fallback) results assert `fallback_reason is
None` for all 4 methods; every fallback path (SLSQP non-convergence for the 3 SLSQP-based
methods, the infeasible-`max_weight` bypass for all 4 methods — `ai_allocation`'s infeasibility
path had zero prior test coverage, added here) asserts a real, descriptive `fallback_reason`
string; a new end-to-end test calls the real `optimize()` endpoint function with an infeasible
`max_weight` and confirms `fallback_reason` survives all the way into the actual HTTP response
dict, not just on the internal dataclass.

Adversarially verified 3 times, each caught and reverted: sabotaged `mean_variance()`'s
SLSQP-failure `fallback_reason` assignment (1 test caught it); sabotaged
`_cap_and_redistribute()`'s infeasibility-branch return value to `None` (3 tests caught it,
including the HRP-level integration test — proving the signal genuinely propagates end-to-end,
not just at the helper's own unit level); sabotaged `_pack()`'s own `fallback_reason`
pass-through to a hardcoded `None` (5 tests caught it, including the new end-to-end endpoint
test — confirming the flag would still be visible even if a regression were introduced at the
shared packing layer, not just at an individual method's own call site).

**What to check if this looks wrong**:
```bash
docker exec stockai-portfolio-optimizer-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.optimizers.methods import risk_parity
import pandas as pd, numpy as np
rng = np.random.default_rng(0)
returns = pd.DataFrame({'A': rng.normal(0.0005, 0.01, 60), 'B': rng.normal(0.0003, 0.015, 60)})
# 2 symbols * 0.2 max_weight = 0.4 < 1.0 -> infeasible, must set fallback_reason
r = risk_parity(returns, max_weight=0.2)
print(r.fallback_reason)
"
```

---

## Feature Reference: AUD250-DECISIONENGINE-GAMEPLAN-SHARED-EXECUTOR — Dedicated Thread Pool (Built 2026-07-19)

**The gap**: `services/decision-engine/src/api/core/aggregator.py`'s `abuild_game_plan()` (added
in `T247-DECISIONENGINE-STYLEPARAMS-BLOCKING`, 2026-07-07, to move a blocking `httpx.get()` off
the event loop) reused `_yf_executor` — a 4-worker `ThreadPoolExecutor` originally built for a
completely unrelated purpose, the yfinance price-fallback path in the same file. Two distinct
kinds of blocking work sharing one small pool means a burst of one kind can queue behind the
other, undercutting (though not fully defeating) the parallelism a batch `POST /decide/batch`
request is supposed to get. `regime.py` already hit and fixed the identical pattern for its own
blocking regime fetch, via a dedicated `_regime_executor` — `aggregator.py` just never got the
same treatment when `abuild_game_plan()` was added later.

**Fix**: added `_game_plan_executor = ThreadPoolExecutor(max_workers=2,
thread_name_prefix="game_plan")` to `aggregator.py`, matching `regime.py`'s exact pattern, and
switched `abuild_game_plan()`'s `run_in_executor()` call to use it instead of `_yf_executor`.

**Tests**: 2 new cases in `services/decision-engine/tests/test_aggregator.py`:
- **Identity test** — spies on the actual executor object passed to `run_in_executor()` inside
  a running event loop (patching `aggregator.asyncio.get_running_loop` to return a wrapper that
  records the executor argument before delegating to the real loop), then asserts
  `abuild_game_plan()` submitted work to `_game_plan_executor`, not `_yf_executor`.
- **Contention test** — saturates every one of `_yf_executor`'s workers with self-releasing
  blocking tasks, then confirms a concurrent `abuild_game_plan()` call still completes promptly
  rather than queuing behind them.

**Two real bugs caught in my own first-draft tests, both via adversarial verification**
(temporarily reverting the fix and confirming the tests still passed — a red flag caught before
either test shipped with false confidence):
1. The first version of the identity test only asserted `_game_plan_executor is not
   _yf_executor` — true regardless of which executor the CODE actually uses, since it's just
   comparing two objects that exist side by side. Fixed by spying on the real argument passed to
   `run_in_executor()` instead of comparing unrelated objects.
2. The first version of the contention test ran only ONE concurrent task against
   `_yf_executor`'s 4 workers — comfortably fits without contention even if `abuild_game_plan()`
   WERE still using the shared pool, so the sabotage silently passed. Fixed by submitting one
   saturating task per `_yf_executor` worker (reading `_max_workers` directly rather than
   hardcoding a count) before making the concurrent call under test, forcing genuine contention
   to become observable if the pools were ever shared again. The saturating tasks self-release
   after a fixed short delay (rather than waiting on a manually-set flag) specifically so this
   test can never hang even if its own assertion were to fail — a hung test is a worse failure
   mode than a fast, clear assertion error.

Re-verified after both fixes: reverting `abuild_game_plan()` to use `_yf_executor` again made
both tests fail cleanly (no hang) before restoring the real fix.

**What to check if this looks wrong**:
```bash
docker exec stockai-decision-engine-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.api.core import aggregator
print('game_plan_executor is yf_executor:', aggregator._game_plan_executor is aggregator._yf_executor)
print('game_plan_executor workers:', aggregator._game_plan_executor._max_workers)
"
```

---

## Feature Reference: T252-AUTO-SWING-PIVOTS — Chart Swing Pivot Markers + Click-Snap (Built 2026-07-19)

**Gap this closes**: `services/technical-analysis/src/indicators/trendlines.py` already had
`_find_pivots(series, order=5)` — real, tested local-max/local-min detection, used internally
to anchor server-side trendlines and support/resistance levels — but it was never exposed as a
standalone list of pivot points, and nothing client-side ever called it. Fixed Range VP (built
2026-07-16) requires two manual clicks to pick a swing high and swing low, and eyeballing the
exact extremum bar is imprecise.

**Chose a client-side port over a new backend endpoint**: Fixed Range VP's click handler already
reads bar indices out of `activePrices[]`, the exact array PriceChart.tsx has in memory — a new
backend endpoint would need its own index-alignment logic against whatever bar window the
frontend happens to be showing, a real synchronization risk. This matches the established
convention (`volumeProfile.ts`, `indicators.ts`) of doing chart-only computation locally instead
of adding a network round-trip.

**New `frontend/src/lib/swingPivots.ts`**: `detectSwingPivots(bars, order=5)` ports
`_find_pivots()`'s exact algorithm — detecting on `high`/`low`, NOT `close`. This deliberately
matches `trendlines.py`'s own `T247-TA-CLUSTERPIVOTS-CLOSE-HIGH-MISMATCH` fix (a genuine swing
high/low is the bar's actual extremum, not wherever it happened to close) rather than
`detect_trendlines()`'s close-based pivots, which serve an unrelated purpose (trendline
least-squares fitting) and would give the wrong answer for "where's the real swing high."
`nearestPivot(pivots, targetIdx, maxDistance)` snaps an arbitrary clicked bar index to the
closest real pivot within tolerance.

**Verified against the real Python reference**, not just internally-consistent TS expectations —
per this repo's own standing lesson from the Tier 250 EMA/RSI/MACD port (a hand-translated
formula that "looks right" can still be wrong in a way only a real reference run catches). Ran
the identical zigzag fixture through both the real `_find_pivots(pd.Series(highs), order=3)`
and `detectSwingPivots()`: both produced the identical pivot indices (high at idx 4, low at idx
8), confirming the port is faithful.

**PriceChart.tsx wiring**:
- A new "Swing Pivots" toggle in the Indicators dropdown (off by default, daily-only), rendering
  small dot markers via `candles.setMarkers()`.
- **A real clobbering bug avoided during implementation, not shipped**: `setMarkers()` replaces
  the ENTIRE marker set on each call — the existing signal-transition-arrow code already called
  it once. Adding a second `setMarkers()` call for pivot dots would have silently erased
  whichever ran second. Restructured both marker sources to accumulate into one array and call
  `setMarkers()` exactly once.
- Fixed Range VP's click handler now always snaps the raw clicked bar index to the nearest pivot
  within 3 bars, regardless of whether the pivot-marker overlay itself is toggled on — the
  snap-to-precision benefit shouldn't require turning on the visual dots.

**Tests**: `frontend/src/lib/swingPivots.test.ts`, 10 cases — empty/too-short input, correct
high/low identification on a zigzag fixture (cross-checked against the real Python function as
described above), no false positives on a strictly monotonic run (a monotonic series has no
interior local extremum at all), the `+-order` edge-exclusion matching Python's
`range(order, n-order)`, `ts` pass-through, and `nearestPivot`'s within-tolerance / out-of-range
/ tie-break / empty-list behavior. Full 52-test frontend vitest suite, typecheck, and a full
`next build` all green.

**What to check if this looks wrong**: `detectSwingPivots()` in `swingPivots.ts` is the only
place this logic lives — if a marker looks like it's not a real local extremum, or Fixed Range
VP's clicks aren't landing where expected, re-run the cross-check above (`_find_pivots()` in
`trendlines.py` vs. `detectSwingPivots()` on the same fixture) to confirm the two haven't
drifted apart. Extended 2026-07-19 to also run on intraday timeframes (5m/15m/1h/4h), not just
daily — the client-side computation has no dependency on the backend's daily-only
`/ta/{symbol}/levels` endpoint, so the earlier daily-only restriction wasn't structurally
necessary. A separate bug (pivot markers set to `size: 0`, making them invisible even with the
toggle on) was found and fixed the same day.

---

## Design Reference: Swing Pivots + Fixed Range VP — What Each One Finds, and How to Use Them Together

**What a "swing pivot" is finding.** A swing high is a bar whose high is the highest point
within a window of nearby bars on both sides (`+-order`, default 5) — i.e. a real local top,
not just "a candle that went up." A swing low is the mirror: a real local bottom. These are
the same reference points every discretionary trader means when they say "draw your trendline
from swing low to swing low" or "the market made a lower high" — this feature just finds them
mechanically instead of eyeballing the chart. The small gray dots (▾ toggle: Indicators →
"Swing Pivots") mark every such point currently detected on the chart.

**What Fixed Range VP is finding.** Fixed Range VP answers a completely different question:
"of all the volume that traded between these two exact points I pick, where did most of it
concentrate?" It needs two clicks — a start bar and an end bar — and computes POC/VAH/VAL/HVN
(see the Volume Profile section above for what those mean) using ONLY the bars between those
two points. Unlike Session VP or Range VP (which profile a fixed calendar window), Fixed
Range VP is deliberately structure-anchored: the two points you pick define what "this move"
means, and the profile tells you how the market actually traded during it.

**Why they're built to be used together, not separately.** Fixed Range VP's whole value
depends on picking a *meaningful* start/end pair — profiling from a random Tuesday to a random
Friday tells you very little. Profiling from one real swing low to the next real swing high
(or vice versa) tells you exactly how a specific, identifiable move built its volume structure.
Before this feature, picking those two points meant zooming in and clicking as close as
possible to what looked like the swing extreme by eye. Now: turn on Swing Pivots to see the
dots, then use Fixed Range VP as normal — every click is silently snapped to the nearest real
pivot within 3 bars, whether or not the dots themselves are visually toggled on. You don't have
to be pixel-perfect anymore; clicking near a dot is enough.

**A concrete example of what this combination is trying to help you find**: suppose a stock
ran from a swing low at $80 to a swing high at $110, then pulled back to $95. Turn on Swing
Pivots, Fixed Range VP the $80→$110 leg specifically (snap-clicking near each dot), and read
the profile:
- If POC/HVN cluster near $95-98, that's telling you the pullback has landed almost exactly on
  the price level the market spent the most volume agreeing was fair DURING that specific
  rally — a materially stronger signal than "price is near a round number" or "price touched
  the 50-day MA," because it's derived from real, structural volume during the exact move in
  question, not a generic indicator.
- If the pullback has instead landed in a thin, low-volume gap of that same profile (an LVN
  region, or clearly below VAL), that tells you the current price wasn't a place the market
  spent much time agreeing on last time it was here — a weaker-conviction support level, more
  likely to be sliced through than held.
- If POC/HVN sit much higher (say, near $105), that tells you most of the rally's volume
  happened late and high, near the top — often a sign the move was thin/fast on the way up
  (a LVN-heavy rally per the "how to trade it" section above) and more fragile than it looked
  candle-by-candle alone.

**In one sentence**: Swing Pivots finds the real structural anchor points a discretionary
trader would draw lines between; Fixed Range VP tells you how volume actually distributed
across the specific move between two such points — together they replace "eyeball the chart
and guess where support is" with "profile the exact swing you care about, anchored precisely."

---

## Feature Reference: T252-FVG-COMBINATION-BADGES — Pivot-Anchor + Volume-Context Badges on FVG Trade Plan (Built 2026-07-19)

**Direct follow-on from the swing-pivots + Fixed-Range-VP combination above** — after the user
said they liked that pattern and asked for more, this closes the two cheapest, purely-wiring
proposals: cross-referencing the existing Fair Value Gap Trade Plan pick against two OTHER
already-computed features it had never been checked against.

**`nearestActionableFvg()`'s pick is pure price-distance** — the nearest unfilled gap to the
current price, nothing more. Two new pure functions in `frontend/src/lib/fvgTradePlan.ts`
corroborate (or don't) that pick:

- **`nearestPivotToFvg(gap, pivots, tolerancePct=0.015)`** — compares the gap's FAR edge (the
  one the stop sits beyond) against every `detectSwingPivots()`-detected swing pivot's price.
  Returns the closest one within tolerance (a % of price, so it scales sensibly across a $5
  stock and a $500 stock) or `null`. Deliberately compares the FAR edge, not the near edge —
  the far edge is the one whose structural significance actually matters to the trade thesis
  (it's where the stop sits and where the setup would be invalidated), not wherever price
  happens to be retracing from right now.
- **`classifyFvgVolumeContext(gap, profile, tolerancePct=0.005)`** — checks the gap's
  `[bottom, top]` range against a `computeVolumeProfile()` result: `'poc'` if it contains the
  Point of Control, `'hvn'` if it contains a High Volume Node (checked second, since POC is
  itself always also technically a volume peak — POC takes priority), `'thin'` if it overlaps
  the profiled range but hits neither, `'unknown'` if the gap falls entirely outside what was
  profiled (a different range was profiled — NOT the same as "definitely thin").

**UI**: `frontend/src/pages/stock/[symbol].tsx`'s existing "Fair Value Gap Trade Plan" card now
computes `detectSwingPivots()` and `computeVolumeProfile()` from the same `data.prices` already
on the page, and shows up to two extra badges next to the existing LONG/SHORT one: "⚓
Pivot-anchored" and one of "📊 At POC" / "📊 At HVN" / "📊 Thin zone" — each with a hover
tooltip explaining what it means, matching the card's existing badge convention.

**Tests**: 12 new cases in `fvgTradePlan.test.ts` — 10 for `nearestPivotToFvg` (the far-vs-near
edge distinction, tolerance behavior, closest-pivot tie-breaking among several candidates), 5
for `classifyFvgVolumeContext` (all four return states, including the POC-over-HVN priority
ordering). Adversarially verified 3 guards by sabotage, all caught and reverted: swapping the
far/near edge comparison (4 tests caught it — a bearish gap's pivot match landed on the wrong
edge entirely); disabling the `'thin'` fallback classification (1 test caught it); swapping
POC's priority over HVN (1 test caught it, correctly expecting `'poc'` and getting `'hvn'`
instead). **A real test-writing bug of my own was caught and fixed before it could ship**: the
first version of the volume-profile test fixture built its `poc`/`hvn` fields by re-deriving
the max-volume bucket generically regardless of which spike the test intended, which silently
produced a fixture where the "HVN, not POC" test case actually had POC land inside the gap too
— caught immediately by the test failing for the RIGHT reason (asserting `'hvn'`, getting
`'poc'`), fixed by rewriting the fixture to take an explicit, distinct POC price and a separate
list of HVN prices rather than inferring one from the other. Full 63-test frontend vitest
suite, typecheck, and a full `next build` all green.

**What to check if this looks wrong**: both functions live in `fvgTradePlan.ts` — if a badge
looks wrong, check `nearestPivotToFvg()`'s edge selection (`gap.kind === 'bullish' ? gap.bottom
: gap.top`) and `classifyFvgVolumeContext()`'s POC-then-HVN-then-thin ordering directly; both
are pure functions with no network/state dependency, so a wrong badge on a real symbol should
be reproducible by feeding that symbol's actual gap/pivot/profile data into either function
directly in a REPL.

---

## Feature Reference: T252-ANCHORED-VWAP — Click-to-Anchor VWAP Recalculation (Built 2026-07-19)

**Gap this closes**: `PriceChart.tsx` already computed VWAP (`computeVwap()` — cumulative
typical-price×volume / cumulative volume), but only ever anchored to the start of whatever
date-range window was currently selected. There was no way to anchor it to an arbitrary bar a
user picks — an earnings gap, a breakout day, a swing low — the standard "is price still above
VWAP from the day I would have entered" trend-continuation check.

**Implementation**: reuses `computeVwap()` completely unchanged — the only difference from the
existing rolling VWAP is which slice of `activePrices` it's fed
(`activePrices.slice(anchoredVwapIdx)` instead of the full array) and that the resulting line
only draws starting at the anchor bar's own time, not from the first visible bar. New
`showAnchoredVwap`/`anchoredVwapPickState`/`anchoredVwapIdx` state, a new "Anchored VWAP" entry
in the existing Volume Profile toolbar dropdown, and a dedicated click-subscribe `useEffect`
(same separate-effect-from-the-main-chart-rebuild pattern already established for Fixed Range
VP and the drawing tools) — one click sets the anchor directly, unlike Fixed Range VP's
two-click start/end pair. The click snaps to the nearest `detectSwingPivots()` pivot within 3
bars, same reasoning as Fixed Range VP's snap: an anchor planted on a real swing high/low is
far more useful than one landing a few pixels off from what the user actually meant to click.
Rendered as a solid cyan line, visually distinct from the existing dashed violet rolling VWAP,
plus its own legend entry.

**Correctness check performed** (this repo's own established discipline of verifying
hand-translated/derived math against a real computed reference, not just "it compiles"):
manually ran a 4-bar fixture through `computeVwap()` twice — once on the full series, once on
`.slice(2)` — and confirmed the anchored version's first value correctly resets to bar 2's own
typical price (115) and diverges meaningfully from the full-window VWAP at that same point
(107.2 vs. 115), proving the anchor genuinely changes the underlying calculation, not just
which portion of an unchanged line gets drawn.

**A real, PRE-EXISTING bug found and fixed while touching this code, unrelated to Anchored
VWAP itself**: this repo has no live Tailwind pipeline (no `tailwind.config.js`/
`postcss.config.js` — the same root cause already documented for `ToolbarDropdown.tsx`'s
fully-transparent-dropdown bug earlier this session). Fixed Range VP's own click-picking status
pill (`bg-violet-900/40`, `border-violet-500/50`, `text-violet-300`) and the VWAP legend
swatch (`border-violet-400`) both used classes with zero matching rule anywhere in
`globals.css` — silently no-oping in production the whole time, just less noticeably than the
fully-invisible dropdown (a missing border/background tint on a small status pill is easy to
miss; a fully see-through dropdown panel is not). Fixed both to inline styles while implementing
the new Anchored VWAP status pill and legend swatch, using them as the reference for what the
broken ones were supposed to look like.

**No dedicated test file** — `computeVwap()` lives inline in `PriceChart.tsx`, which has no
test file at all (same seam gap as every other `PriceChart.tsx`-only change in this repo, e.g.
the marker-clobbering fix documented in the Swing Pivots entry above). Correctness relies on the
manual verification above plus the fact that `computeVwap()` itself is unchanged — only its
input slice is new. Full 63-test frontend vitest suite (unaffected — none of it imports
`PriceChart.tsx` directly), typecheck, and a full `next build` all green.

**What to check if this looks wrong**: `computeVwap()` is the only place the math lives, and
it's untouched by this feature — if the anchored line looks wrong, first confirm
`anchoredVwapIdx` is the bar index you expect (log it, or check via React DevTools), since the
slicing (`activePrices.slice(anchoredVwapIdx)`) is the only new logic here. If the anchor point
seems to have moved from where you actually clicked, check the swing-pivot snap radius (3 bars)
— clicking near, but not on, a real pivot will snap to that pivot instead of your exact click.

---

## Feature Reference: T233-ARCH-INSERVICE-SPLITS (research-engine half) — Scoring Functions Extracted to scoring.py (Built 2026-07-19)

**Gap this closes**: `services/research-engine/src/api/routes.py` had grown to 1,877 lines,
bundling report aggregation/orchestration (Claude calls, caching, route handlers) with three
independently-testable quant subsystems (technical scoring, fundamental scoring, DCF
valuation). `tests/test_scoring.py` already imported several of these functions directly with
zero FastAPI/network dependency, proving they were already decoupled in practice — just not in
file layout, making the file a review hazard (a change to Claude-prompt-building code sits in
the same diff/file as a change to DCF math, with no structural signal separating the two).

**What moved**: a verified, genuinely self-contained block — `_last`, `_second_last`, `_atr`,
`_institutional_ownership_pct`, `_fmt_cap`, `_score_technical` (+ its `_rsi_interp`/
`_macd_interp`/`_hist_interp` helpers), `_sector_bench`, `_score_fundamental`,
`_build_checklist`, `_position_sizing_matches`, `_position_size`, `_dcf_fair_value` — into a
new `services/research-engine/src/scoring.py`. Verified before moving that this block has zero
`httpx`/`log`/`async`/network dependency (a plain `grep` across the extracted range came back
empty) — confirming it's pure computation, not orchestration wearing a scoring-sounding name.
`_call_claude()` and `_fallback_ai()` — which sit immediately after this block in the original
file and DO make a real `httpx.AsyncClient` call — were deliberately NOT moved; they're
orchestration, not scoring, despite living in the same neighborhood of the original file.

**`routes.py` re-imports all 15 names from `..scoring`**, so every existing `from
src.api.routes import X` call site — both the real route handlers below and every test file in
`tests/` — keeps working completely unchanged. This was a deliberate choice: an alternative
("update every test file's import path to `from src.scoring import X`") would have touched 4
test files for zero behavioral benefit, just to avoid one import-forwarding block in `routes.py`.

**Result**: `routes.py` went from 1,877 → 1,018 lines; `scoring.py` is a new, self-contained
893-line module with no FastAPI/network/logging dependency at all — genuinely independently
testable and reviewable now, not just in principle.

**signal-engine's half of this same tracker item was deliberately NOT done this session** —
`services/signal-engine/src/api/routes.py` is 6,190 lines across 34 routes (grown from the
tracker's stale 4,805-line citation), and is the single most safety-critical service in this
app (live signal generation, self-tuning, backtesting). A split there is a materially larger
and riskier undertaking than research-engine's clean, already-isolated 15-function extraction
— it doesn't fit the "about as safe as a refactor gets" framing the tracker's own impact note
uses for the pair. Left as its own separately-scoped follow-up (enumerate the 24 self-tuning/
analytics routes vs. 8 hot-path routes by `@router` decorator, split into `outcomes.py` +
`calibration.py`) rather than rushed into the same session as the low-risk half.

**Verification performed**:
1. **Zero test regression**: ran `python3 -m pytest tests/` on research-engine both before and
   after the split (via `git stash`/`git stash pop` to compare the exact same test run against
   the unmodified file) — identical result both times: 53 passed, 3 failed. The 3 failures
   (`test_fundamental_empty_returns_neutral_50` and two balance-sheet assessment tests) are a
   **real, pre-existing bug** unrelated to this split — confirmed by reproducing them on the
   completely unmodified original `routes.py` — left uninvestigated as genuinely out of scope
   for a pure file-layout task (a fix would need to determine whether `_score_fundamental`'s
   empty-input early return or the test fixtures themselves are wrong, a separate decision).
2. **Import chain verified directly**: `from src.api.routes import router, _score_technical,
   _score_fundamental, _build_checklist, _dcf_fair_value, _position_size,
   _position_sizing_matches, _atr, _last, _second_last, _institutional_ownership_pct,
   _fmt_cap` — all 15 re-exported names resolve correctly under the same stubbed test harness
   `main.py` itself would use in production (conftest.py's `pydantic`/`fastapi`-as-MagicMock
   stubbing), not just "the file parses."

**What to check if this looks wrong**: if a route handler in `routes.py` throws
`NameError`/`ImportError` on one of the 15 moved names, check the `from ..scoring import (...)`
block near the top of `routes.py` first — it's the only place those names re-enter this
file's namespace. If a scoring function itself looks wrong, it lives in `scoring.py` now, not
`routes.py` — the extraction was verbatim (no logic changes), so a bug found there was
already present before this split, not introduced by it.

```bash
# Confirm the split is live and both files parse in production:
docker exec stockai-research-engine-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.api.routes import router, _score_technical, _dcf_fair_value
print('routes.py + scoring.py import chain OK')
"
docker exec stockai-research-engine-1 wc -l /app/src/api/routes.py /app/src/scoring.py
```

---

## Feature Reference: T232-DL-DUALSCORER-DEBT — Conviction Gate + Signal Staleness Hard Rejects Ported to decision-engine (Built 2026-07-20)

**Continues the ongoing dual-scorer reconciliation** (see the T232-DL-DUALSCORER-DEBT entries
elsewhere in this file for the full 34-dimension background) — this session ported 2 more of
the 18 fallback-only hard rejects into `decision-engine`'s `hard_rejects.py`, both chosen
because they're binary safety/data-quality gates (not scoring judgment calls the item's own
`what` field warns against blind-porting).

**1. Conviction gate cross-check** — reads the same `conv_gate:{symbol}:{style}` Redis key
`paper_trading_engine.py`'s `_scan_for_entries()` already writes (1-day TTL, from the alert
system's own 7-layer conviction check). If that check already evaluated this BUY and failed
it, decision-engine now blocks too, instead of silently approving an entry the alert system
itself would never have notified on. Reads Redis directly (decision-engine already depends on
`redis` for `llm_scorer.py`/`risk_agent.py`, and shares the same `redis_url` as every other
service) rather than requiring the caller to pre-compute and forward it — this specific check
now makes `/decide/{symbol}` self-sufficient regardless of caller, directly closing part of
the item's own group-(e) "pipeline-topology gap" for this one gate.

**2. Signal-staleness hard reject (T222-C)** — a genuinely separate finding from what
`T234-CONFIG-UNJUSTIFIED-THRESHOLDS` originally claimed. That item described `paper_trading_
engine.py`'s 72h staleness cutoff and `decision-engine`'s scorer.py Layer 3e (4h/18h bands) as
"the same conceptual threshold set to different values" — re-verified before touching anything
and found this framing wrong: Layer 3e's 4h/18h bands are a SOFT scoring adjustment that
already correctly matches `_should_enter()`'s own identical SA-24 soft-scoring thresholds
(confirmed via grep — both literally use 4/18). The 72h value is a completely different,
EARLIER, HARD cutoff in `_scan_for_entries()` that decision-engine had no equivalent of at
all — meaning `/decide/{symbol}` would silently accept a signal so old that
`paper_trading_engine` would have discarded it before ever reaching a scorer. Ported as a new
hard reject (not a threshold reconciliation, since there was never a real numeric mismatch to
reconcile).

**Implementation**: `check_hard_rejects()` gained 3 new optional parameters (`symbol`, `style`,
`sig_ts`, all defaulting to `None`) so every pre-existing call site keeps working unchanged. The
conviction-gate check only runs `if symbol and style:`; the staleness check only runs `if
sig_ts is not None:` — both fail open on any error (malformed timestamp, Redis unavailable),
matching every other gate in this file. `routes.py`'s `_decide()` already had `sig_ts` computed
at line 99 and `symbol`/`style` in scope well before the `check_hard_rejects()` call at line
158 — no new data-fetching needed, just threading already-available values through.

**A real test-writing bug of my own, caught via adversarial verification, not shipped**: the
first version of "conviction gate skipped when symbol/style missing" relied on leaving Redis
completely UNMOCKED, reasoning "if the code tried to reach Redis without symbol/style it would
hit a real connection attempt and presumably fail." This test still passed even after
temporarily removing the `if symbol and style:` guard entirely — investigated why (the
"sabotage still passes" red flag this repo's testing discipline treats as a finding in its own
right, not a shrug) and found: with `common.config` stubbed as `MagicMock` (this test file's
own established convention for this Docker-only dependency, matching `test_risk_agent.py`),
`get_settings().redis_url` is itself a `MagicMock`, and `redis.Redis.from_url()` raises a real
`TypeError` trying to use it — caught by the SAME outer `except Exception` that handles
genuine Redis failures elsewhere in the same function. Removing the guard just swapped which
exception path produced the identical `result=None`, invisible to a test that only checks the
final return value. Fixed with a call-counting mock (`_TrackedRedis.get()` increments a
counter) that asserts the Redis lookup was never attempted at all — this version correctly
fails when the guard is removed.

**Tests**: 17 new cases in `services/decision-engine/tests/test_hard_rejects.py` (now 47 total,
up from 35 before AUD232-005/060's earlier session and 41 immediately before this one) — 6 for
the conviction gate (failed/passed/missing-key/redis-error/non-BUY-cached-signal/missing-
symbol-or-style), 6 for signal staleness (beyond-max-age/within-max-age/custom-max-age/absent-
ts/malformed-ts/real-datetime-object-not-just-string). Adversarially verified 3 guards by
sabotage, all caught and reverted: disabling the conviction-gate `if` condition, disabling the
staleness age comparison, and the call-counting-mock fix described above. Full 108-test
decision-engine suite green (up from 96 at the start of this session's work).

**What to check if this looks wrong**:
```bash
docker exec stockai-decision-engine-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.api.core.hard_rejects import check_hard_rejects
import inspect
print(inspect.signature(check_hard_rejects))
"
# Confirm the conviction-gate Redis key format matches what paper_trading_engine.py writes:
docker exec stockai-redis-1 redis-cli keys 'conv_gate:*' | head -5
docker exec stockai-redis-1 redis-cli get 'conv_gate:<SYMBOL>:<STYLE>'
```

---

## Feature Reference: T230-UX-MOBILE-RESPONSIVE (Phase 2 slice) — Stock Detail Page Grid Collapses on Mobile (Built 2026-07-20)

**Scoped down from the original ask** ("refactor the whole ~4000-line page, ~3 days") to the
single highest-value, lowest-risk slice: the page's ONE genuinely rigid layout. Matches the
same Phase 1/Phase 2 split already established for the Mobile Nav Drawer
(`T251-MOBILE-RESPONSIVE-DESIGN`) — fix the one broken thing that actually clips content off
mobile screens now, defer a full ground-up mobile redesign as its own larger, separately-scoped
item.

**The fix**: the page's outer chart+sidebar layout (`frontend/src/pages/stock/[symbol].tsx`)
was a hardcoded `gridTemplateColumns: '1fr 320px'` inline style — inline styles can't respond
to a media query directly, so a new `.stock-detail-main-grid` class was added to
`globals.css` instead, following the exact same `.desktop-nav-row`/`.mobile-nav-toggle`
breakpoint-class pattern already proven for the nav drawer. Above 768px it's pixel-identical to
the prior inline style; below it, the sidebar collapses to a single column below the chart
instead of being cut off entirely.

**Audited the rest of the page first** to confirm this really was the ONLY rigid layout that
needed fixing, rather than assuming: every other grid/flex container in the file already uses
`flexWrap: 'wrap'` (16 occurrences), self-wrapping `repeat(auto-fill, minmax(...))` grid
tracks, or constrains only small individual elements (badges/icons at 8-48px) rather than large
rigid columns. This means the sidebar's own internal content (AI Signal card, K-Score panel,
etc.) needed zero changes — it already rendered correctly at any width; only the OUTER grid
cutting the whole sidebar off-screen needed the fix.

**Verification is CSS-only, not browser-verified** — no browser/device-emulator tool was
available in this environment to visually confirm real rendered behavior (touch target sizes,
actual scroll behavior, chart legibility at narrow width). What WAS verified: the compiled
production CSS (`.next/static/css/*.css`) contains both the unconditional base rule
(`.stock-detail-main-grid{grid-template-columns:1fr 320px}`) and the correct media-query
override (`@media(max-width:767px){.stock-detail-main-grid{grid-template-columns:1fr!important}}`)
— proving the intended CSS reaches production, but not that it renders as expected on a real
device. Flagged explicitly in the tracker as not fully closed pending an actual visual check.

**Explicitly not done in this pass**: the chart itself was not made touch-pinch-zoomable
(lightweight-charts' default touch handling is used as-is); the page's remaining internal
density (many small stat grids and tables) was not restructured for a genuinely mobile-
optimized reading experience. This fix stops the sidebar from being cut off — it does not
redesign the page for mobile.

**What to check if this looks wrong**:
```bash
# Confirm the compiled CSS contains both the base rule and the breakpoint override:
docker exec stockai-frontend-1 sh -c "grep -o 'stock-detail-main-grid[^}]*}' /app/.next/static/css/*.css"
docker exec stockai-frontend-1 sh -c "grep -o 'max-width:767px)[^{]*{[^}]*stock-detail[^}]*}' /app/.next/static/css/*.css"
```
If either line is missing, the CSS didn't compile/deploy correctly — re-check
`frontend/src/styles/globals.css` and confirm a real frontend rebuild (not just a `docker cp`
hotfix — CSS is baked into the Next.js build) was actually run.

---

## Feature Reference: T232-DL-DUALSCORER-DEBT — K-Score Floor Hard Reject Ported to decision-engine (2026-07-20)

**Gap closed**: one more of the ~28 remaining `_should_enter()`-vs-decision-engine divergences
tracked under T232-DL-DUALSCORER-DEBT — the K-Score floor. `_scan_for_entries()`'s `min_kscore`
(per-style hard pre-filter, `_DEFAULT_CONFIG["min_kscore"] = 48.0`, GROWTH=48, LONG=50, SWING=52
via `_STYLE_OVERRIDES`) discards a candidate entirely before it's ever scored. decision-engine's
`scorer.py` already has AUD232-042's soft ±1 K-Score layer (fixed 55 boundary) — a genuinely
different mechanism (a scoring nudge, never a block) at a genuinely different threshold, so a
candidate the soft layer barely penalizes could still be one `_scan_for_entries` would have
discarded outright. `/decide/{symbol}` had no equivalent hard floor at all.

**Two-sided fix** (the threshold itself, not just the candidate's kscore value which was
already threaded, had to start reaching decision-engine):
1. `paper_trading_engine.py`'s `_call_decision_engine()` — added
   `**( {"min_kscore": cfg.get("min_kscore", _DEFAULT_CONFIG["min_kscore"])} if kscore is not None else {} )`
   to the `config_overrides` dict, conditional on `kscore` also being sent (same pattern as the
   existing `kscore` inclusion and the `llm_scoring_enabled` block).
2. `hard_rejects.py`'s `check_hard_rejects()` — needed zero new function parameters (`cfg`
   already carries both `min_kscore` and `kscore` via its existing merge mechanism):
   ```python
   if cfg.get("min_kscore") is not None:
       _kscore_val = cfg.get("kscore")
       if _kscore_val is not None and float(_kscore_val) < float(cfg["min_kscore"]):
           return f"K-Score {float(_kscore_val):.0f} below minimum {float(cfg['min_kscore']):.0f} — fundamental/momentum quality gate not met"
   ```
   Fail-open exactly like every other optional gate in this file — an older caller not sending
   `min_kscore` (or `kscore`) is unaffected.

**Tests**: `services/market-data/tests/test_min_kscore_config_wiring.py` (new, 3 cases) guards
the write side via source-text extraction (matching `test_llm_scoring_config_wiring.py`'s
established technique, since `paper_trading_engine.py` can't be imported directly in this test
environment) — confirms `min_kscore` actually appears in `config_overrides`, falls back to the
real `_DEFAULT_CONFIG` value rather than a hardcoded literal, and is conditional on `kscore`'s
own presence. `services/decision-engine/tests/test_hard_rejects.py` gained 5 cases (47→52):
below/at-or-above the floor, gate skipped when `min_kscore` or `kscore` itself is absent, and
the real per-style thresholds (a candidate clearing GROWTH's 48 but not SWING's 52 is blocked
under SWING's).

**Adversarial verification** — 3 separate guards sabotaged and reverted:
1. The comparison logic (`if False:`) — caught by the below-floor and per-style tests.
2. The outer `cfg.get("min_kscore") is not None` guard (`if True:`) — produced a genuine
   `KeyError: 'min_kscore'` in the absent-threshold test, confirming the guard prevents a real
   crash, not just redundant defensive code.
3. The write-side `config_overrides` line in `paper_trading_engine.py` (replaced with a bare
   comment) — confirmed all 3 new wiring tests correctly failed (2 via assertion, 1 via a real
   `ValueError` from `.index()` no longer finding the string) before reverting.

Full market-data suite (316 tests) and decision-engine suite (113 tests) green after every
revert; frontend typecheck clean (no frontend files touched).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n '"min_kscore":' /app/src/services/paper_trading_engine.py
docker exec stockai-decision-engine-1 grep -n 'min_kscore' /app/src/api/core/hard_rejects.py
```
Both should show the fix present. If a low-K-Score candidate is still approved by
`/decide/{symbol}` after confirming both, check whether the caller (e.g. `decide.tsx`) is
actually sending a `kscore` in `config_overrides` at all — the gate is a no-op without one.
