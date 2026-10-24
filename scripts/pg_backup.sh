#!/usr/bin/env bash
# Nightly PostgreSQL dump, safe against sharing the disk with image builds.
#
# WHY THIS REPLACED /home/ec2-user/pg_backup.sh (2026-10-22). At 02:00 UTC the dump and a
# frontend build ran together, the disk filled, and `stockai-2026-10-22.sql.gz` was left
# truncated under its final name. The old script wrote straight to that name, took no lock, did
# not check space, and with `set -e` exited before logging anything about the failure. Its
# rotation kept the newest seven files by mtime, so truncated files would have displaced good
# backups after a few bad nights.
#
# Now:
#   1. Takes the host lock (scripts/deploy_lock.sh), WAITING up to BACKUP_LOCK_WAIT_S. A late
#      backup beats a skipped one. Deploys refuse while it is held.
#   2. Refuses to dump unless free space >= max(2 x newest good backup, BACKUP_MIN_FREE_BYTES).
#   3. Dumps to <name>.partial, then checks the gzip stream end to end AND pg_dump's completion
#      marker in one decompression pass, and only then renames to the final name.
#   4. Rotates only after a successful publish, over published files only.
#   5. Logs exactly one completed/failed line per run, with the reason.
#
# Output goes to stdout; cron appends it to backups/backup.log.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/home/ec2-user/backups}"
BACKUP_KEEP="${BACKUP_KEEP:-7}"
BACKUP_LOCK_WAIT_S="${BACKUP_LOCK_WAIT_S:-3600}"
BACKUP_MIN_FREE_BYTES="${BACKUP_MIN_FREE_BYTES:-5000000000}"
PG_CONTAINER="${PG_CONTAINER:-stockai-postgres-1}"

DATE=$(date -u +%F)
FINAL="${BACKUP_DIR}/stockai-${DATE}.sql.gz"
PARTIAL="${FINAL}.partial"

log() { echo "$(date -u '+%a %b %d %H:%M:%S UTC %Y'): $*"; }
fail() {
  rm -f -- "$PARTIAL"
  log "backup stockai-${DATE}.sql.gz FAILED: $*"
  exit 1
}

# Published backups only: the exact dated name, never a .partial.
published() {
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'stockai-*.sql.gz' \
    -regextype posix-extended -regex '.*/stockai-[0-9]{4}-[0-9]{2}-[0-9]{2}\.sql\.gz' | sort
}

# shellcheck source=scripts/deploy_lock.sh
. "$REPO_ROOT/scripts/deploy_lock.sh"
if ! deploy_lock_acquire_wait "backup-${DATE}" "$BACKUP_LOCK_WAIT_S"; then
  log "backup stockai-${DATE}.sql.gz FAILED: lock_timeout after ${BACKUP_LOCK_WAIT_S}s"
  exit 75
fi

# A .partial is only ever an earlier failed run's leftover, and this process now holds the lock.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'stockai-*.sql.gz.partial' -delete \
  || fail "cleanup_error: could not remove leftover partial files"

newest=$(published | tail -n 1) || fail "insufficient_space: could not list backups"
newest_bytes=0
if [[ -n "$newest" ]]; then
  newest_bytes=$(stat -c %s -- "$newest") \
    || fail "insufficient_space: could not read newest backup size"
fi
need=$(( newest_bytes * 2 ))
(( need < BACKUP_MIN_FREE_BYTES )) && need=$BACKUP_MIN_FREE_BYTES
free=$(df -B1 --output=avail -- "$BACKUP_DIR" | tail -n 1 | tr -dc '0-9') \
  || fail "insufficient_space: could not read free space"
[[ -n "$free" ]] || fail "insufficient_space: could not read free space"
(( free >= need )) || fail "insufficient_space: ${free} bytes free < ${need} required"

docker exec "$PG_CONTAINER" pg_dump -U stockai stockai | gzip >"$PARTIAL"
status=("${PIPESTATUS[@]}")
(( status[0] == 0 )) || fail "dump_error: pg_dump exited ${status[0]}"
(( status[1] == 0 )) || fail "dump_error: gzip exited ${status[1]}"

# One pass: gzip -dc fails on a truncated or corrupt stream; the tail must carry the marker.
if ! tail_lines=$(gzip -dc -- "$PARTIAL" | tail -n 20); then
  fail "verify_error: gzip stream invalid"
fi
grep -q -- '-- PostgreSQL database dump complete' <<<"$tail_lines" \
  || fail "verify_error: completion marker missing"

mv -f -- "$PARTIAL" "$FINAL" || fail "publish_error: rename failed"

expired=$(published | head -n -"$BACKUP_KEEP") \
  || fail "rotation_error: could not list expired backups (new backup published)"
while IFS= read -r old; do
  [[ -n "$old" ]] || continue
  rm -f -- "$old" \
    || fail "rotation_error: could not remove $old (new backup published)"
done <<<"$expired"

log "backup stockai-${DATE}.sql.gz complete ($(du -sh -- "$FINAL" | cut -f1))"
