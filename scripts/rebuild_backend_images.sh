#!/usr/bin/env bash
# Durably rebuild + recreate backend service images on EC2.
#
# WHY THIS EXISTS. `docker cp` is a session-scoped hotfix: it writes to the container's
# writable layer, not the image, so any recreation or reboot reverts it (see
# docs/incidents/docker-deploy-staleness.md). Anything that must survive needs a rebuild —
# and a rebuild of 12 services done by hand is exactly where mistakes hide.
#
# IT EXISTS IN THIS FORM BECAUSE OF A REAL OUTAGE (2026-09-17). An ad-hoc inline loop doing
# the same job ran:
#
#     docker compose up -d --force-recreate "$svc" >/dev/null 2>&1
#
# and then polled health for a bounded time before moving on. api-gateway declares
# `depends_on: market-data: condition: service_healthy`. market-data was briefly unhealthy
# (concurrent startup schema-sync deadlock), so compose CREATED the api-gateway container and
# refused to START it, printing an error — straight into /dev/null. The loop's health poll then
# hit a container with no Health state at all, shrugged, and the script exited 0.
#
# Result: the API was down for ~10 minutes while the deploy reported success. That is the same
# failure-masking class as AUD-T400-CIHIDESFAILURE, one layer down: the runner could not go red.
#
# So this script's rules are:
#   1. Never discard command output on failure.
#   2. Wait for BOTH `running` AND `healthy`, and treat a missing Health key as failure, not
#      as "not applicable".
#   3. Stop at the first failure — with 12 interdependent services, continuing past a broken
#      one turns one problem into several.
#   4. Exit non-zero if anything failed, and say which.
#
# Usage, on EC2 from the repo root:
#   bash scripts/rebuild_backend_images.sh                 # all backends
#   bash scripts/rebuild_backend_images.sh market-data     # just these
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$REPO_ROOT/docker/docker-compose.yml"
HEALTH_TIMEOUT_S=240

# api-gateway LAST on purpose: nginx proxies the public site through it, and it depends on the
# health of several services above, so it must be the final thing recreated.
ALL_SERVICES=(technical-analysis ml-prediction research-engine ranking-engine strategy-engine
              portfolio-optimizer event-intelligence news-intelligence decision-engine
              signal-engine market-data api-gateway)

if [ "$#" -ge 1 ]; then SERVICES=("$@"); else SERVICES=("${ALL_SERVICES[@]}"); fi

cd "$REPO_ROOT"
echo "Rebuilding from $(git rev-parse --short HEAD) — ${#SERVICES[@]} service(s)"
[ -n "$(git status --porcelain -- shared services)" ] && \
  echo "WARNING: working tree is dirty; the images will contain uncommitted changes."
echo

failed=()
for svc in "${SERVICES[@]}"; do
  echo "──────── $svc ────────"

  # Build. On failure the output is SHOWN, not swallowed.
  if ! build_out=$(DOCKER_BUILDKIT=0 docker build -q -f "services/$svc/Dockerfile" \
                   -t "stockai-$svc:latest" . 2>&1); then
    echo "BUILD FAILED:"; echo "$build_out" | tail -25
    failed+=("$svc (build)"); break
  fi
  echo "built"

  # Recreate. Compose refuses to START a container whose depends_on health gate is unmet — it
  # leaves it in `Created` and says so on stderr. That message is the one that got discarded
  # during the outage above, so it is captured and printed on any failure below.
  up_out=$(docker compose -f "$COMPOSE" up -d --force-recreate "$svc" 2>&1)
  up_rc=$?

  # Wait for running AND healthy. A container stuck in `Created` never gains a Health key,
  # which is the exact state that previously read as success.
  ok=0
  for _ in $(seq 1 $((HEALTH_TIMEOUT_S / 3))); do
    state=$(docker inspect -f '{{.State.Status}}' "stockai-$svc-1" 2>/dev/null || echo missing)
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
             "stockai-$svc-1" 2>/dev/null || echo none)
    [ "$state" = "running" ] && [ "$health" = "healthy" ] && { ok=1; break; }
    [ "$state" = "exited" ] && break
    sleep 3
  done

  if [ "$ok" -ne 1 ]; then
    echo "NOT HEALTHY — state=${state:-?} health=${health:-?} (compose rc=$up_rc)"
    echo "--- compose output ---"; echo "$up_out" | tail -15
    echo "--- container logs ---"; docker logs --tail 25 "stockai-$svc-1" 2>&1 | tail -25
    failed+=("$svc (state=${state:-?}/health=${health:-?})"); break
  fi
  echo "$svc: running/healthy"
done

echo
if [ "${#failed[@]}" -gt 0 ]; then
  echo "REBUILD FAILED: ${failed[*]}"
  echo "Roll back one service with:"
  echo "  docker tag <previous-image-id> stockai-<svc>:latest && \\"
  echo "  docker compose -f $COMPOSE up -d --force-recreate <svc>"
  exit 1
fi

echo "All ${#SERVICES[@]} service(s) rebuilt, recreated, and healthy."
echo "Now confirm drift is zero from your workstation: bash scripts/check_deploy_drift.sh"
