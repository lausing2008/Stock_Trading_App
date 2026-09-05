#!/usr/bin/env bash
# AUD-DEPLOYDRIFT: compares each EC2 backend container's running /app/src against the
# service's committed source at the current local git HEAD, and flags any mismatch.
#
# Motivated by THREE deploy-drift incidents found live in a single session on 2026-09-05:
#   - news-intelligence ran SIX-WEEK-OLD code (a real fix from 2026-07-27 reverted by a
#     2026-09-04 container recreation) — its real-time Alpaca feed was dead the whole time,
#     while the container reported healthy throughout.
#   - decision-engine had a stale shared/db/models.py and would have crash-looped on its next
#     restart touching the missing columns.
#   - api-gateway was missing an entire route registration (fix-effectiveness, added to git
#     2026-09-02) — the page was live in git, deployed on EC2's git checkout, but the RUNNING
#     container's /app/src was never docker cp'd the change.
# A follow-up sweep of all 12 containers found FIVE MORE already drifted
# (ranking-engine, technical-analysis, portfolio-optimizer, event-intelligence,
# decision-engine) — none had thrown an error yet, because nothing was exercising the
# specific changed code paths. This is exactly the gap: `docker cp` is a session-scoped
# hotfix (see CLAUDE.md's own Deployment Pattern section) that is silently reverted by any
# container recreation, and there was no way to know it had happened short of hitting the
# broken feature by chance.
#
# This is deliberately the SIMPLE version: an external script run on demand (or via your own
# cron/launchd), not a baked-in GIT_SHA file — containers currently have no git or SHA marker
# inside them at all, so a build-time approach would need every Dockerfile changed and every
# image rebuilt before it helped, and today's already-running images still wouldn't have it.
# This works right now, against what is running right now.
#
# Usage:
#   bash scripts/check_deploy_drift.sh                # check all known backend services
#   bash scripts/check_deploy_drift.sh market-data     # check just one
#
# Exit code: 0 if everything matches, 1 if any service has drifted (so this is safe to wire
# into a cron job that alerts on nonzero exit, once you're ready for that).
set -euo pipefail

EC2_HOST="${EC2_HOST:-ec2-user@18.205.121.71}"
EC2_KEY="${EC2_KEY:-$HOME/Documents/Stock_AI/lausing.pem}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Every backend service with an /app/src tree that gets docker cp'd (matches CLAUDE.md's own
# container list, minus frontend/postgres/redis which don't have a comparable src/ tree).
ALL_SERVICES=(
  market-data signal-engine api-gateway ml-prediction research-engine ranking-engine
  strategy-engine technical-analysis portfolio-optimizer event-intelligence
  news-intelligence decision-engine
)

if [ "$#" -ge 1 ]; then
  SERVICES=("$@")
else
  SERVICES=("${ALL_SERVICES[@]}")
fi

drifted=0
checked=0

echo "Comparing local git HEAD ($(cd "$REPO_ROOT" && git rev-parse --short HEAD)) against EC2 running containers..."
echo

for svc in "${SERVICES[@]}"; do
  local_dir="$REPO_ROOT/services/$svc/src"
  if [ ! -d "$local_dir" ]; then
    echo "SKIP  $svc — no local services/$svc/src directory"
    continue
  fi

  # LC_ALL=C pins byte-order sorting on BOTH sides — without it, the local shell's locale
  # (e.g. en_US.UTF-8) and the container's (typically C.UTF-8) sort paths containing
  # underscores/slashes in different orders, so `sort | xargs cat | md5sum` silently hashes
  # the same file SET in a different concatenation order and reports false drift. Confirmed
  # live: this cost an hour chasing a "drifted" strategy-engine/technical-analysis that were
  # actually byte-identical (file lists matched exactly once both were re-sorted the same way).
  local_hash=$(LC_ALL=C find "$local_dir" -name '*.py' | LC_ALL=C sort | xargs cat 2>/dev/null | md5sum | cut -d' ' -f1)
  remote_hash=$(ssh -i "$EC2_KEY" "$EC2_HOST" \
    "docker exec stockai-${svc}-1 sh -c \"LC_ALL=C find /app/src -name '*.py' 2>/dev/null | LC_ALL=C sort | xargs cat 2>/dev/null | md5sum\"" \
    2>/dev/null | cut -d' ' -f1 || echo "")

  checked=$((checked + 1))
  if [ -z "$remote_hash" ]; then
    echo "ERROR $svc — could not read from container (is stockai-${svc}-1 running?)"
    drifted=$((drifted + 1))
  elif [ "$local_hash" = "$remote_hash" ]; then
    echo "OK    $svc"
  else
    echo "DRIFT $svc — running container does NOT match local git HEAD"
    echo "        redeploy with:"
    echo "        ssh -i $EC2_KEY $EC2_HOST \"cd /home/ec2-user/Stock_Trading_App && docker cp services/$svc/src stockai-${svc}-1:/app/ && docker restart stockai-${svc}-1\""
    drifted=$((drifted + 1))
  fi
done

echo
echo "Checked $checked service(s), $drifted drifted/errored."
if [ "$drifted" -gt 0 ]; then
  echo
  echo "NOTE: this compares against LOCAL git HEAD, not EC2's git checkout — if EC2 hasn't"
  echo "pulled the latest commit yet, run 'git pull origin prod' there first (see"
  echo "CLAUDE.md's Deployment Pattern), or this will report false drift for pending commits"
  echo "that simply haven't been deployed at all yet, which is a different (and less urgent)"
  echo "problem than a REVERTED hotfix."
  exit 1
fi
exit 0
