# Intraday Stage C — contract and storage design

Date: **2026-10-29**. Branch: `intraday-contracts`. Status: **design for review; nothing built**.
Inputs: [Stage C inventory](2026-10-29-intraday-stage-c-inventory.md) (`fc6fd388`), the
[Intraday plan](2026-10-26-intraday-and-signal-performance-plan.md), and the reviewer's
2026-10-29 requirements (separate Intraday tables approved; production generation disabled).

**Stage C establishes contracts and isolation only.** Its acceptance does not claim execution
readiness, a working generator, or any return. The generator follows later, in shadow mode, under
its own approval.

## 1. Does the shared `signalhorizon` enum need INTRADAY?

**No. Recommendation: do not add INTRADAY to `signalhorizon`.**

- **No consumer.** The enum types three columns: `signals.horizon`, `signal_outcomes.horizon` and
  `signal_outcome_horizons.horizon`. Under the approved design, no Intraday row is ever written
  to any of them. A value no table may hold is not part of any contract.
- **Its absence is the strongest daily write guard available.** Without the value, PostgreSQL
  itself rejects INTRADAY at every daily write boundary: the signal-engine upserts' `CAST(:hor AS
  signalhorizon)` and every ORM `SAEnum(SignalHorizon)` bind. Adding the value would remove that
  guard and leave only application checks.
- **It cannot be rolled back.** PostgreSQL has `ALTER TYPE … ADD VALUE` but no `DROP VALUE`.
  Adding it creates permanent exposure: any image built before the value exists raises
  `LookupError` on the first row carrying it. The inventory showed that this affects every
  unfiltered ORM read.
- **The public horizon contract is not the database enum.** The five horizons a user sees are an
  API and UI contract. They belong in a code registry (§2) that names daily and intraday horizons
  separately and points each to its own storage.

The Intraday tables use `VARCHAR` columns with `CHECK` constraints for their small closed
vocabularies (states, reasons, modes), as F03's tables do. They do not get a new PostgreSQL enum
type: that would reintroduce the same irreversible-evolution problem for values that will grow.

**If the reviewer prefers to keep the enum value** for symmetry, these safeguards are required:
- Add it only in a ledgered one-shot migration (`_apply_once`), run after all 12 backends have
  been rebuilt and verified (drift 12/12) with code that knows the value.
- Keep the code-level daily write rejections (§7), since the database guard would be gone.
- Never treat the migration as a rollout step that enables anything.

My recommendation is still to omit it.

## 2. Horizon registry — `shared/common/horizons.py` (new)

```
DAILY_HORIZONS = ("SHORT", "SWING", "LONG", "GROWTH")        # the only values daily code accepts
INTRADAY = HorizonSpec(
    id="INTRADAY", label="Intraday · same session", kind="intraday",
    storage="intraday_setups",                 # never signals / signal_outcomes
    markets=("US",),                           # v1: everything else refused
    trigger_timeframe="5m", context_timeframe="15m",
    forecast_windows_min=(30, 60, 120), primary_forecast_min=60,
    max_hold_min=120,
)
INTRADAY_PRODUCTION_ENABLED = False           # a constant, not an env flag: enabling needs a code change and review
class UnsupportedHorizon(ValueError): ...
def parse_daily_horizon(value) -> str          # raises UnsupportedHorizon for INTRADAY / unknown
def require_intraday_market(market) -> None    # raises UnsupportedMarket for anything but US
```

INTRADAY is distinct from the daily SHORT horizon by construction. SHORT stays in
`DAILY_HORIZONS` and keeps its daily model and the TIER83 intraday refresh. INTRADAY has no
daily model, map entry or style profile. The daily maps `_HORIZON_BY_STYLE`, `_STYLE_PROFILES`
and `_OUTCOME_HOLD_DAYS` must not gain it, and a test pins each map's key set to `DAILY_HORIZONS`.

## 3. Venue and session contract

- **US only in v1.** `require_intraday_market` refuses HK and any other venue with
  `unsupported_market`; the read API returns that status rather than an empty list. HK waits for
  a spans-aware bar preparation (lunch break).
- **Session calendar:** `common.market_calendar.session_bounds('US', d)` is the only source of
  open and close. That includes the NYSE early closes (13:00 ET) and the coverage stop: past
  `calendar_coverage()`, every evaluation is `calendar_coverage_expired` and nothing is produced.
- **Session identity:** a setup's `session_date` is the New York date of its trigger bar's open.
  Every window (entry, forecast, hold, liquidation) is computed from that session's bounds and
  never reaches into another session's bars.
- **Session times derived per date** (values for a 16:00 close; an early close shifts all of them):

| Name | Definition | Normal day | Early close |
|---|---|---|---|
| `session_open` | calendar | 09:30 | 09:30 |
| `last_entry_at` | `session_close − 30 min` | 15:30 | 12:30 |
| `liquidation_deadline` | `session_close − 10 min` | 15:50 | 12:50 |
| `session_close` | calendar | 16:00 | 13:00 |

The 30- and 10-minute offsets are versioned contract parameters (§5), proposed here and reviewable.

## 4. Time and availability contract

Every input bar a setup reads is recorded with these separate fields. None is inferred from
another.

| Field | Meaning | Source | When unknown |
|---|---|---|---|
| `bar_open` | interval start | `prices.ts` (all US providers label by open; naive UTC read as UTC) | — |
| `bar_close` | interval end | `min(bar_open + step, session_close)` | — |
| `readable_at` | earliest time the contract may read it | `bar_close + SETTLE` (`SETTLE = 2 min`, RZY's cutoff, named) | — |
| `platform_first_observed_at` | first time the platform held **exactly this version** of the bar | frozen at read time from `bar_provenance` (below) | `NULL` + basis `unknown` |
| `provider_published_at` | when the provider published it | none today | always `NULL`, basis `unavailable` |

**Version identity.** `version_hash = sha256(timeframe, bar_open, open, high, low, close,
volume, source)`, computed from the values actually read. A setup stores the values and the hash,
so a later revision cannot change what the setup is recorded as having read.

**First observation of the version used, not of an earlier one.** At read time, compare the read
values with `bar_provenance`'s current values for the bar:
- **OHLC equal and volume equal:** the version was first held at `last_changed_at` if set,
  otherwise `first_written_at`. If that time is before `bar_close`, it was a forming snapshot that
  happened to match. The completed version is then first available at
  `max(that time, bar_close)`, recorded with basis `held_since_forming`.
- **Volume differs, or the provenance row is missing:** `NULL` with basis `unknown`. Today a
  volume-only revision updates the stored values with no timestamp, so its version time cannot be
  established. Stage C adds the observation-only column `bar_provenance.last_volume_changed_at`
  (idempotent `ADD COLUMN IF NOT EXISTS`, written alongside the existing counter). Once that is
  in place, `volume_basis` becomes known for new revisions.
- **Derived 15m:** the maximum `platform_first_observed_at` over its three constituents, with
  basis `derived_max_constituent`. It is unknown if any constituent is unknown.

**Setup-level instants**, all stored:
- `data_cutoff`: the evaluation's input cutoff.
- `data_available_at`: the maximum `platform_first_observed_at` over the setup's inputs, or
  `NULL` if any input is unknown.
- `detected_at`: the actual wall-clock time this evaluation produced the result (the F09 clock,
  not the scan start).
- `actionable_at` and `entry_expires_at` (§5).

**Ordering invariants**, checked on write; a violating record is refused, not stored:
- `bar_open < bar_close ≤ readable_at ≤ data_cutoff ≤ detected_at`
- `data_available_at ≤ detected_at`, when known
- `detected_at ≤ actionable_at < entry_expires_at ≤ last_entry_at`

## 5. Setup lifecycle

**Strategy and config identity.** Every setup carries:
- `strategy_id`: `AI_INTRADAY`. `LITTLE_RZY_INSPIRED_V1` stays RZY's own identity and is never
  relabelled.
- `strategy_version` and `contract_version`.
- `config_hash`: the canonical JSON of every parameter, including the session offsets, entry
  window, max hold, fill model and costs.

A parameter change is a new `config_hash`; it never rewrites an existing record.

**Occurrence identity:** `occurrence_key = sha256(record_mode, replay_run_id, strategy_id,
strategy_version, config_hash, symbol, trigger_bar_open, direction)`, unique. Two setups on
different trigger bars in the same session coexist. Bullish and bearish are separate occurrences.

**Proposed levels** are frozen in the setup row: `entry_reference`, `stop`, `target`,
`risk_per_share`, and the reasons that produced them. They are proposals, never fills.

**States** (each change is an appended transition row with `at`, `reason`, `evidence`; the setup
row itself is never updated):

```
detected ──► actionable ──► entered ──► exited(stop | target | max_hold | liquidation)
   │             │              └─────► exit_unfilled_at_close
   │             ├──► expired                  (entry window passed without a fill)
   │             └──► cancelled(reason)
   └──► cancelled(reason)                      (failed a check before becoming actionable)
```

- **Becomes actionable** at `actionable_at = detected_at`, only if all of these hold:
  - inputs are fresh and gap-free (inventory §3);
  - `detected_at < last_entry_at`;
  - the direction is supported for the record's capability (bearish is `research_only`;
    borrow is unknown);
  - `INTRADAY_PRODUCTION_ENABLED` is irrelevant to research records and gates only a future
    production path.
- **Expires** at `entry_expires_at = min(bar_close(trigger) + ENTRY_WINDOW, last_entry_at)`, with
  `ENTRY_WINDOW` = 15 minutes (three bars) proposed. The setup expires if no entry fill exists by
  then.
- **Cancelled on invalidation** before entry, with the first applicable reason:
  - `invalidated_price`: a completed bar trades through the stop (bullish) or above it (bearish)
    before entry;
  - `input_revised`: the trigger or context bar's version changes before entry;
  - `data_unavailable`: a gap or staleness appears in the bars needed to manage the entry;
  - `session_end`: `last_entry_at` is reached.
  A revision after entry does not cancel; it is flagged on the outcome.
- **Email expiry is not eligibility.** Any later notification carries its own `deliver_by`.
  Trade eligibility reads only the setup's state and `entry_expires_at`, and a test asserts the
  email field is never consulted.

**Non-setup results** are recorded in an evaluation ledger, one row per (stock, trigger bar,
config). They give full-session denominators: `setup_detected`, `no_setup`, `wait`,
`data_unavailable`, `unsupported_market`, `calendar_coverage_expired`. Expected volume is about
11,000 rows per session (142 symbols × 78 bars), with the same 35-day retention as the provenance
tables.

## 6. Session-end management and causal execution (research fill model v1)

These rules define the *research* outcome. They are labelled `execution_grade = false` and are
not an execution contract.

- **Clipping an entry window does not close a position.** `last_entry_at` stops new entries only.
  An open position is managed until it exits or until the liquidation rule below resolves it.
- **Causality.** An order is ready at `order_ready_at = detected_at` for entries, and at the
  decision time for exits. A fill may use only an *executable observation strictly after*
  `order_ready_at`. With bar data, the first such observation is the open of the first 5m bar
  whose `bar_open ≥ order_ready_at`. The fill price is that bar's open, adjusted by the versioned
  slippage and cost parameters, with `fill_basis = next_bar_open_research`. Because
  `detected_at ≥ trigger close + 2 min`, the earliest entry is the open of the bar after the
  trigger's successor. A fill can never predate the decision.
- **Stops and targets** are checked on completed bars after entry:
  - a bar whose range reaches the stop exits at the stop, or at the bar's open if it gaps through;
  - a bar reaching both the stop and the target is scored as the stop (conservative);
  - the target fills at the target, never better.
- **Max hold:** an exit decision at `min(entry_fill_at + 120 min, liquidation_deadline)`, filled
  by the same next-bar-open rule.
- **Liquidation deadline:** at `liquidation_deadline`, any open position gets an exit decision.
  It fills at the open of the first bar with `liquidation_deadline ≤ bar_open < session_close`.
- **No executable observation before close:** the missing bar, a gap or stale data leaves the
  outcome as `exit_unfilled_at_close`:
  - no fill is recorded, and no closing price is assumed;
  - the position is reported as open exposure;
  - a mark at the last observed price is stored as `mark`, never as realized P&L;
  - the next session's bars are never used to close it. The case is listed for review and counts
    against the operational gate.
- **Two outcome records, never merged:**
  - **forecast outcome:** return from the trigger close to the close of the last completed bar at
    +30, +60 and +120 minutes. A window that would pass `session_close` is stored as
    `truncated_session_end` with its actual length.
  - **managed-trade outcome:** fills, R, MAE, MFE, exit reason and the unfilled-at-close state.

## 7. Immutable records and observed vs replay

Tables are owned by market-data (the only writer and reader), defined in a service module
registered before startup `create_all`, like `bar_provenance`. They are new tables, so no
`ALTER` is involved.

| Table | Content | Mutability |
|---|---|---|
| `intraday_replay_runs` | replay id, input snapshot hash, code version, as-of, config_hash | insert-only |
| `intraday_evaluations` | ledger, one row per (mode, run, config, stock, trigger bar): result status, data cutoff, detected_at | insert-only |
| `intraday_setups` | identity (§5), versions, proposed levels, reasons, times (§4), capability, `record_mode`, `replay_run_id` | insert-only |
| `intraday_setup_inputs` | one row per input bar: timeframe, source (`native` / `derived_from_5m`), values, `version_hash`, the §4 times and bases | insert-only |
| `intraday_setup_transitions` | append-only state changes (§5) | insert-only |
| `intraday_forecast_outcomes` | per setup and window, with `evaluator_version` and the input versions used | insert-only; a re-evaluation is a new `evaluator_version` row |
| `intraday_trade_outcomes` | per setup: fills, exits, R, MAE/MFE, `exit_unfilled_at_close`, `execution_grade=false` | insert-only, same rule |

- **Immutability** is enforced twice:
  - **application:** no update path exists, and a test scans the module for `update(`;
  - **PostgreSQL:** a `BEFORE UPDATE` trigger on the insert-only tables raises (idempotent
    `CREATE OR REPLACE`). `DELETE` is allowed only to the named retention job.
- **Observed vs replay.** `record_mode` is either:
  - `observed`: written only by the live shadow evaluator, using wall-clock times and inputs as
    the platform held them;
  - `replay`: a corrected historical reconstruction tied to one `intraday_replay_runs` row. Its
    availability fields carry basis `replay` and are never presented as observations.
  The unique keys include the mode, so a replay can never overwrite, fill in for or relabel an
  observed record. Every reader takes the mode as a required parameter; there is no default.

## 8. Daily isolation

Required before any producer exists.

**Write boundaries:**
- **signal-engine upserts** (`routes.py:469, 1240`): reject anything outside `DAILY_HORIZONS`
  with `parse_daily_horizon`, ahead of the database enum.
- **Outcome writers** (`outcomes.py`): `_OUTCOME_HOLD_DAYS[horizon]` becomes
  `parse_daily_horizon` first. An unknown horizon is skipped and counted per row, so it no longer
  aborts the batch with `KeyError`.
- **Unknown-style fallbacks** stop guessing:
  - signal-engine `routes.py:60, 633, 1131, 1311`, `/signals/{sym}?style=…`, decision-engine
    `/decide`, watchlist `trading_style` and the curation job (`scheduler.py:11555`) answer 422
    / skip with `unsupported_horizon` instead of returning all horizons or SWING.
  - For INTRADAY, the error points to the Intraday API.

**Readers:** the seven high-risk readers in the inventory get an explicit `horizon IN
DAILY_HORIZONS` filter:
- `outcomes.py:229`
- `calibration.py:697` and `:969`
- `meta_trainer.py:128`
- event-intelligence `scheduler.py:128`
- `paper_trading_engine.py:3205`
- `analytics.py:1517`

This is defence in depth, since no Intraday row can reach those tables. The medium readers
(`/consensus`, `/recent_changes`, analytics accuracy/drift/factor/walk-forward, the
data-quality `MAX(sig.ts)` check, research `adapters.py:391`) get the same filter.

**Isolation tests**, run on PostgreSQL 16 with the real enum:
1. An INTRADAY daily write is refused at every boundary above: by the application, and by the
   database when the application check is bypassed.
2. **Influence tests.** Populate the Intraday tables with extreme setups and outcomes (every
   setup a 100% winner, RSI 99, ta_score 1.0). Then run, comparing the outputs byte for byte with
   an empty-Intraday control:
   - TA and conviction weight calibration;
   - meta-model training rows;
   - the catalyst recompute;
   - the paper-exit RSI read;
   - `/outcomes/summary`, the morning digest and the alert-email win-rate badge.
3. Each daily map's key set equals `DAILY_HORIZONS` (pins §2).
4. A sabotage pass: removing each filter or guard in turn must fail a test.

## 9. Read API

market-data gets read-only `GET /intraday/setups?market=US&session=YYYY-MM-DD&mode=observed|replay[&symbol=]`
and `GET /intraday/evaluations?…` (the same required `mode`). They:
- return `unsupported_market` for anything but US, and `production_generation: disabled` in
  every response;
- return empty results in Stage C;
- need a new gateway prefix in `proxy.py`'s `_ROUTES` (the gateway-route-gap incident class),
  covered by the existing scan-every-service regression test.

No write endpoint exists.

## 10. Rollout sequence

Stage C is one release, because the readers' compatibility does not depend on the schema:
1. **Build and verify off-host.** On a pinned release commit:
   - `make test-all`;
   - the PostgreSQL 16 isolation suite (§8) on a throwaway database;
   - sabotage checks.
2. **Pre-checks on the host:** drift 12/12 clean, deploy lock free, rollback images tagged for
   all 12 backends.
3. **Deploy all 12 backends sequentially** with `scripts/deploy.sh`. `shared/` changed (the
   registry and the daily guards), and shared is one versioned unit. Startup `create_all` creates
   the new tables under the cross-service advisory lock. `ADD COLUMN IF NOT EXISTS
   last_volume_changed_at` and the update-blocking triggers run in `_apply_isolated_ddl`
   (idempotent). **No `ALTER TYPE`.** No generator exists in this release.
4. **Post-checks:**
   - drift 12/12 clean;
   - 15/15 healthy;
   - the M24 capability check reports the seven tables with their unique constraints and
     triggers;
   - the tables are empty;
   - `GET /intraday/setups` answers `production_generation: disabled`;
   - an INTRADAY write to the daily boundary returns 422;
   - one daily signal refresh and one outcome evaluation complete unchanged.
5. **Rollback:** redeploy the tagged images. The new tables stay, empty and unread, and the
   added column stays NULL. Dropping any of them is a separate, explicitly approved step, and
   only if the tables are empty. Nothing in the enum changed, so nothing is irreversible.
6. **Later, separately approved:** the shadow generator in market-data, which writes only
   `record_mode='observed'` research records. A database migration alone can never enable
   generation: there is no enum step, and the generator ships disabled.

## 11. Tracked separately (not Stage C)

- **SHORT-CLOCK:** `scheduler._check_short_intraday_triggers` uses host-local `date.today()` and
  hand-written 09:30–16:00 hours, with no early close and no HK lunch.
- **GROWTH-TRADEPERF:** `analytics.py:584-585` has no GROWTH key in `_default_max_hold`, so
  `/trade_performance?horizon=GROWTH` without `max_hold_days` raises `KeyError`.

## 12. Stage C acceptance

1. The registry is the single source of horizons, and the daily maps are pinned to
   `DAILY_HORIZONS`.
2. Every daily write boundary and unknown-style fallback rejects INTRADAY, with the database
   enum unchanged.
3. The seven high-risk and the medium readers are filtered, and the influence tests (§8) are
   byte-identical against the control.
4. The seven tables exist with their constraints and update-blocking triggers. Contract
   functions are tested on frozen bars:
   - session times, including an early close and the coverage stop;
   - US-only refusal;
   - the time ordering invariants;
   - version hashing and first observation, including a revised bar, a forming snapshot,
     volume-unknown and derived 15m;
   - the state machine and cancellation reasons;
   - causal fills, liquidation and `exit_unfilled_at_close` with no invented fill;
   - observed and replay separation.
5. The release is deployed per §10 with no generator. Production generation stays disabled.

Not claimed: execution readiness, data latency adequacy, a working generator, or any edge.
