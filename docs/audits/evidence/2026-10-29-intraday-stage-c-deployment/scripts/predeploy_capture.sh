#!/usr/bin/env bash
# Runs ON EC2 before the Intraday Stage C release a3107b47. Read-only apart from adding one
# rollback tag per backend service and writing the evidence directory. No build or recreate.
set -euo pipefail
SHA=a3107b47
EV=/home/ec2-user/deploy-evidence/2026-10-29-intraday-stage-c-$SHA
SERVICES="market-data signal-engine decision-engine ml-prediction research-engine api-gateway ranking-engine strategy-engine technical-analysis portfolio-optimizer event-intelligence news-intelligence"
mkdir -p "$EV/pre-logs"
date -u +%FT%TZ > "$EV/predeploy_started_utc.txt"
: > "$EV/pre-identities.tsv"
for s in $SERVICES; do
  c="stockai-$s-1"
  img=$(docker inspect --format '{{.Image}}' "$c")
  started=$(docker inspect --format '{{.State.StartedAt}}' "$c")
  health=$(docker inspect --format '{{.State.Status}}/{{.State.Health.Status}}' "$c")
  printf '%s\t%s\t%s\t%s\n' "$s" "$img" "$started" "$health" >> "$EV/pre-identities.tsv"
  docker logs --since 24h "$c" > "$EV/pre-logs/$s.log" 2>&1 || true
  docker tag "$img" "stockai-$s:rollback-before-$SHA"
done
# Verify every rollback tag resolves to the image the container was running.
while IFS=$'\t' read -r s img _ _; do
  t=$(docker image inspect --format '{{.Id}}' "stockai-$s:rollback-before-$SHA")
  if [[ "$t" == "$img" ]]; then echo "ok   $s $img"; else echo "BAD  $s tag=$t running=$img"; fi
done < "$EV/pre-identities.tsv" | tee "$EV/rollback-tags-verified.txt"
( cd "$EV/pre-logs" && sha256sum *.log ) > "$EV/pre-logs.sha256"
git -C /home/ec2-user/Stock_Trading_App rev-parse HEAD > "$EV/pre-remote-head.txt"
df -BG --output=avail / | tail -1 > "$EV/pre-disk-free.txt"
# The loop runs in a pipeline subshell, so failure is read back from its output.
[[ $(grep -c '^ok ' "$EV/rollback-tags-verified.txt") -eq 12 ]] && ! grep -q '^BAD' "$EV/rollback-tags-verified.txt"
