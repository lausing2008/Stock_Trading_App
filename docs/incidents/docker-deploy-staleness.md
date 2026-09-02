## Recurring Issue: Adding a Column to an EXISTING Table Doesn't Auto-Apply — `create_all()` Only Creates Missing Tables

**Symptom:** Adding a new field to an existing SQLAlchemy model (e.g. a new column on `User`,
which already has rows in production) breaks EVERY query against that model in production
immediately after deploy — `psycopg2.errors.UndefinedColumn: column users.new_field does not
exist`. This is different from (and easy to confuse with) the "stale `shared/db/` in a
container" issue below — this happens even with a perfectly fresh, freshly-rebuilt container.

**Root cause (found 2026-07-10):** This repo has no active Alembic migrations (`alembic.ini`
exists but zero real migration files do) — the only schema-application mechanism is
`Base.metadata.create_all()` in `shared/db/session.py`, run on every service startup.
`create_all()` only creates tables that don't exist yet; it does **not** `ALTER TABLE` an
existing table to add a newly-declared column. Adding a brand-new table's model (e.g.
`PushSubscription`, same session) works fine via this mechanism — but adding a field to an
existing, already-populated table (e.g. `User.notification_webhook`) silently does nothing to
the real schema, and the gap isn't visible until the first request that queries that column.

**Fix applied (2026-07-10):** Manually ran `ALTER TABLE users ADD COLUMN IF NOT EXISTS
notification_webhook VARCHAR(2048);` directly against production Postgres to add the missing
column. Login (`GET /auth/me`) recovered immediately once the column existed.

**What to check before adding ANY field to an EXISTING model (not a new one):**
```bash
# Does the table already exist and have rows? If yes, create_all() will NOT add the new column.
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\d table_name"
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "SELECT COUNT(*) FROM table_name"
```
If the table already exists, a manual `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...` must run
against production (and any local dev Postgres) BEFORE or immediately after deploying the code
change — plan this as an explicit deploy step, not something the deploy pipeline does for you.

**Design invariant:** `create_all()` is only sufficient for adding a brand-new table. Any new
column on an existing table needs its own manual `ALTER TABLE`, run against every environment
(production, local dev) separately — there is no migration system doing this automatically in
this repo today. Consider this a standing gap until real Alembic migrations are adopted.

---


## Recurring Issue: Local Dev Containers Run Stale `shared/db/` — AttributeError on Recently Added Model Fields

**Symptom:** A backend endpoint that reads a recently-added SQLAlchemy model field crashes with
`AttributeError: 'ModelName' object has no attribute 'field_name'` on **local dev only** — the
same endpoint works fine in production. Confirmed instances: `TuneHistory` missing from
signal-engine's `shared/db/__init__.py` (2026-07-06), `Stock.index_membership` missing from
ranking-engine's `shared/db/models.py` (2026-07-07, crashed `GET /rankings` — which also broke
the Screener page and its RVOL feature, since both read from that endpoint).

**Root cause:** `shared/db/` is baked into every service's Docker image at build time (see
Deployment Pattern above — it is NOT one of the directories `docker cp` normally targets for
day-to-day code changes). When a new field is added to `shared/db/models.py` and deployed via
`docker cp` to the ONE service that immediately needs it (e.g. signal-engine for `TuneHistory`),
every OTHER local dev container keeps running its old, pre-existing `shared/db/` copy from
whenever its image was last built — silently, with no error, until something finally tries to
read the new field through that stale container's ORM class.

**Fix pattern (apply to any container showing this error):**
```bash
docker cp shared/db/__init__.py stockai-<service>-1:/app/shared/db/__init__.py
docker cp shared/db/models.py stockai-<service>-1:/app/shared/db/models.py
docker exec stockai-<service>-1 rm -rf /app/shared/db/__pycache__
docker restart stockai-<service>-1
```

**Check ALL local dev containers proactively, not just the one that errored** — this bug is
systemic, not isolated to one service. On 2026-07-07, checking every container after fixing
ranking-engine found 4 MORE containers with the exact same staleness (technical-analysis,
event-intelligence, strategy-engine, portfolio-optimizer, api-gateway all missing
`index_membership` too) that had not yet crashed only because nothing had exercised that
specific field on them yet:
```bash
for c in market-data signal-engine ranking-engine technical-analysis event-intelligence \
         research-engine api-gateway ml-prediction decision-engine strategy-engine portfolio-optimizer; do
  echo -n "$c: "; docker exec stockai-$c-1 grep -c '<newest_field_name>' /app/shared/db/models.py
done
```
(A `0` or a non-fatal `grep` exit code with `0` output — not a real error — both mean "stale,
missing the field.")

---


## Recurring Issue: PRODUCTION Container Ran Stale Service-Local Files Despite Git Being Up To Date

**Symptom:** `docker restart` on a production container crashes on startup with
`ImportError: cannot import name 'X' from 'module'`, even though `git log`/`git status` on the
EC2 host show the repo checkout is fully up to date and does NOT reference `X` anywhere.

**Root cause (found 2026-07-07):** An earlier fix (TA-D1, removing the dead `vwap()` indicator)
was committed, pushed, and deployed to **local dev** — but the corresponding `docker cp` step to
the **production** `stockai-technical-analysis-1` container was never actually run at the time.
`git pull` on the EC2 host updates the host's checkout, not the running container's `/app/src/`
files — those only change via an explicit `docker cp`. Production kept running its old
`routes.py`/`indicators/__init__.py` (both still importing the now-deleted `vwap`) completely
unnoticed, because nothing had restarted that container since the gap was introduced — routine
`docker restart`s only became necessary again once a later, unrelated fix (T237, ATR/pattern
fixes) needed deploying to the same container, which is what finally surfaced the crash.

**This means "deployed to production" was previously asserted for TA-D1 without actually being
true** — a gap between the deployment checklist being followed in spirit (committed, pushed,
`git pull`'d on EC2) and in fact (the specific `docker cp` + restart for that specific service
never happening, or being silently skipped/forgotten).

**Fix applied:** Synced the current (correct, git-matching) `routes.py` and
`indicators/__init__.py` from the EC2 checkout into the container via `docker cp`, cleared
`__pycache__`, restarted. Confirmed via `grep vwap` inside the container (empty result) and a
successful `/health` check plus a real `GET /ta/{symbol}/patterns` call.

**What to check after ANY deploy that touches a service's Python files:**
```bash
# Immediately after docker restart, tail logs for an ImportError/crash-loop —
# don't just assume "docker restart" succeeding means the app booted:
docker logs stockai-<service>-1 --tail 20
# A clean boot ends in "Application startup complete." / "Uvicorn running on ...".
# If you see a traceback instead, the container's /app files disagree with the
# current git checkout — diff them directly:
docker exec stockai-<service>-1 grep -n '<symbol_removed_or_added_by_the_last_fix>' /app/src/<file>.py
```

**Design invariant:** Never assume a past "deploy to production" step actually completed just
because it's described as done in a tracker entry or prior session summary — after any
`docker restart` in production, always tail logs and confirm a clean startup message before
considering the deploy verified. A container that "looks running" (`docker ps` shows `Up`) can
still be serving requests from **before** a crash-and-silent-fallback, or — as here — simply
never picked up the intended change at all until the next unrelated restart exposes it.

**CORRECTED 2026-07-08 — production CAN also be affected, this claim was wrong:** this section
previously claimed "production is NOT usually affected" based on two prior checks
(TuneHistory, index_membership) that happened to find production current. On 2026-07-08, a
routine signal-engine deploy (unrelated congress-score fix, T237-EI1) crashed on restart with
the EXACT same `ImportError: cannot import name 'TuneHistory' from 'db'` on **production** —
proving production's `shared/db/` had silently drifted too, the same way local dev containers
do. The original theory (production always copies `shared/db/` explicitly per the Deployment
Pattern) is only true when someone actually remembers to run that step for every affected
container on every relevant deploy — exactly the kind of manual step that gets silently skipped,
per the pattern already documented above ("PRODUCTION Container Ran Stale Service-Local Files").
Fixed by syncing `shared/db/__init__.py` + `models.py` to `stockai-signal-engine-1` and
restarting — same fix pattern as the local-dev case. **Do not assume production's `shared/db/`
is current just because it "usually" was in the past** — always verify with a clean-startup log
check after any restart, the same discipline required for service-local files.

**Consider:** after any `shared/db/models.py` change, proactively sync `shared/db/` to every
local dev container in the same pass, rather than waiting for each one to surface its own crash
on a different field weeks later.

---


## Recurring Issue: `docker compose up -d --force-recreate <one-service>` Can Recreate EVERY Service — And Recreation Silently Reverts `docker cp`-Patched Files

**Symptom:** Running `docker compose -f docker/docker-compose.yml up -d --force-recreate frontend`
(the standard, documented frontend deploy step) unexpectedly recreates every other service too —
market-data, ml-prediction, signal-engine, decision-engine, etc. — not just frontend. Any
in-progress background work in one of those other containers (e.g. a long-running model retrain
started via `docker exec ... python3 -c "..."`) gets killed when its container is destroyed and
rebuilt. Separately — and more dangerously — any file previously deployed via `docker cp` (the
standard "hotfix without a full image rebuild" pattern used throughout this file) is **silently
reverted** to whatever was baked into the image at its last build, because recreation destroys
the container's writable layer entirely and starts fresh from the image.

**Root cause (found 2026-07-08):** An `.env` change (SMTP_PASSWORD) earlier in the same session
apparently altered docker-compose's computed config hash for other services too (likely because
they share `.env` as their env_file), making compose consider them "changed" and eligible for
recreation on the next `up -d`, even though only `frontend` was named. This surfaced in two ways
in the same incident: (1) a production meta-model retrain running inside `stockai-ml-prediction-1`
was silently killed mid-run when that container was swept into the same recreate; (2) after
restarting the retrain, it *appeared* to succeed (wrote a new artifact, real AUC) but actually
trained against a **stale, reverted** `builder.py` — the recreate had silently undone an earlier
`docker cp` of a real code fix (removing a feature column), so the retrain used the OLD feature
set while live inference was already using the NEW one, causing a real shape-mismatch error
("index 66 is out of bounds for axis 1 with size 66") that looked like a fresh bug but was
actually stale-file poisoning of the retrain itself.

**What to check before AND after any `docker compose up -d --force-recreate <service>`:**
```bash
# Before: note which containers are currently running which images/uptimes, so you can tell
# afterward if anything you didn't name also got recreated:
docker ps --format '{{.Names}}: {{.Status}}'

# After: re-run the same command and diff — any container with a suspiciously fresh "Up X
# seconds" that you didn't intend to touch was swept in too:
docker ps --format '{{.Names}}: {{.Status}}'

# If ANY service besides the one you named got recreated, re-verify every docker cp-patched
# file in that service is still current — recreation reverts to the baked-in image silently,
# with no error, no warning:
docker exec stockai-<service>-1 md5sum /app/<path/to/file.py>
md5sum services/<service>/src/<path/to/file.py>   # compare against the git checkout
# If they differ, re-run the docker cp + restart for that file before trusting anything that
# depends on it (a retrain, a manual verification, etc.) — a mismatch here means the container
# is running an older version of the code than what's actually committed.
```

**Design invariant:** After ANY `docker compose up -d --force-recreate`, treat every currently
running container as a candidate for having reverted, not just the one you named — check `docker
ps` before and after, and re-verify file checksums on anything you'd previously hotfixed via
`docker cp` in a container that got swept in. Never assume a long-running background job (a
retrain, a bulk backfill) survived a `docker compose up` on an unrelated service without checking
`docker ps`/process state directly afterward.

---


## Recurring Issue: A Full EC2 Instance Reboot Reverts EVERY `docker cp` Hotfix Across ALL Containers At Once

**Symptom (2026-07-17):** the EC2 instance became completely unreachable (no SSH, no HTTPS,
100% ping loss) for an unknown external reason (not caused by anything deployed this session —
last confirmed action before the outage was a routine frontend image build). On recovery, every
container had been recreated fresh — `api-gateway` crash-looped immediately
(`ModuleNotFoundError: No module named 'numpy'`, a real, separately-documented bug below), and
a systematic check found **12 service-local files across 5 services, plus `shared/db/models.py`
and `shared/common/logging.py` across all 10 other backend containers, had silently reverted**
to whatever was baked into each image at its last real build — every fix applied via `docker cp`
during this entire session (T230-CHARTING-PREMARKET's ingestion.py/yfinance_adapter.py,
AUD250's scheduler.py/routes.py fixes across 4 services, SELFIMPROVE-NEVER-CALIBRATED-PARAMS'
paper_portfolio.py/paper_trading_engine.py, T254's trendlines.py FVG detector, and both
`shared/` files) was gone. One file (`event-intelligence`'s `macro_reaction.py`) didn't exist
in the image AT ALL — that container's image predates the file's creation and it had only ever
lived via `docker cp`, never a real rebuild.

**Root cause:** this is the exact risk already documented elsewhere in this file under
"`docker compose up -d --force-recreate <one-service>` Can Recreate EVERY Service — And
Recreation Silently Reverts `docker cp`-Patched Files" — except at MAXIMUM scale. That entry
was about ONE `docker compose up` sweeping in unintended sibling services. A full instance
reboot recreates **literally every container**, all at once, with no warning and no way to
`docker ps`-diff "before" against "after" the way that entry's own mitigation describes — there
is no "before" snapshot when the whole machine went down external to any action taken here.

**Fix applied:** systematically diffed every `docker cp`-patched file this session had touched,
service-by-service, against the git checkout (`diff <(docker exec ... cat ...) <local path>`)
for all 11 backend services — not just the ones that crashed. Found and re-`docker cp`'d 12
service-local files + `shared/db/models.py` + `shared/common/logging.py` to every affected
container, cleared `__pycache__`, restarted, verified clean startup logs and a live functional
check (confirmed `fair_value_gaps` still returns real data from technical-analysis post-restart).

**What to check after ANY event that force-restarts the whole instance (reboot, host
maintenance, an EC2 status check failure, `docker compose down && up` at the compose-file
level) — not just after a single-service `--force-recreate`:**
```bash
# For EVERY service you've ever docker cp'd a fix into this session (check your own session
# history, not just what crashed) — diff the running container against the git checkout:
for f in <list of every file you docker cp'd this session>; do
  diff <(docker exec stockai-<service>-1 cat /app/<path>) <local repo path>
done
# Also check shared/db/ and shared/common/ across ALL 11 containers, not just the ones you
# personally touched — a shared file synced to container A during today's work is just as
# reverted as one synced to container B.
for c in market-data signal-engine ranking-engine technical-analysis event-intelligence \
         research-engine api-gateway ml-prediction decision-engine strategy-engine portfolio-optimizer; do
  diff <(docker exec stockai-$c-1 cat /app/shared/db/models.py) shared/db/models.py
  diff <(docker exec stockai-$c-1 cat /app/shared/common/logging.py) shared/common/logging.py
done
```

**Design invariant, stated more strongly than the earlier single-service version of this
entry:** `docker cp` is fundamentally a SESSION-SCOPED hotfix, not a deployment. ANY event that
recreates a container — a targeted `--force-recreate`, a full `docker compose down/up`, or an
entire instance reboot outside anyone's control — reverts it back to whatever the image was
built with. The only way a fix survives across an unplanned full-instance event is if it was
baked into a real image via `docker compose build` / `docker build` at some point. Every
`docker cp` fix applied in a session should be treated as "still owed a real image rebuild"
until that rebuild actually happens — this incident is the proof that the gap between
"hotfixed" and "durably deployed" is not hypothetical.

**CORRECTED same day — the first recovery pass above was itself incomplete.** It only diffed
files this session specifically remembered `docker cp`'ing (the ones tied to fixes made
earlier in the same conversation), not an exhaustive sweep of every `.py` file in every
service. The user then reported two NEW-looking bugs in the just-built Reports page — CAPE
stuck loading forever, News & Macro not working — which traced back to the SAME root cause,
just in files the first pass never checked: `event-intelligence`'s `routes.py`, `scheduler.py`,
`economic.py`, and `services/valuation.py` had ALSO reverted. `valuation.py` (the entire CAPE
feature, Tier 249) was missing from the container **entirely** — its image predates the file's
creation, meaning `docker cp` had been the ONLY way that feature was ever deployed, the whole
time since it was built. A genuinely exhaustive re-sweep (every `.py` file under every
`services/*/src/`, not a remembered subset) found **31 reverted files across 9 services**
total: `event-intelligence` (7 files + 1 missing entirely — worst offender), `ranking-engine`
(2), `ml-prediction` (5), `decision-engine` (6 — its whole core scoring pipeline), `strategy-
engine` (3), `portfolio-optimizer` (2), `market-data`'s `admin.py` + 2 more, `signal-engine`'s
`generators/signals.py`, `technical-analysis`'s `indicators/core.py`. Only `research-engine`
and `api-gateway` were genuinely fully clean. All 31 re-synced, verified byte-identical,
restarted, and confirmed live (`GET /events/valuation/cape` returns real data; `GET
/events/overview` includes `latest_macro_reaction`).

**The lesson under the lesson:** after an incident like this, a "fixed" claim based on
checking only the files you personally remember touching is itself unverified — the same
"verify in both directions, don't trust a status claim" discipline this file already applies
to stale tracker entries (see the SE-F2 section) applies just as much to your OWN prior
"done" claim within the same session. The only reliable check is exhaustive: every file, every
service, not a remembered subset. A dedicated sweep agent doing `find services/<svc>/src -name
'*.py'` then diffing each one against the container is cheap enough to just always do fully,
rather than trying to reconstruct "which files did I touch this session" from memory.

---


## Recurring Issue: `event-intelligence` Never Called `init_db()` — A New Table Silently Depended on Which Sibling Service Restarted First (Found + Fixed 2026-08-19)

**Found live while deploying IF-04's new `CrossAssetReading` table** — a real, previously-
unnoticed bootstrap gap surfaced by trying to sync real data immediately after deploy, not by
theoretical review.

**Symptom**: `sync_cross_asset()` raised `psycopg2.errors.UndefinedTable: relation
"cross_asset_readings" does not exist` when manually invoked right after restarting
`event-intelligence` with the new model in its `shared/db/models.py`.

**Root cause**: `Base.metadata.create_all()` (`shared/db/session.py`'s `init_db()`) is only
ever called by **2 of 11** backend services — confirmed via `grep -rln "init_db()"
services/*/src/main.py` returning just `market-data` and `research-engine`. Every other
service, including `event-intelligence`, never runs `create_all()` at all — it silently
depended on ONE OF THOSE TWO services happening to restart AFTER a new shared table's model
was added, purely by chance of deploy ordering. Confirmed directly: `economic_events`
(an old, long-established table) already existed in production, but `cross_asset_readings`
(brand new) did not — until `market-data` was restarted, at which point `create_all()` ran and
the table appeared immediately.

**Fix applied**: added `init_db()` to `event-intelligence`'s own `main.py` startup, wrapped in
a small local `async def _on_startup(): init_db(); await start_scheduler()` (matching
`market-data`'s own established `on_startup` pattern exactly — `create_app()`'s `lifespan`
always `await`s whatever callable is passed). `Base.metadata.create_all()` is idempotent, so
calling it from a second service is completely safe — it only ever creates tables that don't
exist yet, never touches ones that do.

**Live-verified end-to-end**: before the fix, a direct call to `sync_cross_asset()` inside the
freshly-restarted `event-intelligence` container failed with `UndefinedTable`. After adding
`init_db()` and restarting, the container's own startup log showed `sync_cross_asset` firing
its startup-seed task and correctly reporting `{"synced": 105, "skipped": null}` — the table
existed and real FRED data landed on the very first boot, with no dependency on `market-data`'s
own restart timing at all.

**What to check if a similar `UndefinedTable` error appears after adding a new shared model**:
```bash
# Confirm which services actually call init_db() today:
grep -rln "init_db()" services/*/src/main.py

# Check whether the table actually exists in production:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\dt" | grep <new_table_name>

# If missing, restart market-data OR research-engine (either one's create_all() will create
# it) — or, better, add init_db() to whichever service actually OWNS/writes the new table,
# matching this fix's own reasoning, so it's never dependent on a sibling's restart order again.
```

**Design invariant**: any service that WRITES to a brand-new shared table should call
`init_db()` in its own startup, not rely on `market-data`/`research-engine` happening to have
restarted more recently. This is now the 2nd occurrence of this exact class of bootstrap gap
in this app (only 2 of 11 services do this) — worth a broader audit of the other 9 services if
a similar issue recurs.

---


## INCIDENT 2026-08-20: Full EC2 Instance Reboot During a Routine Frontend Rebuild — Recovered,
## Plus a Real Pre-Existing shared/db/ Drift Found and Fixed During the Sweep

**What happened**: mid-session, while rebuilding the frontend image for a routine tracker-page
docs update (`git pull` on EC2 had already succeeded), the instance became fully unreachable —
SSH timed out at the banner exchange, `ping` showed 100% packet loss, `https://lausing.com`
returned nothing. This lasted roughly 10 minutes across several checks. When SSH came back,
`uptime` showed the host itself had been up only 5 minutes, and **every one of the 15
containers** showed the identical ~5-minute uptime — confirming a genuine full instance
reboot, not just the build process or SSH session dying. Root cause of the reboot itself is
unknown (external to anything this session did, most likely — the same class of unexplained
event this file's own 2026-07-17/2026-08-05 incident entries document, where the trigger was
never determined and a manual/automatic restart was the only recovery path).

**Recovery, following this file's own standing "docker cp is session-scoped, a reboot reverts
everything, sweep exhaustively not from memory" discipline**:

1. Confirmed the git checkout on the EC2 host itself was untouched (git repos are just files
   on disk, unaffected by container recreation) — `git log -1` still showed the latest commit.
2. Rebuilt and redeployed the frontend image (the original build had been interrupted
   mid-way by the reboot and needed re-running from scratch, not resumed).
3. **Ran a genuinely exhaustive sweep** — not a remembered subset — across all 12 backend
   services: collected `md5sum` for every `.py` file under `/app/src`, `/app/shared/db`, and
   `/app/shared/common` inside each running container, then diffed against the equivalent
   local git checkout paths.

**Result — `/app/src` and `/app/shared/common` were completely clean (0 mismatches across all
174 `src/` files + all `common/` files, all 12 services)** — this particular reboot didn't
actually revert anything in those two categories, likely because no outstanding
session-scoped `docker cp` hotfix existed in either at the moment it happened.

**But the sweep found a REAL, PRE-EXISTING drift in `shared/db/`, unrelated to this reboot**:
11 of 12 services (everything except `market-data`) were running STALE copies of
`shared/db/__init__.py`/`models.py`/`session.py` — missing 4 real model classes
(`PortfolioRiskMetric`, `StressTestResult`, `RestrictedSymbol`, `PaperTradeDecisionLog`, all
from the IF-01/IF-12 sessions) and missing `session.py`'s `prebreakout_alert_outcomes` column-
migration block. This wasn't caused by the reboot — `market-data` had received a `docker cp`
update to these 3 files at some point in an earlier session, but that update was never
propagated to the other 11 services, exactly the recurring class of gap this file's own
"Local Dev Containers Run Stale shared/db/" entry documents. **The consequential part**:
`research-engine` — one of only 2 services that call `init_db()` at startup (per this file's
own documented "only 2 of 11 services do this" gap) — was ALSO running the stale copy, meaning
its own `create_all()` call would never have created those 4 tables if `market-data` hadn't
already done so first, and its migration block would never have run the `prebreakout_alert_
outcomes` column additions at all.

**Fix**: synced `shared/db/__init__.py`/`models.py`/`session.py` from the git checkout to all
11 stale services via `docker cp`, cleared `__pycache__`, restarted all 11. Confirmed clean
startup logs (`"Application startup complete"`, zero tracebacks) across every service — the 2
log lines that DID appear during the restart window (`signal-engine`'s `ml.fetch_failed:
timed out` and `news-intelligence`'s `job_edgar ... CancelledError`) were both transient,
expected artifacts of an in-flight request/scheduled job being interrupted by the restart
itself, unrelated to the `shared/db/` sync. Re-ran the full checksum sweep a second time —
0 mismatches across all 12 services for `src/`, `shared/db/`, AND `shared/common/`. Confirmed
all 4 previously-missing tables (`portfolio_risk_metrics`, `stress_test_results`,
`restricted_symbols`, `paper_trade_decision_log`) exist in production Postgres. Final state:
all 15 containers healthy, both the frontend and API responding with real 200s at
`https://lausing.com`.

**Design invariant reinforced (the Nth recurrence of this exact discipline in this file's own
history)**: after ANY event that force-restarts containers — a targeted `--force-recreate`, a
full `docker compose down/up`, or a genuine instance reboot like this one — the only reliable
recovery check is an EXHAUSTIVE sweep (every `.py` file, every service, `src/` AND
`shared/db/` AND `shared/common/`) diffed directly against the git checkout, never a
remembered subset of "files I touched this session." This incident's own drift
(`shared/db/`) predated the reboot entirely and would have stayed silently unnoticed
indefinitely if this sweep hadn't been run as a matter of course during recovery — the reboot
was the OCCASION for finding it, not the CAUSE of it.

**What to check if this recurs**:
```bash
# Confirm host uptime vs. container uptime — if ALL containers share an unusually short,
# identical uptime, that's the signature of a full instance reboot, not a targeted restart:
uptime
docker ps --format '{{.Names}}: {{.Status}}' | sort

# Exhaustive src/ + shared/ sweep, one service at a time (repeat for every service):
docker exec stockai-<service>-1 sh -c "cd /app/src && find . -name '*.py' -exec md5sum {} \;"
docker exec stockai-<service>-1 sh -c "cd /app/shared/db && find . -name '*.py' -exec md5sum {} \;"
docker exec stockai-<service>-1 sh -c "cd /app/shared/common && find . -name '*.py' -exec md5sum {} \;"
# Compare each against the equivalent local `md5sum` output for the git checkout — any
# mismatch means a docker-cp'd file was reverted (or, as found here, was never actually
# propagated to that service in the first place).

# Confirm the 4 tables this incident's own fix restored are still present:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\dt" | grep -iE \
  'portfolio_risk_metrics|stress_test_results|restricted_symbol|paper_trade_decision_log'
```

---

