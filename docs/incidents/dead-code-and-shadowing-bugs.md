## Recurring Issue: A Redundant Local `from datetime import datetime` Made Two Hard Rejects Dead Code (BUG232-DEADCODE)

**Symptom (found 2026-07-18, while writing regression tests for T232-DL-DUALSCORER-DEBT's
already-ported DE-only hard rejects):** `_should_enter()`'s AUD232-005 time-of-day gate
(blocks the first 30 min / last 15 min of the trading session) and its extended-move 6% hard
block never actually fired in production, despite the code looking correctly ported and
passing code review. No visible symptom otherwise — the fallback gate silently ran with two
fewer protections than intended, only during a decision-engine outage (its normal reachable
state never exercises this fallback path at all).

**Root cause:** the macro-blackout hard-reject block (a few lines earlier in the same
function) has `if _macro_evt is None: try: ... from datetime import datetime, timezone,
timedelta ...` — a REDUNDANT local import, since `datetime`/`timezone` are already imported at
module level (line ~34). Per normal Python scoping rules, the mere PRESENCE of a local `import`
statement anywhere in a function body makes that name local for the ENTIRE function, even on
code paths that never execute the import. Since `reasons.get("macro_blackout")` is normally an
explicit `True`/`False` (never bare `None`) thanks to signal-engine's T220-D fast path, the
`if _macro_evt is None:` block — and its local import — is SKIPPED on essentially every real
call. The LATER time-of-day-gate code's `datetime.now(timezone.utc)` call then raises
`UnboundLocalError: cannot access local variable 'datetime' where it is not associated with a
value` — silently swallowed by that block's own `except Exception: pass` (a deliberate
fail-open pattern for tz-lookup failures, which this wasn't).

**Fix applied:** deleted the redundant local `from datetime import datetime, timezone,
timedelta` — the module-level import already covers every use in the function.

**How this was caught:** NOT by code review (the code had already passed review once) — by
writing a direct behavioral test for the time-of-day gate using a custom `datetime` subclass
overriding `.now()` to return a fixed instant, which immediately surfaced the `UnboundLocalError`
in the test output when the mocked call actually executed.

**Design invariant, generalized beyond this one function:** a local `import` statement inside
an `if`/`try` block that is normally SKIPPED will silently shadow the SAME name at module level
for the rest of that function, on every call — not just the branch containing the import. This
is a real, non-obvious Python gotcha (not specific to this codebase), and it is invisible to
static review because the local import LOOKS harmless in isolation ("just re-importing
something already available") — the bug only manifests as an `UnboundLocalError` on a
DIFFERENT code path, and only if that path is reached without the import's own block having
run first. **Grep for `from datetime import` (or any local re-import of an already-module-
level name) inside conditional blocks in any function with multiple hard-reject/early-return
branches** — this exact pattern could recur anywhere a name is imported locally "just in case"
inside one conditional branch of a large function.

**What to check if a similar silently-dead-code bug is suspected:**
```bash
# Grep for local re-imports of already-module-level names inside conditional blocks:
grep -n "^from datetime import\|^import datetime" services/market-data/src/services/paper_trading_engine.py
# Then check whether any local `from datetime import ...` (or similar) exists deeper in the
# same file, inside an if/try block — that's the shape of this bug class.

# Confirm the two hard rejects actually fire when they should (needs a real live-triggered
# UnboundLocalError to have been fixed — a stale deploy would silently still no-op):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from datetime import datetime, timezone
from src.services.paper_trading_engine import _should_enter
# a candidate whose game_plan/signal_data trips the time-of-day gate at whatever the
# real current time is would confirm this live; easier to just re-run the test suite:
"
docker exec stockai-market-data-1 python3 -m pytest tests/test_should_enter_de_parity.py -q
```

