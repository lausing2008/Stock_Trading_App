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

# The free-space thresholds (refuse a frontend build below 25G, prune below 40G) now live in
# scripts/deploy_remote.sh, which applies them on EC2 under the host lock.

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

# ONE LOCKED REMOTE COMMAND (2026-10-22). Everything that builds, recreates, verifies or prunes
# runs on EC2 as a single process holding the host lock from `git pull` to the final prune, so a
# backup or another deploy cannot start in between. The pull runs under the lock too: it rewrites
# the build context. scripts/deploy_remote.sh re-acquires as a nested holder (same token).
#
# Rollout note: the outer acquire sources the deploy_lock.sh already on EC2, BEFORE the pull.
# The first run after this change needs EC2 to have pulled it once (the documented
# `git pull origin prod` step), or the nested acquire will refuse with exit 75.
run_remote() {
  local args="$*"
  ssh_ec2 "cd $REMOTE_DIR && bash -c 'set -o pipefail
    . scripts/deploy_lock.sh
    deploy_lock_acquire \"deploy.sh:$args\" || exit 75
    git pull origin prod 2>&1 | tail -3 || exit 1
    bash scripts/deploy_remote.sh $args'"
}

main() {
  [[ $# -gt 0 ]] || die "usage: $0 <service...> | --check | --prune"
  # Names are interpolated into the remote command; anything but a compose service name is refused.
  local a
  for a in "$@"; do
    [[ "$a" =~ ^(--prune|--check|[a-z][a-z0-9-]*)$ ]] || die "invalid argument: $a"
  done

  if [[ "$1" == "--check" ]]; then preflight; exit 0; fi
  if [[ "$1" == "--prune" ]]; then run_remote --prune; exit $?; fi

  preflight
  local wants_frontend=0
  for s in "$@"; do [[ "$s" == "frontend" ]] && wants_frontend=1; done
  # The low-disk prune and the frontend free-space refusal now run on EC2, under the lock
  # (scripts/deploy_remote.sh), where they cannot race another build.

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

  local rc=0
  run_remote "$@" || rc=$?
  (( rc == 75 )) && die "REFUSED: another deployment or backup holds the host lock (see above)."
  exit "$rc"
}

main "$@"
