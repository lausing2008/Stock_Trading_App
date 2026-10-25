#!/usr/bin/env bash
set -euo pipefail
release=${1:?Pass the exact tested 40-character application revision}
[[ "$release" =~ ^[0-9a-f]{40}$ ]]
cd /home/ec2-user/Stock_Trading_App
. scripts/deploy_lock.sh
deploy_lock_acquire options-risk-frontend || exit 75
short=${release:0:8}
evidence=/home/ec2-user/deploy-evidence/2026-10-25-options-risk-scenarios/final-cents
mkdir -p "$evidence"
test -z "$(git status --porcelain)"
git fetch origin prod
test "$(git rev-parse origin/prod)" = "$release"
git merge --ff-only "$release"
test "$(git rev-parse HEAD)" = "$release"
python3 - "$evidence/before.json" <<'PY'
import json,subprocess,sys
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).split()
data=json.loads(subprocess.check_output(['docker','inspect',*names],text=True))
json.dump([{'name':c['Name'].lstrip('/'),'image':c['Image'],'started_at':c['State']['StartedAt'],'health':c['State'].get('Health',{}).get('Status')} for c in data],open(sys.argv[1],'w'),indent=2)
PY
old_frontend=$(docker inspect -f '{{.Image}}' stockai-frontend-1)
docker tag "$old_frontend" "stockai-frontend:rollback-before-$short"
rollback() {
 docker tag "$old_frontend" stockai-frontend:latest
 docker compose -f docker/docker-compose.yml up -d --no-deps --no-build --force-recreate frontend
}
trap 'rollback; exit 1' ERR
bash scripts/deploy_remote.sh frontend
for svc in frontend; do
 image=$(docker inspect -f '{{.Image}}' "stockai-$svc-1")
 docker tag "$image" "stockai-$svc:release-$short"
done
trap - ERR
printf '%s\n' "$release" > "$evidence/release.txt"
printf 'Pinned frontend-only rollout complete: %s\n' "$release"
