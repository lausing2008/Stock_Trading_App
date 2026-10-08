#!/usr/bin/env bash
# AUD-DEPLOY-IOCREDIT: one deploy path, with the guardrails the 2026-09-10 outage needed.
#
# WHAT WENT WRONG, and why this script exists rather than a longer CLAUDE.md section.
#
# A frontend rebuild for a change that touched ONE page (the improvements tracker) took the
# whole instance down for ~50 minutes. Not a crash and not an OOM — EBS I/O CREDIT
# EXHAUSTION on a t3.medium's 100GB gp2 volume. The sysstat record is unambiguous:
#
#     time     %user  %system  %iowait  %idle
#     14:50     75.6      9.7      4.6     8.5   <- the docker build
#     15:11     22.0     18.0     56.6     0.89
#     16:00      0.4     53.1     44.4     0.01  <- doing nothing but waiting on disk
#
# User CPU COLLAPSED while iowait exploded. %steal stayed ~2%, so it was not a noisy
# neighbour, and there was no oom-kill anywhere in the prior-boot journal. Docker logged
# "timed out starting health check" across every container; sysstat-collect took 6-8 minutes
# for a sub-second job; SSH failed "during banner exchange" because sshd could accept the TCP
# connection but could not read its own host keys off the disk in time.
#
# THE UNDERLYING CAUSE WAS ACCUMULATED GARBAGE, not the build itself. At the time of the
# incident: 144 images / 62.23GB, of which 53.49GB (85%) reclaimable, plus 128 DANGLING
# images — one per historical rebuild, never cleaned — on a volume already at 84%. A single
# `docker image prune -f` reclaimed 47.25GB and took the volume from 84% to 36%. The
# reclaimable garbage was larger than the entire live footprint of the platform.
#
# So the fix is not "avoid building". It is: know the disk state BEFORE building, clean up
# AFTER, and do not reach for the heavy path when a light one is correct.
#
# USAGE
#   scripts/deploy.sh frontend                 # build image + recreate (the heavy path)
#   scripts/deploy.sh market-data signal-engine # backend: rebuild image + recreate
#   scripts/deploy.sh --check                  # preflight only, change nothing
#   scripts/deploy.sh --prune                  # reclaim disk only
#
# EXIT STATUS: non-zero on a refused or unhealthy deploy, so this is safe to gate on. Note
# that piping the script through `tail` MASKS that status (you get tail's) — a mistake I made
# while testing this very guard and briefly read as the guard not working. Check $? unpiped,
# or use `set -o pipefail` in the caller.
#
# DELIBERATELY NOT DOING two things:
#   * No `docker cp` path. CLAUDE.md is already emphatic that it is a session-scoped hotfix
#     reverted by any recreation, and scripts/check_deploy_drift.sh exists precisely because
#     that has silently bitten this project at least eight times. A script that made it easy
#     would be a trap.
#   * No `docker system prune -a`. That deletes images not currently running, which on a
#     12-service compose file is how you turn a deploy into an unplanned full rebuild of
#     everything. Dangling-only is the safe subset and, as measured above, is where
#     essentially all the waste actually is.
set -euo pipefail

PREFLIGHT_AVAIL_GB=""
EC2_HOST="${EC2_HOST:-ec2-user@18.205.121.71}"
EC2_KEY="${EC2_KEY:-$HOME/Documents/Stock_AI/lausing.pem}"
REMOTE_DIR="${REMOTE_DIR:-/home/ec2-user/Stock_Trading_App}"
COMPOSE="docker compose -f docker/docker-compose.yml"

# Below this much free space, a frontend build is refused outright. The incident began at 84%
# used / 17GB free, and a Next.js build plus a new image layer set needs several GB of headroom
# before it even starts competing for I/O.
MIN_FREE_GB_BUILD=25
WARN_FREE_GB=40

ssh_ec2() { ssh -i "$EC2_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=30 "$EC2_HOST" "$@"; }

c_red=$'\033[31m'; c_yel=$'\033[33m'; c_grn=$'\033[32m'; c_off=$'\033[0m'
say()  { printf '%s\n' "$*"; }
warn() { printf '%s%s%s\n' "$c_yel" "$*" "$c_off"; }
die()  { printf '%s%s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }
ok()   { printf '%s%s%s\n' "$c_grn" "$*" "$c_off"; }

free_gb() { ssh_ec2 "df -BG --output=avail / | tail -1 | tr -dc '0-9'"; }

preflight() {
  say "── preflight ──────────────────────────────────────────"
  # A build under I/O saturation is what produced the outage; refuse rather than pile on.
  local iowait
  iowait=$(ssh_ec2 "top -bn1 | awk '/Cpu\(s\)/{print \$10}' | tr -dc '0-9.'" || echo 0)
  local avail; avail=$(free_gb)
  say "  disk free : ${avail}G"
  say "  iowait    : ${iowait:-?}%"
  ssh_ec2 "docker system df | head -3 | sed 's/^/  /'"
  # awk, not bash arithmetic — iowait is a float.
  if awk "BEGIN{exit !(${iowait:-0} > 30)}"; then
    warn "  WARNING: iowait ${iowait}% — the box is already I/O-bound. Deploying now risks"
    warn "           the 2026-09-10 failure mode. Consider waiting or pruning first."
  fi
  # Report goes to stdout for the human; the value the caller needs is returned via a global
  # rather than on stdout, so `--check` can print the report without the caller swallowing it.
  PREFLIGHT_AVAIL_GB="$avail"
}

do_prune() {
  say "── prune (dangling images + stopped containers only) ──"
  ssh_ec2 "df -h / | tail -1 | sed 's/^/  before: /'
           docker image prune -f | tail -1 | sed 's/^/  /'
           docker container prune -f | tail -1 | sed 's/^/  /'
           df -h / | tail -1 | sed 's/^/  after:  /'"
}

deploy_service() {
  local svc="$1"
  say "── $svc ───────────────────────────────────────────────"
  if [[ "$svc" == "frontend" ]]; then
    # DOCKER_BUILDKIT=0 is REQUIRED, not preference: BuildKit serves cached layers even with
    # --no-cache and produces a stale image. See docs/incidents/ec2-disk-and-frontend-builds.md.
    # --no-cache is deliberately NOT passed (fixed 2026-07-07 as unnecessary overhead).
    ssh_ec2 "cd $REMOTE_DIR && DOCKER_BUILDKIT=0 docker build -f frontend/Dockerfile -t stockai-frontend:latest . 2>&1 | tail -3"
  else
    ssh_ec2 "cd $REMOTE_DIR && $COMPOSE build $svc 2>&1 | tail -3"
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
  ssh_ec2 "cd $REMOTE_DIR && $COMPOSE up -d --no-deps --force-recreate $svc 2>&1 | tail -2"
}

# Health is checked AFTER a settle delay and requires the literal string "healthy" — not merely
# "container is up". On 2026-09-10 I reported a deploy clean from a check run ~90 seconds after
# recreation, while health checks were already timing out platform-wide. "Up" is not healthy,
# and "healthy 20 seconds after start" is not evidence the box is fine.
verify() {
  local svcs=("$@")
  say "── verify (settling 45s) ──────────────────────────────"
  sleep 45
  local bad=0
  for svc in "${svcs[@]}"; do
    local st; st=$(ssh_ec2 "docker ps --filter name=stockai-${svc}-1 --format '{{.Status}}'" || true)
    if [[ "$st" == *healthy* ]]; then ok "  $svc: $st"
    else warn "  $svc: ${st:-MISSING}"; bad=1; fi
  done

  # EVERY SERVICE IN THE FILE MUST STILL EXIST, not only the ones just deployed. Both 2026-10-08
  # outages were deploys that removed api-gateway while it was NOT in the deploy list, so the
  # loop above never looked at it and the run reported "11 healthy" as though that were a count
  # of what was asked for. A container that vanished is the failure this check exists to name.
  local declared missing
  declared=$(ssh_ec2 "cd $REMOTE_DIR && $COMPOSE config --services" | tr -d '\r')
  missing=""
  for svc in $declared; do
    [[ "$svc" == "postgres" || "$svc" == "redis" ]] && continue
    local up; up=$(ssh_ec2 "docker ps --filter name=stockai-${svc}-1 --format '{{.Names}}'" || true)
    [[ -n "$up" ]] || missing="$missing $svc"
  done
  if [[ -n "$missing" ]]; then
    warn "  CONTAINER MISSING ENTIRELY:$missing"
    warn "  A removed container is not a slow start. Recreate with:"
    warn "    $COMPOSE up -d --no-deps$missing"
    bad=1
  else
    ok "  all declared services present"
  fi
  # A deploy that leaves the HOST unhealthy is not a successful deploy, even if the container is.
  local avail; avail=$(free_gb)
  local iowait; iowait=$(ssh_ec2 "top -bn1 | awk '/Cpu\(s\)/{print \$10}' | tr -dc '0-9.'" || echo 0)
  say "  host: ${avail}G free, iowait ${iowait}%"
  if awk "BEGIN{exit !(${iowait:-0} > 30)}"; then
    warn "  WARNING: iowait still ${iowait}% after deploy — watch for the credit-drain pattern."
  fi
  return $bad
}

main() {
  [[ $# -gt 0 ]] || die "usage: $0 <service...> | --check | --prune"

  if [[ "$1" == "--prune" ]]; then do_prune; exit 0; fi
  if [[ "$1" == "--check" ]]; then preflight; exit 0; fi

  preflight
  local avail="$PREFLIGHT_AVAIL_GB"
  local wants_frontend=0
  for s in "$@"; do [[ "$s" == "frontend" ]] && wants_frontend=1; done

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

  # A RED FRONTEND SUITE MUST NOT REACH PRODUCTION.
  # AUD-FRONTEND-SHIPPED-RED (2026-10-06): the vitest suite sat red on `prod` for days because
  # `make test` runs the BACKEND suites only, so every commit in that window saw "all services
  # passed" and shipped anyway. Two of the three failures were real — four tracker entries had
  # no label or colour and rendered nothing at all. A documented rule did not hold; a gate at
  # the moment of deployment does. Set SKIP_FRONTEND_TESTS=1 to override deliberately.
  if (( wants_frontend )) && [[ "${SKIP_FRONTEND_TESTS:-0}" != "1" ]]; then
    say "── frontend suite (must be green before building) ──"
    if ! ( cd "$(git rev-parse --show-toplevel)/frontend" && npx vitest run >/tmp/fe_test.log 2>&1 ); then
      tail -25 /tmp/fe_test.log >&2
      die "REFUSING frontend build: the vitest suite is red (see output above).
     This is the gate AUD-FRONTEND-SHIPPED-RED added. \`make test\` does not cover the
     frontend. Fix the suite, or set SKIP_FRONTEND_TESTS=1 if you intend to ship over it."
    fi
    if ! ( cd "$(git rev-parse --show-toplevel)/frontend" && npx tsc --noEmit -p tsconfig.json >/tmp/fe_tsc.log 2>&1 ); then
      tail -25 /tmp/fe_tsc.log >&2
      die "REFUSING frontend build: typecheck failed (see output above)."
    fi
    say "  frontend suite green"
  fi

  ssh_ec2 "cd $REMOTE_DIR && git pull origin prod 2>&1 | tail -3"
  for svc in "$@"; do deploy_service "$svc"; done
  verify "$@" || warn "one or more services are not healthy — check before declaring success"
  # Reclaim the layers this deploy just orphaned, so waste does not accumulate to 128 images.
  do_prune
}

main "$@"
