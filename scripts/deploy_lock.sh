#!/usr/bin/env bash
# A single owner of disk-heavy work on the host at a time: image builds, container recreates
# and database dumps, across EVERY entry point.
#
# WHY THIS EXISTS. On 2026-10-03 a background deploy and a foreground deploy ran against the
# same host at once. Both tried to recreate the frontend; the race removed the container and
# left the replacement unable to claim the name, and the public site returned 502 until it was
# restored by hand. Nothing in the tooling prevented the second run from starting — the only
# thing standing between two deploys was whoever was typing.
#
# On 2026-10-22 the 02:00 database dump and a frontend build ran together and filled the disk;
# the backup was left truncated. The dump now takes this lock too (scripts/pg_backup.sh).
#
# A lock FILE alone is not enough: a crashed deploy would leave a stale one behind forever. This
# uses flock(1), whose lock is held by the file DESCRIPTOR, so the kernel releases it when the
# holding process dies however it dies.
#
# Two ways to take it:
#   deploy_lock_acquire WHAT            refuse at once if held (deploys)
#   deploy_lock_acquire_wait WHAT SECS  wait up to SECS, then fail (backups)
#
# NESTING. The holder exports DEPLOY_LOCK_TOKEN and records it in the lock file. A child that
# calls acquire with that token while the lock is still held proceeds as a nested holder
# instead of refusing (or, with the waiting variant, deadlocking on its own parent). A nested
# holder installs no EXIT trap, so only the outermost holder records the final status.
set -uo pipefail

DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/tmp/stockai-deploy.lock}"
DEPLOY_STATUS_FILE="${DEPLOY_STATUS_FILE:-/tmp/stockai-deploy.status}"

# True when some process holds the lock. Opening a NEW descriptor conflicts with every existing
# holder, including one whose descriptor this process inherited.
_deploy_lock_is_held() {
  ( exec {_probe_fd}>>"$DEPLOY_LOCK_FILE" && ! flock -n "$_probe_fd" ) 2>/dev/null
}

_deploy_lock_nested() {
  [[ -n "${DEPLOY_LOCK_TOKEN:-}" ]] || return 1
  local line; line=$(cat "$DEPLOY_LOCK_FILE" 2>/dev/null) || return 1
  [[ " $line " == *" token=${DEPLOY_LOCK_TOKEN} "* ]] || return 1
  _deploy_lock_is_held
}

_deploy_lock_take() {
  local what="$1" mode="$2" wait_s="${3:-0}"
  if _deploy_lock_nested; then
    DEPLOY_LOCK_NESTED=1
    echo "deploy-lock: nested under $(cat "$DEPLOY_LOCK_FILE" 2>/dev/null)" >&2
    return 0
  fi
  exec {DEPLOY_LOCK_FD}>>"$DEPLOY_LOCK_FILE" || {
    echo "deploy-lock: cannot open $DEPLOY_LOCK_FILE" >&2; return 1; }
  local got=0
  if [[ "$mode" == "wait" ]]; then
    flock -w "$wait_s" "$DEPLOY_LOCK_FD" && got=1
  else
    # NON-BLOCKING on purpose for deploys. A second deploy must REFUSE, not queue: queuing
    # behind a build that is itself recreating containers is how two recreates end up adjacent
    # anyway, and a caller who is told "someone else owns this" can decide what to do.
    flock -n "$DEPLOY_LOCK_FD" && got=1
  fi
  if (( ! got )); then
    if [[ "$mode" == "wait" ]]; then
      echo "deploy-lock: TIMED OUT after ${wait_s}s — still held by:" >&2
    else
      echo "deploy-lock: REFUSED — another deployment owns this host:" >&2
    fi
    cat "$DEPLOY_LOCK_FILE" >&2 2>/dev/null || true
    exec {DEPLOY_LOCK_FD}>&-
    return 1
  fi
  # Recorded in a variable the EXIT trap can still see; reading it from the caller's own
  # DEPLOY_WHAT failed in a subshell, and a completion status that cannot name WHAT finished
  # does not answer the question it exists for.
  DEPLOY_LOCK_WHAT="$what"
  DEPLOY_LOCK_TOKEN=$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
  export DEPLOY_LOCK_TOKEN
  printf 'owner=%s pid=%s what=%s started=%s token=%s\n' \
    "${USER:-unknown}" "$$" "$what" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$DEPLOY_LOCK_TOKEN" \
    >"$DEPLOY_LOCK_FILE"
  printf 'state=running what=%s pid=%s started=%s\n' \
    "$what" "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$DEPLOY_STATUS_FILE"
  # The status file records the OUTCOME even on an unexpected exit, so "did it finish?" is
  # answerable afterwards rather than inferred from whether the site looks up.
  trap 'deploy_lock_finish $?' EXIT
  return 0
}

deploy_lock_acquire() { _deploy_lock_take "$1" refuse; }

deploy_lock_acquire_wait() { _deploy_lock_take "$1" wait "$2"; }

deploy_lock_finish() {
  local code="${1:-0}"
  printf 'state=%s what=%s pid=%s finished=%s exit=%s\n' \
    "$([ "$code" -eq 0 ] && echo completed || echo failed)" \
    "${DEPLOY_LOCK_WHAT:-${DEPLOY_WHAT:-unknown}}" "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$code" \
    >"$DEPLOY_STATUS_FILE"
}

deploy_lock_status() {
  echo "--- deploy status ---"
  cat "$DEPLOY_STATUS_FILE" 2>/dev/null || echo "state=none (no deployment has run on this host)"
}
