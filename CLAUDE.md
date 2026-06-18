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
5. **Frontend:** needs rebuild: `docker compose -f docker/docker-compose.yml build frontend && docker compose -f docker/docker-compose.yml up -d frontend`
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

## Known Ongoing Limitations

- Broker commission: `commission_per_share` defaults to 0.0 (user's broker is commission-free)
- Survivorship bias in ML training data (delisted stocks not included) — requires external data source
- Walk-forward backtest deferred (2+ weeks of work)
- Forward return tracking (INT-8) not yet implemented
