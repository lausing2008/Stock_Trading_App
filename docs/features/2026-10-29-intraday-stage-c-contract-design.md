# Intraday Stage C — contract and storage design (revision 2)

Date: **2026-10-29**. Branch: `intraday-contracts`. Status: **design; nothing built**.
- **Inputs:**
  - the [Stage C inventory](2026-10-29-intraday-stage-c-inventory.md);
  - the [Intraday plan](2026-10-26-intraday-and-signal-performance-plan.md);
  - the [design review](../audits/2026-10-29-intraday-stage-c-design-review.md) of revision 1
    (`c6254311`), findings CDR-01..CDR-06. This revision resolves each one; §13 maps them.
- **Decided by the reviewer:**
  - separate Intraday storage;
  - US-only v1;
  - INTRADAY kept out of the shared `signalhorizon` enum;
  - production generation disabled;
  - the timing parameters accepted as versioned research defaults, with no performance claim.

**Stage C establishes contracts, storage and daily isolation only.** It ships no generator, and
its acceptance claims no execution readiness and no return.

## 1. INTRADAY is not added to `signalhorizon`

Decided (review). The reasons, kept for reference:
- **No table needs it.** Only `signals`, `signal_outcomes` and `signal_outcome_horizons` use the
  enum, and none of them stores Intraday records.
- **Its absence makes PostgreSQL reject INTRADAY** at every daily write.
- **`ADD VALUE` cannot be undone**, and images built before the value exists fail on any row
  that carries it.

The inventory's earlier recommendation of an enum migration is marked superseded.

## 2. Horizon registry — `shared/common/horizons.py` (new)

```
DAILY_HORIZONS = ("SHORT", "SWING", "LONG", "GROWTH")
INTRADAY = HorizonSpec(id="INTRADAY", label="Intraday · same session", kind="intraday",
    storage="intraday_setups", markets=("US",), trigger_timeframe="5m", context_timeframe="15m",
    forecast_windows_min=(30, 60, 120), primary_forecast_min=60, max_hold_min=120)
INTRADAY_PRODUCTION_ENABLED = False      # a constant: enabling requires a code change and review
class UnsupportedHorizon(ValueError)     # .horizon, .hint ("use /intraday/..." for INTRADAY)
def parse_daily_horizon(value) -> str    # anything outside DAILY_HORIZONS raises
class UnsupportedMarket(ValueError)
def require_intraday_market(market)      # anything but "US" raises
```

INTRADAY is distinct from the daily SHORT horizon, including SHORT's TIER83 intraday refresh.
Three daily maps are pinned by test to exactly `DAILY_HORIZONS`: `_HORIZON_BY_STYLE`,
`_STYLE_PROFILES`, and `_OUTCOME_HOLD_DAYS` with `_SELL_OUTCOME_HOLD_DAYS`.

## 3. Venue and session

- **US only.** `require_intraday_market` refuses everything else. The read API returns
  `unsupported_market` rather than an empty page.
- **Calendar.** `common.market_calendar.session_bounds('US', d)` is the only source of session
  times, early closes included. Past `calendar_coverage()`, every evaluation records
  `calendar_coverage_expired`.
- **Session identity:** `session_date` is the New York date of the trigger bar's open. No window
  ever reaches another session's bars.
- **Derived times.** The offsets are versioned research defaults; an early close shifts all of
  them.

| Name | Definition | 16:00 close | 13:00 close |
|---|---|---|---|
| `last_entry_at` | `session_close − 30 min` | 15:30 | 12:30 |
| `liquidation_at` | `session_close − 10 min` | 15:50 | 12:50 |

## 4. Input identity and availability (CDR-01)

**No historical first availability is claimed in Stage C.**
- **Why:** `bar_provenance` cannot timestamp the exact version of a bar:
  - volume-only changes, provider-only switches and changes below `NOISE_REL` all leave
    `last_changed_at` unchanged;
  - a provenance row created for an already-stored price row records when tracing began, not when
    the bar was first observed.
- **Dropped:** the revision-1 `last_volume_changed_at` column. It would cover one field out of
  several, so it is removed and no ingest-path change is part of Stage C.

**What each input records instead:**

| Field | Meaning |
|---|---|
| `bar_open`, `bar_close` | interval; `bar_close = min(bar_open + step, session_close)` |
| `readable_at` | `bar_close + SETTLE` (2 min). An **eligibility bound** only: the contract never reads a bar earlier. It is not evidence the provider had published it. |
| `input_read_at` | when this evaluation read **exactly these values**: an honest observation of the consumed snapshot |
| `first_available_at` | `NULL`, basis `unknown_exact_version` |
| `provider_published_at` | `NULL`, basis `unavailable` |

A later, separately reviewed **accepted-write version ledger** at ingest (part of F09, Stage B)
can fill `first_available_at`. It will do so only for versions whose every hashed field, and the
price basis, are covered by an accepted-write record. Until then the field stays unknown, and a
failed or partial trace keeps it unknown.

**Four hashes, each with a canonical encoding.** The encoding is a UTF-8 JSON array, not an
object, with no whitespace:
- timestamps as UTC ISO-8601 with microseconds and a `Z` suffix;
- floats via `repr()` (shortest round-trip), with `NaN`/`inf` refused;
- `None` as `null`;
- the symbol upper-cased.

| Hash | Covers |
|---|---|
| `raw_hash` | stock_id, symbol, timeframe, bar_open, the stored `open, high, low, close, adj_close, volume, session` exactly as read |
| `source_hash` | `raw_hash` + the bar's recorded provider (from `bar_provenance` read in the same snapshot; `"unknown"` if absent or the trace failed) |
| `input_hash` | the transformed values the strategy consumed + `price_basis` + `price_basis_reason` (`resolve_ohlc` output) |
| `derived_hash` | for a derived 15m bar: its `input_hash` fields + the ordered list of constituent `source_hash`es |

**Consistent snapshot.** An evaluation reads `prices` and `bar_provenance` in one `REPEATABLE
READ` read-only transaction, so both come from the same snapshot. The recorded source is then
consistent with the values. A provenance row whose values differ from the price row in that
snapshot (the write race the trace documents) records source `unknown`; it never borrows a
timestamp.

## 5. Evaluations, setups and their states (CDR-03)

**Strategy and config identity:**
- `strategy_id`: `AI_INTRADAY`. RZY keeps `LITTLE_RZY_INSPIRED_V1` and is never relabelled.
- `strategy_version` and `contract_version`.
- `config_hash`: the canonical hash of every parameter, including the session offsets, entry
  window, max hold, fill model, slippage and costs.

**Evaluation (always written).** One row per `(run_key, config_hash, stock_id, trigger_bar_open)`,
which is unique, and it is the denominator. Its `result` is one of:
- `setup` (it references exactly one setup);
- `no_setup`, `wait`, `data_unavailable`;
- `unsupported_market`, `calendar_coverage_expired`.

Each trigger bar is accounted for exactly once per run and config.

**Setup.** Written for every detected pattern, whether or not it is actionable:
- **Identity:** `occurrence_key`, not null and unique, `sha256(canonical(run_key, strategy_id,
  strategy_version, config_hash, symbol, trigger_bar_open, direction))`.
- **Frozen proposal:** levels (`entry_reference`, `stop`, `target`, `risk_per_share`), reasons,
  input hashes and times.
- **Nullable:** `actionable_at` is null when the setup never becomes actionable.

**Decision-time checks, in this priority order.** All use only evidence readable at `detected_at`:

1. **Expiry first.** `entry_expires_at = min(trigger_bar_close + 15 min, last_entry_at)`;
   expiry is **exclusive**. If `detected_at ≥ entry_expires_at`, the terminal state is
   `expired_before_detection` and `actionable_at` is NULL.
2. **Fill opportunity.** There must be a grid open `t` in this session with
   `detected_at < t < entry_expires_at`. If there is none, the terminal state is
   `no_fill_opportunity`.
3. **Pre-actionable invalidation.**
   - If the stop is already breached, or an input is stale or gapped at detection, the terminal
     state is `cancelled_before_actionable`, with reason `invalidated_price` or
     `data_unavailable`.
   - Bearish setups become `research_only_unsupported_direction`: borrow is unknown, and no fill
     is simulated.
4. **Otherwise** the setup becomes `actionable`, with `actionable_at = detected_at`.

**Events after actionable, before entry.** Each event is timed at the `readable_at` of the bar
that shows it; the earliest event wins, and a tie goes to cancellation:
- `cancelled(invalidated_price | input_revised | data_unavailable)`;
- `expired` at `entry_expires_at`;
- `entered` at a fill (§6).

A bar that is readable only after a fill can never cancel that fill. Future bars never undo a
past event.

**Invariants by state:**

| State | Invariant |
|---|---|
| every setup | `readable_at ≤ input_read_at ≤ detected_at` for every input |
| `actionable` | `detected_at = actionable_at < entry_expires_at ≤ last_entry_at` |
| `entered` | `actionable_at < entry_fill_at < entry_expires_at` |

**Email expiry is not eligibility.** A later notification carries its own `deliver_by`. A test
asserts that no eligibility path reads it.

## 6. Order model and research fills (CDR-02)

These are research fills only: every fill carries `fill_basis` and `execution_grade = false`.
- **Executable open.** For an order that is active at time `t`, the open of a bar is executable
  only if `bar_open > t`. The comparison is strict.
- **Same instant.** When an order's activation and a bar's open coincide, the bar's open is not
  used for that order.

| Order | Activation | How it fills |
|---|---|---|
| **Entry** (market) | `actionable_at` | The first executable open with `bar_open < entry_expires_at`, at that open ± adverse slippage. If none exists before expiry, the setup is `expired`. |
| **Bracket stop** (resting) | the entry fill instant | Adjudicated on completed bars from the **entry bar onward**. The entry bar's range after its open is real exposure. A bar whose low ≤ stop (long) exits at the stop, or at the bar's open if that open is already through the stop, both − slippage. The intrabar time is recorded as `unknown`. |
| **Bracket target** (resting limit) | the entry fill instant | Fills only if the price trades **through** the target (long: high > target), at exactly the target, with no positive slippage. |
| **Time exit** (max hold, scheduled) | the entry fill instant | A scheduled order ready since entry. It fills at the first open `≥ entry_fill_at + 120 min`: an open equal to the scheduled time is allowed because the order was ready earlier. Clipped to the liquidation time. |
| **Liquidation** (scheduled) | the entry fill instant | Fills at the first open `≥ liquidation_at` and `< session_close`. The 15:50 open is valid because the order was scheduled and ready earlier. |
| **Reactive exit** (any exit decided from an observation) | its actual decision time | Uses the strict executable-open rule from its decision time. A stop level decided after a completed bar applies only to bars opening after that decision. It never fills retrospectively. |

- **Precedence (the tie rule, stated once).** Within one bar:
  - a scheduled exit filled at the bar's **open** precedes anything inside that bar;
  - inside the bar, if both the stop and the target are touched, the stop wins;
  - a stop gap at the open fills at the open.
- **Bracket validity at fill.**
  - If the entry fill price is already at or beyond the stop, or at or beyond the target, the
    entry is **rejected**. The terminal state is `rejected_invalid_bracket_at_fill`, the levels
    are unchanged, and no position is recorded.
  - Levels are never moved.
- **Costs:** the versioned per-share fee plus a bps cost on both sides, and adverse slippage on
  market and stop fills. All of them are in `config_hash`.
- **No observation before the close.** If the liquidation order has no executable open (missing
  bar, gap or stale data), the state is `unresolved_exit_at_close` (§8). No fill is recorded and
  no closing price is assumed.

## 7. Outcomes

Two kinds, never merged.

**Forecast outcome**, descriptive, with `entry_basis = trigger_close_descriptive`:
- the return from the trigger bar's close to the close of the bar ending at +30, +60 and +120
  minutes;
- statuses:
  - `resolved`;
  - `truncated_session_end`: the window passes the close, so it is scored at the last completed
    bar and its actual minutes are stored;
  - `missing_terminal_bar`: the bar at the window end is absent.
- It is never combined with managed-trade returns.

**Managed-trade outcome:**
- fills, exit reason, R, MAE, MFE and costs;
- `realized` only for recorded exits;
- `unresolved_exit_at_close` carries a `mark` and no realized P&L.

**Revision identity is independent of code version.** `outcome_key = sha256(setup occurrence_key,
kind, window, evaluator_version, evidence_hash)`, where `evidence_hash` hashes the `source_hash`es
of every bar the evaluation used. Changed input evidence under the same evaluator code is a new
revision.

**Chain.** Each row names `supersedes_id`. A unique partial index allows one root per
`(setup, kind, window)` where `supersedes_id IS NULL`, and `UNIQUE (supersedes_id)` gives each row
at most one successor. The current outcome is the chain's tip. Writing a revision locks the
setup (§9).

## 8. Unresolved exposure (CDR-06)

`unresolved_exit_at_close` means a position was entered and **not** exited in the session. It is
distinct from `expired`, where an entry was never filled.
- **Accounting:**
  - Summaries report `realized` (recorded exits only), `unresolved_count`, `unresolved_marked_exposure`
    (labelled as a mark) and `never_entered` as separate lines.
  - An unresolved setup stays in every denominator as its own category; it is never dropped or
    counted as flat.
- **Reconciliation** is explicit research accounting: an appended `reconciled` transition, with
  the evidence used (for example the next session's first executable open, labelled
  `reconciliation_mark`). It is never folded into the same-session score. No broker exists in
  this release.
- **Retention** never deletes an unresolved or unreconciled setup (§9).

## 9. Storage and constraints (CDR-04)

All tables are new, market-data-owned and registered before startup `create_all`. Each is
insert-only. Vocabularies are `VARCHAR` with `CHECK`.

```
intraday_runs            run_key VARCHAR(64) PK, record_mode CHECK IN ('observed','replay'),
                         input_snapshot_hash, code_version, config_hash, as_of, created_at
                         UNIQUE (run_key, record_mode)
                         CHECK (record_mode='observed') = (run_key='observed')      -- one observed run
                         a sentinel row ('observed','observed') is inserted by the schema step

intraday_evaluations     id PK, run_key, record_mode, config_hash, stock_id, trigger_bar_open,
                         result CHECK IN (...), detected_at, data_cutoff, setup_occurrence_key NULL
                         FK (run_key, record_mode) -> intraday_runs (run_key, record_mode)
                         UNIQUE (run_key, config_hash, stock_id, trigger_bar_open)
                         CHECK (result='setup') = (setup_occurrence_key IS NOT NULL)

intraday_setups          id PK, occurrence_key CHAR(64) NOT NULL UNIQUE, run_key, record_mode,
                         strategy_id, strategy_version, contract_version, config_hash, stock_id,
                         symbol, session_date, trigger_bar_open, direction, levels..., reasons,
                         detected_at, actionable_at NULL, entry_expires_at, last_entry_at,
                         liquidation_at, session_close
                         FK (run_key, record_mode) -> intraday_runs
                         UNIQUE (id, run_key)               -- target for child lineage FKs

intraday_setup_inputs    setup_id, run_key, role ('trigger'|'context'|'lookback'), timeframe,
                         bar_open, source ('native'|'derived_from_5m'), values, raw/source/input/
                         derived hashes, the §4 times and bases
                         FK (setup_id, run_key) -> intraday_setups (id, run_key)
                         UNIQUE (setup_id, role, timeframe, bar_open)

intraday_setup_transitions setup_id, run_key, seq, from_state, to_state, at, reason, evidence,
                         idempotency_key UNIQUE
                         FK (setup_id, run_key) -> intraday_setups (id, run_key)
                         UNIQUE (setup_id, seq)
                         CHECK (from_state, to_state) IN (the legal pairs below)
                         UNIQUE (setup_id) WHERE to_state IN (terminal states)    -- one terminal

intraday_outcomes        id PK, outcome_key UNIQUE, setup_id, run_key, kind ('forecast'|'trade'),
                         window, evaluator_version, evidence_hash, supersedes_id NULL, status, ...
                         FK (setup_id, run_key) -> intraday_setups (id, run_key)
                         UNIQUE (supersedes_id); UNIQUE (setup_id, kind, window) WHERE supersedes_id IS NULL

intraday_evaluation_daily_counts  run_key, config_hash, session_date, result, count   -- archived
                         before evaluation retention; PK on all but count
```

- **Legal transitions:**
  - A setup is inserted together with its first transition, `seq = 1`, `(NULL → detected)`, in
    the same transaction. A setup with no transitions cannot exist.
  - `detected` goes to `actionable`, `expired_before_detection`, `no_fill_opportunity`,
    `cancelled_before_actionable` or `research_only_unsupported_direction`.
  - `actionable` goes to `entered`, `expired`, `cancelled` or `rejected_invalid_bracket_at_fill`.
  - `entered` goes to `exited` or `unresolved_exit_at_close`.
  - `unresolved_exit_at_close` goes to `reconciled`.
- **Run key and lineage:** observed records use `run_key = 'observed'`, which is not null, so
  identity uniqueness never relies on a NULL. Every child carries `run_key` in a composite foreign
  key to its parent, so a replay child cannot attach to an observed setup or the reverse.
- **Concurrency:** a transition or outcome writer runs in one transaction that:
  1. takes `SELECT … FOR UPDATE` on the setup row (a lock, not a modification);
  2. reads the latest transition;
  3. validates the pair;
  4. inserts `seq = latest + 1`.
  `UNIQUE (setup_id, seq)` and the one-terminal index reject a concurrent contradictory append,
  even if a writer skipped the lock. Evaluations and setups are inserted with `ON CONFLICT DO
  NOTHING` on their unique keys and then read back, so duplicate detection is idempotent.
- **Update denial:** a `BEFORE UPDATE` trigger on every table raises. It is created idempotently
  in `_apply_isolated_ddl` (`CREATE OR REPLACE FUNCTION` and `DROP TRIGGER IF EXISTS`/`CREATE
  TRIGGER`).
- **Delete guard:**
  - A `BEFORE DELETE` trigger raises unless the transaction has run
    `SET LOCAL stockai.intraday_retention = 'on'`.
  - All services share one database credential (`docker/docker-compose.yml`, `env_file:
    ../.env`), so this is a guard against accidental deletion, not a permission boundary. A
    dedicated retention role would be new infrastructure and is out of scope; the doc does not
    claim one.
- **Retention:** run by the named job `intraday_retention`, in this order:
  1. Archive `intraday_evaluation_daily_counts` for sessions older than 35 days, then delete those
     evaluations.
  2. Delete inputs older than 35 days for setups that are terminal and resolved.
  3. Keep setups, transitions and outcomes for 400 days, matching `signal_outcomes`.
  4. Never delete a setup in `unresolved_exit_at_close` (unreconciled), a setup with an
     unresolved outcome, or a row another row references.

## 10. Daily isolation and the guard coverage matrix (CDR-05)

**Storage boundary.** Daily readers cannot read the Intraday tables, which they never query.
That is structural isolation. A non-influence test shows it: extreme Intraday rows produce
semantically identical daily outputs on frozen inputs, compared field by field excluding run
timestamps. This test shows only that the storage boundary holds. **It is not counted as a
witness for any filter.**

| Guard | Effective witness | Detects removal? |
|---|---|---|
| PostgreSQL enum lacks INTRADAY | raw PostgreSQL 16 insert of `'INTRADAY'` into `signals`/`signal_outcomes` fails | yes (fails if the value is ever added) |
| `parse_daily_horizon` at the two signal upserts | API test: `style=INTRADAY` gets 422 before any SQL; a spy asserts the upsert is not called | yes |
| outcome writer guard (`outcomes.py:378, 683`) | SQLite: a contaminated `signals` row with horizon `'INTRADAY'` (SQLAlchemy 2.0 `Enum` adds no CHECK on SQLite) is skipped and counted, and the batch completes | yes |
| unknown-style fallbacks (`routes.py:60, 633, 1131, 1311`, decision-engine, watchlist, curation) | request tests expect 422/skip, not all-horizons or SWING | yes |
| daily maps pinned to `DAILY_HORIZONS` | key-set equality test | yes |
| **reader filters** (seven high-risk + medium) | on PostgreSQL the filter `horizon IN DAILY_HORIZONS` matches every possible enum value, so **no data witness exists there**. Witness at a documented seam instead: SQLite readers get a contaminated `'INTRADAY'` row and must exclude it; PostgreSQL-only SQL (`DISTINCT ON`, `LATERAL`) gets a construction test asserting the predicate and bound values in the compiled statement | yes, but **as a code-presence check for PG-only SQL**. Labelled as such; it is defence in depth behind the enum, not an independent data guard |

Each row names its test, and the sabotage pass removes each guard and checks that its own witness
fails. A row whose witness is not data-based is labelled as such.

## 11. Read API (CDR-06)

These are admin-only research endpoints in market-data (`require_model_admin`). Records are
platform-wide over the active US universe and carry no user data. The gateway gets a new
`/intraday` prefix in `proxy.py` `_ROUTES`, covered by the scan-every-service test.

```
GET /intraday/setups?market=US&session=YYYY-MM-DD&mode=observed|replay[&run_key=][&symbol=][&cursor=][&limit<=500]
GET /intraday/evaluations?...   (same parameters)
```

- **Mode and run.**
  - `mode=replay` **requires** `run_key`, and `mode=observed` **rejects** it.
  - Two runs can never be pooled, and there is no default mode.
- **Ordering and pagination.**
  - Results are ordered by `(trigger_bar_open, symbol, occurrence_key)`.
  - A keyset cursor carries those three values, so page boundaries stay stable while new rows
    arrive.
- **Every response carries:**
  - `run_key`, `record_mode`, `config_hash` and `contract_version`;
  - `production_generation: "disabled"`;
  - `coverage`: evaluations by result for the session, the retention horizon, and whether the
    session's evaluations are archived.
  - Unresolved outcomes are listed and never filtered out.
- **Errors:** missing authentication is 401 at the gateway, a non-US market returns
  `unsupported_market`, and there is no write endpoint.

## 12. Rollout

One release, with no generator and no enum change.

1. **Validate off-host on a pinned commit:**
   - `make test-all`;
   - the PostgreSQL 16 suite on a throwaway database (constraints, triggers, concurrency,
     isolation matrix);
   - the sabotage pass.
2. **Host pre-checks:** drift 12/12 clean, deploy lock free, and rollback images tagged for all
   12 backends. `shared/` changes (registry and guards), and shared is one unit.
3. **Deploy the 12 backends sequentially** with `scripts/deploy.sh`:
   - market-data's startup `create_all` creates the seven tables under the cross-service lock;
   - `_apply_isolated_ddl` adds the triggers and the observed-run sentinel row idempotently;
   - there is no `ALTER` on any existing table or type.
4. **Post-checks:**
   - drift 12/12 and 15/15 healthy;
   - the M24 capability check reports the tables, unique constraints, foreign keys and triggers;
   - a direct SQL `UPDATE` and an unguarded `DELETE` fail on production;
   - the tables are empty apart from the sentinel;
   - `GET /intraday/setups` reports generation disabled;
   - `style=INTRADAY` on a daily endpoint returns 422;
   - one daily signal refresh and one outcome evaluation complete normally.
5. **Rollback:**
   - redeploy the tagged images; the tables, triggers and sentinel remain;
   - the old code never reads or writes them;
   - no existing table or type was altered, so nothing needs reverting;
   - dropping the tables is a separately approved step, allowed only while they hold nothing but
     the sentinel.
6. **The shadow generator** is a later, separately approved release. It writes
   `record_mode='observed'` research records only.

## 13. Review findings resolved

| Finding | Resolution |
|---|---|
| CDR-01 exact-version availability | §4: no first-availability claim; `input_read_at` is the observation; four canonical hashes; consistent snapshot; `readable_at` labelled as an eligibility bound; the volume column dropped |
| CDR-02 order activation | §6: activation per order type; strict executable open; scheduled vs reactive exits; resting brackets adjudicated from the entry bar; one tie rule; invalid-bracket rejection; costs |
| CDR-03 late detections | §5: state-specific invariants; `expired_before_detection` and `no_fill_opportunity` recorded; nullable `actionable_at`; exclusive expiry; priority and tie rule; no retroactive cancellation |
| CDR-04 append-only contracts | §9: full keys, `run_key` lineage FKs, transition sequencing under a lock, legal pairs, one terminal, outcome revision chain, update/delete triggers, retention order and archive |
| CDR-05 isolation witnesses | §10: coverage matrix; structural test not counted for filters; seam witnesses labelled |
| CDR-06 replay, API, exposure | §8, §11: `run_key` required for replay; admin auth; keyset pages; coverage; `unresolved_exit_at_close` accounting and reconciliation |
| Clarifications | trigger-close forecast labelled descriptive with truncation and missing-terminal statuses (§7); inventory enum recommendation superseded; the volume column is no longer part of rollback |

## 14. Tracked separately (not Stage C)

- **SHORT-CLOCK:** `_check_short_intraday_triggers` uses host-local `date.today()` and
  hand-written hours, with no early close and no HK lunch.
- **GROWTH-TRADEPERF:** `analytics.py:584-585` raises `KeyError` for GROWTH without
  `max_hold_days`.

## 15. Stage C acceptance

1. The registry is the only horizon source, and the daily maps are pinned.
2. The daily boundaries and fallbacks reject INTRADAY.
3. The isolation matrix (§10) passes, and its sabotage pass fails each guard's own witness.
4. The contract functions pass on frozen bars, including every case in the review's acceptance
   lists:
   - **CDR-01:** volume-only after OHLC revision, source-only switch, sub-tolerance revision,
     basis change, failed trace, price/provenance race, a row first traced today, and a derived
     bar with one unknown constituent. None of them gains an availability claim.
   - **CDR-02:** detection exactly on an open, late liquidation, entry-bar double touch, gap
     through the stop at entry, a fill at the expiry boundary, competing timed and stop exits,
     and a stop decided after a completed bar.
   - **CDR-03:** detection before, at and after expiry; detection just before the cutoff; no
     open remaining; a cancelled setup that was never actionable. Each is accounted for once.
   - **CDR-04 (PostgreSQL 16):** concurrent duplicate detection, competing
     entered/cancelled transitions, observed duplicates, cross-mode references, outcome
     revisions under one evaluator version, retention with referenced and unresolved rows, and
     direct `UPDATE`/unguarded `DELETE`.
   - **CDR-06:** two replay runs not pooled, missing authentication refused, stable pages, and
     unresolved outcomes always visible.
5. Deployed per §12 with no generator; production generation disabled.

Not claimed: execution readiness, provider latency or availability, a working generator, or any
return.
