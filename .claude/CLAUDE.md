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
