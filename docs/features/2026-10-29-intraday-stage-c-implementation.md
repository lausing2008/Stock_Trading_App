# Intraday Stage C — implementation record

Date: **2026-10-29**. Branch: `intraday-contracts`. Status: **built and tested; not deployed;
no generator**. Production generation is disabled (`INTRADAY_PRODUCTION_ENABLED = False`, a
code constant). Implements the [contract design](2026-10-29-intraday-stage-c-contract-design.md)
(revision 3), after the [inventory](2026-10-29-intraday-stage-c-inventory.md) and two review
rounds ([review](../audits/2026-10-29-intraday-stage-c-design-review.md), CDR-01..06 and
CDR-R2-01..04).

**Scope of the acceptance below:** contracts, storage and daily isolation. It establishes
nothing about execution readiness, provider latency, a generator, or returns.

## What was built

| Area | Where | Notes |
|---|---|---|
| Horizon registry | `shared/common/horizons.py` | `DAILY_HORIZONS`, the `INTRADAY` spec (US only), `parse_daily_horizon`, `require_intraday_market`, `INTRADAY_PRODUCTION_ENABLED = False`. The shared `signalhorizon` enum is **unchanged**. |
| Contract | `services/market-data/src/services/intraday/contract.py` | Session times from the exchange calendar (early closes, coverage stop, US only); canonical encoding and the raw/source/input/derived hashes; identity, evaluation, occurrence and outcome keys; state classes and the append rule; detection-time decision (expiry first, exclusive); event timing (eligible/observed/decided, `observed` vs `replay_logical`); invariants. |
| Research fill model | `.../intraday/fills.py` | Market entry at the first open strictly after activation and before expiry; resting bracket judged from the entry bar, stop wins a double touch, target needs a trade-through; scheduled max-hold and liquidation at their open; invalid bracket at fill rejected; `unresolved_exit_at_close` with a mark and no invented fill. Forecast outcomes are descriptive, with truncated and missing-terminal statuses. Every fill has `execution_grade = false`. |
| Storage | `.../intraday/models.py`, `schema.py`, `store.py` | Ten insert-only tables in market-data's own `MetaData`, so no other service creates them. Composite lineage FKs carry `run_key` (and the identity where relevant). On PostgreSQL, triggers refuse every UPDATE and any DELETE outside `SET LOCAL stockai.intraday_retention`, validate each append (first transition, `seq`, `from_state` = latest), refuse writes to a sealed session, and at commit require a setup to be complete. Writers are idempotent. Seal, archive and setup deletion are atomic and retry-safe. |
| Input reader | `.../intraday/inputs.py` | `prices` and `bar_provenance` read in one `REPEATABLE READ` read-only snapshot. RZY's bar preparation and window price basis are reused. 15m context is **derived from 5m only**. `input_read_at` is the real read (observed) or the cutoff (`replay_logical`); first availability is always unknown. |
| Read API | `services/market-data/src/api/intraday.py`, `.../intraday/read.py` | Admin only, under **`/stocks/intraday/*`**: `identities`, `evaluations`, `setups`, `unresolved`. Mode is required; replay requires its run and observed rejects one; identity is required; endpoint-specific keyset cursors; `sealed` vs `changing_view` membership; per-identity coverage; schema failure reported as 503. |
| Daily guards | six services (below) | Daily writes and unknown-style fallbacks reject INTRADAY; the seven high-risk and the medium readers filter to `DAILY_HORIZONS`. |
| Startup | `services/market-data/src/main.py` | `ensure_schema_safely()` runs under the cross-service schema lock and DDL timeouts. A failure is recorded and served as 503 by `/stocks/intraday`; it never takes market-data down. |

## Deviations from the design, and why

- **API prefix:** `/stocks/intraday` instead of a new `/intraday` gateway prefix. The gateway's
  existing `stocks` route serves it, as it does `/stocks/rzy`, so the gateway-route-gap incident
  class cannot occur. No `proxy.py` change.
- **15m context:** derived from complete 5m constituents only, not native 15m. Every context bar
  then has a traceable constituent list (`derived_hash`).
- **`read_clock_basis`** column on inputs. A replay reads history after the fact, so its
  `input_read_at` is the logical cutoff, labelled. Without the column, replay records would
  break the read-before-detection invariant.
- **Seal and retention jobs exist as functions but are not scheduled.** No producer writes in
  Stage C, so nothing needs sealing or retention. Scheduling them is part of the shadow-generator
  release, and must happen before its first observed write.

## Daily isolation — guard coverage matrix and sabotage result

The sabotage script removes each guard in turn and runs **only that guard's own witness**; the
witness must fail. **41 cases: 40 caught; 1 documented as unreachable.**

| Guard | Witness (kind) | Sabotage |
|---|---|---|
| `parse_daily_horizon` (registry) | signal-engine 422 tests (behaviour) | caught |
| Enum lacks INTRADAY | PostgreSQL 16: `CAST('INTRADAY' AS signalhorizon)` fails; all three daily columns use the type; labels are exactly the four (behaviour, real database) | — (enum unchanged by design) |
| `/signals` all-latest, `/suppressed`, `/history`, `/{symbol}` style | 422 before any SQL and no live regeneration (behaviour) | caught (×2) |
| `/{symbol}` `all_sig[style_key]` | — | **unreachable**: style is validated at the top of the function, so the old SWING fallback cannot be hit; the validation itself is witnessed |
| Both signal upserts bind `parse_daily_horizon(...)` | AST (code presence) | caught |
| Outcome evaluator hold days | `_hold_days_for` refuses (behaviour) and both loops go through it, no subscripts (code presence) | caught |
| Outcome evaluator pending select | compiled PostgreSQL SQL (code presence) | caught |
| Calibration TA / conviction / outcomes calibration | compiled SQL. TA and conviction are behind F12's `@research_only` guard, so the witness runs the wrapped body. | caught (×2) |
| Analytics accuracy / rolling / factor / walk-forward / summary, `/consensus`, `/recent_changes`, `calibration_store` | compiled SQL: **every** statement against the daily tables carries the predicate | caught (×5) |
| ML meta-trainer raw SQL | captured SQL and bound parameters | caught |
| ML route style fallbacks | source (code presence) locally; real routes return 422 in the production image (container suite) | caught |
| event-intelligence catalyst read | source (code presence; apscheduler is not installable locally) | caught |
| market-data paper-exit RSI, data-quality freshness, curation skip | source (code presence); the DQ strings are evaluated | caught (×3) |
| Watchlist style write | Pydantic validation (behaviour) | caught |
| research-engine report signal | source (code presence; `db` is stubbed in that suite) | caught |
| decision-engine `/decide` | 422 before any fetch (behaviour) | caught |
| Contract and fills (strict open, exclusive expiry, cancellation ordering, observed clock, stop precedence, trade-through, bracket rejection, unresolved) | frozen-bar tests | caught (×8) |
| PostgreSQL triggers (append, update denial, delete guard, seal), partial indexes, predecessor FK, archive retry, input source identity, read mode and identity rules | storage, input and read suites on PostgreSQL 16 | caught (×10) |

The first sabotage run found **four** unwitnessed guards:
- the outcome pending filter;
- the one-reconciled index;
- the archive retry's reported counts;
- the SWING fallback.

The first three now have real witnesses. The index is tested with the append trigger disabled
inside the test transaction, because the trigger and the pair CHECK refuse first. The fourth is
the unreachable line above.

One sabotage first "passed" for the wrong reason: shrinking the predecessor FK to one column
broke schema creation. It was redone by removing the constraint entirely, and the cross-chain
test then failed as it should.

The structural non-influence test puts extreme Intraday records beside real daily rows and
checks that three daily outputs are identical. It is **not** counted as a witness for any
filter, because daily readers never query those tables.

**Reading the code-presence rows:** on PostgreSQL a `horizon IN DAILY_HORIZONS` filter matches
every value the enum can hold, so it cannot change a result today. Those witnesses show the
defence-in-depth code is present; they are not independent data guards.

## Tests

- **Contract and fills:** `test_intraday_contract.py`, 33 tests on frozen bars.
- **Storage, input reader and API suites** (`intraday_store_suite.py`, `intraday_inputs_suite.py`,
  `intraday_api_suite.py`, run by `test_intraday_store.py` outside the stubbing conftest):
  - SQLite: 23, 10 and 5 tests. Trigger tests are skipped, since SQLite has no triggers.
  - PostgreSQL 16: 23 and 11 tests, **no skips allowed**.
- **Daily isolation witnesses:**

  | Service | Tests |
  |---|---|
  | signal-engine (`intraday_isolation_suite.py`, real ORM models) | 25 |
  | ml-prediction | 3, plus 4 in the production image |
  | market-data | 5 |
  | event-intelligence | 1 |
  | research-engine | 1 |
  | decision-engine | 3 |

- **`make test`** passed for every backend service:

  | Service | Passed | Skipped |
  |---|---|---|
  | market-data | 6008 | 30 |
  | ml-prediction | 555 | 1 |
  | signal-engine | 1008 | 9 |
  | research-engine | 731 | 8 |
  | event-intelligence | 635 | — |
  | decision-engine | 393 | — |

  Every other suite was also green.
- **`make test-integration-pg-schema`** (disposable PostgreSQL 16; the Intraday suites are
  REQUIRED there): 10 passed.
- **ML container suite in the production image:** 24 passed, including the 4 route witnesses.
- **`make test-all` frontend step:** vitest did not start in the release worktree. It has no
  `node_modules`, so `npx` fetched a vitest release that needs a newer Node (`styleText` missing
  from `node:util`). **This branch changes no frontend file**, so the frontend suite is
  unaffected, but that step was not run here.
- **Problems the full runs found, fixed:**
  - A decision-engine witness failed only inside the whole suite, because another test stubs
    `fastapi`. It now uses the `HTTPException` class the routes raise.
  - The enum witness counted rows across all schemas of the shared `schemaprobe` database. It is
    now scoped to `current_schema()`.

## Not done here

- No deployment. `shared/` changed, so the release is all 12 backends, sequentially, per design
  §12.
- No generator, no scheduling, no notification. The shadow generator is a separate approval and
  must add the seal and retention schedule.
- Tracked separately: **SHORT-CLOCK** (`_check_short_intraday_triggers` clock defects) and
  **GROWTH-TRADEPERF** (`/trade_performance?horizon=GROWTH` `KeyError`).
