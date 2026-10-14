# Authentication used as authorization on shared-state mutations

**The bug class.** A route that changes state for EVERY user is guarded by
`get_current_username`. That dependency proves only that somebody signed in. The gateway's role
check covers the `/admin` prefix alone, so the route is open to any account. The correct guard for
a shared mutation is `common.jwt_auth.require_model_admin`. It admits a currently active admin
(the role is re-read from the database, not trusted from the token) or a service token whose `svc`
claim includes `model`. The market-data scheduler's `_service_token()` carries that scope.

**Before adding the guard, audit every internal caller.** A caller that sends no token, or a token
without the `model` scope, does not fail loudly. Its scheduled job just starts getting 403.

## Occurrences

- **DA-08 (2026-09-23), ml-prediction.** Training, tuning and `resweep_suppression` were open to any
  account. Fixed with `require_model_admin`. See `docs/audits/2026-09-24-deep-audit-remediation.md`.
- **RANK-AUTHZ (2026-10-13), ranking-engine.** `POST /rankings/refresh`, `/rankings/tune_kscore_weights`
  and `/rankings/tune_kscore_curve` were open to any account. Any user could trigger a full
  re-score of the universe or retune the K-Score behind everyone's rankings. Found during the
  copilot phase 2 inventory.
  - Callers audited: market-data `scheduler.py` (every call goes through `_post()`, which attaches
    `_service_token()`) and `admin.py`'s add-stock refresh (builds its headers from
    `_service_token()`). Both carry `svc: ["model"]`. No frontend code calls these routes.
  - Tests: `services/ranking-engine/tests/test_rank_authz.py` drives the real routes over HTTP with
    the real dependency. Unauthenticated → 401; ordinary user → 403; demoted admin with an admin
    token → 403; service token without `model` → 403; active admin → 200; scheduler token → 200.
    Plus a check that both callers still send the scoped token. Sabotage-checked: reverting
    `/refresh` to `get_current_username` turns three tests red.

**Sweep for the next occurrence:** `grep -rn "@router.post" services/*/src/api` and inspect each
POST that writes shared state, not per-user rows.
