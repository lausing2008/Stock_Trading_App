#!/usr/bin/env bash
# One-shot, on EC2: capture a session's RZY / bar-revision evidence AFTER the US post-close
# ingest is RECORDED complete (scheduler:job:us_post_close status ok, last_run after 16:30 ET
# that day) — not merely after the clock passes 16:30. Copies everything to durable storage
# before retention pruning (35 days for traces/records) can touch it.
#
#   nohup setsid bash scripts/capture_rzy_session_evidence.sh 2026-10-21 > ~/rzy-capture-2026-10-21.log 2>&1 &
#
# Waits up to DEADLINE_HOURS after 16:30 ET; if the job never confirms, it still captures and
# marks post_close_confirmed=false in capture.json rather than losing the day.
set -u
DAY="$1"
DEADLINE_HOURS="${DEADLINE_HOURS:-6}"
DEST="/home/ec2-user/rzy-session-evidence/$DAY"
REPO="/home/ec2-user/Stock_Trading_App"
start_epoch=$(TZ=America/New_York date -d "$DAY 16:30" +%s)
deadline=$((start_epoch + DEADLINE_HOURS * 3600))
confirmed=false
record=""
while :; do
  record=$(docker exec stockai-redis-1 redis-cli get scheduler:job:us_post_close 2>/dev/null)
  status=$(printf '%s' "$record" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print(d.get("status",""))' 2>/dev/null)
  last=$(printf '%s' "$record" | python3 -c 'import json,sys; from datetime import datetime; d=json.loads(sys.stdin.read() or "{}"); print(int(datetime.fromisoformat(d["last_run"]).timestamp()) if d.get("last_run") else 0)' 2>/dev/null)
  if [ "$status" = "ok" ] && [ "${last:-0}" -ge "$start_epoch" ]; then confirmed=true; break; fi
  if [ "$(date +%s)" -ge "$deadline" ]; then break; fi
  echo "$(date -u +%FT%TZ) waiting: us_post_close status=${status:-none} last_run_epoch=${last:-0} (need >= $start_epoch)"
  sleep 300
done
mkdir -p "$DEST"
release=$(docker inspect -f '{{index .Config.Labels "stockai.release"}}' stockai-market-data-1)
docker cp "$REPO/scripts/rzy_session_report.py" stockai-market-data-1:/tmp/rzy_session_report.py
docker exec -e RZY_RELEASE="$release" -w /app stockai-market-data-1 rm -rf "/tmp/rzy-session-$DAY"
docker exec -e RZY_RELEASE="$release" -w /app stockai-market-data-1 python /tmp/rzy_session_report.py "$DAY" "/tmp/rzy-session-$DAY"
docker cp "stockai-market-data-1:/tmp/rzy-session-$DAY/." "$DEST/"
# daily_ingest_records is overwritten per symbol on every fetch: keep this point-in-time copy.
docker exec stockai-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\copy (select * from daily_ingest_records order by symbol) to stdout with csv header"' > "$DEST/daily_ingest_records.csv"
python3 - "$DEST" "$confirmed" "$release" "$record" <<'EOF'
import json, sys, hashlib, pathlib, datetime
dest, confirmed, release, record = sys.argv[1], sys.argv[2] == "true", sys.argv[3], sys.argv[4]
p = pathlib.Path(dest)
json.dump({"captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "post_close_confirmed": confirmed,
           "us_post_close_record": json.loads(record) if record else None, "market_data_release": release,
           "files": {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.iterdir()) if f.name != "capture.json"}},
          open(p / "capture.json", "w"), indent=1)
EOF
echo "$(date -u +%FT%TZ) captured to $DEST (post_close_confirmed=$confirmed)"
