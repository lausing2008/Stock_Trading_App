#!/usr/bin/env bash
# The EC2 half of scripts/deploy.sh: everything that builds, recreates, verifies or prunes,
# run as ONE process on the host under the host lock (scripts/deploy_lock.sh).
#
# WHY IT IS A SEPARATE SCRIPT (2026-10-22). deploy.sh used to issue one SSH command per step,
# so no single process on the host owned the work and it never took the lock at all. A backup
# or a second deploy could start between its build and its recreate. deploy.sh now runs
# `git pull` and this script inside one locked remote command; this script acquires the lock
# again and, finding the caller's token, proceeds as a nested holder rather than refusing.
# Run directly on EC2 it takes the lock itself.
#
# The step logic below moved here unchanged from deploy.sh except that a failed build now
# stops the run (pipefail), where the old `... | tail -3` reported tail's status and went on to
# recreate the service from its previous image.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
COMPOSE="docker compose -f docker/docker-compose.yml"
# Release identity for durable measurement records: the commit this checkout builds, marked
# "-dirty" when tracked service/shared/docker files differ from it. docker-compose.yml passes it
# into every Python container; a container recreated outside this script reports "unrecorded".
STOCKAI_RELEASE_SHA="$(git rev-parse HEAD 2>/dev/null || echo unrecorded)"
git diff --quiet HEAD -- services shared docker 2>/dev/null || STOCKAI_RELEASE_SHA="${STOCKAI_RELEASE_SHA}-dirty"
export STOCKAI_RELEASE_SHA
MIN_FREE_GB_BUILD="${MIN_FREE_GB_BUILD:-25}"
WARN_FREE_GB="${WARN_FREE_GB:-40}"
DEPLOY_SETTLE_S="${DEPLOY_SETTLE_S:-45}"
# Outgoing container logs are kept here before each recreate (2026-10-30), newest N per service.
DEPLOY_LOG_DIR="${DEPLOY_LOG_DIR:-$HOME/deploy-evidence/container-logs}"
DEPLOY_LOG_KEEP="${DEPLOY_LOG_KEEP:-10}"

c_red=$'\033[31m'; c_yel=$'\033[33m'; c_grn=$'\033[32m'; c_off=$'\033[0m'
say()  { printf '%s\n' "$*"; }
warn() { printf '%s%s%s\n' "$c_yel" "$*" "$c_off"; }
die()  { printf '%s%s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }
ok()   { printf '%s%s%s\n' "$c_grn" "$*" "$c_off"; }

free_gb() { df -BG --output=avail / | tail -1 | tr -dc '0-9'; }
iowait_pct() { top -bn1 | awk '/Cpu\(s\)/{print $10}' | tr -dc '0-9.' || echo 0; }

do_prune() {
  say "── prune (dangling images + stopped containers only) ──"
  df -h / | tail -1 | sed 's/^/  before: /'
  docker image prune -f | tail -1 | sed 's/^/  /'
  docker container prune -f | tail -1 | sed 's/^/  /'
  df -h / | tail -1 | sed 's/^/  after:  /'
}

# A recreate deletes the old container and its json-file log with it. Twice that erased the only
# evidence of an incident: the 2026-10-22 host stall, and the 2026-10-30 19:00 UTC connection-pool
# exhaustion in market-data, recreated by the 23:38 deploy. So the outgoing container's log is
# saved first. Best effort by design: a failure here warns and the deploy continues.
save_outgoing_logs() {
  local svc="$1" name="stockai-$1-1" out
  docker inspect "$name" >/dev/null 2>&1 || return 0
  mkdir -p "$DEPLOY_LOG_DIR" 2>/dev/null || { warn "  could not create $DEPLOY_LOG_DIR; logs not saved"; return 0; }
  out="$DEPLOY_LOG_DIR/$svc-$(date -u +%Y%m%dT%H%M%SZ)-before-${STOCKAI_RELEASE_SHA:0:12}.log.gz"
  if docker logs --timestamps "$name" 2>&1 | gzip -c >"$out"; then
    say "  saved outgoing logs: $out ($(du -h "$out" | cut -f1))"
  else
    warn "  could not save outgoing logs for $svc"; rm -f "$out"
  fi
  # shellcheck disable=SC2012
  # `|| true`: with pipefail, an empty listing (ls exits 2) must not abort the deploy under set -e.
  { ls -1t "$DEPLOY_LOG_DIR/$svc-"*.log.gz 2>/dev/null | tail -n +"$((DEPLOY_LOG_KEEP + 1))" | xargs -r rm -f; } || true
  return 0
}

deploy_service() {
  local svc="$1"
  say "── $svc ───────────────────────────────────────────────"
  if [[ "$svc" == "frontend" ]]; then
    # DOCKER_BUILDKIT=0 is REQUIRED, not preference: BuildKit serves cached layers even with
    # --no-cache and produces a stale image. See docs/incidents/ec2-disk-and-frontend-builds.md.
    # --no-cache is deliberately NOT passed (fixed 2026-07-07 as unnecessary overhead).
    DOCKER_BUILDKIT=0 docker build -f frontend/Dockerfile -t stockai-frontend:latest . 2>&1 | tail -3
  else
    $COMPOSE build "$svc" 2>&1 | tail -3
  fi
  # --no-deps IS LOAD-BEARING, and its absence caused two outages (2026-10-08).
  #
  # api-gateway declares `depends_on: ... condition: service_healthy` on five services. Without
  # --no-deps, recreating any ONE of those makes compose tear the gateway down as a dependent
  # — and across a 12-service loop it was left REMOVED, not merely stopped. The site still
  # answered 200 because nginx serves the frontend, while every /api call returned 500, so the
  # failure was invisible from the outside. Restoring it is exactly
  # `up -d --no-deps api-gateway`.
  #
  # Every dependency is already running during a deploy, so --no-deps changes nothing else.
  save_outgoing_logs "$svc"
  $COMPOSE up -d --no-deps --force-recreate "$svc" 2>&1 | tail -2
}

# Health is checked AFTER a settle delay and requires the literal string "healthy" — not merely
# "container is up". On 2026-09-10 I reported a deploy clean from a check run ~90 seconds after
# recreation, while health checks were already timing out platform-wide. "Up" is not healthy,
# and "healthy 20 seconds after start" is not evidence the box is fine.
verify() {
  local svcs=("$@")
  say "── verify (settling ${DEPLOY_SETTLE_S}s) ──────────────────────────────"
  sleep "$DEPLOY_SETTLE_S"
  local bad=0
  for svc in "${svcs[@]}"; do
    local st
    if ! st=$(docker inspect --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "stockai-${svc}-1" 2>/dev/null); then
      warn "  $svc: inspection failed or container missing"; bad=1
    elif [[ "$st" == "running/healthy" ]]; then
      ok "  $svc: $st"
    else
      warn "  $svc: ${st:-UNKNOWN}"; bad=1
    fi
  done

  # EVERY SERVICE IN THE FILE MUST STILL EXIST, not only the ones just deployed. Both 2026-10-08
  # outages were deploys that removed api-gateway while it was NOT in the deploy list, so the
  # loop above never looked at it and the run reported "11 healthy" as though that were a count
  # of what was asked for. A container that vanished is the failure this check exists to name.
  local declared missing=""
  if ! declared=$($COMPOSE config --services | tr -d '\r'); then
    warn "  cannot read declared services"; bad=1; declared=""
  fi
  for svc in $declared; do
    [[ "$svc" == "postgres" || "$svc" == "redis" ]] && continue
    local up
    up=$(docker inspect --format '{{.State.Running}}' "stockai-${svc}-1" 2>/dev/null) || up="false"
    [[ "$up" == "true" ]] || missing="$missing $svc"
  done
  if [[ -n "$missing" ]]; then
    warn "  CONTAINER MISSING ENTIRELY:$missing"
    warn "  A removed container is not a slow start. Recreate with:"
    warn "    $COMPOSE up -d --no-deps$missing"
    bad=1
  elif [[ -n "$declared" ]]; then
    ok "  all declared services present"
  fi
  # A deploy that leaves the HOST unhealthy is not a successful deploy, even if the container is.
  local avail iowait; avail=$(free_gb); iowait=$(iowait_pct)
  say "  host: ${avail}G free, iowait ${iowait}%"
  if awk "BEGIN{exit !(${iowait:-0} > 30)}"; then
    warn "  WARNING: iowait still ${iowait}% after deploy — watch for the credit-drain pattern."
  fi
  return $bad
}

# shellcheck source=scripts/deploy_lock.sh
. "$REPO_ROOT/scripts/deploy_lock.sh"

main() {
  [[ $# -gt 0 ]] || die "usage: $0 <service...> | --prune"
  deploy_lock_acquire "deploy:$*" || exit 75

  if [[ "$1" == "--prune" ]]; then do_prune; exit 0; fi

  local wants_frontend=0 avail
  for s in "$@"; do [[ "$s" == "frontend" ]] && wants_frontend=1; done
  avail=$(free_gb)
  # Auto-prune when low, BEFORE building. This is the step whose absence caused the outage.
  if (( avail < WARN_FREE_GB )); then
    warn "  only ${avail}G free — pruning before build"
    do_prune
    avail=$(free_gb)
  fi
  if (( wants_frontend )) && (( avail < MIN_FREE_GB_BUILD )); then
    die "REFUSING frontend build: ${avail}G free < ${MIN_FREE_GB_BUILD}G. Prune or grow the volume.
     A Next.js build on a near-full gp2 volume is exactly what drained the I/O credits on
     2026-09-10 and made the instance unreachable for ~50 minutes."
  fi

  for svc in "$@"; do deploy_service "$svc"; done
  local result=0
  verify "$@" || { result=$?; warn "deployment verification failed"; }
  # Reclaim the layers this deploy just orphaned, so waste does not accumulate to 128 images.
  do_prune
  return "$result"
}

main "$@"
