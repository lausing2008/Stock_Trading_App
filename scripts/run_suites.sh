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
#
# NOT A SUBSTITUTE FOR CI. A caller can still ignore any exit status; the 2026-09-24 follow-up
# audit is right that "cannot be chained past" was too strong. Real enforcement is the required
# status check in the deploy/merge policy — this only stops the local mistake it was written for.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 3
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
# W01 (2026-09-24 follow-up audit): invoked with ZERO arguments this printed
# "ALL SUITES GREEN" and exited 0 without running pytest — a gate that passes when it ran
# nothing is the same class of defect it was written to prevent. Refuse instead of guessing a
# default, because a wrong default would quietly test the wrong thing.
if [ "$#" -eq 0 ]; then
  echo "usage: bash scripts/run_suites.sh <service> [service ...]" >&2
  echo "  e.g. bash scripts/run_suites.sh market-data signal-engine" >&2
  echo "REFUSING: no suites requested — a runner that reports success without running is not a gate" >&2
  exit 2
fi

for s in "$@"; do
  if [ ! -d "services/$s" ]; then
    echo "REFUSING: no such service directory: services/$s" >&2
    exit 2
  fi
done

FAILED=0
RAN=0
for s in "$@"; do
  (cd "services/$s" && python -m pytest -q -p no:cacheprovider > "/tmp/suite_$s.log" 2>&1)
  E=$?
  RAN=$((RAN + 1))
  printf "%-22s exit=%s  %s\n" "$s" "$E" "$(tail -1 "/tmp/suite_$s.log")"
  if [ "$E" -ne 0 ]; then FAILED=1; grep "^FAILED" "/tmp/suite_$s.log" | head -5; fi
done
if [ "$FAILED" -ne 0 ]; then echo "SUITES RED — refusing to proceed" >&2; exit 1; fi
if [ "$RAN" -ne "$#" ]; then
  echo "REFUSING: requested $# suite(s) but ran $RAN" >&2
  exit 2
fi
echo "ALL $RAN SUITE(S) GREEN"
