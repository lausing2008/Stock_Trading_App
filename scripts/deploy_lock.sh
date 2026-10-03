#!/usr/bin/env bash
# A single deployment owner at a time, across EVERY entry point.
#
# WHY THIS EXISTS. On 2026-10-03 a background deploy and a foreground deploy ran against the
# same host at once. Both tried to recreate the frontend; the race removed the container and
# left the replacement unable to claim the name, and the public site returned 502 until it was
# restored by hand. Nothing in the tooling prevented the second run from starting — the only
# thing standing between two deploys was whoever was typing.
#
# A lock FILE alone is not enough: a crashed deploy would leave a stale one behind forever. This
# uses flock(1), whose lock is held by the file DESCRIPTOR, so the kernel releases it when the
# holding process dies however it dies.
set -uo pipefail

DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-/tmp/stockai-deploy.lock}"
DEPLOY_STATUS_FILE="${DEPLOY_STATUS_FILE:-/tmp/stockai-deploy.status}"

deploy_lock_acquire() {
  local what="$1"
  exec {DEPLOY_LOCK_FD}>>"$DEPLOY_LOCK_FILE" || {
    echo "deploy-lock: cannot open $DEPLOY_LOCK_FILE" >&2; return 1; }
  # NON-BLOCKING on purpose. A second deploy must REFUSE, not queue: queuing behind a build
  # that is itself recreating containers is how two recreates end up adjacent anyway, and a
  # caller who is told "someone else owns this" can decide what to do.
  if ! flock -n "$DEPLOY_LOCK_FD"; then
    echo "deploy-lock: REFUSED — another deployment owns this host:" >&2
    cat "$DEPLOY_LOCK_FILE" >&2 2>/dev/null || true
    return 1
  fi
  # Recorded in a variable the EXIT trap can still see; reading it from the caller's own
  # DEPLOY_WHAT failed in a subshell, and a completion status that cannot name WHAT finished
  # does not answer the question it exists for.
  DEPLOY_LOCK_WHAT="$what"
  : >"$DEPLOY_LOCK_FILE"
  printf 'owner=%s pid=%s what=%s started=%s\n' \
    "${USER:-unknown}" "$$" "$what" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$DEPLOY_LOCK_FILE"
  printf 'state=running what=%s pid=%s started=%s\n' \
    "$what" "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$DEPLOY_STATUS_FILE"
  # The status file records the OUTCOME even on an unexpected exit, so "did it finish?" is
  # answerable afterwards rather than inferred from whether the site looks up.
  trap 'deploy_lock_finish $?' EXIT
  return 0
}

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
