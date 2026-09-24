#!/usr/bin/env bash
# Run the named service suites and FAIL LOUDLY if any is red.
#
#   bash scripts/run_suites.sh market-data signal-engine
#   bash scripts/run_suites.sh market-data && git commit ...   # safe: this DOES gate
#
# Written because three separate times this session a command chained
#   pytest ... | tail -1 && git commit
#   pytest ... ; echo "EXIT=$?" && git commit
#   for s in ...; do pytest; printf ... ; done && git commit
# and every one of them pushed over a red suite: a pipe, an echo and a printf all return 0,
# so `&&` never saw pytest's status. The gate has to be IN the script, not in the chain.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 3
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
FAILED=0
for s in "$@"; do
  (cd "services/$s" && python -m pytest -q -p no:cacheprovider > "/tmp/suite_$s.log" 2>&1)
  E=$?
  printf "%-22s exit=%s  %s\n" "$s" "$E" "$(tail -1 "/tmp/suite_$s.log")"
  if [ "$E" -ne 0 ]; then FAILED=1; grep "^FAILED" "/tmp/suite_$s.log" | head -5; fi
done
if [ "$FAILED" -ne 0 ]; then echo "SUITES RED — refusing to proceed"; exit 1; fi
echo "ALL SUITES GREEN"
