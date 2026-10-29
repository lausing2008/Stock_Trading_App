# Intraday Stage C — contract and storage design (revision 3)

Date: **2026-10-29**. Branch: `intraday-contracts`. Status: **design; nothing built**.
- **Inputs:**
  - the [Stage C inventory](2026-10-29-intraday-stage-c-inventory.md);
  - the [Intraday plan](2026-10-26-intraday-and-signal-performance-plan.md);
  - the [design review](../audits/2026-10-29-intraday-stage-c-design-review.md) of revision 1
    (`c6254311`), findings CDR-01..CDR-06, and of revision 2 (`0217a3be`), findings
    CDR-R2-01..CDR-R2-04. This revision resolves each one; §13 maps them.
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

**Algorithm identity** (CDR-R2-02). `identity_hash = sha256(canonical(strategy_id,
strategy_version, strategy_source_hash, contract_version, config_hash))`, where:
- `strategy_id` is `AI_INTRADAY`. RZY keeps `LITTLE_RZY_INSPIRED_V1` and is never relabelled.
- `strategy_source_hash` is the SHA-256 of the strategy and contract modules' source, computed at
  import. A logic change therefore yields a new identity even if nobody bumps
  `strategy_version`. A deploy that leaves those modules untouched keeps the identity.
- `config_hash` is the canonical hash of every parameter, including the session offsets, entry
  window, max hold, fill model, slippage and costs.

Each identity is stored once in `intraday_identities`. Each evaluation batch (one scan) is stored
in `intraday_evaluation_batches`: run, identity, release `code_version` (the git commit),
`started_at` and `data_cutoff`. The observed `run_key` is only a namespace; the identity and
code version live on the batch, never on the run row.

**Evaluation (always written).** Its key is `evaluation_key = sha256(canonical(run_key,
identity_hash, stock_id, trigger_bar_open))`, not null and unique. It is the denominator. Its
`result` (not null) is one of:
- `setup` (exactly one setup references it);
- `no_setup`, `wait`, `data_unavailable`;
- `unsupported_market`, `calendar_coverage_expired`.

- **Rerunning the same identity on the same bar** is a duplicate: `ON CONFLICT DO NOTHING`, then
  read back. It stays idempotent even across a restart or a deploy that did not change the
  strategy modules.
- **Evaluating a different identity** (new code or config) is a separate, coexisting row.
  Coverage is always reported per identity and never pooled.

**Setup.** Written for every detected pattern, whether or not it is actionable:
- **Identity:** `occurrence_key`, not null and unique, `sha256(canonical(run_key, identity_hash,
  symbol, trigger_bar_open, direction))`. Each setup references its evaluation through a
  same-run foreign key (§9).
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

**Event timing after actionable** (CDR-R2-01). Every later event (cancellation, reactive exit,
reconciliation) stores three separate times:

| Time | Meaning |
|---|---|
| `evidence_eligible_at` | the earliest time the evidence could be read: the bar's `readable_at`, or for an absent bar the `readable_at` it would have had. It is a **lower bound only**. |
| `evidence_observed_at` | when the evaluator actually read the evidence: the revised version, or the confirmed absence |
| `decided_at` | when the transition was decided and written |

They satisfy `evidence_eligible_at ≤ evidence_observed_at ≤ decided_at`. An event's
**effective time** is set by `clock_basis`:
- **`observed`:** the effective time is `decided_at`, the real processing time. An event is never
  dated at its eligibility time.
  - A revised bar's cancellation is effective when the revision was read, not at the original
    bar's close.
  - A missing bar's cancellation is effective when its absence was observed.
- **`replay_logical`** (replay only): the effective time is `evidence_eligible_at + replay_latency`,
  a versioned config parameter labelled as an assumption.

**Ordering against an entry.** The entry fills at the open `t` of a bar (§6).
- A cancellation whose effective time is **before** `t` defeats the entry: the order was
  cancelled before that bar opened.
- A cancellation effective at or after `t` cannot undo it: the entry stands and the event is
  recorded as `post_entry_evidence` on the outcome.
- If either time is unknown, for example a write whose `decided_at` was lost to a crash and later
  reconstructed, the setup records `chronology_ambiguous`. It is reported, never resolved by
  picking cancellation.
- `expired` is effective at `entry_expires_at` (exclusive).
- Reactive exits (§6) are ready at their `decided_at`.
- Scheduled orders and resting brackets keep their own adjudication rules (§6), which do not
  depend on processing time.

**Invariants by state:**

| State | Invariant |
|---|---|
| every setup | `readable_at ≤ input_read_at ≤ detected_at` for every input |
| `actionable` | `detected_at = actionable_at < entry_expires_at ≤ last_entry_at` |
| `entered` | `actionable_at < entry_fill_at < entry_expires_at`, and no cancellation effective before `entry_fill_at` |
| any later event | `evidence_eligible_at ≤ evidence_observed_at ≤ decided_at` (observed mode) |

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

**Window.** `window` is NOT NULL: `'30'`, `'60'` or `'120'` for forecasts, and the sentinel
`'trade'` for the managed-trade outcome. A CHECK ties each kind to its windows, so PostgreSQL's
NULL-distinct uniqueness can never admit a second root.

**Revision identity is independent of code version.** `outcome_key = sha256(canonical(setup
occurrence_key, kind, window, evaluator_version, evidence_hash))`, where `evidence_hash` hashes
the `source_hash`es of every bar the evaluation used. Changed input evidence under the same
evaluator code is a new revision; an identical re-evaluation is a duplicate (`ON CONFLICT DO
NOTHING`).

**Chain** (CDR-R2-02):
- **Root:** each chain has exactly one root per `(setup, kind, window)`, the row with
  `supersedes_id IS NULL` (unique partial index).
- **One successor:** `UNIQUE (supersedes_id)`.
- **Same-chain predecessor:** a composite foreign key `(supersedes_id, setup_id, kind, window,
  run_key) → intraday_outcomes (id, setup_id, kind, window, run_key)` makes a predecessor
  belong to the same setup, kind, window and run. A cross-setup, cross-window, cross-run or
  orphan predecessor is rejected by the database. The FK is `MATCH SIMPLE`, so it applies to
  every non-root row.
- **Current outcome:** the chain's tip.
- **Lock:** writing a revision locks the setup (§9).

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
  `reconciliation_mark`), carrying the three event times of §5. It is never folded into the
  same-session score. No broker exists in this release.
- **State classes** (CDR-R2-03):
  - `unresolved_exit_at_close` is **session-terminal but not final**.
  - `reconciled` is **final**.
  - Every other session-terminal state is also final.
  - The two partial unique indexes in §9 enforce one session-terminal transition and at most one
    `reconciled` transition per setup, so a setup can be reconciled exactly once.
- **Retention** never deletes an unresolved or unreconciled setup (§9).

## 9. Storage and constraints (CDR-04, CDR-R2-02, CDR-R2-03)

All tables are new, market-data-owned and registered before startup `create_all`. Each is
insert-only. Every column below is `NOT NULL` unless marked `NULL`, and every vocabulary is a
`VARCHAR` with a `CHECK`.

```
intraday_runs              run_key VARCHAR(64) PK, record_mode ('observed'|'replay'), created_at,
                           replay: input_snapshot_hash NULL, as_of NULL
                           UNIQUE (run_key, record_mode)
                           CHECK ((record_mode='observed') = (run_key='observed'))
                           CHECK (record_mode='observed' OR (input_snapshot_hash IS NOT NULL AND as_of IS NOT NULL))
                           sentinel row ('observed','observed') inserted by the schema step: a namespace only

intraday_identities        identity_hash CHAR(64) PK, strategy_id, strategy_version,
                           strategy_source_hash, contract_version, config_hash, config JSON

intraday_evaluation_batches batch_id PK, run_key, record_mode, identity_hash, code_version,
                           started_at, data_cutoff
                           FK (run_key, record_mode) -> intraday_runs (run_key, record_mode)
                           FK identity_hash -> intraday_identities
                           UNIQUE (batch_id, run_key, identity_hash)

intraday_evaluations       id PK, evaluation_key CHAR(64) UNIQUE, batch_id, run_key, identity_hash,
                           stock_id, symbol, session_date, trigger_bar_open,
                           result ('setup'|'no_setup'|'wait'|'data_unavailable'|'unsupported_market'
                                   |'calendar_coverage_expired'),
                           detected_at, data_cutoff
                           FK (batch_id, run_key, identity_hash) -> intraday_evaluation_batches
                           UNIQUE (id, run_key, identity_hash)

intraday_setups            id PK, occurrence_key CHAR(64) UNIQUE, evaluation_id UNIQUE, run_key,
                           identity_hash, stock_id, symbol, session_date, trigger_bar_open,
                           direction ('bullish'|'bearish'), levels, reasons, detected_at,
                           actionable_at NULL, entry_expires_at, last_entry_at, liquidation_at,
                           session_close
                           FK (evaluation_id, run_key, identity_hash)
                              -> intraday_evaluations (id, run_key, identity_hash)
                           UNIQUE (id, run_key)
                           CONSTRAINT TRIGGER (DEFERRABLE INITIALLY DEFERRED): its evaluation's
                              result = 'setup'; and every 'setup' evaluation has exactly one setup

intraday_setup_inputs      setup_id, run_key, role ('trigger'|'context'|'lookback'), timeframe,
                           bar_open, source ('native'|'derived_from_5m'), values, the four §4
                           hashes, readable_at, input_read_at, first_available_at NULL,
                           availability_basis, provider_published_at NULL
                           FK (setup_id, run_key) -> intraday_setups (id, run_key)
                           UNIQUE (setup_id, role, timeframe, bar_open)

intraday_setup_transitions setup_id, run_key, seq, from_state NULL (only for seq 1), to_state,
                           evidence_eligible_at NULL, evidence_observed_at NULL, decided_at,
                           clock_basis ('observed'|'replay_logical'), reason, evidence,
                           idempotency_key UNIQUE
                           FK (setup_id, run_key) -> intraday_setups (id, run_key)
                           UNIQUE (setup_id, seq)
                           CHECK ((from_state, to_state) IN legal pairs)
                           UNIQUE (setup_id) WHERE to_state IN (session-terminal states)
                           UNIQUE (setup_id) WHERE to_state = 'reconciled'
                           BEFORE INSERT trigger: append validation (below)

intraday_outcomes          id PK, outcome_key CHAR(64) UNIQUE, setup_id, run_key,
                           kind ('forecast'|'trade'), window, evaluator_version, evidence_hash,
                           supersedes_id NULL, status, values
                           CHECK ((kind='forecast' AND window IN ('30','60','120'))
                                  OR (kind='trade' AND window='trade'))
                           FK (setup_id, run_key) -> intraday_setups (id, run_key)
                           UNIQUE (id, setup_id, kind, window, run_key)
                           FK (supersedes_id, setup_id, kind, window, run_key)
                              -> intraday_outcomes (id, setup_id, kind, window, run_key)
                           UNIQUE (supersedes_id)
                           UNIQUE (setup_id, kind, window) WHERE supersedes_id IS NULL

intraday_session_seals     run_key, identity_hash, session_date, sealed_at, PK (all but sealed_at)

intraday_evaluation_counts run_key, identity_hash, session_date, result, count, archived_at
                           PK (run_key, identity_hash, session_date, result)
                           FK (run_key, identity_hash, session_date) -> intraday_session_seals
```

**Legal transitions.**
- A setup is inserted with its first transition, `seq = 1`, `(NULL → detected)`, in the same
  transaction. A deferred constraint trigger rejects a commit in which a setup has no `seq = 1`.
- `detected` goes to `actionable`, `expired_before_detection`, `no_fill_opportunity`,
  `cancelled_before_actionable`, `research_only_unsupported_direction` or
  `chronology_ambiguous`.
- `actionable` goes to `entered`, `expired`, `cancelled`, `rejected_invalid_bracket_at_fill` or
  `chronology_ambiguous`.
- `entered` goes to `exited` or `unresolved_exit_at_close`.
- `unresolved_exit_at_close` goes to `reconciled`.

**State classes.** Two partial unique indexes enforce them:
- **Session-terminal:** `expired_before_detection`, `no_fill_opportunity`,
  `cancelled_before_actionable`, `research_only_unsupported_direction`, `expired`, `cancelled`,
  `rejected_invalid_bracket_at_fill`, `exited`, `unresolved_exit_at_close` and
  `chronology_ambiguous`.
- **Final:** every session-terminal state except `unresolved_exit_at_close`, plus `reconciled`.

**Append validation is done in the database, not only by the pair CHECK.** A `BEFORE INSERT`
trigger on transitions does all of the following:
1. Takes `SELECT … FOR UPDATE` on the setup row. That serialises every append for the setup,
   including writers that forgot to lock.
2. Reads the latest transition.
3. Requires either `seq = 1` with `from_state IS NULL` and `to_state = 'detected'` and no prior
   row, or `seq = latest.seq + 1` with `from_state = latest.to_state`.
4. Raises otherwise.

The pair CHECK and the two partial indexes remain as independent backstops. A duplicate append
with the same `idempotency_key` is `ON CONFLICT DO NOTHING`, then read back. Two competing appends
from one state (for example `entered` and `cancelled`, or two `reconciled`): the second sees the
first as latest and is rejected.

**Run key and lineage.**
- `run_key` is never NULL; `'observed'` is a namespace.
- Every child reaches its parent through a composite FK including `run_key`, and evaluations and
  setups also include `identity_hash`. A cross-run or cross-identity link is rejected by the
  database: evaluation→batch, setup→evaluation, input, transition and outcome→setup, and
  outcome→predecessor.

**Mutation denial.**
- A `BEFORE UPDATE` trigger on every table raises.
- A `BEFORE DELETE` trigger raises unless the transaction has run `SET LOCAL
  stockai.intraday_retention = 'on'`.
- Both are created idempotently in `_apply_isolated_ddl`.
- All services share one database credential (`docker/docker-compose.yml`, `env_file: ../.env`),
  so the delete guard prevents accidents. It is not a permission boundary, and none is claimed.

**Seals: closing a session's membership.**
- A seal row for `(run_key, identity_hash, session_date)` means no more evaluations or setups may
  be inserted for that key. A `BEFORE INSERT` trigger on evaluations and setups rejects writes to
  a sealed key.
- **Observed:** the `intraday_seal` job seals each identity's session at `session_close + 2 h`.
  A later observed evaluation for that session is impossible, since the bar's session is over.
  An attempt is rejected and counted, never silently added.
- **Replay:** a run seals each session when it finishes it.
- Transitions and outcomes may still be appended after a seal: reconciliation and outcome
  revisions are expected.

**Retention.** The named job `intraday_retention` runs one transaction per (run, identity,
session), so it is atomic and retry-safe:
1. Only **sealed** sessions older than 35 days are eligible.
2. Insert the per-result counts, computed from that session's evaluations, into
   `intraday_evaluation_counts` with `ON CONFLICT DO NOTHING`.
3. Check that the stored counts equal the computed counts. Abort on a mismatch.
4. Delete that session's evaluations whose `result <> 'setup'`.
5. Commit.

A retry after a failure finds nothing committed and redoes it. A retry after success finds the
counts present, identical, and nothing left to delete. Late evaluations cannot exist, because the
session is sealed. Inputs of resolved final setups are deleted after 35 days.

**400-day deletion of a setup**, again in one transaction, in this order:
1. Delete **all outcome rows of the setup in a single statement**. FK `NO ACTION` checks run at
   statement end, so a whole chain is deleted together and no orphan or broken predecessor can
   remain.
2. Delete its transitions, then its inputs, the setup, and its `'setup'` evaluation.

A setup whose latest state is `unresolved_exit_at_close` (not reconciled) is never deleted, and
neither is any row it references. The archived counts and seals are kept indefinitely (a few rows
per session).

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
| outcome writer guard (`outcomes.py:229, 378, 683`) | SQLAlchemy 2.0's `Enum` adds no CHECK on SQLite, but its **result decoder raises `LookupError`** on an unknown value, so a contaminated ORM row cannot reach the guard. Two witnesses instead: (a) a compiled-query test that the pending-signal select carries `horizon IN DAILY_HORIZONS`; (b) a documented seam: the per-row hold-days function, called with a string horizon `'INTRADAY'`, skips and counts it, and the batch completes | yes (a: code presence; b: behaviour) |
| unknown-style fallbacks (`routes.py:60, 633, 1131, 1311`, decision-engine, watchlist, curation) | request tests expect 422/skip, not all-horizons or SWING | yes |
| daily maps pinned to `DAILY_HORIZONS` | key-set equality test | yes |
| **reader filters** (seven high-risk + medium) | on PostgreSQL the filter `horizon IN DAILY_HORIZONS` matches every possible enum value, so **no data witness exists there**. Witness at a documented seam instead: **raw `text()` readers** that run on SQLite (the column read as a string, no enum decoder) get a contaminated `'INTRADAY'` row and must exclude it; **ORM readers** and PostgreSQL-only SQL (`DISTINCT ON`, `LATERAL`) get a compiled-statement test asserting the predicate and bound values | yes, but **as a code-presence check for PG-only SQL**. Labelled as such; it is defence in depth behind the enum, not an independent data guard |

Each row names its test, and the sabotage pass removes each guard and checks that its own witness
fails. A row whose witness is not data-based is labelled as such.

## 11. Read API (CDR-06, CDR-R2-04)

These are admin-only research endpoints in market-data (`require_model_admin`). Records are
platform-wide over the active US universe and carry no user data. The gateway gets a new
`/intraday` prefix in `proxy.py` `_ROUTES`, covered by the scan-every-service test.

```
GET /intraday/evaluations?market=US&session=D&mode=observed|replay[&run_key=]&identity=<hash>[&symbol=][&cursor=][&limit<=500]
GET /intraday/setups?market=US&session=D&mode=…[&run_key=]&identity=<hash>[&symbol=][&cursor=][&limit<=500]
GET /intraday/identities?mode=…[&run_key=]&session=D      -- which identities have records that session
GET /intraday/unresolved?mode=…[&run_key=][&cursor=]     -- every unreconciled exposure, any session
```

- **Mode and run.** `mode=replay` requires `run_key`, and `mode=observed` rejects it. There is
  no default.
- **Identity.**
  - `identity` is **required** on `evaluations` and `setups`, so two configs or code versions are
    never pooled.
  - `/intraday/identities` lists what exists, with per-identity counts.
  - Every record also carries its own `identity_hash`, and the response echoes the selected
    identity's strategy, contract, config and code versions.
- **Ordering keys:** unique, not null, endpoint-specific.

| Endpoint | Ordering key |
|---|---|
| evaluations | `(trigger_bar_open, symbol, evaluation_key)`. Covers non-setup results, which have no occurrence key. |
| setups | `(trigger_bar_open, symbol, occurrence_key)` |
| unresolved | `(session_date, trigger_bar_open, occurrence_key)` |

  The keyset cursor carries the full key.
- **Membership:**
  - **Sealed session:** membership is frozen (§9). A full keyset traversal returns every record
    exactly once. The response says `membership: "sealed"` and includes the seal time.
  - **Unsealed session:** the response says `membership: "changing_view"`. A row inserted
    concurrently with an earlier key than the cursor can be missed by that traversal. The
    contract is: re-read after the seal for complete coverage, or re-scan from the start. No
    complete-coverage claim is made for an unsealed session.
- **Coverage** is returned per identity:
  - evaluations by result, read from the live rows or, after retention, from
    `intraday_evaluation_counts`, with that source named;
  - the retention horizon;
  - whether non-setup rows were archived.
- **Unresolved exposure** stays discoverable through `/intraday/unresolved` after its session
  ages out of ordinary pages. Unresolved outcomes are never filtered from any response.
- **Errors:** missing authentication is 401 at the gateway, and a non-US market returns
  `unsupported_market`. There is no write endpoint, and every response carries
  `production_generation: "disabled"`.

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
   - market-data's startup `create_all` creates the ten tables under the cross-service lock;
   - `_apply_isolated_ddl` adds the triggers and the observed-run sentinel row idempotently;
   - there is no `ALTER` on any existing table or type.
4. **Post-checks:**
   - drift 12/12 and 15/15 healthy;
   - the M24 capability check reports the tables, unique constraints, foreign keys and triggers;
   - a direct SQL `UPDATE` and an unguarded `DELETE` fail on production;
   - every Intraday business table is empty, and `intraday_runs` holds only the `('observed','observed')` sentinel;
   - `GET /intraday/setups` reports generation disabled;
   - `style=INTRADAY` on a daily endpoint returns 422;
   - one daily signal refresh and one outcome evaluation complete normally.
5. **Rollback:**
   - redeploy the tagged images; the tables, triggers and sentinel remain;
   - the old code never reads or writes them;
   - no existing table or type was altered, so nothing needs reverting;
   - dropping the tables is a separately approved step, allowed only while they hold nothing but
     the observed sentinel in `intraday_runs`.
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
| CDR-R2-01 backdated events | §5: evidence-eligible, observed and decided times stored for every later event; observed mode uses the real decision time; replay uses a labelled logical clock; `chronology_ambiguous` instead of choosing cancellation |
| CDR-R2-02 lineage and uniqueness | §5, §7, §9: algorithm identity (incl. strategy source hash) and per-batch code version; versioned `evaluation_key`; composite FKs evaluation→batch, setup→evaluation; same-chain outcome predecessor FK; NOT NULL trade-window sentinel; NOT NULL vocabularies; deferred setup/evaluation consistency; SQLite decoder seam in §10 |
| CDR-R2-03 reconciliation and retention | §8, §9: session-terminal vs final state classes and two partial indexes; database append validation (first transition, `seq`, `from_state` = latest); seals block late evaluations; one atomic, retry-safe archive transaction per session; single-statement chain deletion; unresolved setups never deleted |
| CDR-R2-04 pagination and identity | §11: endpoint-specific unique keys (evaluation key for the ledger); identity required, `/identities` and per-record identity; sealed = frozen membership, unsealed = labelled changing view; `/unresolved` endpoint |
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
   - **CDR-R2-01:** a delayed missing bar and a delayed revised bar, each eligible before an
     entry but read after it, do not cancel it. A reactive exit is ready at its actual decision
     time. An earlier observed cancellation still defeats a later entry.
   - **CDR-R2-02 (PostgreSQL 16):** orphan and cross-run evaluation links, cross-setup or
     cross-window predecessors, duplicate trade roots and code-version collisions are rejected or
     kept apart as specified. A genuine duplicate stays idempotent.
   - **CDR-R2-03 (PostgreSQL 16):** enter → unresolved → reconcile succeeds exactly once, and
     competing reconciliations fail. An archive retry neither loses nor doubles counts. A late
     evaluation into a sealed session is rejected and counted. Deleting an eligible whole chain
     leaves no orphan.
   - **CDR-R2-04:** non-setup evaluations page correctly, two identities are never pooled, a
     concurrent late insert follows the stated membership rule, and a sealed result set returns
     every record exactly once.
5. Deployed per §12 with no generator; production generation disabled.

Not claimed: execution readiness, provider latency or availability, a working generator, or any
return.
