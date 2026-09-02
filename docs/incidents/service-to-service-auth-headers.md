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


## Recurring Issue: BUG-BROKERROUTE-STALEAUTH — broker.py Never Detected Expired E*Trade Tokens (Fixed 2026-07-28)

**Symptom:** the E*Trade Transactions dashboard (`/etrade-transactions`) showed "Failed to
load" with 0 of 0 orders. Direct query confirmed `GET /broker/connections/{id}/orders` returned
a generic `502` — `"E*Trade list_orders failed: 401 {"Error":{"message":
"oauth_problem=token_expired"}}"` — while `broker_connections.is_authorized` still showed
`true` and no `token_rejected`/`reauth`/`mark_broker_unauthorized` log lines existed anywhere.

**Root cause:** `T257-ETRADE-PROD-SYSTEMATIC` (2026-07-17) built shared token-rejection
detection (`_is_token_rejected_error()`/`_mark_broker_unauthorized_and_notify()` in
`scheduler.py`) and wired it into the paper-trading engine's broker call sites
(`_place_broker_entry`/`_place_broker_exit`/`poll_broker_order_fills`) plus a daily 08:30 ET
health check and an intraday keepalive cron — but `broker.py`'s
`GET /broker/connections/{id}/account` and `.../orders` (both added LATER, for the Load
Balance button and the E*Trade Transactions dashboard) were never wired into this same
detection. A genuinely expired token there just silently 502'd with the DB stuck claiming
`is_authorized=True`, until the next daily health check caught it — up to a full day later.

**Fix applied:** both routes' exception handlers now call the SAME shared
`_is_token_rejected_error(exc)` check; on a match, `_mark_broker_unauthorized_and_notify(
session, conn)` flips `is_authorized=False`, mints a fresh OAuth URL, and emails a re-auth
link, and the route returns `401` with a clear message instead of a bare `502`. Both imports
are lazy (inside the function bodies), matching `scheduler.py`'s own existing reverse-direction
lazy import of `broker.py` — avoids a circular import.

**Tests**: `services/market-data/tests/test_broker_route_staleauth_detection.py` (7 cases,
source-text extraction — `broker.py` needs a real DB session to import directly) — confirm
both routes check for a token-rejected error, call the shared mark-unauthorized-and-notify
helper, raise `401` (not `502`) on a match, and that `get_order_history`'s pre-existing
`NotImplementedError` → `501` branch (for brokers with no real order-history API) is
untouched by this fix. Adversarially verified: reverted `get_account_info`'s detection block
and confirmed its 3 dedicated tests failed correctly before restoring.

**Live verification, both before and after deploy**: before the fix,
`GET /broker/connections/1/orders?status=all` returned `502` with `is_authorized` still `true`
and zero relevant log lines. After deploy, the identical request returned `401` with the
re-auth message, `is_authorized` flipped to `false` in Postgres, and
`broker.auth_expired_notified` appeared in the logs — confirming the fix closes the loop
end-to-end, not just that it compiles.

**Standing note**: this does NOT eliminate E*Trade's own daily midnight-ET token expiry (an
OAuth 1.0a platform constraint, not a bug) — it makes the failure visible and actionable
(a clear 401 + automatic re-auth email) instead of a silent, misleading 502 with a stale
"authorized" status. A user hitting this will still need to re-authorize via Settings once a
token has genuinely expired.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_is_token_rejected_error(exc)" /app/src/api/broker.py
# Should show 2 matches — one in get_account_info, one in get_order_history.

docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT id, name, is_authorized FROM broker_connections;"
docker logs stockai-market-data-1 --since 1h | grep 'auth_expired_notified\|auth_notify_failed'
```

---


## Recurring Issue: BUG-RISKSNAP-NOSERVICETOKEN — risk_snapshots.py's Outbound Calls to portfolio-optimizer Had No Auth Header (Found + Fixed 2026-08-19)

**Found via live verification immediately after IF-01's first deploy** — not by a local test,
matching this repo's own standing discipline that "the tests all pass" and "it works against
real production data" are different bars, and the second one is the one that actually counts.

**Symptom**: `POST /risk-snapshots/var` against a real, currently-authenticated user (real
`UserPosition` rows for user_id=1) returned `502 portfolio-optimizer risk call failed: Client
error '401 Unauthorized'`.

**Root cause**: `portfolio_risk()`/`portfolio_stress_test()` (portfolio-optimizer) both require
`Depends(get_current_username)` (`shared/common/jwt_auth.py`) — a real, correctly-enforced
auth gate. `risk_snapshots.py`'s two outbound `httpx.Client().get(...)` calls to those endpoints
never sent an `Authorization` header at all — a plain service-to-service call against an
auth-protected endpoint, exactly the class of gap `INT-7`/every `_service_token()` site
elsewhere in this file already exists to close, just missed here because this specific new
module was written fresh rather than copied from an existing cross-service-call site.

**Fix applied**: added a `_service_token()` helper to `risk_snapshots.py`, matching
`scheduler.py`'s own established pattern exactly (same 365-day expiry, same 7-day-before-expiry
cache refresh, same `jose`-based HS256 signing against `_settings.jwt_secret`) — `sub:
"risk-snapshots"` rather than `"scheduler"`, since this is a genuinely different caller
identity. Threaded `headers={"Authorization": f"Bearer {_service_token()}"}` into both outbound
`c.get(...)` calls. No changes needed on portfolio-optimizer's side — its `get_current_username`
dependency only validates the JWT signature + `jti` blacklist, no DB user lookup, so any
correctly-signed service token passes.

**Two new regression-guard tests added, and adversarially verified against the EXACT original
bug** (removing both `headers=` lines and confirming the tests fail correctly before
restoring) — neither of the FIRST-PASS tests (`test_save_var_snapshot_persists_a_real_row` etc.,
which mock the httpx response and only assert on the RESULT) would have caught this on their
own, since a fake, already-mocked 200 response never exercises whether a real header was sent.
The new tests capture the actual `kwargs` passed to the fake client's `.get()` call and assert
`Authorization` is present and non-empty.

**Live-verified end-to-end against real production data, both before and after the fix**:
before, `POST /risk-snapshots/var` for user_id=1 (real positions: NU, SPCX, KGS, SCHD, RGTI,
NOK, ZS, NVDA, SOUN, ARMK) returned the 502 above. After redeploying just this one file,
the identical request returned a real, complete VaR/CVaR payload (`var_95_1d_pct: 1.93`,
`cvar_99_10d_pct: 7.65`, etc.), and `SELECT * FROM portfolio_risk_metrics` confirmed the row
actually landed in production Postgres. `POST /risk-snapshots/stress-test?scenario=covid_2020`
and `GET /risk-snapshots/var/history` were also live-verified working end-to-end in the same
pass.

**What to check if this recurs (or a similar gap appears in a future cross-service call)**:
```bash
docker exec stockai-market-data-1 grep -n "_service_token\|Authorization.*Bearer" /app/src/api/risk_snapshots.py
# Should show the helper AND both call sites using it.

# Live-check directly (needs a real user JWT with >=2 real UserPosition rows):
docker exec stockai-market-data-1 curl -s -X POST -H "Authorization: Bearer <token>" \
  'http://localhost:8001/risk-snapshots/var'
# A 502 "risk call failed: ... 401 ..." here means this exact gap has recurred.
```

**Design invariant reinforced**: ANY new module making a service-to-service HTTP call against
an endpoint gated by `Depends(get_current_username)`/`Depends(get_current_user)` must include a
real Authorization header — grep for `Depends(get_current_username)` in the target service and
confirm the calling code's own `httpx`/`c.get`/`c.post` calls include `headers={"Authorization":
...}` BEFORE considering a new cross-service integration done, not after a live 401 surfaces it.

---

