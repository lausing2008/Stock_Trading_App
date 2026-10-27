#!/usr/bin/env bash
# Passive F03 evidence only. Serialize with builds/backups and retain the first capture.
set -euo pipefail
cd /home/ec2-user/f03-observation
. /home/ec2-user/Stock_Trading_App/scripts/deploy_lock.sh
deploy_lock_acquire_wait f03-daily-evidence 60 || exit 75
capture_day=$(date -u +%F)
if [[ -e "captures/${capture_day}.json" ]]; then
  echo "$(date -u +%FT%TZ) capture already exists for ${capture_day}; preserved"
  exit 0
fi
timeout 300 ionice -c3 nice -n 19 /usr/bin/python3 collect_session.py captures
