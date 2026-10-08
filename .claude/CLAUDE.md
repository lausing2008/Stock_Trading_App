# CLAUDE.md — Persistent Session Notes for Claude Code

This file is read at the start of every session. It stays deliberately small — the full,
230-entry dated history of this project (bug postmortems, shipped features, audit reports)
lives in `docs/incidents/`, `docs/features/`, and `docs/audits/` instead, indexed below. Read a
topic file on demand, when a task actually touches that area — never assume you need to read
all of them.

**T322-CLAUDE-MD-CORE-SPLIT (2026-09-02):** this file used to be a single, continuously-growing
20,077-line / ~347k-token changelog, re-read in full on every turn AND re-paid for in full on
every prompt-cache rebuild (measured: 21 rebuilds in one session cost ~7.3M premium-billed
cache-creation tokens just for this file's own unchanged content). Split into this small core
(target <5k tokens) plus the topic files indexed below, with zero content loss — every line of
the original file was mechanically assigned to exactly one target file and the split was
verified byte-for-byte reversible before being applied. See `docs/audits/` for the token-usage
audit that motivated this.

---

## Writing convention — READ THIS BEFORE ADDING A NEW ENTRY

**Do not append a new dated entry directly into this file.** This is the discipline that keeps
this split from regrowing back to 347k tokens within a few months:

- A new entry for an **existing recurring bug class** → append it to that bug class's own file
  under `docs/incidents/<bug-class>.md` (see the index below). If a new bug's title doesn't
  cleanly match an existing incidents file, check the file's own content first — several bug
  classes recur under different symptom names (e.g. every "jose/redis/feedparser missing from a
  container" incident lives in one file, `dependency-missing-from-container.md`).
- A new entry for a **shipped feature** (built, tested, deployed) → append it to that feature
  area's own file under `docs/features/<area>.md`, or create a new topic file if it's a genuinely
  new area, and add ONE line to this file's index.
- A new **dated audit/review/session-report** (a bounded investigation, not a bug or a shipped
  feature) → create `docs/audits/<date>-<short-name>.md`, and add ONE line to this file's index.
- **Only the one-line index pointer below ever lands in this file.** The full entry, however long,
  goes in the topic file.
- **HARD LIMIT: an index entry is at most ~300 characters** — roughly two lines. This number
  exists because "one line" without a number does not hold: entries grew into paragraphs
  gradually, each addition looking reasonable on its own, until the index was **86% of this
  file** (~18,577 of ~21,596 tokens) with a single bullet at **1,831 tokens**. `T382-CLAUDEMD-REINDEX`
  (2026-09-10) relocated 33 oversized entries into their topic files and cut the file from
  ~21,596 to ~8,300 tokens (**-62%**), which is re-paid on every prompt-cache rebuild.
  **An entry's only job is to let a reader decide whether to open the file** — findings,
  measurements and counterexamples belong in the topic file, not here. Before adding to an
  existing entry, check its length; if the addition would push it over, put the detail in the
  topic file and leave the pointer alone.

---

## Deployment Pattern

**Standard deployment (git-based, preferred):**
1. Commit changes locally on `prod` branch
2. `git push origin prod`
3. SSH to EC2: `ssh -i ~/Documents/Stock_AI/lausing.pem ec2-user@18.205.121.71`
4. On EC2: `cd /home/ec2-user/Stock_Trading_App && git pull origin prod`
   - If there are local changes on EC2 blocking the pull: `git stash && git pull origin prod`
   - If there are untracked files blocking: move them to /tmp first, then pull
5. **Frontend:** `bash scripts/deploy_frontend.sh` on EC2.

   **Do NOT paste the build/recreate commands by hand.** Both deploy scripts take a single
   host-wide lock and a second deployment REFUSES to start (exit 75) while another owns the
   host — `scripts/deploy_lock.sh`, added after two concurrent deploys removed the frontend
   container and took the public site down on 2026-10-03. A pasted command sits outside that
   guard, which is exactly how the race happened. `deploy_lock_status` answers "did it finish?"
   The script also clears an orphaned `*_stockai-frontend-1` container left by an interrupted
   recreate, and waits for health rather than reporting success on `docker compose` exiting 0.
   **WARNING:** `docker compose build frontend` (i.e. via `docker compose`, not `docker build`
   directly) uses BuildKit which silently serves cached layers even with `--no-cache`, producing a
   stale image. Always invoke `docker build` directly with `DOCKER_BUILDKIT=0` for frontend builds
   to guarantee the latest source is compiled — this is the part that matters, not `--no-cache`.
6. **Backend services:** `docker cp` changed files to `/app/shared/` (for shared/) and `/app/src/` (for service-specific files), then `docker restart <container>`
   - **IMPORTANT:** `shared/db/models.py` and `shared/common/` must be copied to `/app/shared/db/` and `/app/shared/common/` (NOT `/app/src/db/`!)
   - Use: `docker cp shared/db/__init__.py <container>:/app/shared/db/__init__.py`
   - `docker cp` is a SESSION-SCOPED HOTFIX, not a durable deploy — any container recreation
     (a single `--force-recreate`, a full `docker compose down/up`, or an unplanned instance
     reboot) reverts it. See `docs/incidents/docker-deploy-staleness.md` before assuming a past
     "deployed" claim is still true, and sweep `shared/db/`/`shared/common/` across ALL 11
     backend containers, not just the one that needs the change today.

Container names: `stockai-market-data-1`, `stockai-signal-engine-1`, `stockai-frontend-1`,
`stockai-api-gateway-1`, `stockai-ml-prediction-1`, `stockai-research-engine-1`,
`stockai-ranking-engine-1`, `stockai-strategy-engine-1`, `stockai-technical-analysis-1`,
`stockai-portfolio-optimizer-1`, `stockai-event-intelligence-1`, `stockai-news-intelligence-1`

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
- `UserRole` (ADMIN/USER) gates admin-only operations; `UserTier` (BASIC/ADVANCED, added
  2026-09-01) is a separate axis gating which trading FEATURES a regular user sees (e.g. the
  Options Game Plan) — see `docs/features/` for the feature this was built for. Both are
  baked into the JWT itself for synchronous frontend gating, with the same accepted staleness
  trade-off (a role/tier change needs a fresh login/token to take effect on the frontend) —
  the BACKEND's own authorization always re-checks the live DB row, never the JWT's claim.

### Connectivity Audit Invariants (verified 2026-06-17, still binding)

1. **Any endpoint that uses `Depends(get_current_username)` must receive an Authorization header**
   when called from another service. All scheduler → service calls use `_service_token()`. Add the
   same pattern to any new service-to-service call against an auth-protected endpoint — see
   `docs/incidents/service-to-service-auth-headers.md` for the recurring bug class this guards
   against.
2. **The `/research/{symbol}/trigger` endpoint is intentionally unauthenticated** — do not add
   auth to it. It is only reachable from the internal Docker network.
3. **The `/stocks/conviction` endpoint is intentionally open** — it reads from Redis only (no
   sensitive data), and signal-engine calls it without auth.

---

## System Port Map (verified 2026-07-01 from Dockerfiles)

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
| news-intelligence | 8011 |

**Note:** Only api-gateway (8000) is exposed externally. All others are Docker-internal only.
Nginx proxies `lausing.com` → `localhost:8000`.

---

## Known Ongoing Limitations

- Broker commission: `commission_per_share` defaults to 0.0 (user's broker is commission-free)
- Survivorship bias in ML training data (delisted stocks not included) — requires external data source
- Walk-forward backtest deferred (2+ weeks of work)
- Forward return tracking (INT-8) not yet implemented
- No real margin/leverage concept exists anywhere in the trading engine — this is a cash-only
  paper-trading platform by design (confirmed via the 2026-09-01 Market Pressure Engine scoping,
  `docs/audits/2026-09-01-market-pressure-engine-scoping.md`)

---

## Process Note: Background Agents Can Drift Scope — Re-Confirm Before Deploying

**Observed 2026-07-14**, while re-deriving 6 audit findings lost to an earlier spend-limit
interruption. The user's instruction was narrow: recover those 6 specific candidates. The
background agents dispatched for this instead ran an open-ended fresh bug hunt across untouched
services — a reasonable-sounding interpretation, but broader than what was actually asked, and
one agent got stuck spawning further sub-agents and reporting a non-answer ("I'll wait for the
other agents...") instead of concrete findings.

Separately, once 2 of 3 resulting findings had been explicitly approved for fixing, a 3rd finding
arrived from a still-running background agent AFTER that approval — and very nearly got bundled
into the same deploy as the 2 approved ones, which would have shipped an unapproved change to
production under cover of an approved one.

**What to check going forward when using background/multi-agent workflows on this repo:**
1. If a background agent's report describes doing something broader than what was literally
   asked, treat that extra output as candidate findings requiring their own explicit go-ahead —
   not as pre-approved just because they arrived attached to a task that WAS approved.
2. Before any deploy, re-list exactly which changes are being shipped and cross-check that list
   against what was actually approved in the conversation.
3. If an agent's own final message describes waiting on other agents or otherwise doesn't
   contain a real, substantive answer, treat that as a failed/incomplete run and resume or
   re-dispatch it directly rather than assuming "no findings" or moving on.

**Also see `docs/AUDIT_DOMAIN_SERIES_TEMPLATE.md`** for the extracted methodology of the 6-part
sequential domain-audit pattern (grounding-before-dispatch, the required subagent prompt shape,
independent verification of claims before recording them) — use it whenever a future request is
shaped like "audit domain X across the whole platform, one at a time, with my approval between
each." Distinct from `docs/AUDIT_FINDINGS_TEMPLATE.md`, a lighter checklist for reviewing a
recent diff.

---

## Topic File Index

Read a file below only when a task actually touches that area. Every dated entry that used to
live in this file's own body is preserved verbatim in exactly one of these files.

### Incidents — recurring bug classes (`docs/incidents/`)

- **`docs/incidents/alert-email-spam-and-suppression.md`** — Signal Alert Email Spam — BUY→HOLD→BUY Oscillation; Alert Email Suppression — market:refresh_failed Flag (BUG-8); BUG-MORNINGDIGEST-SENDLOOP — Same Unguarded …

- **`docs/incidents/auto-research-trigger-gating.md`** — A SECOND, Completely Independent Auto-Research Trigger — Never Gated By `auto_research_enabled` At All (Fixed 2026-07-29)
- **`docs/incidents/backtest-wall-clock-and-lookahead-bugs.md`** — BUG233-BACKTESTWALLCLOCK — Phase 2a/2b Backtest Harness Always Returned Zero Trades Unless Run During Real Live Market Hours (Fixed 2026-07-22)
- **`docs/incidents/chart-drawing-bugs.md`** — BUG-TRENDLINE-STALEBARINDEX — Trendline Drawings Broke Across Timeframe Switches (Fixed 2026-07-21)
- **`docs/incidents/dead-code-and-shadowing-bugs.md`** — A Redundant Local `from datetime import datetime` Made Two Hard Rejects Dead Code (BUG232-DEADCODE)
- **`docs/incidents/decide-endpoint-crash-bugs.md`** — BUG-DECIDE-GAMEPLAN-STYLEFLOAT — decision-engine Crashed on Every Real Game-Plan-Bearing BUY Candidate, Silently Falling Back to the DE-Outage Scorer (Fixed ...
- **`docs/incidents/delisted-stock-generation-blind.md`** — BUG-DELISTED-GENERATION-BLIND — 8 More Generation/Scan Paths Never Consulted `Stock.delisted` (Fixed 2026-07-30); BUG-DELISTED-GENERATION-BLIND — 2 More Sibl...
- **`docs/incidents/dependency-missing-from-container.md`** — Signal Refresh 401 — jose Library Missing from signal-engine; tune_all 401 — jose Library Missing from ml-prediction; Stale Rankings — jose Missing from rank...
- **`docs/features/2026-10-08-outcome-measurement-and-adjustment-evidence.md`** — AUD-EVENTINTEL-BLOCKEDLOOP (2026-10-08): a daily 07:30 sync ran thousands of upserts ON the event loop, so /health timed out and the container went unhealthy every day at CPU 0.02%. Logs were clean [event loop, health]
- **`docs/incidents/db-connection-pool-exhaustion.md`** — AUD-CONNPOOL-NESTEDSESSION (2026-09-22): PT-H08's own scan-log write opened a 2nd DB connection per "no entry" cycle while the caller's was still held — exhausted the pool, cascaded platform-wide. Root-caused live via py-spy. Same-day defensive audit found + fixed 2 more instances: `_should_enter()`, `_compute_hk_breadth()` [production incident, connection pool]
- **`docs/incidents/design-doc-math-verification.md`** — BUG-SA33-UNREACHABLETHRESHOLD — A Design Doc's Own Fix Was Mathematically Unable to Achieve Its Stated Goal (Fixed 2026-07-27)
- **`docs/incidents/docker-deploy-staleness.md`** — AUD-DEPLOYDRIFT-T370REVERT (2026-09-10) — found by RUNNING `scripts/check_deploy_drift.sh` while answering "everything looks good now?" …

- **`docs/incidents/docker-deploy-staleness.md`** — AUD-DEPLOY-GATEWAYREMOVED (2026-10-08): a multi-service deploy REMOVED api-gateway twice (depends_on service_healthy, no --no-deps). Site stayed 200 via nginx while every /api was 500 [deploy, outage]
- **`docs/incidents/docker-deploy-staleness.md`** — AUD-REGISTER-POSTBUILD-DRIFT (2026-10-02): editing ANY file under `shared/` after the rebuild — a register note, a docstring — drifts all 12. shared/ is one versioned unit; finish it before rebuilding [drift, shared]
- **`docs/incidents/docker-deploy-staleness.md`** — AUD-OBS-SESSIONBOUNDS (2026-10-08): the walk admitted a session at 12:00 UTC — before the US OPEN — and skipped the cutoff's own day. Adjustment was asserted, not checked: only 1.61% of daily bars carry adj_close [sessions, adjustment]
- **`docs/incidents/docker-deploy-staleness.md`** — AUD-OBS-RESOLVERVERSION (2026-10-08): `create_all()` skips columns AND constraints on an existing table. Behind that, a corrected resolver was refused by its own "never rewritten" rule, so the rerun changed nothing [create_all, supersession]
- **`docs/incidents/docker-deploy-staleness.md`** — Adding a Column to an EXISTING Table Doesn't Auto-Apply — `create_all()` Only Creates Missing Tables; Local Dev Containers Run Stale `shared/db/` — Attribute...
- **`docs/incidents/ec2-disk-and-frontend-builds.md`** — EC2 Disk Fills Up from Dangling Docker Images; Slow Frontend Builds (24–47 min) — `--no-cache` Was Unnecessary
- **`docs/incidents/ebs-io-credit-exhaustion.md`** — INCIDENT 2026-09-10: a frontend rebuild made the whole instance unreachable for ~50 min. NOT network, NOT OOM (`journalctl -b -1` had zero oom-kills): EBS I/O credit exhaustion …
- **`docs/incidents/ebs-io-credit-exhaustion.md`** — T382-CLAUDEMD-REINDEX (2026-09-10) — this file cut 21.6k→8.7k tokens, 0 of 806 claims lost. Entries were NOT duplicates of their topic files, so relocate-then-verify before trimming [CLAUDE.md, tokens]

- **`docs/AWS_INFRASTRUCTURE_RUNBOOK.md`** — t3 credit exhaustion caused 5 reboots (surplus pinned at the 576 cap -> throttled to 0.4 vCPU DESPITE `unlimited` mode); resized to m7i-flex.large 2026-09-17. Also the ECS webhook stop/restart runbook and what still bills at zero [EC2, ECS, cost]
- **`docs/incidents/ec2-reboot-and-tls-cert-incidents.md`** — INCIDENT 2026-08-05: Full EC2 Reboot Reverted signal-engine to a PRE-SPLIT Image — SA-33 No Longer Live; INCIDENT 2026-08-05 (RESOLVED): TLS Certificate Expi...
- **`docs/incidents/gateway-proxy-route-gaps.md`** — a new APIRouter prefix absent from proxy.py's `_ROUTES` 404s at the gateway however complete the backend is. THREE real occurrences (rl-agent, conditional-orders, options-income). Regression test now scans every service [404, api-gateway]
- **`docs/incidents/ci-failure-masking.md`** — two halves of a FAKE test signal: `exit 1` in a subshell made `make test` exit 0 over a red suite (T400); a test asserting on source TEXT passed while settlement raised on every run (A17). Sabotage-check both [make, CI, assertions]
- **`docs/incidents/ci-failure-masking.md`** — AUD-FRONTEND-SHIPPED-RED (2026-10-06): `make test` runs BACKEND suites only, so the vitest suite sat red on prod for days — and 2 of the 3 were real: tiers 419-422 rendered nothing. Use `make test-all` [vitest, make]
- **`docs/incidents/ci-failure-masking.md`** — AUD-T401-SHIPPED-RED (2026-10-02): a commit shipped to `prod` with the repo-wide T401 ratchet red, because it was validated with ONE service's pytest. Test-file changes need `make test`; name which suite you ran [make, CI, T401]
- **`docs/incidents/scheduler-misfire-data-gaps.md`** — AUD-T398-MISFIREGAP (2026-09-16): a job reporting `ok` while its data is 5 days stale. `misfire_grace_time=60` makes APScheduler DISCARD a job a brief outage delayed; UW history is a rolling window so the day becomes uncapturable [OPTHIST]
- **`docs/incidents/self-tuning-job-performance-bugs.md`** — AUD-MINRR-STYLEBLIND (2026-09-19): style axis instead of market. **CORRECTED same day** — calibrated min_rr_ratio was never the real base floor for any portfolio; see entry's own correction [min_rr_ratio, calibration]
- **`docs/audits/2026-09-19-paper-trading-horizon-audit-review.md`** — re-derived an external audit's PT-H01: every portfolio hardcodes min_rr_ratio=2.0, so calibration never reaches the real gate. Also confirms `_monitor_positions()` missed the 2026-09-07 config-merge fix [min_rr_ratio]
- **`docs/incidents/external-data-source-liveness.md`** — T404 (2026-09-18): Yahoo's OPTIONS endpoint returned empty for EVERY symbol 3 days; `history()` still worked. All 5 chain consumers moved to Unusual Whales (+ real greeks). An empty result must say WHICH nothing [yfinance, UW]
- **`docs/incidents/external-data-source-liveness.md`** — AUD-UWCAL-NONUS422 (2026-09-10) — the earnings calendar took 61s and the page showed "Failed to load events": it returned a VALID 174-event payload, just past every timeout … [Alpha Vantage, EDGAR, HKEX, Polygon, Redis, holiday, yfinance]

- **`docs/incidents/external-data-source-liveness.md`** — Congress Trading Data Silently Empty — Free Source Domains Permanently Dead; "It's Reachable" ≠ "It's Current" — Always Check Last-Modified, Not Just HTTP 200 … [Alpha Vantage, EDGAR, HKEX, Polygon, Redis, holiday, yfinance]

- **`docs/incidents/float-noise-variance-epsilon.md`** — AUD292-SHARPE-VAREPS — paper_portfolio.py's Sharpe/Sortino Had the Exact Float-Noise-Explosion Bug strategy-engine's Own T237-SE1 Fix Already Found and Guard...
- **`docs/incidents/game-plan-build-failures.md`** — AUD-GAMEPLAN-NONERECOMMENDATION — `_build_game_plan()` Crashed on Every ETF, Silently Dropping the Game Plan From the Signal Alert Email (Fixed 2026-09-04) …

- **`docs/incidents/hk-connect-logging-typeerror.md`** — hk_connect_flows Logging TypeError (BUG-9)
- **`docs/incidents/improvements-tracker-bugs.md`** — Improvements Page Not Showing New Tiers; BUG-IMPROVEMENTSPAGE-STALESTATUS — Improvements Tracker's "Done" Count Could Get Stuck Forever (Fixed 2026-07-21)
- **`docs/incidents/login-redirect-loop.md`** — Login Redirect Loop After Deployment
- **`docs/incidents/market-hours-gating-bugs.md`** — BUG-VOLANOM-STALEMARKET — Volume-Anomaly Alert Fired on a Closed Market's Frozen Daily Volume (Fixed 2026-07-21) … [HKEX, holiday]

- **`docs/incidents/ml-training-degenerate-slices.md`** — AUD-MLCV-SINGLECLASSFOLD — a single-class TRAINING slice made sklearn's `predict_proba` return shape `(n,1)`, so `[:, 1]` killed the ENTIRE `train_model()` call …

- **`docs/incidents/market-pulse-dashboard-bugs.md`** — Market Pulse Dashboard's "Top Movers" Could Go Entirely One-Sided (Fixed 2026-08-25); Market Pulse Dashboard's Top Movers/Sector Heat Map Silently Mixed HK S...
- **`docs/incidents/research-report-network-and-persistence.md`** — Research Generation "NetworkError" in Browser Despite Server Success; Research Reports Vanished on Every research-engine Restart — No DB Persistence At All (...
- **`docs/incidents/router-ordering-catchall-shadowing.md`** — BUG233-ROUTERORDER — Catch-All `/{symbol}` Route Silently Shadowed Literal Paths From Sibling Routers (Fixed 2026-07-22)
- **`docs/incidents/self-tuning-job-performance-bugs.md`** — BUG-WEEKLYREFRESH-HEAVYSWEEP-TIMEOUT — Heavy Weekly Sweeps Were Timing Out And Silently Truncating the Rest of Sunday's Tuning Chain (Fixed 2026-08-31); BUG …

- **`docs/incidents/service-to-service-auth-headers.md`** — INT-7 Signal-Engine Research Divergence — Missing Auth Header; BUG-BROKERROUTE-STALEAUTH — broker.py Never Detected Expired E*Trade Tokens (Fixed 2026-07-28)...
- **`docs/incidents/sqlalchemy-raw-sql-gotchas.md`** — SQLAlchemy text() Named Params with PostgreSQL ::type Casts (BUG-6)
- **`docs/incidents/stale-price-and-data-bugs.md`** — AUD-PAPER-PREMARKET (2026-10-02): paper trading ran 9:00-9:25 pre-open (BOTH markets). SCHD exited ABOVE that day's high — a price it never traded. New shared `is_regular_session`; a trading DAY is not a trading HOUR [premarket, exits]
- **`docs/incidents/stale-price-and-data-bugs.md`** — AUD-PREMARKET-CADENCE (2026-10-02): premarket ingest ran 5-minutely for no extra data — one fetch returns the whole day's bars. 8,520 -> 2,840 calls/day, zero bars lost [yfinance, cadence]
- **`docs/incidents/stale-price-and-data-bugs.md`** — BUG-MONITORPOS-STALEPRICE — `_monitor_positions()` Could Run Exit Checks Against a Frozen Price Forever (Fixed 2026-07-21) … [backtest]

- **`docs/incidents/inferred-fiscal-period-mislabels-non-calendar-years.md`** — `fiscal_quarter` is derived from the calendar month, so MU's Q4 is stored as "Q3 2026". Traced: not a predicate, not a key, not in prompts, emitted but unconsumed. Never match on it [earnings, fiscal]
- **`docs/incidents/concentration-cap-stale-snapshot.md`** — M15: candidates sized off one pre-loop snapshot, so 2 valid entries opened 20.02% vs a 15% sector cap. Fixed by atomic reservation under a portfolio row lock; re-querying alone would NOT have closed the race [concentration, race]
- **`docs/incidents/utc-vs-et-date-boundary.md`** — T409: naive UTC `.date()` reads ONE DAY AHEAD for 4-5h every evening. Dormant: EST-season Fridays get silently skipped as "weekend". 6 sites fixed, ~16 more SCOPED [timezone, DST]
- **`docs/incidents/tracker-status-staleness.md`** — Stale Tracker Entries Can Point Either Direction — Verify Before Trusting Severity/Status; Stale Tracker Entry — T171-RETURN-TARGET-ANALYSIS Was Already Full...
- **`docs/incidents/wire-shape-mismatches.md`** — `/events/overview`'s Nested `top_buys` Is a DIFFERENT Shape Than the Standalone Leaderboard Endpoints — Reused the Wrong Type … [Polygon, Redis]

- **`docs/incidents/yfinance-rate-limit-amplification.md`** — AUD-5M-DUPLICATE-9AM (2026-10-02): two jobs ingested the same bars at 9:00-9:25 ET (~852 dup calls/day). A cron minute list applies to EVERY hour in its hour list — expand triggers to real fire times [5m, cron, duplicate]
- **`docs/incidents/yfinance-rate-limit-amplification.md`** — AUD-ADDSTOCK-MISATTRIBUTED (2026-10-02): the retry already existed — `grep -A` from a def hides decorators. A 2nd layer would be 9 calls. Real bug: 502-for-everything + "check the ticker" [add stock, 429]
- **`docs/incidents/yfinance-rate-limit-amplification.md`** — BUG-YFCALLVOL2 — `_fetch_live_bulk()`'s Unconditional Per-Symbol Fallback Amplified a Real Yahoo Rate-Limit Event (2026-08-17)

### Features — shipped feature documentation (`docs/features/`)

- **`docs/features/admin-and-settings.md`** — Admin AI Assistant Features Page; AUD-ALERTPREFS (2026-09-24): every alert used to go to anyone holding ANY untriggered price alert, symbol never matched. Per-type prefs + HMAC unsubscribe. Absence = subscribed, filter fails OPEN [alerts, email, unsubscribe]
- **`docs/features/aud250-small-fixes.md`** — AUD250-PORTFOLIOOPTIMIZER-SILENT-FALLBACK-NO-FLAG — Fallback Reason Now Visible in Response (Built 2026-07-19)
- **`docs/features/broker-integration.md`** — T257-BROKER-ORDER-HISTORY — E*Trade Sandbox/Prod Order History (Built 2026-07-17); T230-PORTFOLIO-BROKER-SYNC — Automatic Broker Position Sync (Built 2026-07...
- **`docs/features/chart-volume-profile-and-fvg.md`** — Volume Profile (Tier 250) — How to Read It; Chart Toolbar Redesign + Intraday Indicators (Tier 250 follow-up); Fair Value Gap (FVG) — What It Is and How to U...
- **`docs/features/notification-outbox.md`** — M20 outbox: a UNIQUE event_id is idempotency, a Redis TTL never was. Ambiguous sends are `unknown`, not guessed; exactly-once is NOT promised. Pre-cutover backfill is suppressed to prevent a historical burst [notifications, delivery]
- **`docs/features/jev-credential-and-flags.md`** — the OpenRouter key goes in `.env.jev` (news-intelligence ONLY, not the shared .env), never a UI field: a key typed in a browser transits the gateway and logs. Admin shows status only. Key and `jev_enabled` are independent [Jev, secrets]
- **`docs/audits/2026-10-01-training-sample-size-trace.md`** — stored `n_test` is NOT the metric denominator: MU GROWTH 27 vs 14 report rows, MU LONG 21 vs 11 — on the HOLDOUT branch only. 755 bars does not rule out insufficient history [ML, sample size]
- **`docs/audits/2026-10-02-hk-swing-generation-trace.md`** — 211 HK SWING signals, ALL in `choppy` (bar 0.74). Best score in 7 days: 0.706. 200 of 211 PROVABLY never at the bar (cap-floor argument); 11 unresolved. Cap fires on 51% vs 27% fleet-wide [HK SWING, thresholds]
- **`docs/audits/2026-10-02-post-fix-outcome-collection.md`** — frozen baseline at commit f9b6edb3 so "are the decisions useful" is answerable later. BUY is negative on every horizon, SELL positive on both measured. Read per direction x horizon, never pooled [baseline, outcomes]
- **`docs/audits/2026-10-02-runtime-counter-reading.md`** — 119 UW 429s in 48h, ALL inside market hours, with daily budget at 63% and minute headroom free. The counter records no PATH, so which endpoint is throttled is unknown [UW, 429, counters]
- **`docs/audits/2026-10-02-broker-lifecycle-verification.md`** — 9 real-PostgreSQL races. SF-01 now has a behavioural acceptance (R2). THE M25 BLOCKER: `submit_pending` has no production caller, so enabling the flag would silently stop every broker entry [M25, broker, races]
- **`docs/audits/2026-10-02-sf-remediation-review.md`** — review of the SF fixes found TWO still open: the fallback recommended a covered call for 1 share (ordering is not eligibility), and a malformed expiry still built a leg. Both closed, full-builder tests [SF residuals]
- **`docs/audits/2026-10-02-system-followup-audit-and-feature-roadmap.md`** — SF-01..SF-05 all fixed: a CLOSED trade could still be claimed for a broker order (claim re-tested 2 of 5 conditions); 1 share counted as covered; expired contract as primary [SF-01..05]
- **`docs/audits/2026-10-02-signal-decision-options-news-remediation.md`** — SR-01..SR-08 fixed: one conviction+timestamp contract shared with the AUTHORITATIVE decision engine, options send-accounting, newest-event selection, HK holidays, spread/collar validity, delayed news [SR-01..08]
- **`docs/audits/2026-10-02-signal-decision-options-news-remediation.md`** — also traced the inactivity: HK SWING produced ZERO BUY signals in 3 days (126 non-BUY), so pf9's empty scans are upstream of every gate. 9 entries since Sep 8 [paper inactivity, HK SWING]
- **`docs/audits/2026-10-02-base-training-ledger.md`** — nine reconciled stages, bars to reported rows. `dropped` is first-match and must sum; `diagnostics` overlap and never reconcile. MEASURED: 252 bars (one year) lost to feature warm-up; MU/GROWTH's stored 289/26/27/14 reproduces exactly [ML, M13]
- **`docs/audits/2026-10-02-production-model-inventory.md`** — first real run: 1,313 artifacts, 92.8% suppressed, resweep would change NOTHING (stale-suppression closed). Found one model serving on auc 1.0 over 12 rows with cv_auc and overfit_gap both NULL [ML, suppression]
- **`docs/features/capability-readiness.md`** — M24: a migration ledger says a statement RAN; this says whether an action CAN be performed (a table can exist with its unique constraint missing). 3 states — unknown blocks entries, never exits [readiness, capabilities]
- **`docs/features/measurement-contract-and-register.md`** — `shared/metrics/`: a metric cannot be built without declaring population/weighting/outcome; 0-denominator is UNDEFINED, not 0.0. Plus M01-M25 as data, where a trigger must be a condition, not a date [measurement, M01-M25]
- **`docs/features/ci-and-testing-infra.md`** — CI Coverage Gap Closed + T255-REPORTS-TAB Phase 2 (HK Breadth + Flow Leaderboard) (2026-07-28)
- **`docs/features/confidence-calibration.md`** — AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK — Confirmed Real, Deliberately Not Yet Built; AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK — Calibration Now Persisted...
- **`docs/features/congress-insider-data.md`** — Congress data + the "Who to Follow" tab. AUD-UWCONGRESS-FIELDNAMES (2026-09-23): the parser read key names UW does not send, discarding 81% of rows on arrival. Repair took followable traders 3 -> 17 and CORRECTED every figure the tab shipped [congress]
- **`docs/features/decision-engine-dualscorer-parity.md`** — `_should_enter()` / decision-engine Score Parity (T232-DL-DUALSCORER-DEBT, partial); T232-DL-DUALSCORER-DEBT — 4 DE-Only Hard Rejects, Test Coverage Added (2...
- **`docs/features/delisted-stock-detection.md`** — aud14-survivorship — Real Delisting Detection Closes a Dead Column (Built 2026-07-27); T260-DELISTED-BADGE — Informational Badge, Deliberately No Auto-Remova...
- **`docs/features/earnings-data-and-forecasts.md`** — T249-EARNINGS-LLM-IMPACT — Earnings LLM Impact Report (Built 2026-07-29); Earnings Calendar Now Shows Analyst Consensus + Beat-Rate History (Built 2026-08-25 … [migration]
- **`docs/features/earnings-data-and-forecasts.md`** — AUD-EARNSURPRISE-SECTOR (2026-09-22): >10% EPS beat → +4.19% over 5d AFTER OPEN (n=250) vs −0.05% in-line; the platform gates AGAINST it. Two drift figures always — the gap is not tradeable [earnings, surprise, sector]
- **`docs/features/earnings-data-and-forecasts.md`** — T379-CALENDAR-PRICE (2026-09-10) — the calendar showed EPS/target/expected-move but not the PRICE they are relative to. ONE bulk Redis read, never per-symbol; NULL renders "—", never 0.0 [Events Calendar, live_prices]

- **`docs/features/macro-valuation-cape.md`** — CAPE (Shiller PE) — AI Bubble Warning Indicator
- **`docs/features/market-mover-monitoring.md`** — Tier 249 — Market-Mover Monitoring (P0/P1/P2); Reports Tab — Per-Market (US/HK) Report Aggregation (2026-07-16); Tier 257 — Four Feature Designs (2026-07-17,...
- **`docs/features/ml-prediction-features.md`** — AUD-MLAGE (2026-09-07) — model training age surfaced on the ML Model Accuracy panel; carries the three-state null rule (known age / UNKNOWN / a genuine 0 = trained today) that must not … [retrain]

- **`docs/features/mobile-responsive-design.md`** — Mobile Nav Drawer (T251-MOBILE-RESPONSIVE-DESIGN, Phase 1); T230-UX-MOBILE-RESPONSIVE (Phase 2 slice) — Stock Detail Page Grid Collapses on Mobile (Built 202...
- **`docs/features/news-intelligence-service.md`** — T259-NEWS-INTELLIGENCE — New Service (port 8011), Real-Time Company Headline Ingestion + Hot-News Signal Gate (Built 2026-07-27)
- **`docs/features/2026-10-03-report-interpretation-and-decision-support-design.md`** — PROPOSED, not built. Assessment -> drivers -> scenarios -> what to watch -> limitations as the first screen, per report type. §9 is the market-driver framework [interpretation, design]
- **`docs/features/2026-10-04-n1-evidence-packets-and-narration-validation.md`** — frozen packets, eligibility computed BEFORE any text. The narrator NAMES quantities, never writes numbers; comparability is pairwise; the ungated prose path was deleted. 4 review P1s closed [N1, narration]
- **`docs/audits/2026-10-04-report-field-reconciliation-and-association-evidence.md`** — one report, two answers: $54.23B beside "revenue unavailable". Reconciles evidence into primary metrics; durable association evidence; facts-vs-bytes hashes; cross-subject correction pointer [reports, evidence]
- **`docs/audits/2026-10-04-browser-acceptance-and-presentation-fixes.md`** — real-DOM acceptance over a real payload. The nav crossing content is a full-page CAPTURE artifact (sticky), demonstrated. Carries the 12-backend-plus-frontend deploy denominator [browser, acceptance]
- **`docs/features/2026-10-03-earnings-coverage-discovery-and-repair.md`** — MU's Sept quarter was never ingested, and the pending-event loop structurally cannot find what was never created. Discovery + bounded repair; watermark is coverage, not processing time [earnings, coverage]
- **`docs/features/2026-10-06-prospective-capture-and-persisted-evaluations.md`** — a consensus overwritten in place is unrecoverable, so capture is daily and append-only; verdicts carry a fingerprint OF THE RULES, so a threshold change files beside, never over [capture, immutability]
- **`docs/features/2026-10-08-outcome-measurement-and-adjustment-evidence.md`** — tier 427: a coverage claim reaching into the future; a gold trust marked down for having no moat; and a name used without being imported, green across all three suites [applicability, coverage]
- **`docs/features/2026-10-08-outcome-measurement-and-adjustment-evidence.md`** — tier 426: the fingerprint covered only part of the calculation, so fixing the session and adjustment defects would have left stale figures frozen and looked like a clean deploy [outcomes, adjustment, contract]
- **`docs/features/2026-10-06-quality-value-shadow-evaluation.md`** — gates compose by AND, never a score: UNKNOWN is not PASS and a gate never evaluated BLOCKS. No usable share count exists, so valuation compares whole equity to market cap, never per share [quality-value, gates]
- **`docs/features/2026-10-03-intelligence-reports-implementation.md`** — 4 report types, deterministic, versioned immutable snapshots. A frozen pre-earnings baseline survives a later consensus revision; a missing one is recorded, never reconstructed [reports, M-INTEL]
- **`docs/features/data-provider-routing-and-schedule.md`** — ONE PAGE: which provider serves which market/timeframe, every ingest job's window and cadence, where the UW budget goes (option chains 89%), and what is metered. Read before touching ingest scheduling [routing, schedule, UW]
- **`docs/features/ops-tooling.md`** — T270-DBSYNC-PROD-TO-LOCAL-WEEKLY Layer 2 — scripts/sync_prod_to_local.sh (Built 2026-08-17)
- **`docs/features/paper-trading-gates.md`** — Paper Portfolio Badges Are Two Independent Layers — layer-1 (portfolio/market-wide gates) vs … [holiday]

- **`docs/features/options-and-institutional-data.md`** — T230-DATA-OPTIONS-CHAIN — Full Strike/Expiry Options Chain (Built 2026-07-22); TIER82-FMP-ANALYST-ESTIMATES — analyst_pt_upside ML Feature (Built 2026-08-18 … [backtest, OPTHIST, options history, LEAPS]
- **`docs/features/options-and-institutional-data.md`** — T412 (2026-09-29): calculator fields couldn't be retyped — controlled number input, `Number("")` -> 0 -> fallback, written back. Plus expiry buttons + 4-position gain/loss table; a SHORT's ROI denominator is capital, never premium
- **`docs/features/options-and-institutional-data.md`** — T411-IVHV (2026-09-29): IV vs HV chart + 3-week ATM call/put gain-loss table. One UW request buys a YEAR of daily IV (251 rows vs 5). Found `get_iv_rank()` taking rows[0] = the OLDEST row [IV, HV, options performance]
- **`docs/features/options-and-institutional-data.md`** — T402 (2026-09-18): the Game Plan showed only BUY-put/SELL-call; now all 4 legs + collar/verticals + a recommendation keyed to POSITION then IV regime (rich IV -> sell premium), signal weakest. Plus /options-calculator [options, IV rank]
- **`docs/features/options-and-institutional-data.md`** — T384-T387 (2026-09-15) — LEAPS capture 13->29. "Stable and strong" is the WRONG filter: a 0.70-delta call needs a TREND (TQQQ -73.78%/336d). Holiday entries shift + SAY SO [LEAPS, holiday, IV rank]
- **`docs/features/options-and-institutional-data.md`** — T380/T381 (2026-09-10) — a LEAPS contract can stop being quoted MID-HOLD while the symbol's coverage looks fine; walk the delta band instead of committing to `LIMIT 1`. Relaxed ±0.20 fallback is LABELLED, never silent [LEAPS, delta, QLD]
- **`docs/features/options-and-institutional-data.md`** — AUD-INSTFOLLOW (2026-09-23): "Big Funds" tab, 16 managers' 13F adds vs SPY. Raw returns are BETA — all 16 read negative while SPY fell 2.44%. 13F is the LONG BOOK only, so hedged multi-strats look wrong [13F, institutional]
- **`docs/features/options-income-engine.md`** — T398/T399: covered-call/CSP engine, then BACKTESTED (417 trades, 724 archived days). quality_score didn't order win rate, so weights were re-derived 40/40/20 -> 25/75/0, +0.83pp out of sample [options income, cushion]

- **`docs/features/research-engine-reports.md`** — Research Tab on the Stock Detail Page (Built 2026-07-29)
- **`docs/features/self-tuning-walk-forward-harness.md`** — Per-Horizon AI Signal Strategy Tuning (2026-07-16); T255-STRATEGY-TUNER-PER-HORIZON — Joint Buy-Threshold x ML-Weight-Cap Tuner (Phase 1, Built 2026-07-18); ...
- **`docs/features/service-architecture-splits.md`** — T233-ARCH-PORTFOLIO-CONSOLIDATE — portfolio.py Moved to portfolio-optimizer (Built 2026-07-18); T233-ARCH-INSERVICE-SPLITS (research-engine half) — Scoring F...
- **`docs/features/signal-engine-pillars.md`** — AUD-SIGNALCOHORT (2026-09-24): the email's win-rate badge pooled BUY with SELL, which point OPPOSITE ways (BUY -1.22% vs SELL +1.03%, n=18,561). Now per direction+horizon; per-symbol floor 3 -> 8. Why a BUY Signal Can Show Low Confidence; The ↑/↓ Percentage Arrows on the Daily Chart; T232-SIG10 — Bearish Pillar Mirror (`bearish_pillars_active`, Built 2...
- **`docs/features/squeeze-and-options-alerts.md`** — AUD-SQUEEZE250725-BATCH — 6 Squeeze-Audit Issues + 2 Performance Items (2026-08-16); AUD288-SQUEEZE-NO-VOLUME-CONFIRM — RVOL Gate for the Classic Short-Squee … [NBBO, Unusual Whales, dark pool, gamma, migration, options flow]
- **`docs/features/squeeze-and-options-alerts.md`** — AUD-SQUEEZE-ACCURACY (2026-09-24): Short Squeeze Alert is ANTI-PREDICTIVE — 13.3% win (n=15), and it fires at intraday spike tops (median -6.65% alert->entry gap). Other 3 alerts are coin-flips. Reading guide + gate reference [alerts, win rate]
- **`docs/features/squeeze-and-options-alerts.md`** — T383-DARKPOOL-UI (2026-09-10) — T377 stored the dark-pool side but NO endpoint serialised it; now on the Dark Pool tab + alert email. 0 resolved outcomes: an OBSERVATION, not an edge [dark pool, NBBO, side]

- **`docs/features/squeeze-and-options-alerts.md`** — T376-DIGEST-SIZE (2026-09-10) — the Flow Digest showed a price but never the SIZE, reported by the USER … [NBBO, Unusual Whales, dark pool, gamma, migration, options flow]

- **`docs/features/t258-risk-and-postmortem-tools.md`** — T258-WHATCOULDGOWRONG-AGENT — Adversarial Pre-Trade Risk Check (Built 2026-07-18); T258-PORTFOLIO-CORRELATION-PREENTRY — Correlation-Aware Entry Scoring (Bui...
- **`docs/features/tier287-improvement-batch.md`** — Tier 287 — 5-Item Improvement Batch (Goals, Tiered Pyramid, Drawdown Alert, Trade-Pattern Coach, Earnings Playbook) + 1 Deferred (2026-08-17)
- **`docs/features/unusual-whales-integration-batch.md`** — Unusual Whales Integration Batch — Guide Examples, Squeeze Corroboration, Options Game Plan Surfacing (screener + email, Advanced-tier gated), AUD-DECIDE4-EXPECTEDMOVE real expected-move replacing the fabricated 2.00:1 R:R (2026-09-03)
- **`docs/features/watchlist-style-classification.md`** — Watchlist Style Assignment — 64 Untagged Sector-Watchlist Stocks Made Tradeable via Win-Rate + Backtest Cross-Check (2026-09-03); repeatable methodology for re-running as more data accumulates
- **`docs/features/fed-watch.md`** — T405: market-implied FOMC hike/cut odds from CBOT 30-day Fed Funds futures (the CME method). FOMC was only a BLACKOUT GATE before. The Fed moves in 25bp steps, so -12bp = 50% of a 25bp cut, not a 12bp cut [FOMC, rates, macro]
- **`docs/features/learning-section.md`** — the Learning nav group is advanced-tier/admin only, enforced by `isGroupVisible()` in `_app.tsx` AND a page-level guard on all 7 pages; plus the QQQ-LEAPS and squeeze playbooks (tiers 353-354)

### Audits — dated audit/review/session reports (`docs/audits/`)

- **`docs/2026-10-07/market-stock-intelligence-{gap-analysis,architecture}.md`** — fit-gap vs the 52-section Market & Stock Intelligence spec: 17 implemented, 16 partial, 6 not, 5 BLOCKED on data. The missing layer is evidence BUCKETS, not a new engine [gap analysis, spec]
- **`docs/audits/2026-10-07-mu-crdo-completed-assessment.md`** — MU and CRDO end to end from SEC filings. MU is 14.1x PEAK earnings and 70.0x the 6-yr mean; CRDO has ~90% of revenue in 10 customers. EDGAR supplies the share count, basis and filing date the provider never had [MU, CRDO, EDGAR]
- **`docs/audits/2026-10-06-quality-value-readiness-inventory.md`** — measured: 2 of 7 gates computable, the 2 that decide eligibility are not. Corrects 2 claims of my own — "the table is stale" (153/156 current) and "no share count anywhere" (it exists, unusably) [readiness, valuation]
- **`docs/audits/2026-10-01-checkback-calendar.md`** — 8 dated check-backs for the open M01-M25 work, each dated by WHAT IT WAITS ON (expiring decision / scheduled run / occurrence / outcome count / market). Replay JSON beside it: gcal was `invalid_grant`, so NONE were created [calendar, M01-M25]
- **`docs/audits/2026-10-01-portfolio-concentration-synthetic-tests.md`** — M15 synthetic tests: 2 concurrent entries opened 20.02% vs a 15% sector cap; pending orders invisible; a missing mark values a position at entry price. Exits NOT gated by caps on the scheduled path [concentration, risk]
- **`docs/audits/2026-09-28-email-followup-remediation.md`** — EF-01..EF-05: a review of the EA fixes found five more, THREE of them regressions that work introduced. EF-01 aborted the signal batch (`except as` deletes its name on exit); a retry rendered the threshold as the price [email]
- **`docs/audits/2026-09-28-email-audit-remediation.md`** — EA-01..EA-12. EA-01 was a LIVE OUTAGE of mine: a keyword-name used as a value raised NameError, silencing all 362 signal subscriptions for 4 days AND consuming transitions, every test green. Plus 12 unenforced opt-outs [email, outage]
- **`docs/audits/2026-09-28-postdeployment-audit-remediation.md`** — audit of the DEPLOYED code: settlement returned a close read BEFORE its corroboration (assigned vs worthless on a $100 strike), and the label date was still an estimate. Includes a conclusion I got wrong the round before [audit]
- **`docs/audits/2026-09-28-predeployment-audit-remediation.md`** — an audit of the R01-R10 fix ITSELF, pre-deploy: 7 defects. Two shipped DEAD code (a query naming a column that does not exist; DDL in a function that returns early in prod) behind passing source-text tests [audit]
- **`docs/audits/2026-09-24-followup-audit-remediation.md`** — R01-R10 of the follow-up audit, all closed. Eight are one shape: a defect DETECTED, RECORDED, then used anyway. Carries the 4-of-130 fleet measurement, and 4 test-harness failures incl. a sabotage loop that ran zero tests [audit]
- **`docs/audits/2026-10-01-session-index-measurement-outbox-exposure.md`** — START HERE for 2026-10-01: measurement contract, outbox, and the concentration race (2 valid entries, 20.02% vs a 15% cap). Lists 5 claims of mine that did not survive review [session index]
- **`docs/audits/2026-10-01-session-status-completed-and-remaining.md`** — START HERE for 2026-09-29..10-01: what is deployed vs committed vs open, mapped to the M01-M25 register. MU-02 v1 deployed, v2 committed only; M02 markers and M21 digest await approval [session index]
- **`docs/audits/2026-09-30-mu-earnings-latency-and-event-intelligence-review.md`** — MU's result headline landed in 5.08s and no alert went out: a PREVIEW had taken the day's single dedup slot. Dedup is now per release PHASE. The NULL EPS was NOT provider latency [earnings, dedup]
- **`docs/audits/2026-09-30-darkpool-relative-threshold-effectiveness.md`** — the relative bar rejects 15 of 9,710 prints (0.2%). For 55 of 57 symbols 5x median is BELOW the $1M absolute floor, so it can never bind. Explains why the fire rate never moved [dark pool, threshold]
- **`docs/audits/2026-09-30-three-week-deferred-checkpoint.md`** — the scheduled 3-week read. Confidence bands are FLAT (36.5-43.9%, n=19,256) so calibration feedback is closed, not pending. Options-flow BEARISH arm is anti-predictive: 29.1% win, +4.0% after [checkpoint, outcomes]
- **`docs/audits/2026-09-30-signal-outage-recovery-manifest.md`** — READ-ONLY. The "69 consumed transitions" recounts to 65 actionable; only 18 are both consumed AND still true, and ALL 18 are SELL. Outage was 2 trading days, not 4. Nothing sent [recovery, outage]
- **`docs/audits/2026-09-29-email-fix-closure-remediation.md`** — EC-01..EC-03. A row-mutating migration ran on EVERY startup: the next restart would stamp every pending alert delivered. Ledger + watermark. EC-03: /health said ok however the migration went [migration, readiness]
- **`docs/audits/2026-09-24-deep-audit-remediation.md`** — all 12 findings of the 2026-09-23 external deep audit, fixed and deployed. Two remedies deliberately NOT followed on measurement (enforcing the embargo would disable GROWTH for all 180 symbols; redefining the yield denominator invalidates a 417-trade calibration) [audit]
- **`docs/audits/2026-09-23-session-index.md`** — START HERE for 2026-09-23/24: a parse bug discarding 81% of congress rows, Who to Follow + Big Funds tabs, alert-type prefs/unsubscribe, and THREE datasets measuring returns from a date nobody could act on [session index]
- **`docs/audits/2026-09-22-session-index.md`** — START HERE for 2026-09-21/22: the connpool incident, the prediction audit, Phase 0/1 fixes, the earnings edge, purged walk-forward — and a NOT BUILT section with the reasoning for each rejected item [session index]
- **`docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md`** — BUY alpha −1.08pp (t=−9.24) BUT Sept alone is −0.32pp (ns) — READ THE CORRECTION BOX FIRST. Losses are TWO episodes, not a trend. SELL beats BUY by 3.67pp; confidence INVERTED; HMM never uses its transmat [alpha, HMM, inversion]
- **`docs/audits/2026-09-19-session-index.md`** — START HERE for the 2026-09-18/19 session: T410, A01-A03 scoping, the AUD-MINRR-STYLEBLIND fix-then-correction arc, the four-audit implementation batch (3 rounds), the fix-effectiveness crash, and the PT-H0x audit review, all in one place with links.
- **`docs/audits/2026-09-21-deferred-audit-items-batch.md`** — PT-H08/PT-H03/PT-H01's own deferred real fixes, built without changing any live portfolio's resolved behavior; plus UW-07 and E09 from the broader deferred list. Records what's still deliberately unbuilt and why.
- **`docs/audits/2026-09-21-utc-date-boundary-triage.md`** — triaged the 16 files docs/incidents/utc-vs-et-date-boundary.md scoped but deferred; fixed 3 risk-limit gates + 13 more real sites, ~90 left as likely-fine. Follow-up resolved the UNCLEAR tail + swept every remaining service: event-intelligence (4 sites) + ranking-engine's own US/HK-aware fix [timezone, DST]
- **`docs/audits/2026-09-16-codebase-and-feature-review.md`** — Static project orientation: architecture, feature map, scoring/execution paths, backtest boundaries, and documentation reconciliation; no runtime verification.
- **`docs/features/independent-horizon-resolution.md`** — T410 (AUD-C02/C03): audited all 201 SignalOutcome refs, found EVERY one is safe, built an ADDITIVE table instead of patching. LONG/SWING/GROWTH BUY now get 5-day outcomes without waiting for a 14-28 day primary [outcomes, calibration]
- **`docs/audits/2026-09-18-c02-c03-outcome-horizon-scoping.md`** — why C02/C03 was SCOPED not built: pending outcome rows change what "a SignalOutcome row exists" means across 201 non-test refs incl. ML TRAINING. Audit those first [outcomes, horizons, calibration]
- **`docs/audits/2026-09-17-three-audit-verification.md`** — verified 3 audits, 18/18 claims correct. Fixed A17 (T400's own regression: settlement raised on EVERY success, uncommitted mutation then committed by a LATER function). Found A18 (CI never runs on `prod`) + A19 [audit review]
- **`docs/audits/2026-09-17-audit-review-and-t400-fixes.md`** — verified 6 claims from the 09-16/09-17 audits; all 6 correct. Fixed A13/A04/A05. A01-A03 (broker lifecycle) left open as design work. The reported service timeouts were a harness artefact [audit review]

- **`docs/audits/2026-09-18-a01-a03-broker-lifecycle-scoping.md`** — re-verified A01-A03 still true; found 2 more: conditional_orders.py duplicates A02's exact bug in a hand-maintained "reimplementation," and manual_exit/liquidate never touch the broker at all [broker lifecycle]
- **`docs/audits/2026-09-18-uw-and-broker-report-review.md`** — independent review of the UW/broker audits: defects confirmed, but call-site counts overstated; expanded broker scope to B01-B12 (partial fills, scale-in/out, reconciliation) [audit review]
- **`docs/audits/2026-09-19-four-audit-implementation-batch.md`** — fixed UW-01, B01/B02/B05 (broker), E01-E04/E06/E08(partial)/E10/E12 (email accuracy). B03/B06-B12, UW-02-09, E05/E07/E09/E11/E13/E14 scoped not built [broker, UW, email]

- **`docs/audits/2026-07-16-aud250-deep-audit-series.md`** — AUD250 — Deep Audit of the 2026-07-11 to 2026-07-16 Work Window (73 Commits, 11 Services); Deep Audit: Trading Gate / Chart / Reports (2026-07-17) — 10 Confi...
- **`docs/audits/2026-07-20-duplicate-code-and-redis-pooling-audit.md`** — Full-Codebase Audit — Duplicate Code / Single-Source-of-Truth (Phase 1: Redis Connections, 2026-07-20); Full-Codebase Audit — Duplicate Code / Single-Source-...
- **`docs/audits/2026-07-22-t258-session-deep-audit.md`** — Deep Audit: Everything Shipped in the T258 Session (2026-07-22) — 6 Confirmed Findings, All Fixed
- **`docs/audits/2026-07-28-claude-api-cost-audit.md`** — Claude API Cost Audit (2026-07-28) — Full Usage Map + Fix for the Real Leak
- **`docs/audits/2026-08-05-six-part-deep-audit-series.md`** — Deep Audit #1 of 6: AI Signal Performance / Accuracy / Win Rate / Return (2026-08-05); Deep Audit #2 of 6: Prediction / Decision-Making / Paper Trading (2026...
- **`docs/audits/2026-08-16-stale-doc-reviews.md`** — Recurring Doc Review: 2 Stale Audit/Roadmap Documents — One Held Up, One Didn't (2026-08-16)
- **`docs/audits/2026-08-18-institutional-features-review.md`** — Review: docs/recomm_or_audit/ — 13 Institutional Features (IF-01..IF-13), Verified 2026-08-18
- **`docs/audits/2026-08-20-external-audit-doc-review.md`** — Review: docs/recomm_or_audit/DEEP_PLATFORM_AUDIT_2026-08-20_VERIFIED.md — Accurate Data, Stale Implementation-Status Claims (Reviewed 2026-08-20)
- **`docs/audits/2026-08-21-next-improvement-and-doc-review.md`** — Next Improvement Batch — 4 Real Fixes Found by Re-Running Established Bug-Class Sweeps (2026-08-21); Review: docs/recomm_or_audit/AI_SIGNALS_SQUEEZE_ALERTS_A...
- **`docs/audits/2026-08-22-external-doc-reviews-and-actions.md`** — Review: docs/recomm_or_audit/AI_SIGNALS_SQUEEZE_ALERTS_DEEP_AUDIT_2025-08-22.md ("Deep Audit v2") — Raw Data Mostly Real, Every P0/P1 Analysis Conclusion Sta...
- **`docs/audits/2026-08-24-data-subscriptions-and-improvement-batches.md`** — Review: docs/AI Stock Intelligence Data & Decision Engine.md — FMP + Unusual Whales Paid Data Subscriptions, Deliberately Deferred (2026-08-24); Next Improve...
- **`docs/audits/2026-08-25-next-improvement-batches-and-mutation-sweep.md`** — Next-Improvement Batch (2026-08-25) — 4 Real Fixes From 3 Parallel Survey Angles; Next-Improvement Batch (2026-08-25b) — Squeeze/Gamma Alert Family Re-Audite...
- **`docs/audits/2026-08-26-metamodel-nan-fix.md`** — AUD232-METAMODEL-MEDIUM-GROUP — Meta-Model NaN-Preserving Fix (2026-08-26)
- **`docs/audits/2026-08-31-five-part-deep-audit-series.md`** — Deep Audit Series (2026-08-31): AI Signal — 1 of 5; Deep Audit Series (2026-08-31): Short Squeeze / Gamma / Prebreakout alerts — 2 of 5; Deep Audit Series (2...
- **`docs/audits/2026-09-01-market-pressure-engine-scoping.md`** — Scoping Decision: Market Pressure, Options, Short Squeeze & Margin Risk Engine (2026-09-01)
- **`docs/audits/2026-09-02-six-part-platform-audit-1-ai-signal.md`** — Deep Audit Series (2026-09-02): AI Signal — 1 of 6 (confidence is meaningless, ground-truth eval selection bias, stale regime vocab, falsy-zero AUC — 2 fixed, 1 fixed+migrated, 1 deferred)
- **`docs/audits/2026-09-03-six-part-platform-audit-2-decision-making.md`** — Deep Audit Series (2026-09-03): Decision-Making — 2 of 6 (4 production portfolios had min_confidence=15/min_entry_score=3, far below real defaults — config fixed live …

- **`docs/audits/2026-09-03-six-part-platform-audit-3-paper-trading.md`** — Deep Audit Series (2026-09-03): Paper Trading — 3 of 6 (poll_broker_order_fills never cleared its "still needs polling" signal, silently re-polling filled broker orders forever … [migration]

- **`docs/audits/2026-09-03-six-part-platform-audit-4-model-training.md`** — Deep Audit Series (2026-09-03): Model Training — 4 of 6 (41/249 live models had zero true positives ever observed — "dead recall" … [retrain]

- **`docs/audits/2026-09-03-six-part-platform-audit-5-short-squeeze.md`** — Deep Audit Series (2026-09-03): Short Squeeze Alerts — 5 of 6 (fresh re-audit despite an exhaustive pass just 3 days prior … [backtest, gamma]

- **`docs/audits/2026-09-03-six-part-platform-audit-6-options-trading.md`** — Deep Audit Series (2026-09-03): Options Trading & Alerts — 6 of 6, SERIES COMPLETE (compute_options_pressure_score()'s cp_ratio component was asymmetric …

- **[`docs/2026-09-04/DATA_QUALITY_AUDIT.md`](../docs/2026-09-04/DATA_QUALITY_AUDIT.md)** — Phase A of the revised independent trading-edge audit (see `docs/recomm_or_audit/AI Stock Trading Platform … [Alpha Vantage, Polygon, Unusual Whales, yfinance]

- **[`docs/2026-09-04/PHASE_B2_INVERSION_ROOT_CAUSE_FOUND.md`](../docs/2026-09-04/PHASE_B2_INVERSION_ROOT_CAUSE_FOUND.md)** — THE CONFIDENCE INVERSION IS SOLVED AND WAS ALREADY FIXED. It was an artifact of `AUD232-BUY-FROM-TOP` (commit `aee6d17` …

- **[`docs/2026-09-05/SESSION_INDEX_AND_NEXT_STEPS.md`](../docs/2026-09-05/SESSION_INDEX_AND_NEXT_STEPS.md)** — START HERE for the 2026-09-04/05 audit cycle. Index of 11 documents + 16 deployed fixes, with the balanced read (`WHERE_THE_APP_ACTUALLY_STANDS.md`) that the individual defect-scoped au …

- **`docs/2026-09-08/NEXT_IMPROVEMENTS.md`** — open items after the two audits, plus AUD-ING7-DELISTNEVERFIRES (tier 365). Two facts worth knowing before touching delisting or ingest liveness: (1) `Stock.delisted` IS set … [yfinance]

- **`docs/2026-09-08/INGESTION_AUDIT_PARTIAL.md`** — DATA INGESTION & PRICE INTEGRITY audit (2026-09-08): 6 areas, 3 findings, all fixed (tier 364) …

- **`docs/audits/2026-09-07-six-part-audit-{1..6}-*.md`** — SIX-PART DEEP PLATFORM AUDIT (2026-09-07): Decision-Making, ML Training, AI Signal, Entry Timing, Paper Trading, Option Alerts. 17 findings, ALL FIXED AND DEPLOYED (tier 363) … [retrain, watchdog]

- **`docs/audits/2026-09-08-ranking-kscore-audit.md`** — RANKING / K-SCORE audit (2026-09-08): 6 findings, all fixed (tier 366). Read before touching `services/ranking-engine/` or `kscore.py` … [Redis, yfinance]

- **`docs/audits/2026-09-08-backtest-harness-audit.md`** — BACKTEST HARNESS audit (2026-09-08): 4 findings fixed + 1 RETRACTION (tier 367). Read before touching `services/market-data/src/backtest/` or quoting any backtest number … [HKEX]
- **`docs/audits/2026-09-08-backtest-harness-audit.md`** — T388/T389 (2026-09-15) — fee/slippage + look-ahead had ZERO tests; now 17 behavioural ones, 8 engine sabotages caught. The audit's P0 RSI "drift" is unreachable: 0 differing bars on 3,897 real rows [backtest, RSI, fees]

- **`docs/audits/2026-09-08-gate-audit.md`** — ALL-GATES audit (2026-09-08): 12 findings, 6 FIXED (tier 368), run as 3 parallel audits (entry / exit+risk / signal+alert) over 158 gate constants in 14 files … [Redis, watchdog]

- **[`docs/2026-09-06/DEEP_SYSTEM_AUDIT.md`](../docs/2026-09-06/DEEP_SYSTEM_AUDIT.md)** — Full-system architecture + trading-logic deep audit, 27 verified findings. All 19 priority fixes applied and deployed (`prod` commits `9225995`, `bed7ed9`, `50c710f`, `0fb74c3`) …
