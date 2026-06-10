# Audit Checklist

Token-efficient deep audit framework. One agent per dimension, each reads only
the files listed under that dimension. Findings go to `AUDIT_FINDINGS_<date>.md`.

---

## How to Run an Audit

1. For each dimension below, spawn one agent with:
   - The "Read these files" list
   - The "Check for" bullets as search criteria
   - Instruction: write confirmed findings only (file, line, severity, fix)
2. Aggregate all dimension findings into `AUDIT_FINDINGS_<date>.md`
3. Triage: P0 (deploy blocker) → P1 (fix next session) → P2 (backlog)

Estimated token cost: ~40k per audit run (vs ~150k for a full codebase scan).

---

## Dimension 1 — Auth & Security

**Read these files:**
- `services/market-data/src/api/auth.py`
- `shared/common/jwt_auth.py`
- `shared/common/config.py`
- `services/api-gateway/src/api/proxy.py`
- `frontend/src/lib/auth.ts`

**Check for:**
- [ ] JWT secret length / entropy (should be ≥32 chars, not a dictionary word)
- [ ] Token expiry set to a reasonable value (config `jwt_expire_days`)
- [ ] All protected routes require auth (proxy `_PUBLIC_PREFIXES` is minimal)
- [ ] Logout endpoint blacklists JTI before returning
- [ ] Redis blacklist check fails-open (no exception crashes the auth path)
- [ ] Password hashing uses bcrypt (not md5/sha1)
- [ ] Admin-only endpoints have role guard, not just auth guard
- [ ] No secrets logged or returned in error messages
- [ ] CORS origins are restricted (not `*`) in production

---

## Dimension 2 — Data Integrity & DB

**Read these files:**
- `shared/db/models.py`
- `services/market-data/src/api/routes.py` (ingest + price endpoints)
- `services/market-data/src/services/base.py`
- Any recent Alembic migration files in `alembic/versions/`

**Check for:**
- [ ] All FK relationships have `ondelete` set appropriately
- [ ] No raw string SQL (use ORM or `text()` with bound params)
- [ ] Ingest functions use upsert (not blind insert) to avoid duplicates
- [ ] Timestamps stored in UTC (not local time)
- [ ] HK daily bar UTC offset is correct (2025 fix: stored at 08:00 UTC)
- [ ] `nullable=False` columns have server defaults or are always populated
- [ ] No `SELECT *` in hot paths (use explicit column lists)
- [ ] Migrations are idempotent (safe to re-run)

---

## Dimension 3 — API Contract & Error Handling

**Read these files:**
- `services/market-data/src/api/routes.py`
- `services/signal-engine/src/api/routes.py`
- `services/api-gateway/src/api/proxy.py`
- `services/api-gateway/src/api/ai_proxy.py`

**Check for:**
- [ ] All endpoints return consistent error shapes (`{"detail": "..."}`)
- [ ] 404 vs 422 vs 500 used correctly
- [ ] No bare `except Exception: pass` that silently swallows errors
- [ ] FastAPI path ordering: specific routes before parameterized ones
  (e.g. `/signals/history` before `/signals/{symbol}`)
- [ ] Pagination or limits on list endpoints (no unbounded queries)
- [ ] Request body validation uses Pydantic models (not manual parsing)
- [ ] Background tasks (scheduler) have exception handling so one failure
  doesn't kill the loop
- [ ] External API calls (AI proxy, email SMTP) have timeouts set

---

## Dimension 4 — Frontend State & UX

**Read these files:**
- `frontend/src/lib/api.ts`
- `frontend/src/pages/_app.tsx`
- `frontend/src/pages/stock/[symbol].tsx` (key sections: hooks, render)
- `frontend/src/pages/alerts.tsx`
- `frontend/src/pages/signal-filters.tsx`

**Check for:**
- [ ] SWR keys are unique and descriptive (no accidental key collisions)
- [ ] Loading and error states shown to user (not silent blank panels)
- [ ] No `useSWR` / `useEffect` hooks called after early returns (React hook order)
- [ ] `localStorage` reads are guarded with `typeof window !== 'undefined'`
- [ ] Auth token attached to all authenticated fetch calls
- [ ] No hardcoded API URLs (use `/api/...` proxy prefix)
- [ ] Numeric formatting consistent (prices: 2dp, percentages: 1dp)
- [ ] No console.log left in production code
- [ ] `key` props on mapped lists (no missing keys causing reconciliation bugs)
- [ ] Forms have basic validation before submitting

---

## Dimension 5 — Scheduler & Background Jobs

**Read these files:**
- `services/market-data/src/services/scheduler.py`
- `services/market-data/src/services/email_alerts.py` (if exists, else signal_alerts.py)

**Check for:**
- [ ] Each scheduled job has a try/except at the top level
- [ ] Jobs that modify DB use a session and commit/rollback correctly
- [ ] HK holiday guard in place for HK data refresh jobs
- [ ] Price/signal refresh jobs are idempotent (safe to run twice)
- [ ] Email alert checker rate-limits sends (no spam on repeated signals)
- [ ] Alert checker deduplicates — won't re-send for same signal bar
- [ ] Scheduler intervals match documented schedule in memory (`project_scheduler_schedule.md`)
- [ ] No UTC/local time confusion in cron trigger times

---

## Dimension 6 — ML & Signal Engine

**Read these files:**
- `services/signal-engine/src/api/routes.py`
- `services/signal-engine/src/services/` (model training, prediction)
- `services/market-data/src/services/ml_signals.py` (if exists)

**Check for:**
- [ ] Feature list consistent between training and inference (no train/serve skew)
- [ ] Model loaded once at startup (not re-loaded per request)
- [ ] Missing features handled gracefully (not silently NaN → wrong prediction)
- [ ] Optuna trials use deterministic seed (reproducible tuning)
- [ ] CV metric reported is out-of-fold (not train set accuracy)
- [ ] Signal history endpoint returns data ordered ascending (chart direction)
- [ ] `days_active` counts consecutive bars correctly (resets on gap)
- [ ] Suppressed signals still appear in filter page with `suppressed=true` flag

---

## Dimension 7 — Docker & Deployment

**Read these files:**
- `docker/docker-compose.prod.yml`
- `docker/docker-compose.yml`
- `services/*/Dockerfile` (check each service)
- `.github/workflows/` (if CI exists)

**Check for:**
- [ ] All services have `restart: unless-stopped`
- [ ] Health checks defined (so compose knows when a service is ready)
- [ ] `depends_on` uses `condition: service_healthy` where order matters
- [ ] No secrets in Dockerfiles or compose files (use env_file)
- [ ] Volumes defined for DB data (not stored in container layer)
- [ ] Frontend build uses `.env.production` with `API_GATEWAY_URL=http://api-gateway:8000`
- [ ] Images pinned to specific versions (not `:latest` in prod)
- [ ] Redis service present and reachable by market-data and api-gateway

---

## Severity Definitions

| Level | Meaning | Action |
|-------|---------|--------|
| P0 | Auth bypass, data loss, crash in critical path | Fix before deploy |
| P1 | Wrong behavior visible to user, security hardening | Fix next session |
| P2 | Code quality, performance, minor UX | Backlog |
