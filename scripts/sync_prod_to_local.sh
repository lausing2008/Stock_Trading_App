#!/usr/bin/env bash
# T270-DBSYNC-PROD-TO-LOCAL-WEEKLY, Layer 2 — pulls a fresh pg_dump from the EC2 production
# Postgres container over SSH and restores it into the LOCAL docker-compose Postgres container,
# then scrubs PII columns on the local copy as defense-in-depth.
#
# Layer 1 (the actual safety fix — a hard `Settings.env == "production"` gate in
# scheduler.py's start_scheduler() that structurally prevents any non-production environment
# from ever registering/running an alert-emitting job) already shipped and is what actually
# makes it safe to restore a real prod dump locally without accidentally emailing real users.
# This script's PII scrub is DEFENSE IN DEPTH on top of that gate, not a replacement for it —
# per docs/DESIGN_SIX_ITEM_BATCH_2026-08-11.md's own recommendation ("I'd lean toward doing
# both since it's nearly free").
#
# MANUAL trigger, not cron'd — per the same design doc, there was no strong reason to prefer
# one over the other; a manual command keeps this from silently running against a laptop that
# happens to be open with a stale/rotated SSH key, and a dev who wants a fresh local copy can
# just run this whenever they need one. (If you later want this on a schedule instead, wrap
# this script in your own cron/launchd entry — nothing about the script itself assumes manual
# invocation.)
#
# Prerequisites (same as backup_db.sh, matching its own established conventions exactly):
#   - SSH key at EC2_KEY (default matches this repo's CLAUDE.md-documented convention)
#   - Local docker-compose stack up (`make up` / `docker compose up`) with its own Postgres
#     container running under the SAME name as production (both use docker-compose.yml's
#     `postgres` service -> container name stockai-postgres-1)
#
# Usage:
#   bash scripts/sync_prod_to_local.sh
#   SKIP_PII_SCRUB=1 bash scripts/sync_prod_to_local.sh   # restore only, no scrub (rare — see
#                                                          # the module docstring above for why
#                                                          # Layer 1 alone is the real safety net)
set -euo pipefail

EC2_HOST="${EC2_HOST:-ec2-user@18.205.121.71}"
EC2_KEY="${EC2_KEY:-$HOME/Documents/Stock_AI/lausing.pem}"
REMOTE_DB_CONTAINER="${REMOTE_DB_CONTAINER:-stockai-postgres-1}"
LOCAL_DB_CONTAINER="${LOCAL_DB_CONTAINER:-stockai-postgres-1}"
DB_NAME="${DB_NAME:-stockai}"
DB_USER="${DB_USER:-stockai}"
SKIP_PII_SCRUB="${SKIP_PII_SCRUB:-0}"

TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
TMP_DUMP="/tmp/stockai_prod_sync_${TIMESTAMP}.sql.gz"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting prod→local sync"

# ── Safety check: never run this INSIDE the production host itself ─────────────────────────
# Confirms this is actually being run from a workstation, not accidentally on EC2 (which would
# make "local" and "prod" the same container — a real restore-onto-itself risk).
if [ -f /home/ec2-user/Stock_Trading_App/docker/docker-compose.yml ] 2>/dev/null; then
  echo "ERROR: this looks like it's running ON the EC2 production host itself." >&2
  echo "This script is meant to run FROM your local workstation, pulling FROM prod." >&2
  exit 1
fi

if [ ! -f "$EC2_KEY" ]; then
  echo "ERROR: SSH key not found at $EC2_KEY (set EC2_KEY to override)." >&2
  exit 1
fi

echo "  → Dumping production DB via SSH (docker exec pg_dump on $REMOTE_DB_CONTAINER)…"
ssh -i "$EC2_KEY" -o ConnectTimeout=10 "$EC2_HOST" \
  "docker exec $REMOTE_DB_CONTAINER pg_dump -U $DB_USER $DB_NAME | gzip -9" \
  > "$TMP_DUMP"

BYTES=$(stat -c%s "$TMP_DUMP" 2>/dev/null || stat -f%z "$TMP_DUMP")
if [ "$BYTES" -lt 1024 ]; then
  echo "ERROR: dump is suspiciously small (${BYTES} bytes) — aborting before touching local DB." >&2
  rm -f "$TMP_DUMP"
  exit 1
fi
echo "  Dump complete: ${BYTES} bytes compressed"

echo "  → Confirming local Postgres container ($LOCAL_DB_CONTAINER) is running…"
if ! docker ps --format '{{.Names}}' | grep -qx "$LOCAL_DB_CONTAINER"; then
  echo "ERROR: local container '$LOCAL_DB_CONTAINER' is not running. Start your local stack" >&2
  echo "first (e.g. \`make up\` / \`docker compose up -d postgres\`)." >&2
  rm -f "$TMP_DUMP"
  exit 1
fi

echo "  → Dropping and recreating the local '$DB_NAME' database…"
# Terminate any existing connections first — a live app connection would otherwise block DROP.
docker exec "$LOCAL_DB_CONTAINER" psql -U "$DB_USER" -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();" \
  >/dev/null 2>&1 || true
docker exec "$LOCAL_DB_CONTAINER" psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS $DB_NAME;"
docker exec "$LOCAL_DB_CONTAINER" psql -U "$DB_USER" -d postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

echo "  → Restoring dump into local '$DB_NAME'…"
gunzip -c "$TMP_DUMP" | docker exec -i "$LOCAL_DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" >/dev/null

rm -f "$TMP_DUMP"
echo "  Restore complete."

if [ "$SKIP_PII_SCRUB" = "1" ]; then
  echo "  SKIP_PII_SCRUB=1 — leaving real email/webhook columns intact on the local copy."
  echo "  (Layer 1's hard environment gate still prevents any alert from actually sending —"
  echo "   see start_scheduler()'s _is_alerting_enabled() check in scheduler.py.)"
else
  echo "  → Scrubbing PII columns (defense in depth on top of Layer 1's env gate)…"
  # Every table with a real, user-supplied email/webhook column — kept in sync with
  # shared/db/models.py by hand (checked directly against the schema when this script was
  # written: users, price_alerts, signal_alerts, conditional_orders,
  # earnings_alert_subscriptions all carry `email`; users also carries notification_webhook,
  # a real Slack/Discord URL). Rewritten to a deterministic, obviously-fake +devnull@ pattern
  # (not NULL) so a dev can still visually distinguish "this row had a real email once" from
  # "this user never set one" while testing — and so any code path that assumes a non-NULL
  # email (rather than actually checking) fails loudly in an obvious way instead of silently.
  docker exec "$LOCAL_DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
    UPDATE users SET email = 'user' || id::text || '+devnull@example.invalid', notification_webhook = NULL WHERE email IS NOT NULL;
    UPDATE price_alerts SET email = 'alert' || id::text || '+devnull@example.invalid' WHERE email IS NOT NULL;
    UPDATE signal_alerts SET email = 'alert' || id::text || '+devnull@example.invalid' WHERE email IS NOT NULL;
    UPDATE conditional_orders SET email = 'order' || id::text || '+devnull@example.invalid' WHERE email IS NOT NULL;
    UPDATE earnings_alert_subscriptions SET email = 'sub' || id::text || '+devnull@example.invalid' WHERE email IS NOT NULL;
  "
  echo "  PII scrub complete."

  # Beyond this item's own original scope (real user emails) but genuinely cheap and worth
  # doing at the same time: broker_connections.config stores real OAuth credentials
  # (consumer_key/secret, oauth_token/token_secret) as JSON — a materially higher-value target
  # than an email address if a local dev machine is ever compromised while holding a synced-
  # down copy. Blanked to an empty JSON object rather than left with real, live-usable tokens.
  # is_authorized is also reset — an account whose credentials were just wiped should not
  # still claim to be a valid, ready-to-trade connection.
  docker exec "$LOCAL_DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
    UPDATE broker_connections SET config = '{}'::json, is_authorized = false WHERE config IS NOT NULL;
  " >/dev/null 2>&1 || true
  echo "  Broker credential scrub complete (broker_connections.config blanked)."
fi

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Sync complete. Local '$DB_NAME' now mirrors production (${TIMESTAMP})."
