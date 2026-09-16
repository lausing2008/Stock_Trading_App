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
# Weekly cron (Saturday 06:00 local time — comfortably after the 02:00 UTC nightly backup
# under any US timezone, since 02:00 UTC is already the previous evening in North America):
#   crontab -e
#   0 6 * * 6 /usr/bin/bash /home/lausing/Documents/MyProjects/Stock_Trading_App/scripts/download_weekly_backup.sh >> /home/lausing/Documents/Stock_AI/db_backups/download.log 2>&1
set -euo pipefail

EC2_HOST="${EC2_HOST:-ec2-user@18.205.121.71}"
EC2_KEY="${EC2_KEY:-$HOME/Documents/Stock_AI/lausing.pem}"
REMOTE_BACKUP_DIR="${REMOTE_BACKUP_DIR:-/home/ec2-user/backups/}"
LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-$HOME/Documents/Stock_AI/db_backups}"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-90}"   # ~13 weekly copies

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting weekly backup download"

if [ ! -f "$EC2_KEY" ]; then
  echo "ERROR: SSH key not found at $EC2_KEY (set EC2_KEY to override)." >&2
  exit 1
fi
chmod 600 "$EC2_KEY" 2>/dev/null || true

mkdir -p "$LOCAL_BACKUP_DIR"

# rsync over the existing SSH key — only transfers files not already present/changed locally,
# so a weekly re-run after the first one is cheap (typically just the one new day's dump).
rsync -avz --progress \
  -e "ssh -i ${EC2_KEY} -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new" \
  "${EC2_HOST}:${REMOTE_BACKUP_DIR}" \
  "${LOCAL_BACKUP_DIR}/"

TOTAL=$(du -sh "$LOCAL_BACKUP_DIR" 2>/dev/null | cut -f1)
echo "  Local backup directory now: ${TOTAL} (${LOCAL_BACKUP_DIR})"

# Prune LOCAL copies past the retention window. Deliberately independent of (and longer than)
# the source's own 7-day prune — this local copy exists specifically to outlive that window.
DELETED=$(find "$LOCAL_BACKUP_DIR" -name "stockai-*.sql.gz" -mtime "+${LOCAL_RETENTION_DAYS}" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
  echo "  Pruned ${DELETED} local backup(s) older than ${LOCAL_RETENTION_DAYS} days"
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Weekly backup download complete."
