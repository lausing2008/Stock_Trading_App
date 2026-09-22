# AUD-CONNPOOL-NESTEDSESSION — PT-H08's own scan-log write exhausted the DB connection pool (2026-09-22)

## Symptom

Mid-deploy on 2026-09-22, `event-intelligence` came up unhealthy after a routine rebuild and
stayed that way through a full container recreate: `docker logs` showed `service.start` logged,
then nothing — no "Application startup complete," port 8010 never bound, health check failing
continuously. Reverting the file that had just changed (`earnings.py`, that session's own
UTC-date-boundary fix) and rebuilding again reproduced the **exact same hang**, proving the new
code wasn't the cause at all — something else, unrelated to the deploy, was already wrong.

## Investigation

`pg_stat_activity` showed the real picture: `event-intelligence`'s own startup migration
(`ALTER TABLE stocks ADD COLUMN IF NOT EXISTS name_zh VARCHAR(256)`) was queued waiting for an
**ACCESS EXCLUSIVE** lock on `stocks`, and dozens of unrelated queries across the whole platform
were queued up *behind that one blocked ALTER TABLE* — Postgres's own lock-queue-ordering rule
(a pending exclusive-lock request blocks every later shared-lock request on the same relation,
even ones that would otherwise be compatible with each other) turned one stuck writer into a
platform-wide jam. `pg_blocking_pids()` traced the actual root holders: a rotating set of
`market-data` connections sitting in `idle in transaction` state — meaning Postgres was waiting
for the **client** (the Python process) to send anything further, and it simply never did, for
20-30+ minutes per connection. Killing individual stuck backends only bought seconds: new ones
kept appearing roughly once a minute, confirming an **active, ongoing** leak, not a one-time
stuck connection — and `market-data`'s own logs already showed the resulting secondary symptom:
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed
out, timeout 30.00`, plus multiple scheduled jobs skipping their own runs ("maximum number of
running instances reached").

**The decisive step was a live `py-spy dump` against the actual running process** (installed
directly into the container, no restart needed) rather than continuing to reason from
`pg_stat_activity` text or guessing at which of several externally-called functions (yfinance,
decision-engine, research-engine — all independently confirmed to have real, bounded timeouts)
might be hanging. The dump showed a worker thread parked at:

```
commit (sqlalchemy/orm/session.py:2028)
_persist_scan_log (paper_trading_engine.py:4689)
_write_no_entry_summary (paper_trading_engine.py:4769)
_scan_for_entries (paper_trading_engine.py:6913)
paper_trading_step (paper_trading_engine.py:7278)
```

## Root cause

`_persist_scan_log()` — added the same day for PT-H08 (see
`docs/audits/2026-09-21-deferred-audit-items-batch.md`) — opened its **own** `with
SessionLocal() as session:` block, a separate connection from the pool, checked out **while**
`paper_trading_step()`'s own outer session was still held open for its entire multi-portfolio
scan loop (`_scan_for_entries()` and `_monitor_positions()` share one session across every
portfolio in one scheduler run, refreshing/committing per-portfolio inside that same loop). Every
portfolio whose scan hit "no entry" — the exact condition PT-H08 exists to make diagnosable —
called `_write_no_entry_summary()` → `_persist_scan_log()`, which grabbed a **second** connection
on top of the first. With most portfolios in that state at the time, and a small shared pool
(`pool_size=5, max_overflow=10` = 15 total, split across a dozen other 1-minute-interval
scheduled jobs), this compounded into full exhaustion within a few hours of real trading-hours
traffic — a self-reinforcing failure mode, since the more portfolios stuck not trading, the more
often the leak fired.

This was never caught before deploy because the original PT-H08 tests exercised
`_persist_scan_log()` against an isolated in-memory SQLite engine with no connection-pool limits
at all — correct in isolation (it wrote the right row, failed silently on error), but the tests
never modeled the actual constraint that broke: a small, shared, size-limited pool already
holding one connection open per scan cycle. That is a resource-composition question a green unit
test suite structurally cannot answer; it needed to be asked directly ("does this new
DB-touching helper open its own session, and if so, what does that cost when it's called from
inside a loop that already has one open?") before shipping, not caught after.

## Fix

`_persist_scan_log()` now takes the caller's `session` as its first parameter and reuses it —
threaded through `_write_gate_block()`/`_write_no_entry_summary()` (both already called from
inside `_scan_for_entries()`, which already has `session` in scope at all 15 real call sites) —
instead of opening a second one. Writes via `session.begin_nested()` (a SQL SAVEPOINT), not
directly against the caller's own transaction: a failure inside this insert (e.g. a constraint
violation) rolls back only that one savepoint on exit and is then swallowed, leaving the
caller's own in-progress scan transaction exactly as it was. A bare `session.add()` with a
swallowed exception would instead have left the *entire session* in SQLAlchemy's post-flush-
error "inert, needs rollback" state, silently breaking every later use of that same session for
the rest of the scan cycle — a second, subtler failure mode the savepoint specifically avoids.

12 tests (`test_paper_entry_scan_log.py`), including one that deliberately forces a real insert
failure and confirms the *same* session still accepts further real work afterward — proving the
savepoint isolation actually holds, not just that the function doesn't crash. Sabotage-verified:
reverting to the old nested-`SessionLocal()` shape is caught by 5 of the 12 tests.

## Collateral findings from the same incident

- **Two pre-existing tests were silently broken by real-clock drift**, unrelated to this
  incident but found while re-running the full suite mid-investigation — see
  `docs/audits/2026-09-21-utc-date-boundary-triage.md`'s own write-up (fixed separately, commit
  `10e776c`).
- **A deploy-batch ordering gap**: `scripts/rebuild_backend_images.sh` correctly stops at the
  first failure (by design, see the script's own docstring) — but that meant `ranking-engine`'s
  own, already-tested, unrelated fix from earlier the same session never actually got deployed,
  because `event-intelligence` failed first in the same batch and the script never reached it.
  Confirmed via `check_deploy_drift.sh` after the incident was resolved; deployed separately once
  found. Worth remembering: a partial-batch failure means checking drift on *every* service in
  that batch once resolved, not just the one that failed.
- **A `git pull` on EC2 silently preserved a stale local file** during recovery: an emergency
  `git checkout <old-commit> -- <file>` used mid-incident to restore service left that path
  staged with local modifications; a subsequent `git pull origin prod` (reported clean
  fast-forward, no conflict) did not overwrite that specific file's working-tree content to
  match the new HEAD, silently leaving the OLD (reverted) version running under a "fully
  pulled" repo state. Caught by `git diff --cached`/`git diff` disagreeing on the same path
  during a routine drift re-check, not by the pull itself reporting anything unusual. Fixed with
  a targeted `git checkout HEAD -- <file>`. Worth remembering: after any mid-incident manual
  `git checkout <ref> -- <file>` hotfix, explicitly re-sync that specific path once the real fix
  lands — don't assume a clean `git pull` alone put it back.

## Two more instances of the same anti-pattern, found by defensive audit (2026-09-22)

After the incident above was resolved and deployed, the user gave direct, serious feedback that a
"next improvements" session had made things worse rather than better, and asked for stability
verification before any more changes. In response, `paper_trading_engine.py` was swept for any
other function that opens its *own* `SessionLocal()` while potentially being called from inside
`paper_trading_step()`'s already-open outer session — the exact shape of the bug above. Two more
were found and, per the user's explicit choice ("fix both now, same rigor as today's fix"), fixed
with the same discipline: signature change, every real call site updated, full test coverage,
sabotage verification.

1. **`_should_enter()`** — its macro-blackout check opened its own `with SessionLocal() as
   _evsess:` for a single `SELECT` on every candidate evaluated, inside `_scan_for_entries()`'s
   own per-symbol loop (itself inside `paper_trading_step()`'s outer session) — the highest-
   frequency call site of any of these three. Fixed by adding `session` as `_should_enter()`'s
   first parameter and using the caller's session directly for that `SELECT` (no savepoint
   needed — a plain read, not a write). All 7 real call sites updated: `_scan_for_entries()`,
   `conditional_orders.py`'s entry-signal check, and 5 sites in `backtest/gate_harness.py`'s
   replay/verification functions (all of which already had a `session` in scope). Verified with
   `tests/test_should_enter_de_parity.py` (61 tests), including one real end-to-end test that
   inserts a genuine blackout-window `EconomicEvent` row into an in-memory SQLite DB and confirms
   `_should_enter()`, given a real session, actually queries and rejects on it.

2. **`_compute_hk_breadth()`** (called via `_fetch_hk_market_regime()`) — unconditionally opened
   its own session on every cache-refresh (dormant most cycles thanks to a 30-minute cache, real
   whenever it actually re-runs). Fixed with an optional-session pattern rather than a required
   parameter, since this one has two genuinely different callers: `paper_trading_step()`'s
   per-scan regime lookup (which has a session to lend) and the standalone
   `/stocks/regime?market=HK` route via `get_last_hk_regime()` (which does not). Both branches now
   call into one shared `_compute_hk_breadth_with(session)` so the query logic can't drift between
   them. Verified with `tests/test_aud_connpool_hk_breadth_session_reuse.py` (6 source-text tests
   covering the signature, the conditional branch, and both call sites) plus the pre-existing
   `_compute_hk_breadth` delisted-stock test in `test_redis_pooling_and_delisted_sweep.py`, updated
   to extract across the new `_compute_hk_breadth`/`_compute_hk_breadth_with` split. A genuine
   end-to-end behavioral test (real SQLite session, real Stock/Price rows) was attempted but
   dropped: unlike `_should_enter()`'s macro-blackout check (a local `from sqlalchemy import text`
   + raw SQL, re-resolved fresh at call time), this function's query uses module-level
   `select(Stock.id, ...)`/`Price.stock_id` — names bound once at import time — so a real
   behavioral proof would require a genuine fresh import of `paper_trading_engine.py` against a
   real `db` package, which isn't importable in this unit-test environment (`shared/db/session.py`
   calls `get_settings().database_url` and `create_engine(...)` at import time, needing a real
   Postgres connection string this local environment doesn't have). Sabotage-verified instead:
   reverting `_compute_hk_breadth()` to its old unconditional-own-session shape is caught by the
   test that checks for the `if session is not None:` branch.

Full market-data test suite re-run after both fixes: 4180 passed, 1 skipped, 0 regressions.

## The lesson

A small, size-limited connection pool (15 total, here) shared across a dozen independent
1-minute-interval scheduled jobs has almost no slack: any single code path that opens an *extra*
connection from inside a scan that already holds one — even briefly, even "just for
observability" — multiplies real load on that pool by however many times that path fires per
cycle. The question to ask before adding any new DB write inside an existing hot loop is not
"does this correctly write the row" (which a unit test answers fine) but "what does the caller
already have open, and does this need its own connection at all, or can it ride on the one
already there" — a resource-composition question no amount of green tests in isolation will
surface, and the kind of check that needs to happen at write time, not discovered live in
production hours later.
