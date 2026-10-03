#!/usr/bin/env bash
# Rebuild and recreate the frontend, under the same deployment lock as the backends.
#
# WHY A SCRIPT RATHER THAN A TYPED COMMAND. The frontend deploy was an ad-hoc
# `docker build && docker compose up -d --force-recreate` pasted by hand, so it sat outside
# every guard the backend path had. That is how it ended up racing a background deploy: the
# lock cannot cover an entry point that does not exist.
#
# It also handles the failure that race produced — a container left under a hash-prefixed name
# because the canonical one was still held — rather than requiring someone to notice it.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="$REPO_ROOT/docker/docker-compose.yml"

# shellcheck source=scripts/deploy_lock.sh
. "$REPO_ROOT/scripts/deploy_lock.sh"
DEPLOY_WHAT="frontend"
deploy_lock_acquire "$DEPLOY_WHAT" || exit 75

cd "$REPO_ROOT"
echo "Building frontend from $(git rev-parse --short HEAD)"

# DOCKER_BUILDKIT=0 deliberately: BuildKit serves cached layers even with --no-cache here and
# produces a stale image. See docs/incidents/ec2-disk-and-frontend-builds.md.
if ! out=$(DOCKER_BUILDKIT=0 docker build -q -f frontend/Dockerfile -t stockai-frontend:latest . 2>&1); then
  echo "FRONTEND BUILD FAILED:"; echo "$out"; exit 1
fi
echo "built"

# Clear any orphan left by an earlier interrupted recreate, so the canonical name is free.
for orphan in $(docker ps -a --format '{{.Names}}' | grep -E '_stockai-frontend-1$' || true); do
  echo "removing orphaned container $orphan"
  docker rm -f "$orphan" >/dev/null 2>&1 || true
done

if ! out=$(docker compose -f "$COMPOSE" up -d --force-recreate frontend 2>&1); then
  echo "FRONTEND RECREATE FAILED:"; echo "$out"; exit 1
fi

for _ in $(seq 1 30); do
  state=$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
          stockai-frontend-1 2>/dev/null || echo "missing/none")
  case "$state" in
    running/healthy) echo "frontend: $state"; exit 0 ;;
    running/none)    echo "frontend: running (no healthcheck declared)"; exit 0 ;;
  esac
  sleep 5
done
echo "FRONTEND DID NOT BECOME HEALTHY (last state: ${state:-unknown})"
docker ps -a --filter name=frontend --format '{{.Names}} {{.Status}}'
exit 1
