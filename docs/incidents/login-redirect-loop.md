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

