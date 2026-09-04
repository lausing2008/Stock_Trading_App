## Recurring Issue: EC2 Disk Fills Up from Dangling Docker Images

**Symptom:** `docker build` fails mid-copy with `no space left on device`, even though the
image being built is a normal size. `df -h /` shows the root volume nearly 100% full.

**Root cause (found 2026-07-03):** Every `DOCKER_BUILDKIT=0 docker build --no-cache` for the
frontend (the required pattern per this file's Deployment Pattern section) leaves the
previous image's layers behind as dangling `<none>:<none>` images once the tag moves to the
new build. These accumulate silently across repeated deploys — one session's worth of
frontend rebuilds alone consumed 77GB of reclaimable, unused image layers.

**Fix:** `docker image prune -f` — safe, only removes dangling/untagged images, never touches
anything currently running or tagged. Freed 460MB → 76GB available in the 2026-07-03 incident
with zero container disruption (all services stayed healthy throughout).

**What to check before any frontend rebuild:**
```bash
df -h /                    # if root volume is >90% full, prune first
docker system df           # shows reclaimable space by type
docker image prune -f      # safe cleanup — dangling images only
```

**Consider:** a periodic (weekly) `docker image prune -f` cron job on EC2 so this doesn't
require noticing a failed build first.

---


## Recurring Issue: Slow Frontend Builds (24–47 min) — `--no-cache` Was Unnecessary

**Symptom:** `docker build -f frontend/Dockerfile` (with `DOCKER_BUILDKIT=0`, per the Deployment
Pattern section) routinely took 24–47 minutes on the EC2 t3.medium, even for tiny changes (a few
lines in one file). Build time trended upward across a session (24 → 28 → 40 → 47 min across four
consecutive deploys on 2026-07-07), which looked like — but was not — EC2 resource degradation.

**Root cause (found 2026-07-07):** The deployment pattern included `--no-cache`, which disables
ALL Docker layer caching, not just the specific BuildKit cache bug it was meant to guard against.
`frontend/Dockerfile` has a multi-stage build where `RUN npm install --legacy-peer-deps` is its own
early layer, keyed only on `package.json`/`package-lock.json` (see `COPY frontend/package.json
frontend/package-lock.json* ./` before the install line) — this layer is safe to cache and almost
never needs to be invalidated across normal deploys, since dependencies change far less often than
application source. `--no-cache` forced a full `npm install` from the registry on every single
deploy regardless, which is the actual reason builds took as long as they did — not `improvements.tsx`'s
size as initially (incorrectly) suspected mid-investigation, and not EC2 hardware degrading.

**The original justification for `--no-cache` doesn't hold up:** the CLAUDE.md warning that
motivated it was about `docker compose build --no-cache frontend` silently serving stale layers —
that bug is specific to BuildKit's cache, not Docker's classic (non-BuildKit) cache. Once
`DOCKER_BUILDKIT=0` is set and `docker build` is invoked directly (not via `docker compose build`),
the classic builder's normal layer caching is safe — cached layers are correctly invalidated when
their `COPY`'d inputs change, which is exactly the guarantee needed.

**Verification before trusting this (important — don't just take the theory on faith):** built
with `DOCKER_BUILDKIT=0 docker build -f frontend/Dockerfile -t stockai-frontend:cache-test .` (no
`--no-cache`, separate test tag so `latest`/prod traffic was never at risk) and confirmed BOTH: (1)
build time — **~6 minutes**, vs. 24–47 minutes with `--no-cache`; (2) freshness — grepped the built
image's compiled JS chunks for two strings that only existed in that session's latest, uncommitted-
until-then source (`'Unusual Vol Today'`, `'Min RVOL'`) and confirmed both were present in
`screener-*.js` and `improvements-*.js`, proving the cached build correctly picked up the latest
source rather than serving something stale.

**Investigation mistake worth noting for next time:** while monitoring the test build, `ps aux |
grep docker build` kept showing a process as "still running" long after the actual image had
finished (confirmed later via `docker images ... --format '{{.CreatedAt}}'`, which showed the real
6-minute completion time). A lingering shell/SSH pipeline process, not the build itself, was still
alive. **Always check the image's actual `CreatedAt` timestamp to determine whether a build
finished — `ps aux` can show a stale process long after `docker build` itself has completed.**

**Fix:** Deployment Pattern (above) updated to drop `--no-cache` — `DOCKER_BUILDKIT=0 docker build
-f frontend/Dockerfile ...` (no `--no-cache`) is now the standard. `docker compose build frontend`
(going through docker compose) must still never be used, regardless of cache flags — that's the
part of the original warning that remains true.

**What to check if builds are slow again:**
```bash
# Confirm which build path is being used — must be a direct `docker build`, not `docker compose build`
# Confirm --no-cache is NOT present (it shouldn't be, per this fix)
# Check actual completion via image timestamp, not `ps aux`:
docker images stockai-frontend:latest --format '{{.CreatedAt}}'
```
If builds are still slow with `--no-cache` correctly removed, the next suspect is `npm run build`
itself (Next.js compiling/statically-generating every page) — `improvements.tsx` is 13,700+ lines
and growing every session; splitting it up or trimming its content would be the next lever to pull,
but wasn't needed once `--no-cache` was correctly identified as the actual cause here.

---

## Recurring Issue: AUD-LOGROTATE — No Container Log Rotation Configured At All, 54GB Accumulated Platform-Wide (2026-09-04)

**User request:** "let's clear up some of the huge log files on the server to free up some
spaces." `df -h /` showed 76% used (25GB free of 100GB). `docker system df` broke this down:
28.14GB reclaimable in dangling images, 22.82GB in stale BuildKit build cache (226 layers, 0
active — leftover from repeated frontend rebuilds, including a duplicate/BuildKit-vs-legacy
mess from earlier the same session), and container logs themselves: `docker system df`'s
Containers row showed 1.17GB, 99% reclaimable.

**Root cause:** `docker-compose.yml` had never configured a `logging:` block on ANY service —
every container ran on Docker's default `json-file` driver with no `max-size`/`max-file` at
all, so a container's own log file grows completely unbounded for as long as it stays up. Direct
inspection (`sudo du -sh /var/lib/docker/containers/*/`) confirmed this concretely:
market-data's own log file alone had grown to **1.9GB** (the busiest/longest-running container —
66 scheduled jobs, 5-min-cadence market refreshes), ml-prediction 929MB, news-intelligence 417MB
(1-2 min RSS/EDGAR polling cadence), signal-engine 245MB, api-gateway 170MB. No `daemon.json`
existed either, so there was no daemon-level default catching this.

**Fixed, in order:**
1. `docker image prune -af` — reclaimed 23.45GB of dangling/untagged images (confirmed safe:
   these are images no running container references, mostly leftover intermediate layers from
   today's several frontend rebuild attempts).
2. `docker builder prune -af` — reclaimed 33.19GB of stale BuildKit build cache (0 active
   layers; this is the LEGACY (`DOCKER_BUILDKIT=0`) build path's own cache store, separate from
   what a `docker compose build` would use — pruning it does not affect the deployment
   pattern's own standard `DOCKER_BUILDKIT=0 docker build` command).
3. Truncated (not deleted) every container's own `*-json.log` file in place via
   `truncate -s 0 <file>` — the standard safe way to reclaim log space on a LIVE container:
   Docker keeps writing/appending to the same (now-empty) file handle afterward, unlike deleting
   the file outright, which can orphan the file descriptor until the container restarts.
   Confirmed all containers stayed healthy/running through this with zero disruption.
4. Added a shared `x-logging: &default-logging` anchor (`max-size: "20m"`, `max-file: "5"` —
   caps any one container's total log footprint at 100MB, generous enough for a real multi-day
   `docker logs` debugging window, but bounded instead of unbounded) to `docker/docker-compose.yml`.
   Threaded into the existing `x-py-common` anchor (covers all 11 backend services in one
   place, matching this file's own established DRY convention) plus `postgres`/`redis`/
   `frontend` individually, since those 3 don't extend `x-py-common`. Validated via
   `docker compose config --quiet` (no errors) and a full `docker compose config` grep
   confirming all 14 real services resolve `max-size: 20m`/`max-file: "5"`.

Net result: disk usage dropped from 76% (25GB free) to 30% (71GB free) on this pass alone.
The rotation config only takes effect for CONTAINERS RECREATED after this change (a plain
`docker restart` does not pick up a new `logging:` block — needs `--force-recreate` or the
container's next natural recreation via a future deploy) — so some containers may still be
running without rotation until their next recreation.

**What to check if disk fills up again:**
```bash
df -h /
docker system df
# If Containers row is high-% reclaimable again, either rotation was never picked up by a
# still-running old container, or a NEW service was added without extending x-py-common /
# getting its own explicit `logging: *default-logging` line — check docker-compose.yml first.
docker compose -f docker/docker-compose.yml config --quiet   # validates the anchors resolve
docker compose -f docker/docker-compose.yml config | grep -c "max-size: 20m"   # should be 14
```

---

