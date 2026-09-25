#!/usr/bin/env bash
# W01 acceptance checks for scripts/run_suites.sh, from the 2026-09-24 follow-up audit.
#
# The runner exists to stop a red suite reaching a commit. A gate that reports success when it
# ran nothing has the same defect it was written to prevent, so its own edge cases need a test.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
R="$ROOT/scripts/run_suites.sh"
pass=0; fail=0
check() { # name expected_exit actual_exit
  if [ "$2" -eq "$3" ]; then printf "  ok   %-46s (exit %s)\n" "$1" "$3"; pass=$((pass+1));
  else printf "  FAIL %-46s expected %s got %s\n" "$1" "$2" "$3"; fail=$((fail+1)); fi
}

bash "$R" >/dev/null 2>&1; check "zero arguments are refused" 2 $?
bash "$R" definitely-not-a-service >/dev/null 2>&1; check "an unknown service name is refused" 2 $?
bash "$R" market-data definitely-not-a-service >/dev/null 2>&1
check "one bad name rejects the whole invocation" 2 $?

# A green suite still passes, and the message names how many ran.
out="$(bash "$R" news-intelligence 2>&1)"; rc=$?
check "a green suite still exits zero" 0 $rc
case "$out" in *"ALL 1 SUITE(S) GREEN"*) printf "  ok   %-46s\n" "reports the number of suites run"; pass=$((pass+1));;
  *) printf "  FAIL %-46s\n" "reports the number of suites run"; fail=$((fail+1));; esac

echo "  ---"
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
