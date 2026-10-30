# Intraday Stage C — implementation record

Date: **2026-10-29**. Branch: `intraday-contracts`. Status: **deployed to production as merge
`a3107b47` (reviewed code `cfd86973`), 2026-10-30 00:01–00:14 UTC; scheduled daily-path checks observed on Oct 30 as a
combined-release operational check; no generator**. See [Production deployment](#production-deployment-2026-10-30-utc) and the
[final review](../audits/2026-10-29-intraday-stage-c-final-review.md) that cleared
[Release candidate `cfd86973`](#release-candidate-cfd86973). Production generation is disabled (`INTRADAY_PRODUCTION_ENABLED = False`, a
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

## Implementation review remediation

The [implementation review](../audits/2026-10-29-intraday-stage-c-implementation-review.md) of
`e4cf9903` found five defects that the passing suites had not exercised. All are fixed, each with
a behavioural regression that runs through the real writer and, where it matters, direct SQL.

| Finding | Fix | Regression |
|---|---|---|
| **IC-01** A committed seal did not freeze membership | A SQL function `intraday_session_lock(run, identity, session)` takes a transaction-scoped advisory lock. The evaluation/setup insert trigger, `seal_session` and `archive_session` all take it, and the trigger re-checks the seal after acquiring it. Inserts must run in READ COMMITTED, so that re-check reads a fresh snapshot; otherwise they are refused. | PostgreSQL, threaded. (1) An in-flight insert: the sealer is observed **blocked** on the advisory lock (`pg_locks`), then includes the committed row, and nothing joins afterwards. (2) An insert behind an open seal is observed blocked, then refused. (3) An insert outside READ COMMITTED is refused. The archive counts include the in-flight row. |
| **IC-02** Observed records accepted replay clocks and backwards chronology | The clock basis is derived from the run and enforced by CHECKs on both transitions and inputs. A new `effective_at` column holds the research event time (a fill's open, a scheduled exit's open), separate from `decided_at` (decision/recording time), with `effective_at <= decided_at`. The append trigger requires the first transition at detection, neither time going backwards, and an `entered` fill strictly inside (actionable, expiry). The observed eligible ≤ observed ≤ decided order is a CHECK. The writer mirrors all of it, so it fails before SQL. | The writer refuses cross-mode inputs and events and a pre-detection observed entry. Direct SQL refuses a cross-mode event, a cross-mode input, effective after decided, a backwards or pre-detection entry, and a fill outside the window. A scheduled exit recorded later keeps its 16:10 fill time with a 16:18 decision time. |
| **IC-03** Stored hashes could not be recomputed | Every input persists a versioned `evidence` payload: raw hashed fields, transformed fields with the basis **reason**, provider identity and basis, and for derived bars the **ordered constituents' full evidence**. The reader computes every hash *from* that payload, and the writer refuses an input whose stated hashes do not recompute from its evidence, before any insert. | On SQLite and PostgreSQL, raw and adjusted: store a real snapshot (one unknown source, derived 15m), drop the in-memory records, revise every price, delete all provenance and some price rows, then recompute all four hashes from the stored evidence alone. Equal. Raw prices survive the adjusted basis. Tampered evidence is refused with nothing written. |
| **IC-04** A missing holding bar could still yield a confirmed return | The first unobserved holding interval makes the path `path_indeterminate`: a new session-terminal, **non-final** open-exposure state. It carries no exit, R or return, only a labelled mark and `first_unobserved_at`. It is listed by `/unresolved` with `unresolved_exit_at_close`, reconcilable, and never deleted. A missing liquidation bar is also indeterminate: the working order may have filled unseen. | Remove one holding bar from an otherwise winning and an otherwise losing path: neither stays confirmed, and the complete-data controls keep their results. |
| **IC-05** MAE/MFE included unordered exit-bar extremes | Excursions cover the fill, fully held bars and the exit price only. The exit bar's low/high go in `exit_bar_bounds` (order relative to the exit unknown). A scheduled or gap exit at an open excludes that bar's range. | The reviewer's case (entry 100, stop 99, bar 200/98) now gives MAE −1%, MFE 0%, bounds {98, 200}. Widening only post-exit-possible extremes of a target-only, a double-touch and a gap-stop bar leaves the exit and excursions unchanged. |

**Follow-ups from the review, also done:**
- **Concurrent identical retry:** idempotency is re-checked under the setup lock. Two racing
  appends with one key give one created row, and both return seq 3 (PostgreSQL, threaded).
- **`/identities`:** includes identities known only from archived counts.
- **Router order:** the Intraday router is registered before `data_router`'s `/stocks/{symbol}`
  routes, and is admin-gated (code-presence witness). Live gateway routing and auth are a deploy
  check, not claimed here.
- **Retention:** `archive_session`, `purge_inputs` and `delete_setup` take no age argument. The
  **caller** owns the 35- and 400-day policy, and the scheduled entry point that applies it ships
  with, and is tested in, the shadow-generator release, before any producer writes.
  `delete_setup` refuses any setup whose latest state is not final, which covers open exposure.
- **Exact tie:** a cancellation taking effect exactly at the entry open is `chronology_ambiguous`
  rather than "entered".
- **Count:** the earlier record listed 23 storage tests; that commit collected 22. Current counts
  are below.

**The reviewer's own witnesses against the fixed code:**
- `fill_witness.py` now reports `path_indeterminate` (no R), MAE −1%/MFE 0%, and an ambiguous tie.
- `review_witness.py` now **blocks** at its seal step: in its single thread an uncommitted insert
  holds the session lock, so the sealer waits, which is the fixed behaviour. It was stopped by
  timeout. Its later clock and backdating steps are covered by the regressions above.

## Candidate review remediation (ICR-01, ICR-02)

The [candidate review](../audits/2026-10-29-intraday-stage-c-candidate-review.md) of `c49a6e17` found two
residual defects, each with a PostgreSQL witness that committed through the real writer. Both are
fixed in the writer **and**, for ICR-01, at the database boundary.

| Finding | Fix | Behavioural regression |
|---|---|---|
| **ICR-01** — an observed cancellation could be backdated through `effective_at` (observed 14:17, dated 14:08, beating a 14:10 entry) | Timing is classified by event type (`contract.required_effective_time`). Only research fills and scheduled orders (`FILL_STATES`: entered, bracket rejection, exited, unresolved, path-indeterminate) may carry an effective time earlier than their decision. A cancellation takes effect when decided (observed) or at eligible + the identity's replay latency (replay); without its observation (or, in replay, its eligibility) it is refused, so it must be recorded as `chronology_ambiguous`. A cancellation must also take effect **before** the candidate entry open (`candidate_entry_open`, the first 5m open after `actionable_at`). The tie is refused too, so it is recorded as ambiguous. The one exception is the entry bar's own confirmed absence (`data_unavailable`, eligible exactly at that bar's `readable_at`). `expired` takes effect at `entry_expires_at`; detection-time states at `detected_at`; `chronology_ambiguous` and `reconciled` at `decided_at`. A bracket rejection must lie in the entry window, like an entry. `session_end` is no longer a cancellation reason: nothing used it, and an unfilled setup at the end is `expired`. The trigger repeats every rule, including the replay latency read from the identity's config and the 5-minute grid arithmetic. | Helper and direct SQL both refuse the review's witness. A revision observed after the entry (and the exact tie) leaves the entry standing. A cancellation decided at 14:09 defeats the 14:10 entry. A missing observation is refused. A liquidation recorded at 16:05 keeps its 15:50 fill time. Expiry cannot be retimed. A replay cancellation lands at eligible + 60 s. Both databases are covered. |
| **ICR-02** — consumed values were not bound to their hashed evidence (stored close 199.0 beside evidence 100.2, all hashes recomputing) | One authoritative representation: `contract.consumed_values(evidence)` (OHLCV plus price basis, projected from `evidence.transformed`). The writer refuses before any insert (`check_input_consistency`) unless the record's values, timeframe, bar open and source kind match the evidence. Every native bar, and every constituent, must belong to the evaluation's stock and symbol, with raw and transformed agreeing. A derived bar must be 15m, built from native constituents, have exactly its ordered 5m constituents, and equal their aggregate and price basis. `bar_values` is written from the evidence projection. | Changing only the consumed close, price basis, timeframe, bar open or source kind is refused before any write. So are a derived bar missing a constituent, one that is not the aggregate, one not 15m, a nested derived constituent, mismatched raw/transformed opens and another stock's evidence. Untampered native, adjusted and derived inputs round-trip with `bar_values == consumed_values(evidence)` and recompute every hash from storage alone. The real snapshot reader's records (raw and adjusted basis, derived 15m) pass the same check. The incomplete canned fixture (`values={'o': 1}`) is replaced. |

**Not changed:** the architecture, timing defaults, shared enum and disabled generation. **Not
enforced:** a direct-SQL input insert can still disagree with its own evidence (the JSON payloads are
not compared in the database). The review leaves this as a separate enforcement decision. The
supported writer refuses, and readers have one authoritative representation, `evidence`.

The mutation set grows from 64 to **93 required cases**: 19 for ICR-01 (contract, writer, trigger)
and 10 for ICR-02. Each guard has its own witness, under the same bounded, expected-witness rule.

## Production deployment (2026-10-30 UTC)

**Deployed; scheduled daily-path checks observed Oct 30 (combined release, below).** Contract-only: no generator, scheduler,
alert delivery, training activation or model promotion. `INTRADAY_PRODUCTION_ENABLED` stays `False`
and the shared `signalhorizon` enum is unchanged. Evidence:
[`evidence/2026-10-29-intraday-stage-c-deployment/`](../audits/evidence/2026-10-29-intraday-stage-c-deployment/).

**Merge.** `a3107b47` merges `origin/intraday-contracts` (`2131678`, code `cfd86973`) into `prod` at
`8182b28b`. `prod` had gained five valuation commits since the branch base, two of them during the
15-minute hold before the release. The only conflict was `services/market-data/src/main.py`: each
side added one import and one router. Both are kept, in the order valuation, rzy, intraday, ahead of
`data_router`'s catch-all; the prefixes `/stocks/valuation-inputs` and `/stocks/intraday` are distinct.
Every other reviewed code file is byte-identical to the branch.

**Checks on the merged commit** (HEAD `a3107b47` before and after, 0 dirty files):
- `make test`: every backend service passed (market-data 6029 passed, 30 skipped; signal-engine 1008; research-engine 748; ml-prediction 555; the rest as listed in `release-checks/make_test.txt`).
- `make test-integration-pg-schema`: 10 passed. This includes the Intraday storage (57) and input (13) PostgreSQL suites.
- The ML production-image suite was not rerun. The merge adds nothing to ml-prediction beyond the reviewed branch.

**Release.**
- Pushed as a fast-forward of `origin/prod`.
- Before any recreate: each of the 12 running backend images was tagged `rollback-before-a3107b47` (verified equal to the running image), and logs and identities were saved under `/home/ec2-user/deploy-evidence/2026-10-29-intraday-stage-c-a3107b47/`. **Log coverage:** each container's life since its last start, capped at 24 h. That is about 17 h for ten services, but only about 20 min for market-data and research-engine, which the valuation release had recreated at 23:34–23:41 UTC.
- Deployed one service at a time with `scripts/deploy.sh` under the host lock: market-data first (00:01:57 UTC, it creates the schema), then technical-analysis, ranking-engine, strategy-engine, portfolio-optimizer, news-intelligence, event-intelligence, research-engine, ml-prediction, decision-engine, signal-engine and api-gateway (done 00:14:07). Every run ended `running/healthy`.
- All 12 rollback tags were re-verified after the post-deploy prune. No older tags were removed.

**Production checks.**
- **Health:** 15 of 15 containers healthy. Every backend has a new image and a start time inside the window; the frontend was untouched.
- **Source parity:** `scripts/check_deploy_drift.sh` at `a3107b47` finds 12 of 12 backends matching, 0 drifted.
- **Remote checkout:** `a3107b47`.
- **Schema** (`verify_intraday_release.py`, 18 of 18 checks pass): all 10 tables, 37 named constraints, 25 triggers (all enabled) and 7 functions present. The deployed `intraday_validate_append` carries the ICR-01 rules. The only run row is the `observed` sentinel; the 9 business tables are empty. `signalhorizon` = SHORT, SWING, LONG, GROWTH.
- **Gateway access** to `/stocks/intraday/identities`: 401 unauthenticated, 403 for a non-admin, 200 for an admin with an empty page reporting `production_generation: disabled`. HK is refused with 422 `unsupported_market`.
- **Daily isolation:** INTRADAY is refused with 422 `unsupported_horizon` by `/signals`, `/signals/suppressed`, `/signals/{symbol}` and `/decide/{symbol}`. `/signals?style=SWING` still returns 200 (200 rows).
- **Daily path:** SHORT, SWING, LONG and GROWTH all compute for AAPL and MSFT through the gateway with `persist=false` (`verify_daily_compute.py`).
- **Scheduler:** 290 executions in market-data since its restart and no errors in six service logs.
- **Test tokens:** the two minted tokens were logged out, and reusing either returns 401.
- **Host:** 28 GB free, I/O wait 0–6%.

**Pending, recorded as not yet observed:**

| Check | Baseline | Due | Status |
|---|---|---|---|
| HK scheduled refresh writes advance, all four horizons | HK `signals.ts` last 2026-10-29 08:33:26 UTC | HK open burst 01:25 UTC Oct 30 | **Pass, closed.** Read 01:50 UTC: all four horizons at 01:46:48 UTC, 42 rows each; 0 tracebacks; every container healthy. Logged Yahoo and UW errors are handled fetch noise ([hk-refresh-check.txt](../audits/evidence/2026-10-29-intraday-stage-c-deployment/hk-refresh-check.txt)) |
| US scheduled refresh writes advance, all four horizons | US `signals.ts` last 2026-10-29 22:24:16 UTC | US open burst 13:25 UTC Oct 30 | **Pass, combined release.** All four horizons at 13:33 UTC, 127 rows each; 0 tracebacks ([US check](../2026-10-30/durable-measurement-and-checks.md#5-fridays-us-check-a-combined-release-check)) |
| Outcome evaluation completes after the release | `max(signal_outcomes.ts_evaluated)` 2026-10-29 20:32:12 UTC, 26,881 rows | HK post-close 08:30 UTC, US post-close 20:30 UTC Oct 30 | **Pass, combined release.** HK at 08:30 UTC (26,986 rows). US finished 20:34:51 UTC: 234 evaluated, 376 horizon outcomes resolved, 27,220 rows. The scheduler's HTTP call timed out on all three attempts while the first request ran, so the job is logged as failed although it completed ([evidence](../2026-10-30/evidence/us-check/02-post-close.txt)) |

**Combined release.** By Friday's US session production also ran the UW cooldown commits
(`1d85a240`, `ad55a635`) and others deployed after Stage C. At 13:40–13:45 UTC `49802ce5` was deployed
to market-data, research-engine and the frontend. These checks confirm the daily path ran on that
combined release. They cannot isolate Stage C's own effect.

These were on the Fri Oct 30 13:45 PT calendar checkpoint and the
[Deferred Checks Calendar](https://claude.ai/code/artifact/f2462c2d-bde6-4f30-99d9-121ede27f411).
Still separate future gates: sealing and retention before a shadow producer's first write, execution
and profitability qualification, and any generator.

## Release candidate `cfd86973`

`75af1708` holds the ICR fixes. `cfd86973` adds only a runner parser fix (below). Every result here
was produced against **`cfd86973`**: its hash was recorded before and after the checks, unchanged,
with a clean tree. Evidence and a SHA-256 manifest of 194 files are in
[`docs/audits/evidence/2026-10-29-intraday-stage-c-icr-candidate/`](../audits/evidence/2026-10-29-intraday-stage-c-icr-candidate/MANIFEST.json).

- **Mutation run:** 93 required cases, **93 CAUGHT**: 0 missed, 0 wrong reason, 0 inconclusive.
  The longest case took 11.8 s, against the 240 s bound. The caught rule is unchanged, and no
  bytecode was written during the run. The 64 earlier cases are included, with the two re-anchored
  ones described in the remediation section.
- **`make test`:** all services passed.

  | Service | Passed | Skipped |
  |---|---|---|
  | market-data | 6020 | 30 |
  | signal-engine | 1008 | 9 |
  | research-engine | 731 | 8 |
  | event-intelligence | 635 | — |
  | ml-prediction | 555 | 1 |
  | decision-engine | 393 | — |
  | ranking-engine | 201 | — |
  | news-intelligence | 200 | — |
  | portfolio-optimizer | 83 | — |
  | technical-analysis | 67 | — |
  | strategy-engine | 62 | — |
  | api-gateway | 58 | — |
  | scripts | 44 | — |

- **`make test-integration-pg-schema`:** 10 passed, REQUIRED PostgreSQL 16, no skips. This target
  runs the 57-test storage and 13-test input suites, subprocessed.
- **ML container suite in the production image:** 24 passed.
- **Frontend:** not run. The branch changes no frontend file.

**Runner defect found in this round.** The first full run, at `75af1708`, reported 85 CAUGHT and
8 WRONG_REASON, all in the ICR-02 group. The runner's failed-test parser cut each node id at its
first space. Parametrized ids such as `[consumed close]` were therefore truncated, and the expected
witness never matched. Each saved output does show the intended case failing. Even so, that run is
**not counted**: it is kept under `superseded-75af1708/`. The parser was fixed in `cfd86973`, and
the full set was rerun on that commit.

## Release candidate `c49a6e17` (superseded by the ICR remediation)

Every result in this section was produced against **commit `c49a6e17`**. The hash was recorded
before and after the checks, and was identical. Evidence and a SHA-256 manifest are in
[`docs/audits/evidence/2026-10-29-intraday-stage-c-mutation-run/`](../audits/evidence/2026-10-29-intraday-stage-c-mutation-run/MANIFEST.json).
The commit that adds this record and the evidence changes documentation only.

**Mutation run** (`scripts/intraday_mutation_run.py`, refuses a dirty checkout):
- **Result:** 64 required cases, **64 CAUGHT**: 0 missed, 0 wrong reason, 0 inconclusive.
- **What counts as caught:** a case is CAUGHT only when its own witness fails within 240 s with
  test failures (pytest exit 1, no collection or usage error) **and** the expected witness test is
  among the failures. A timeout is INCONCLUSIVE, never a catch.
- **Recorded per case** (`results.json`): the file hash, the mutated file hash, the diff hash, the
  witness command, the failing tests, the exit code, the duration and the output hash. Outputs are
  in `outputs/`.
- **Seal witnesses:** the in-flight case releases the writer, requires both transactions to
  finish, and asserts that the final membership equals the seal's view and that a later insert is
  refused. The insert-behind-an-open-seal case asserts the seal holds and nothing joined.
- **Two lines outside the required set, with reasons** (`not_required`):
  - the SWING fallback in `signal_for` is unreachable once the style is validated;
  - the end of the fill loop is unreachable in fill model v1, and it raises rather than inventing
    an outcome.

**Release checks against `c49a6e17`:**
- **`make test`:** all backend services passed.

  | Service | Passed | Skipped |
  |---|---|---|
  | market-data | 6016 | 30 |
  | ml-prediction | 555 | 1 |
  | signal-engine | 1008 | 9 |
  | research-engine | 731 | 8 |
  | event-intelligence | 635 | — |
  | decision-engine | 393 | — |

  The other suites were also green.
- **`make test-integration-pg-schema`:** 10 passed. These are the Intraday storage and input
  suites on a disposable PostgreSQL 16 database, REQUIRED, with no skips allowed.
- **ML container suite in the production image:** 24 passed, including the four route witnesses.
- **Frontend:** this branch changes no frontend file, and the frontend suite was not run here
  (unchanged from the earlier record).

**Earlier mutation tallies are void.** While running the earlier sabotage passes I found that a
same-length mutation (`evidence=ev` → `evidence={}`) had been restored within the same second.
CPython trusts a `.pyc` whose recorded source mtime (seconds) and size still match, so later runs
executed the **mutated** bytecode, and two tests failed against correct source. Every test result
produced after those passes, including the 36/40, 40/41 and 56/61 tallies, may have executed stale
code and is **not** relied on. The runner now purges every `__pycache__`, runs each witness with
`PYTHONDONTWRITEBYTECODE=1`, refuses to start if any compiled file survives, and records that none
was written during the run (`bytecode_written_during_run: []`).

**Other process corrections:**
- **Fix commit before its check:** `ae168ec4` was committed before the result of its test run was
  read, and that run had failed. The cause was the stale bytecode above, not the code; the same
  code passes with clean caches. It is not the candidate.
- **Malformed mutation:** the first run at `d5681088` reported 63/64. The 64th mutation
  (`r_archived_identity`) deleted the only statement in a loop body, an import error that the strict
  rule correctly refused to count. It was corrected to a valid no-op in `c49a6e17`, the only change
  between the two commits, and the full set was rerun.

## Not done here

- No deployment. `shared/` changed, so the release is all 12 backends, sequentially, per design
  §12.
- No generator, no scheduling, no notification. The shadow generator is a separate approval and
  must add the seal and retention schedule.
- Tracked separately: **SHORT-CLOCK** (`_check_short_intraday_triggers` clock defects) and
  **GROWTH-TRADEPERF** (`/trade_performance?horizon=GROWTH` `KeyError`).
