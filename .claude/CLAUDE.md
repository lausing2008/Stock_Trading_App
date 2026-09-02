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
   but do NOT pass `--no-cache` (see `docs/incidents/ec2-disk-and-frontend-builds.md` — `--no-cache`
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

- **`docs/incidents/alert-email-spam-and-suppression.md`** — Signal Alert Email Spam — BUY→HOLD→BUY Oscillation; Alert Email Suppression — market:refresh_failed Flag (BUG-8); BUG-MORNINGDIGEST-SENDLOOP — Same Unguarded...
- **`docs/incidents/auto-research-trigger-gating.md`** — A SECOND, Completely Independent Auto-Research Trigger — Never Gated By `auto_research_enabled` At All (Fixed 2026-07-29)
- **`docs/incidents/backtest-wall-clock-and-lookahead-bugs.md`** — BUG233-BACKTESTWALLCLOCK — Phase 2a/2b Backtest Harness Always Returned Zero Trades Unless Run During Real Live Market Hours (Fixed 2026-07-22)
- **`docs/incidents/chart-drawing-bugs.md`** — BUG-TRENDLINE-STALEBARINDEX — Trendline Drawings Broke Across Timeframe Switches (Fixed 2026-07-21)
- **`docs/incidents/dead-code-and-shadowing-bugs.md`** — A Redundant Local `from datetime import datetime` Made Two Hard Rejects Dead Code (BUG232-DEADCODE)
- **`docs/incidents/decide-endpoint-crash-bugs.md`** — BUG-DECIDE-GAMEPLAN-STYLEFLOAT — decision-engine Crashed on Every Real Game-Plan-Bearing BUY Candidate, Silently Falling Back to the DE-Outage Scorer (Fixed ...
- **`docs/incidents/delisted-stock-generation-blind.md`** — BUG-DELISTED-GENERATION-BLIND — 8 More Generation/Scan Paths Never Consulted `Stock.delisted` (Fixed 2026-07-30); BUG-DELISTED-GENERATION-BLIND — 2 More Sibl...
- **`docs/incidents/dependency-missing-from-container.md`** — Signal Refresh 401 — jose Library Missing from signal-engine; tune_all 401 — jose Library Missing from ml-prediction; Stale Rankings — jose Missing from rank...
- **`docs/incidents/design-doc-math-verification.md`** — BUG-SA33-UNREACHABLETHRESHOLD — A Design Doc's Own Fix Was Mathematically Unable to Achieve Its Stated Goal (Fixed 2026-07-27)
- **`docs/incidents/docker-deploy-staleness.md`** — Adding a Column to an EXISTING Table Doesn't Auto-Apply — `create_all()` Only Creates Missing Tables; Local Dev Containers Run Stale `shared/db/` — Attribute...
- **`docs/incidents/ec2-disk-and-frontend-builds.md`** — EC2 Disk Fills Up from Dangling Docker Images; Slow Frontend Builds (24–47 min) — `--no-cache` Was Unnecessary
- **`docs/incidents/ec2-reboot-and-tls-cert-incidents.md`** — INCIDENT 2026-08-05: Full EC2 Reboot Reverted signal-engine to a PRE-SPLIT Image — SA-33 No Longer Live; INCIDENT 2026-08-05 (RESOLVED): TLS Certificate Expi...
- **`docs/incidents/external-data-source-liveness.md`** — Congress Trading Data Silently Empty — Free Source Domains Permanently Dead; "It's Reachable" ≠ "It's Current" — Always Check Last-Modified, Not Just HTTP 200
- **`docs/incidents/float-noise-variance-epsilon.md`** — AUD292-SHARPE-VAREPS — paper_portfolio.py's Sharpe/Sortino Had the Exact Float-Noise-Explosion Bug strategy-engine's Own T237-SE1 Fix Already Found and Guard...
- **`docs/incidents/hk-connect-logging-typeerror.md`** — hk_connect_flows Logging TypeError (BUG-9)
- **`docs/incidents/improvements-tracker-bugs.md`** — Improvements Page Not Showing New Tiers; BUG-IMPROVEMENTSPAGE-STALESTATUS — Improvements Tracker's "Done" Count Could Get Stuck Forever (Fixed 2026-07-21)
- **`docs/incidents/login-redirect-loop.md`** — Login Redirect Loop After Deployment
- **`docs/incidents/market-hours-gating-bugs.md`** — BUG-VOLANOM-STALEMARKET — Volume-Anomaly Alert Fired on a Closed Market's Frozen Daily Volume (Fixed 2026-07-21)
- **`docs/incidents/market-pulse-dashboard-bugs.md`** — Market Pulse Dashboard's "Top Movers" Could Go Entirely One-Sided (Fixed 2026-08-25); Market Pulse Dashboard's Top Movers/Sector Heat Map Silently Mixed HK S...
- **`docs/incidents/research-report-network-and-persistence.md`** — Research Generation "NetworkError" in Browser Despite Server Success; Research Reports Vanished on Every research-engine Restart — No DB Persistence At All (...
- **`docs/incidents/router-ordering-catchall-shadowing.md`** — BUG233-ROUTERORDER — Catch-All `/{symbol}` Route Silently Shadowed Literal Paths From Sibling Routers (Fixed 2026-07-22)
- **`docs/incidents/self-tuning-job-performance-bugs.md`** — BUG-WEEKLYREFRESH-HEAVYSWEEP-TIMEOUT — Heavy Weekly Sweeps Were Timing Out And Silently Truncating the Rest of Sunday's Tuning Chain (Fixed 2026-08-31); BUG-...
- **`docs/incidents/service-to-service-auth-headers.md`** — INT-7 Signal-Engine Research Divergence — Missing Auth Header; BUG-BROKERROUTE-STALEAUTH — broker.py Never Detected Expired E*Trade Tokens (Fixed 2026-07-28)...
- **`docs/incidents/sqlalchemy-raw-sql-gotchas.md`** — SQLAlchemy text() Named Params with PostgreSQL ::type Casts (BUG-6)
- **`docs/incidents/stale-price-and-data-bugs.md`** — BUG-MONITORPOS-STALEPRICE — `_monitor_positions()` Could Run Exit Checks Against a Frozen Price Forever (Fixed 2026-07-21); BUG-TALEVELS-EMPTYPIVOTS-FLOATIDX...
- **`docs/incidents/tracker-status-staleness.md`** — Stale Tracker Entries Can Point Either Direction — Verify Before Trusting Severity/Status; Stale Tracker Entry — T171-RETURN-TARGET-ANALYSIS Was Already Full...
- **`docs/incidents/wire-shape-mismatches.md`** — `/events/overview`'s Nested `top_buys` Is a DIFFERENT Shape Than the Standalone Leaderboard Endpoints — Reused the Wrong Type
- **`docs/incidents/yfinance-rate-limit-amplification.md`** — BUG-YFCALLVOL2 — `_fetch_live_bulk()`'s Unconditional Per-Symbol Fallback Amplified a Real Yahoo Rate-Limit Event (2026-08-17)

### Features — shipped feature documentation (`docs/features/`)

- **`docs/features/admin-and-settings.md`** — Admin AI Assistant Features Page (Built 2026-07-28)
- **`docs/features/aud250-small-fixes.md`** — AUD250-PORTFOLIOOPTIMIZER-SILENT-FALLBACK-NO-FLAG — Fallback Reason Now Visible in Response (Built 2026-07-19)
- **`docs/features/broker-integration.md`** — T257-BROKER-ORDER-HISTORY — E*Trade Sandbox/Prod Order History (Built 2026-07-17); T230-PORTFOLIO-BROKER-SYNC — Automatic Broker Position Sync (Built 2026-07...
- **`docs/features/chart-volume-profile-and-fvg.md`** — Volume Profile (Tier 250) — How to Read It; Chart Toolbar Redesign + Intraday Indicators (Tier 250 follow-up); Fair Value Gap (FVG) — What It Is and How to U...
- **`docs/features/ci-and-testing-infra.md`** — CI Coverage Gap Closed + T255-REPORTS-TAB Phase 2 (HK Breadth + Flow Leaderboard) (2026-07-28)
- **`docs/features/confidence-calibration.md`** — AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK — Confirmed Real, Deliberately Not Yet Built; AUD288-CONFIDENCE-CALIBRATION-NOT-FEDBACK — Calibration Now Persisted...
- **`docs/features/congress-insider-data.md`** — Congress Trading Data (Two Independent Implementations)
- **`docs/features/decision-engine-dualscorer-parity.md`** — `_should_enter()` / decision-engine Score Parity (T232-DL-DUALSCORER-DEBT, partial); T232-DL-DUALSCORER-DEBT — 4 DE-Only Hard Rejects, Test Coverage Added (2...
- **`docs/features/delisted-stock-detection.md`** — aud14-survivorship — Real Delisting Detection Closes a Dead Column (Built 2026-07-27); T260-DELISTED-BADGE — Informational Badge, Deliberately No Auto-Remova...
- **`docs/features/earnings-data-and-forecasts.md`** — T249-EARNINGS-LLM-IMPACT — Earnings LLM Impact Report (Built 2026-07-29); Earnings Calendar Now Shows Analyst Consensus + Beat-Rate History (Built 2026-08-25...
- **`docs/features/macro-valuation-cape.md`** — CAPE (Shiller PE) — AI Bubble Warning Indicator
- **`docs/features/market-mover-monitoring.md`** — Tier 249 — Market-Mover Monitoring (P0/P1/P2); Reports Tab — Per-Market (US/HK) Report Aggregation (2026-07-16); Tier 257 — Four Feature Designs (2026-07-17,...
- **`docs/features/ml-prediction-features.md`** — AUD232-059 — meta_trainer.py's Per-Row Feature Recomputation Deduplicated (Fixed 2026-07-21); T237-ML2b — eps_revision_direction Reintroduced Point-in-Time-C...
- **`docs/features/mobile-responsive-design.md`** — Mobile Nav Drawer (T251-MOBILE-RESPONSIVE-DESIGN, Phase 1); T230-UX-MOBILE-RESPONSIVE (Phase 2 slice) — Stock Detail Page Grid Collapses on Mobile (Built 202...
- **`docs/features/news-intelligence-service.md`** — T259-NEWS-INTELLIGENCE — New Service (port 8011), Real-Time Company Headline Ingestion + Hot-News Signal Gate (Built 2026-07-27)
- **`docs/features/ops-tooling.md`** — T270-DBSYNC-PROD-TO-LOCAL-WEEKLY Layer 2 — scripts/sync_prod_to_local.sh (Built 2026-08-17)
- **`docs/features/paper-trading-gates.md`** — Paper Portfolio Badges Are Two Independent Layers — layer-1 (portfolio/market-wide gates) vs. layer-2 (per-candidate "why no entry") badges on `/paper-portfolio/list`
- **`docs/features/options-and-institutional-data.md`** — T230-DATA-OPTIONS-CHAIN — Full Strike/Expiry Options Chain (Built 2026-07-22); TIER82-FMP-ANALYST-ESTIMATES — analyst_pt_upside ML Feature (Built 2026-08-18,...
- **`docs/features/research-engine-reports.md`** — Research Tab on the Stock Detail Page (Built 2026-07-29)
- **`docs/features/self-tuning-walk-forward-harness.md`** — Per-Horizon AI Signal Strategy Tuning (2026-07-16); T255-STRATEGY-TUNER-PER-HORIZON — Joint Buy-Threshold x ML-Weight-Cap Tuner (Phase 1, Built 2026-07-18); ...
- **`docs/features/service-architecture-splits.md`** — T233-ARCH-PORTFOLIO-CONSOLIDATE — portfolio.py Moved to portfolio-optimizer (Built 2026-07-18); T233-ARCH-INSERVICE-SPLITS (research-engine half) — Scoring F...
- **`docs/features/signal-engine-pillars.md`** — Why a BUY Signal Can Show Low Confidence; The ↑/↓ Percentage Arrows on the Daily Chart; T232-SIG10 — Bearish Pillar Mirror (`bearish_pillars_active`, Built 2...
- **`docs/features/squeeze-and-options-alerts.md`** — AUD-SQUEEZE250725-BATCH — 6 Squeeze-Audit Issues + 2 Performance Items (2026-08-16); AUD288-SQUEEZE-NO-VOLUME-CONFIRM — RVOL Gate for the Classic Short-Squee...
- **`docs/features/t258-risk-and-postmortem-tools.md`** — T258-WHATCOULDGOWRONG-AGENT — Adversarial Pre-Trade Risk Check (Built 2026-07-18); T258-PORTFOLIO-CORRELATION-PREENTRY — Correlation-Aware Entry Scoring (Bui...
- **`docs/features/tier287-improvement-batch.md`** — Tier 287 — 5-Item Improvement Batch (Goals, Tiered Pyramid, Drawdown Alert, Trade-Pattern Coach, Earnings Playbook) + 1 Deferred (2026-08-17)

### Audits — dated audit/review/session reports (`docs/audits/`)

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
