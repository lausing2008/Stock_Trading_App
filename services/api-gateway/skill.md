# API Gateway — Domain Knowledge & Coding Standards

Single entry point for all frontend traffic. Validates JWTs, routes requests to the correct
service, aggregates responses, and caches. All external HTTP traffic passes through here.

---

## What This Service Does

| Responsibility | Key file(s) |
|---|---|
| Transparent reverse proxy | `api/proxy.py` (~169 lines) |
| JWT validation + auth enforcement | `api/proxy.py` `_require_auth()` |
| Claude AI proxy (chat) | `api/ai_proxy.py` (~166 lines) |
| Cross-service response aggregation | `api/aggregate.py` (~60 lines) |
| Health check endpoints | `api/health.py` (~54 lines) |

---

## Route Mapping (`proxy.py`)

| URL prefix | Routed to | Auth required |
|---|---|---|
| `/stocks` | market-data:8001 | Yes (most endpoints) |
| `/auth` | market-data:8001 | No (login/register) |
| `/ta` | technical-analysis:8009 | Yes |
| `/ml` | ml-prediction:8003 | Yes |
| `/rankings` | ranking-engine:8007 | Yes |
| `/signals` | signal-engine:8005 | Mixed (see below) |
| `/strategies`, `/backtest` | strategy-engine:8010 | Yes |
| `/portfolio` | portfolio-optimizer:8011 | Yes |
| `/research` | research-engine:8008 | Yes |
| `/decide` | decision-engine:8006 | Yes |
| `/events`, `/catalyst` | event-intelligence:8012 | Yes |
| `/ai` | ai_proxy (Claude API) | Yes |
| `/health` | health.py (local) | No |

### Signal endpoint auth exceptions
- `GET /signals/{symbol}` — no auth (public stock detail page, auto-persist)
- `GET /signals/{symbol}/history` — no auth
- `POST /signals/refresh` — YES auth required (JWT mandatory)

---

## JWT Validation (`_require_auth()`)

The gateway validates every request:
1. Extract `Authorization: Bearer <token>` header
2. Verify signature with shared `jwt_secret` using `shared/common/jwt_auth.py`
3. Check Redis blacklist (`auth:blacklist:{jti}`)
4. On valid token: forward request with original headers to downstream service
5. On invalid/expired: return 401

**401 response behavior at the gateway:** Returns 401 to the frontend. The frontend's `api.ts`
`request()` function then decides whether to clear the JWT (only if locally expired) or just
throw `'Unauthorized'` (if locally valid but server rejected). Never redirect at the gateway level.

---

## Health Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Shallow — gateway is up |
| `GET /health/deep` | Deep — checks all downstream services |
| `GET /health/system` | System-level metrics |

The `/health/deep` endpoint is what the admin health dashboard (`admin-health.tsx`) uses.
A 404 on `/health/system` from curl is expected if hitting the wrong path — use `/health/deep`.

---

## Aggregate Endpoint

`api/aggregate.py` handles cross-service aggregation for pages that need data from multiple
services in a single call (e.g., stock detail page overview: prices + signals + TA + research).
Calls are made in parallel; missing data from one service doesn't block the others.

---

## Common Issues

**All services returning 401 unexpectedly:** Check the gateway's JWT verification first.
If the gateway is rejecting valid tokens, all downstream services appear broken.

**A single service returning 404:** The gateway is proxying correctly but the downstream
service doesn't have that route. Check the downstream service's router registration.

**Slow responses:** The gateway is synchronous per request. A slow downstream (e.g., research
engine generating a fresh Claude report) blocks the entire request. Check response caching.
