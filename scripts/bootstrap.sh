#!/usr/bin/env bash
# One-shot local bootstrap: up stack, seed universe, ingest 10 tickers, train XGB on AAPL.
set -euo pipefail

GATEWAY="${GATEWAY:-http://localhost:8000}"

echo "→ Building + starting stack…"
make up

echo "→ Waiting for gateway to be ready…"
for i in {1..60}; do
  if curl -fs "$GATEWAY/health" >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "→ Seeding universe…"
curl -fsS -X POST "$GATEWAY/admin/seed"
echo

echo "→ Ingesting universe…"
curl -fsS -X POST "$GATEWAY/admin/ingest" \
  -H 'content-type: application/json' \
  -d '{"symbols":["AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","JPM","V","WMT","0700.HK","0005.HK"]}'
echo

echo "→ Training XGB on AAPL (runs in background task)…"
curl -fsS -X POST "$GATEWAY/ml/train" \
  -H 'content-type: application/json' \
  -d '{"symbol":"AAPL","model":"xgboost"}'
echo

echo "✓ Bootstrap complete. Open http://localhost:3000"
