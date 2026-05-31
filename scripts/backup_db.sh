#!/usr/bin/env bash
# Nightly PostgreSQL backup — dumps the stockai DB from the running Docker
# container, compresses it, and uploads to S3.
#
# Prerequisites on EC2:
#   1. AWS CLI installed:  sudo dnf install -y awscli
#   2. IAM role with s3:PutObject on your backup bucket attached to the instance
#      (no credentials file needed when using an IAM role)
#   3. Set BACKUP_BUCKET below, or pass it as an env var before running.
#
# Cron setup (runs daily at 02:00 server time):
#   echo "0 2 * * * ec2-user /home/ec2-user/stockai/scripts/backup_db.sh >> /var/log/stockai-backup.log 2>&1" \
#     | sudo tee /etc/cron.d/stockai-backup
#
# Test manually:
#   bash /home/ec2-user/stockai/scripts/backup_db.sh
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
BACKUP_BUCKET="${BACKUP_BUCKET:-your-s3-backup-bucket}"
DB_CONTAINER="${DB_CONTAINER:-stockai-postgres-1}"
DB_NAME="${DB_NAME:-stockai}"
DB_USER="${DB_USER:-stockai}"
RETAIN_DAYS="${RETAIN_DAYS:-30}"   # delete S3 objects older than this

TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
FILENAME="stockai_db_${TIMESTAMP}.sql.gz"
TMP_FILE="/tmp/${FILENAME}"
S3_KEY="backups/${FILENAME}"
# ──────────────────────────────────────────────────────────────────────────────

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting backup → s3://${BACKUP_BUCKET}/${S3_KEY}"

# Dump from the running postgres container and compress in one pipe
docker exec "$DB_CONTAINER" \
  pg_dump -U "$DB_USER" "$DB_NAME" \
  | gzip -9 > "$TMP_FILE"

BYTES=$(stat -c%s "$TMP_FILE")
echo "  Dump complete: ${BYTES} bytes compressed"

# Upload to S3
aws s3 cp "$TMP_FILE" "s3://${BACKUP_BUCKET}/${S3_KEY}" \
  --storage-class STANDARD_IA \
  --no-progress

echo "  Uploaded to s3://${BACKUP_BUCKET}/${S3_KEY}"

# Clean up temp file
rm -f "$TMP_FILE"

# Delete S3 objects older than RETAIN_DAYS
echo "  Pruning backups older than ${RETAIN_DAYS} days …"
CUTOFF=$(date -u -d "-${RETAIN_DAYS} days" +"%Y-%m-%dT%H:%M:%SZ")
aws s3api list-objects-v2 \
  --bucket "$BACKUP_BUCKET" \
  --prefix "backups/stockai_db_" \
  --query "Contents[?LastModified<='${CUTOFF}'].Key" \
  --output text \
| tr '\t' '\n' \
| grep -v '^$' \
| while read -r key; do
    echo "  Deleting old backup: $key"
    aws s3 rm "s3://${BACKUP_BUCKET}/$key"
  done

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Backup complete."
