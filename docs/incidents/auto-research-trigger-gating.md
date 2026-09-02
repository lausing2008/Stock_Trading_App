## Recurring Issue: A SECOND, Completely Independent Auto-Research Trigger — Never Gated By `auto_research_enabled` At All (Fixed 2026-07-29)

**Symptom:** user directly asked "how often does Market Pulse trigger and is it asking
Claude?", which surfaced a much bigger, unrelated finding while checking real production
usage: "but my claude still consume a lot of tokens, where could that from?" — followed by
"I never clicked on Generate report today," directly contradicting an initial (wrong)
assumption that 68 real research-report generations in 24h were manual user activity.

**Root cause:** the 2026-07-28 Claude API cost audit (see that section elsewhere in this
file) found and fixed exactly ONE auto-research trigger path — market-data's own
`_auto_trigger_research()` in `scheduler.py` (a bounded top-5-per-refresh-cycle sweep,
correctly gated behind the new `auto_research_enabled` flag). That audit was thorough for the
path it investigated, but never searched for OTHER callers of `POST /research/{symbol}/
trigger` anywhere else in the codebase. A second, completely independent, much older call
site existed inside signal-engine's own `_bulk_persist()` (`services/signal-engine/src/api/
routes.py:440`, tagged `INT-4` in a pre-existing comment, predating the 2026-07-28 audit by
weeks) — fires the SAME `/trigger` POST on **every symbol with a BUY signal on ANY of its 4
horizons, every single signal-refresh cycle**, with zero cap and zero flag check anywhere.

**Live evidence that pinned this down, not assumption:** correlated the exact timestamps of
all 68 `research.generated` log lines over a real 24h window and found they were NOT spread
out like manual browsing — a dense automated burst of ~45 reports fired between 04:20:02 and
04:28:03 (one roughly every 10-15 seconds across 45 distinct symbols, far faster than any
human clicking through stock pages), plus a second burst at 01:26-01:29. Cross-checked against
`docker logs ... | grep auto_research_triggered` — **zero** hits, proving the ALREADY-FIXED
scheduler sweep was correctly firing zero times. Directly queried production Postgres:
`SELECT COUNT(DISTINCT stock_id) FROM signals WHERE signal = 'BUY' AND ts > now() - interval
'24 hours'` returned **46** — matching the ~45-report burst almost exactly. This confirmed
INT-4's per-BUY-signal trigger, not manual usage or the already-fixed scheduler sweep, was the
real and only remaining cause.

**Fix applied:** gated ONLY the `/trigger` POST call behind the exact same
`stockai:admin:feature:auto_research_enabled` Redis key `_auto_trigger_research()` already
uses (`== "1"` string-equality check, matching that function's own convention exactly —
deliberately NOT a bare truthiness check, which would incorrectly treat an unset/`None` value
as enabled). **Deliberately did NOT gate the adjacent `/summary` GET or the INT-7 research-
divergence log line that reads it** — that GET only reads whatever report is ALREADY cached
(costs nothing, schedules no new generation) and is a genuinely separate, useful mechanism
(`signal.research_divergence` warns when a BUY signal disagrees with the research
recommendation) that must keep working regardless of whether auto-generation itself is
enabled. `trigger_research()`'s own pre-existing 6h cooldown + in-flight dedup (already fixed
2026-07-28) still applies underneath this new gate as a second layer of protection, unchanged.

**Tests**: `services/signal-engine/tests/test_int4_research_trigger_gated.py` (5 cases,
source-text regression checks — `_bulk_persist()` is 250+ lines with heavy DB/HTTP
dependencies disproportionate to this fix's actual scope, matching this repo's established
precedent for functions of this shape). Confirms: the `/trigger` POST is gated by the flag
check (not the reverse order), the gate uses exact `"1"` string equality, the `/summary` GET
and INT-7's divergence check are positioned OUTSIDE the gate's own if-body, the whole new gate
still sits inside the pre-existing outer `try`/`except Exception` (a Redis outage on this
check must fail open, never crash signal persistence for the whole symbol), and
`_research_fetched` still flips to `True` regardless of the gate's outcome (otherwise a
disabled flag would cause this block to re-attempt the gated-no-op trigger AND re-fetch
`/summary` on every BUY-style iteration for the same symbol within one `_bulk_persist()` call,
instead of exactly once).

**Adversarial verification** — 2 sabotage cycles, both caught and reverted: removing the gate
entirely (all 5 tests correctly failed); moving the `/summary` GET and INT-7's divergence
check INSIDE the gate's own if-body (an over-broad-gate mistake that would have also silenced
INT-7 whenever auto-research is disabled) — caught by the dedicated
`test_summary_get_and_int7_divergence_check_are_not_gated` test. Full 119-in-scope-test
signal-engine suite green (up from 114, excluding the 2 pre-existing, unrelated failure groups
already documented elsewhere in this file — `test_signal_generator.py`'s `_decide` import-
collection error and 4 `test_analyst_momentum.py` failures, both confirmed via `git stash` to
predate this change). `pyflakes` clean (all 3 pre-existing warnings confirmed unchanged via
`git stash`).

**Also updated**: `admin-ai-features.tsx`'s "Auto Research Report Generation" toggle
description now accurately describes BOTH gated trigger paths (the scheduler sweep AND the
signal-engine per-BUY-signal trigger), since the same one switch now genuinely controls both.

**Design invariant reinforced**: an audit that finds and fixes ONE call site of a pattern
(here: "something calls `/research/{symbol}/trigger` without a cost gate") should not be
assumed to have found EVERY call site of that same pattern — a `grep -rn` for the actual
endpoint/function being called, across the WHOLE codebase, not just the file the original bug
report pointed at, is the only way to be sure. This is the same class of lesson already
documented elsewhere in this file for the `shared/common/` file-sync sweeps after container
reboots — "found and fixed one instance" and "found and fixed every instance" are different
claims, and only an exhaustive search proves the second one.

**What to check if this looks wrong**:
```bash
docker exec stockai-signal-engine-1 grep -n 'auto_research_enabled' /app/src/api/routes.py
# Should show the new gate. If real Sonnet report generations still appear to correlate with
# BUY-signal refresh cycles rather than manual "Generate Report" clicks, confirm the flag's
# live value and re-check for a THIRD, still-undiscovered caller of /research/{symbol}/trigger
# — grep the whole repo, not just the two files already found:
grep -rn "research/{.*}/trigger\|research_engine_url}/research/" services/*/src/ --include="*.py"

docker exec stockai-redis-1 redis-cli get stockai:admin:feature:auto_research_enabled

# Check whether the INT-4 gate is actually suppressing triggers now (should show zero real
# POST /trigger calls reaching research-engine while the flag is off):
docker logs stockai-research-engine-1 --since 1h | grep -c 'research.generated'
```

---

