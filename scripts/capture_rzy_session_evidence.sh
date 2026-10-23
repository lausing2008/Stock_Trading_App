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
#
# CONFIRMED means BOTH (2026-10-22): the us_post_close job recorded ok after 16:30 ET on $DAY
# (16:30 is the post-close JOB time; the regular session closes at 16:00), AND at least
# MIN_SESSION_COVERAGE of active US symbols have daily_ingest_records.last_observed_session =
# $DAY written after that time. A clock threshold alone would accept a run for another date.
set -u
DAY="$1"
DEADLINE_HOURS="${DEADLINE_HOURS:-6}"
MIN_SESSION_COVERAGE="${MIN_SESSION_COVERAGE:-0.95}"
DEST="/home/ec2-user/rzy-session-evidence/$DAY"
REPO="/home/ec2-user/Stock_Trading_App"
start_epoch=$(TZ=America/New_York date -d "$DAY 16:30" +%s)
deadline=$((start_epoch + DEADLINE_HOURS * 3600))
confirmed=false
record=""
coverage="0 0"
session_coverage() {   # prints "<symbols observing $DAY after the post-close time> <active US symbols>"
  docker exec stockai-postgres-1 sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -At -F ' ' -c \"
    select count(*) filter (where d.last_observed_session::date = date '$DAY'
                              and d.last_success_at >= (timestamp '$DAY 16:30' at time zone 'America/New_York') at time zone 'UTC'),
           count(*)
    from stocks s left join daily_ingest_records d on d.stock_id = s.id
    where s.market = 'US' and s.active and not s.delisted\"" 2>/dev/null
}
while :; do
  record=$(docker exec stockai-redis-1 redis-cli get scheduler:job:us_post_close 2>/dev/null)
  status=$(printf '%s' "$record" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print(d.get("status",""))' 2>/dev/null)
  last=$(printf '%s' "$record" | python3 -c 'import json,sys; from datetime import datetime; d=json.loads(sys.stdin.read() or "{}"); print(int(datetime.fromisoformat(d["last_run"]).timestamp()) if d.get("last_run") else 0)' 2>/dev/null)
  if [ "$status" = "ok" ] && [ "${last:-0}" -ge "$start_epoch" ]; then
    coverage=$(session_coverage); coverage=${coverage:-"0 0"}
    if python3 -c "import sys; o,t=map(int,sys.argv[1].split()); sys.exit(0 if t and o/t >= float(sys.argv[2]) else 1)" "$coverage" "$MIN_SESSION_COVERAGE"; then
      confirmed=true; break
    fi
    echo "$(date -u +%FT%TZ) post-close ok but session $DAY coverage only $coverage (need >= $MIN_SESSION_COVERAGE)"
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then break; fi
  echo "$(date -u +%FT%TZ) waiting for session $DAY: us_post_close status=${status:-none} last_run_epoch=${last:-0} (need >= $start_epoch)"
  sleep 300
done
mkdir -p "$DEST"
coverage=$(session_coverage); coverage=${coverage:-"0 0"}
release=$(docker inspect -f '{{index .Config.Labels "stockai.release"}}' stockai-market-data-1)
image=$(docker inspect -f '{{.Image}}' stockai-market-data-1)
started=$(docker inspect -f '{{.State.StartedAt}}' stockai-market-data-1)
repo_head=$(git -C "$REPO" rev-parse HEAD 2>/dev/null)
script_sha=$(sha256sum "$0" "$REPO/scripts/rzy_session_report.py" 2>/dev/null | awk '{print $1}' | paste -sd, -)
docker cp "$REPO/scripts/rzy_session_report.py" stockai-market-data-1:/tmp/rzy_session_report.py
docker exec -e RZY_RELEASE="$release" -w /app stockai-market-data-1 rm -rf "/tmp/rzy-session-$DAY"
# 2026-10-22: the first run of this report coincided with a 3-hour host stall (disk saturated,
# no OOM, cause unproven). Lowest CPU and I/O priority, and a hard time limit, so the report
# can never be the thing that holds the host.
docker exec -e RZY_RELEASE="$release" -w /app stockai-market-data-1 \
  timeout "${REPORT_TIMEOUT_S:-1800}" ionice -c3 nice -n 19 python /tmp/rzy_session_report.py "$DAY" "/tmp/rzy-session-$DAY"
report_exit=$?
echo "$(date -u +%FT%TZ) report exit=$report_exit"
docker cp "stockai-market-data-1:/tmp/rzy-session-$DAY/." "$DEST/"
# daily_ingest_records is overwritten per symbol on every fetch: keep this point-in-time copy.
docker exec stockai-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\copy (select * from daily_ingest_records order by symbol) to stdout with csv header"' > "$DEST/daily_ingest_records.csv"
python3 - "$DEST" "$confirmed" "$release" "$record" "$DAY" "$coverage" "$MIN_SESSION_COVERAGE" "$image" "$started" "$repo_head" "$script_sha" "$report_exit" <<'EOF'
import json, sys, hashlib, pathlib, datetime
dest, confirmed, release, record = sys.argv[1], sys.argv[2] == "true", sys.argv[3], sys.argv[4]
day, (observed, total), min_cov = sys.argv[5], map(int, sys.argv[6].split()), float(sys.argv[7])
image, started, repo_head, script_sha, report_exit = sys.argv[8:13]
p = pathlib.Path(dest)
json.dump({"captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "post_close_confirmed": confirmed,
           "target_session": day, "report_exit_code": int(report_exit),
           "session_coverage": {"observed_after_post_close": observed, "active_us_symbols": total, "required": min_cov},
           "release_identity": {"market_data_release": release, "image_id": image, "container_started_at": started,
                                "ec2_repo_head": repo_head, "capture_and_report_script_sha256": script_sha},
           "us_post_close_record": json.loads(record) if record else None, "market_data_release": release,
           "files": {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.iterdir()) if f.name != "capture.json"}},
          open(p / "capture.json", "w"), indent=1)
EOF
echo "$(date -u +%FT%TZ) captured to $DEST (post_close_confirmed=$confirmed)"
