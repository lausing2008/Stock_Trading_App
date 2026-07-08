# API Gateway — Engineering Agent Behavior

How to behave when working on `services/api-gateway/`. Every user request passes through here —
changes have the highest visibility of any service.

---

## Mindset for This Service

The gateway is the trust boundary. It is the only service that talks to the internet directly.
Changes to auth validation (`_require_auth()`), route mapping, or 401 handling affect every user
and every request. Test carefully; deploy with extra caution.

**The login redirect loop lives here and in `api.ts`.** If you change how 401s are returned or
how auth headers are handled, re-read the login redirect loop documentation in CLAUDE.md before
deploying. One wrong change recreates a loop that breaks all logins.

---

## Working on Auth (`_require_auth()`)

Never change the 401 response shape without updating `api.ts` on the frontend. The frontend
parser expects a specific response format — a schema change breaks auth handling silently.

The gateway must NOT redirect on 401. It returns 401 to the frontend and lets the frontend
decide what to do (clear token if expired, throw error if valid). Redirection at the gateway
level causes loops.

```
Gateway: 401 → Frontend api.ts: check token expiry
    ├── token expired → clear localStorage, redirect to /login
    └── token valid → throw 'Unauthorized', don't redirect
```

---

## Working on Route Mapping

When adding a new service or a new route:
1. Add the route prefix to `proxy.py`'s routing table
2. Decide: authenticated or public? Default to authenticated.
3. Add the internal hostname (Docker service name) to the mapping
4. Verify the downstream service is registered in `docker-compose.yml`

Do not expose internal-only endpoints (like `/research/{symbol}/trigger`) through the gateway.
Internal endpoints are reachable only via Docker network DNS, not through the gateway.

---

## Verifying Gateway Health

```bash
# Gateway responding
curl -s https://lausing.com/health

# Deep health (all downstream services)
curl -s https://lausing.com/health/deep | python3 -m json.tool

# Verify JWT validation is working
curl -s -H "Authorization: Bearer invalid_token" https://lausing.com/stocks | python3 -m json.tool
# Should return 401

# Verify a valid token passes through
TOKEN=$(curl -s -X POST https://lausing.com/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"lausing","password":"<ask user>"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" https://lausing.com/stocks | python3 -m json.tool | head -20
```

---

## Deployment

The gateway is the most sensitive service to redeploy. After any gateway change:
1. Deploy and restart
2. Verify `/health/deep` shows all services healthy
3. Test login end-to-end
4. Test a protected endpoint

```bash
ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71 \
  "git -C /home/ec2-user/Stock_Trading_App pull origin prod && \
   docker cp /home/ec2-user/Stock_Trading_App/services/api-gateway/src/api/<file> \
   stockai-api-gateway-1:/app/src/api/<file> && \
   docker restart stockai-api-gateway-1"
```
