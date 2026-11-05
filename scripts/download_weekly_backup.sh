#!/usr/bin/env bash
# Weekly off-instance copy of the nightly EC2 DB backups (pg_backup.sh, cron'd 02:00 UTC daily
# on the EC2 host itself, keeping only the last 7 daily .sql.gz files under
# /home/ec2-user/backups/). Until this script existed, that 7-day rolling window was the ONLY
# copy of the database backup anywhere — a real single-point-of-failure: losing the EC2
# instance/EBS volume would lose every backup along with the live DB.
#
# Meant to run FROM your local workstation (matching sync_prod_to_local.sh's own established
# convention), on a schedule — see the crontab line below. Mirrors (does not restore) the
# remote backups directory via rsync, so re-runs are cheap (only new files transfer), then
# prunes LOCAL copies older than LOCAL_RETENTION_DAYS. Deliberately a MUCH longer local
# retention than the source's 7-day window — the whole point of a second copy is to retain
# further back than the original, not just mirror its same short window.
#
# Usage:
#   bash scripts/download_weekly_backup.sh
#
# DAILY cron since 2026-11-04 (06:00 local time, after the 02:00 UTC nightly backup under any US
# timezone). It used to run weekly, which moved the same bytes but left no slack: one missed
# Saturday (the workstation was off on Sep 26) meant the Sep 20-26 dumps rotated off EC2's
# 7-day window before they were ever copied. Run daily, a missed day is picked up the next day.
#   crontab -e
#   0 6 * * * /usr/bin/bash /home/lausing/Documents/MyProjects/Stock_Trading_App/scripts/download_weekly_backup.sh >> /home/lausing/Documents/Stock_AI/db_backups/download.log 2>&1
#
# Local retention (by the date in the file name, not mtime): every dump for DAILY_KEEP_DAYS,
# then only Sunday dumps up to LOCAL_RETENTION_DAYS. Keeping every daily for 90 days would be
# ~2.6 GB x 90 = ~230 GB on a disk with ~100 GB free.
set -euo pipefail

EC2_HOST="${EC2_HOST:-ec2-user@18.205.121.71}"
EC2_KEY="${EC2_KEY:-$HOME/Documents/Stock_AI/lausing.pem}"
REMOTE_BACKUP_DIR="${REMOTE_BACKUP_DIR:-/home/ec2-user/backups/}"
LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-$HOME/Documents/Stock_AI/db_backups}"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-90}"   # Sunday dumps kept this long (~13)
DAILY_KEEP_DAYS="${DAILY_KEEP_DAYS:-14}"             # every dump kept this long

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting backup download"

if [ ! -f "$EC2_KEY" ]; then
  echo "ERROR: SSH key not found at $EC2_KEY (set EC2_KEY to override)." >&2
  exit 1
fi
chmod 600 "$EC2_KEY" 2>/dev/null || true

mkdir -p "$LOCAL_BACKUP_DIR"

# rsync over the existing SSH key — only transfers files not already present/changed locally,
# so a weekly re-run after the first one is cheap (typically just the one new day's dump).
# --exclude '*.partial': pg_backup.sh dumps to <name>.partial and renames only after verifying it,
# so a .partial is an in-progress or failed dump and must never become a local "backup".
rsync -avz --progress --exclude '*.partial' \
  -e "ssh -i ${EC2_KEY} -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new" \
  "${EC2_HOST}:${REMOTE_BACKUP_DIR}" \
  "${LOCAL_BACKUP_DIR}/"

TOTAL=$(du -sh "$LOCAL_BACKUP_DIR" 2>/dev/null | cut -f1)
echo "  Local backup directory now: ${TOTAL} (${LOCAL_BACKUP_DIR})"

# Prune LOCAL copies. Deliberately independent of (and longer than) the source's own 7-day
# prune — this local copy exists specifically to outlive that window.
today=$(date -u +%s)
DELETED=0
for f in "$LOCAL_BACKUP_DIR"/stockai-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].sql.gz; do
  [ -e "$f" ] || continue
  d=$(basename "$f" .sql.gz); d=${d#stockai-}
  t=$(date -u -d "$d" +%s 2>/dev/null) || continue
  age=$(( (today - t) / 86400 ))
  if [ "$age" -gt "$LOCAL_RETENTION_DAYS" ] || { [ "$age" -gt "$DAILY_KEEP_DAYS" ] && [ "$(date -u -d "$d" +%u)" != 7 ]; }; then
    rm -f -- "$f" "$f.sha256"
    DELETED=$((DELETED + 1))
  fi
done
if [ "$DELETED" -gt 0 ]; then
  echo "  Pruned ${DELETED} local backup(s): non-Sunday past ${DAILY_KEEP_DAYS} days, any past ${LOCAL_RETENTION_DAYS}"
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Backup download complete."
