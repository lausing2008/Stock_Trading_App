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

---

# AUD-POOL-INGESTHOLD — ingest held a pooled connection through every provider request (2026-11-02)

## Symptom

On Mon 2026-11-02, market-data's pool (`pool_size=5, max_overflow=10`) ran out 11 times between
16:45 and 20:40 UTC (`QueuePool limit of size 5 overflow 10 reached`). Each time one caller
failed: three HTTP requests, an alert check, an ingest, squeeze ignition, the RZY mail drain and
options flow. The same had happened 3 times on Oct 30, 19:00–19:35 UTC.

## Evidence

The `pool_watch` holder log added on Oct 30 (`2609ff6c`) recorded 117 `db.pool_pressure` lines.
There were 12 snapshots at 15 of 15 connections; in every one of them, 5 to 12 of the holders
were `ingestion.py:306 ingest_symbol ← ingestion.py:702 _fetch`, each held for up to 18.5 s.
Extracts are in `docs/2026-11-02/evidence/`, and the full log is kept off-git at
`~/evidence/2026-11-02/` on EC2.

## Root cause

- `ingest_symbol` opens `with SessionLocal() as session` and reads the stock, the head bar and
  the cooldown record.
- It then calls `adapter.fetch_ohlcv` (a provider HTTP request) inside the same session, and only
  commits after the upsert.
- A session keeps its connection from the first SELECT until it commits, so every ingest worker
  held a pooled connection through the whole network round trip.
- `ingest_universe` runs 6 workers. When the 5m ingest and the market refresh overlap, up to 12
  of 15 connections are pinned.
- The UW 429 retry (`f5eabd6a`, Nov 1) waits 1.5–5 s *inside* that request, which lengthened the
  holds. It did not start the problem, which predates it.

## Fix

- Commit the read-only transaction immediately before the provider loop, so the connection goes
  back to the pool during the fetch. The writes check one out again.
- `force=True` is excluded: its DELETE must commit together with the INSERT.
- Test: `test_ingest_pool_hold.py` runs the real `ingest_symbol` on PostgreSQL with a real
  QueuePool and counts the checked-out connections while the fake provider is being asked.
  Incremental: 0, and the write still advances the head. Force: 1, held deliberately.
  Removing the commit fails the test (1 held).
- Deployed 2026-11-03 08:40 UTC as `92ae1f28` (market-data), between sessions. Healthy, drift 0/12,
  no pool errors at startup. Production effect is unconfirmed until a US session has run on it.
- First US session on it (Nov 3, saved evidence `~/stockai-evidence/2026-11-03`): 0 pool-exhaustion
  errors (Nov 2: 11). Ingest holders moved from `ingestion.py:306` (the head read, held through the
  fetch, max 18.5 s) to `:523` (the write, max 2.7 s). The pool still reached 15/15 twice
  (16:35, 20:01 UTC; Nov 2: 12 times) without a failed checkout, so the headroom is thin, not gone.
- AUD-IDLETX-LONGHOLD: `pool_watch` now logs `db.long_hold` once for any connection held past
  240 s, and `db.long_hold_released` when it returns, both with the call-site origin. This is to
  name the session behind the 300 s idle-in-transaction kills
  (`docs/audits/2026-11-03-earnings-history-write-and-idle-transaction.md`). Deployed
  2026-11-04 08:40 UTC as `de639a94` (market-data), after the HK post-close run. Healthy, 0
  tracebacks, 0 pool errors, drift 0/12. Rollback image `stockai-market-data:rollback-before-longhold`.

## The lesson

- A session is a connection lease from its first query, not from its first write.
- Any I/O placed between a read and a write inside one session holds a pool slot for the length
  of that I/O, whether it is HTTP, a sleep or a retry wait.
- `pool_watch`'s call-site attribution is what made this a one-line diagnosis rather than another
  open-ended investigation.

---

# AUD-SIG-REFRESHOVERLAP: stacked signal refresh passes exhausted market-data's pool (2026-11-04)

## Symptom

- **When:** from 21:03 UTC on 2026-11-04, the last hour of the US session.
- **Failures:** market-data logged 6,722 tracebacks, 2,160 of them `QueuePool limit of size 5
  overflow 10 reached`. `get_prices` accounted for 4,879 of the 5,495 failed requests; the
  others were `fundamentals_bulk` 264, user authentication 166, `latest_prices` 144 and
  `data_freshness` 38.
- **Knock-on:** the US post-close run and `flow_digest_close` failed on the same timeout.

## Cause, in order

1. **The host had no memory headroom.**
   - Usage: market-data 2.58 GB (1.27 GB of it glibc per-thread arenas), ml-prediction 2.57 GB
     (the training peak, kept all day), signal-engine 1.87 GB (1.05 GB of it in swap).
   - The host has 7.6 GB of RAM plus 2 GB of swap, and the swap was full; about 160 MB was free
     even with the market closed.
   - During market hours both cores ran about 90% busy.
2. **market-data slowed from about 19:16 UTC.** signal-engine gives up on a price request after
   15 s, while market-data waits up to 30 s for a connection, so abandoned requests kept using
   its capacity.
3. **Refresh passes stacked.**
   - market-data posts `/signals/refresh` every 5 minutes, and a US pass over ~160 symbols takes
     about 9 minutes (HK was measured at 3.3 s per symbol).
   - Nothing stopped a new pass while one was running, so about a dozen ran at once: AAPL was
     attempted 12 times and NVDA 14 times in 21:00–21:10.
   - On 2026-11-03 passes started from 19:38 UTC finished between 23:21 and 23:39.
4. **Thread starvation turned slow into stuck.** market-data's handlers are sync, and closing a
   request's session needs a free threadpool worker. Once every worker waited for a connection,
   used connections sat idle in transaction until PostgreSQL's 300 s kill.

## Fix

- **signal-engine (`AUD-SIG-REFRESHOVERLAP`):** one pass per market at a time.
  - A request while a pass is running returns `skipped_running`; the next request after it ends
    runs.
  - Each pass now logs `signals.refresh.pass_done` with its duration. A pass with no failures
    used to log nothing, so passes could not be timed.
  - Test: `test_refresh_overlap_guard.py` compiles the real endpoint and task from the source.
    Removing either check, or the release, fails it.
- **ml-prediction (`AUD-ML-TRAINCHILD`):** nightly training and `tune_all` run in a child process
  that exits, so the training peak is not kept. Each market trains only after its own close.
- **market-data (`AUD-MD-ARENAS`):** `MALLOC_ARENA_MAX=2`, and a 500 MB log cap (it was 100 MB,
  which kept less than a day).
- **Not done:** a shorter connection wait. The pool is configured in `shared/db/session.py`, so
  changing it means rebuilding all 12 backends. Revisit if pool errors recur with the guard in
  place.

# AUD-DIGEST-CLOSEDSESSION: the morning digest used its session after closing it (2026-11-05)

- **What:** `send_morning_digest` passed `session` to `_may_send` in its send loop, which runs
  after `with SessionLocal() as session:` has exited.
- **Why that leaks:** a closed Session silently checks out a new connection and begins a
  transaction that nothing ends.
- **Seen:** the HK digest's connection was opened at 00:50:06 UTC. PostgreSQL killed it at
  00:55:05 (idle-in-transaction timeout), and garbage collection released it at 01:05:08. That
  caused the traceback at 01:05. It happened on every digest, twice a day.
- **Found by:** the `db.long_hold` log added under AUD-IDLETX-LONGHOLD, its first real catch.
- **Fix:** the preference is read in its own short session before any SMTP send.
- **Tests:** `test_no_session_use_after_with.py` scans every market-data module for a session
  used after its `with` block. It fails on the old code. A repo-wide scan found no other
  instance.
