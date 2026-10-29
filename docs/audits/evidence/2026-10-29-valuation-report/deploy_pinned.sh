#!/usr/bin/env bash
set -euo pipefail
release=${1:?Pass the exact tested 40-character application revision}
[[ "$release" =~ ^[0-9a-f]{40}$ ]]
cd /home/ec2-user/Stock_Trading_App
. scripts/deploy_lock.sh
deploy_lock_acquire valuation-report || exit 75
test -z "$(git status --porcelain)"
git fetch origin prod
test "$(git rev-parse origin/prod)" = "$release"
git merge --ff-only "$release"
test "$(git rev-parse HEAD)" = "$release"
evidence=/home/ec2-user/deploy-evidence/2026-10-29-valuation-report
mkdir -p "$evidence"
services=(market-data research-engine signal-engine api-gateway ml-prediction ranking-engine strategy-engine technical-analysis portfolio-optimizer event-intelligence news-intelligence decision-engine frontend)
if [[ "${2:-}" == "--presentation-fix" ]]; then services=(research-engine frontend); fi
if [[ "${2:-}" == "--data-enrichment" ]]; then services=(market-data research-engine frontend); fi
if [[ "${2:-}" == "--market-data-only" ]]; then services=(market-data); fi
declare -A old_images
for svc in "${services[@]}"; do
  image=$(docker inspect -f '{{.Image}}' "stockai-$svc-1")
  old_images[$svc]=$image
  docker tag "$image" "stockai-$svc:rollback-before-${release:0:8}"
done
snapshot() {
  python3 - "$1" <<'PY'
import json,subprocess,sys
names=subprocess.check_output(['docker','ps','--format','{{.Names}}'],text=True).split()
rows=json.loads(subprocess.check_output(['docker','inspect',*names],text=True))
json.dump([{'name':r['Name'].lstrip('/'),'image':r['Image'],'started_at':r['State']['StartedAt'],
            'health':r['State'].get('Health',{}).get('Status')} for r in rows],open(sys.argv[1],'w'),indent=2)
PY
}
snapshot "$evidence/before.json"
rollback() {
  trap - ERR
  for svc in "${services[@]}"; do
    docker tag "${old_images[$svc]}" "stockai-$svc:latest"
    docker compose -f docker/docker-compose.yml up -d --no-deps --no-build --force-recreate "$svc"
  done
  snapshot "$evidence/rollback.json"
  exit 1
}
trap rollback ERR
# Shared report-type registration changes shared/ in every backend image.
bash scripts/deploy_remote.sh "${services[@]}"
for svc in "${services[@]}"; do
  image=$(docker inspect -f '{{.Image}}' "stockai-$svc-1")
  docker tag "$image" "stockai-$svc:release-${release:0:8}"
done
snapshot "$evidence/after.json"
printf '%s\n' "$release" > "$evidence/release.txt"
trap - ERR
printf 'Pinned valuation rollout complete: %s\n' "$release"
