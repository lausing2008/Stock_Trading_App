#!/usr/bin/env bash
# Run all DB migrations in order against the production database.
#
# Usage (from EC2, in the docker/ directory):
#   bash /home/ec2-user/Stock_Trading_App/scripts/migrations/run_migrations.sh
#
# Each migration is idempotent (IF NOT EXISTS, ON CONFLICT DO NOTHING, etc.)
# Safe to re-run — already-applied changes are skipped automatically.
#
# For a FRESH instance: migrations 001 and 002 can be skipped because
# create_all() builds the schema from models.py already. Only 003 is needed
# on every instance (data fix, not schema).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="/home/ec2-user/Stock_Trading_App/docker"

run_migration() {
  local num="$1"
  local file="$SCRIPT_DIR/$num"
  echo "▶ Running $num ..."
  docker compose -f "$DOCKER_DIR/docker-compose.yml" exec -T postgres \
    psql -U stockai -d stockai < "$file"
  echo "✓ $num done"
  echo ""
}

echo "=== Stock Trading App — DB Migrations ==="
echo ""

# Schema migrations — skip on fresh instances (create_all handles these)
run_migration "001_add_sector_to_paper_trades.sql"
run_migration "002_add_signal_at_exit_to_paper_trades.sql"

# Data migrations — run on EVERY instance (fresh and upgraded)
run_migration "003_fix_paper_portfolio_config.sql"

# Schema migrations continued
run_migration "004_add_stock_id_to_paper_trades.sql"

# Data integrity — partial unique index on active paper portfolios
run_migration "005_paper_portfolio_unique_index.sql"

# Signal deduplication — unique constraint prevents concurrent refresh races
run_migration "006_signal_unique_constraint.sql"

echo "=== All migrations complete ==="
