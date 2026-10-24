#!/usr/bin/env bash
set -euo pipefail
cd /home/ec2-user/Stock_Trading_App
. scripts/deploy_lock.sh
deploy_lock_acquire options-learning-frontend
release=c93d1695df3be4dd83d9b33837a514baa0b99e85
short=${release:0:8}
test -z "$(git status --porcelain)"
git fetch origin prod
git merge --ff-only "$release" >/dev/null
test "$(git rev-parse HEAD)" = "$release"
avail=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
if [ "$avail" -lt 25 ]; then
  docker image prune -f > /tmp/options-learning-prune-$short.log
  tail -1 /tmp/options-learning-prune-$short.log
fi
avail=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
test "$avail" -ge 25
echo "Disk available before build: ${avail}G"
old=$(docker inspect -f '{{.Image}}' stockai-frontend-1)
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker tag "$old" "stockai-frontend:rollback-$stamp"
printf '%s %s\n' "$old" "stockai-frontend:rollback-$stamp" > /tmp/options-learning-rollback-$short.txt
echo "Building frontend $short from production settings"
DOCKER_BUILDKIT=0 docker build --label "stockai.release=$release" -t "stockai-frontend:release-$short" -f frontend/Dockerfile . > /tmp/options-learning-build-$short.log 2>&1
rollback() {
 docker tag "$old" stockai-frontend:latest
 docker compose -f docker/docker-compose.yml up -d --no-deps --no-build --force-recreate frontend
}
trap 'rollback; exit 1' ERR
docker tag "stockai-frontend:release-$short" stockai-frontend:latest
docker compose -f docker/docker-compose.yml up -d --no-deps --no-build --force-recreate frontend
healthy=0
for _ in $(seq 1 45); do
 state=$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' stockai-frontend-1)
 if [ "$state" = running/healthy ]; then healthy=1; break; fi
 sleep 2
done
test "$healthy" = 1
trap - ERR
printf 'Frontend healthy; release=%s; backends not restarted\n' "$release"
