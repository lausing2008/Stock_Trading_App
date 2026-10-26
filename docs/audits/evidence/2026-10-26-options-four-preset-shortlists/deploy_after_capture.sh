#!/usr/bin/env bash
# On EC2, release only after the October 26 RZY session capture is complete and verified.
set -euo pipefail
release=${1:?Pass exact tested 40-character commit}
expected_start=${2:?Pass market-data start time observed before session}
[[ "$release" =~ ^[0-9a-f]{40}$ ]]
repo=/home/ec2-user/Stock_Trading_App
evidence=/home/ec2-user/deploy-evidence/2026-10-26-options-four-preset-shortlists
capture=/home/ec2-user/rzy-session-evidence/2026-10-26-a8316be3
mkdir -p "$evidence"
exec > >(tee -a "$evidence/scheduled-rollout.log") 2>&1
printf '%s waiting for verified October 26 RZY capture before options rollout %s\n' "$(date -u +%FT%TZ)" "$release"
deadline=$(date -u -d '2026-10-27 03:00:00 UTC' +%s)
while [[ ! -f "$capture/capture.json" ]]; do
  if (( $(date -u +%s) >= deadline )); then echo 'Capture deadline passed; deployment withheld.'; exit 75; fi
  sleep 300
done
python3 - "$capture" "$expected_start" <<'PY'
import hashlib, json, pathlib, sys
folder = pathlib.Path(sys.argv[1])
data = json.loads((folder / 'capture.json').read_text())
assert data['target_session'] == '2026-10-26'
assert data['post_close_confirmed'] is True and data['capture_complete'] is True
assert data['release_identity']['container_started_at'] == sys.argv[2]
assert data['release_identity']['image_id']
assert data['files'] and all(
    pathlib.Path(name).name == name and (folder / name).is_file()
    and hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
    for name, digest in data['files'].items()
)
print('RZY capture flags, source start time and artifact hashes verified.')
PY
cd "$repo"
. scripts/deploy_lock.sh
deploy_lock_acquire_wait options-four-after-capture 900
test "$(docker inspect -f '{{.State.StartedAt}}' stockai-market-data-1)" = "$expected_start"
test -z "$(git status --porcelain)"
git fetch origin prod
test "$(git rev-parse origin/prod)" = "$release"
git show "$release:docs/audits/evidence/2026-10-26-options-four-preset-shortlists/deploy_pinned.sh" > "$evidence/deploy-pinned-tested.sh"
bash "$evidence/deploy-pinned-tested.sh" "$release"
python3 docs/audits/evidence/2026-10-26-options-four-preset-shortlists/verify_production.py "$release" > "$evidence/production-verification.log"
python3 docs/audits/evidence/2026-10-26-options-four-preset-shortlists/verify_served_build.py > "$evidence/served-verification.log"
bash docs/audits/evidence/2026-10-26-options-four-preset-shortlists/verify_behavior.sh > "$evidence/behavior-verification.log"
python3 docs/audits/evidence/2026-10-25-options-risk-scenarios/verify_backend_sources.py > "$evidence/backend-sources.log"
printf '%s options four-shortlist release verified: %s\n' "$(date -u +%FT%TZ)" "$release"
