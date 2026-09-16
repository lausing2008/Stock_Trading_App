# API-Gateway Proxy Route Gaps — a new router prefix that 404s at the gateway

A recurring bug class with **three independent real occurrences**, each caught in production
rather than in review.

## The shape

`services/api-gateway/src/api/proxy.py` holds `_ROUTES`, a hand-maintained
`prefix -> upstream URL` table. `_upstream()` has **no default fallback**, so any request whose
first path segment is missing from that table 404s at the gateway — no matter how completely
the backend implements the feature. The backend is fine, its tests pass, its endpoints work
when hit directly inside the container, and every request through the real domain still 404s.

## Occurrences

1. **`rl-agent`** (T247-APIGATEWAY-RLAGENT) — `rl.py` registered `APIRouter(prefix="/rl-agent")`
   on market-data. Every request 404'd; the admin-health page's RL Agent tile and the RL
   training trigger were both dead.
2. **`conditional-orders`** (BUG-PROXYGAP-CONDITIONALORDERS) — T286 shipped the whole feature
   and was never added to the table. Found only because the table was being read closely while
   wiring an unrelated route (IF-01's risk-snapshots).
3. **`options-income`** (T398, 2026-09-16) — the Options Income page's Create Portfolio modal
   returned a raw **"404 Not Found"** to the user. Same omission.

## The fix, and the test that should stop a fourth

Adding the entry is trivial. Three occurrences of one omission is a structural problem, so
2026-09-16 added `services/api-gateway/tests/test_proxy_route_coverage.py`: it scans every
service's `main.py` router registrations, resolves each imported router back to its
`APIRouter(prefix=...)` declaration, and asserts every such prefix has an entry in `_ROUTES`.

Sabotage-verified — removing the `options-income` entry again fails immediately with a message
naming the exact fix.

Deliberate non-failures the test handles:
- A router file that exists but is never wired into `main.py` is ignored (dead code, not a live
  404 risk).
- Three services (decision-engine, event-intelligence, strategy-engine) declare a bare
  `APIRouter()` with no prefix — every route there carries its own full path and the gateway
  maps to them differently. A router with no `prefix=` argument is correctly skipped, not
  flagged.
- Two guard tests assert the scan found a realistic number of routes/services, so a regex that
  silently matches nothing can't make the whole suite vacuously pass — the exact failure mode
  that let this class survive review three times.

## Diagnosing it

The signature is distinctive: the page 404s, but the container answers correctly.

```bash
# 404 through the domain but 401/200 direct = routing gap, not a backend bug
curl -s -o /dev/null -w "%{http_code}\n" https://lausing.com/api/<prefix>/<path>
ssh ... "docker exec stockai-api-gateway-1 curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/<prefix>/<path>"
```

**One caveat that cost time on 2026-09-16**: the frontend calls `/api/...` and Next.js rewrites
`/api/:path*` to the gateway. Testing `https://lausing.com/<prefix>` WITHOUT the `/api` prefix
hits Next.js's own page router and 404s for an entirely unrelated reason — which looks exactly
like the bug still being present after it has been fixed. Always test the `/api/`-prefixed URL.
