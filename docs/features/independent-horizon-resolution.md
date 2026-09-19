# T410 — Independent per-window signal outcome resolution (AUD-C02 / AUD-C03)

**Built 2026-09-19**, on top of the [2026-09-18 scoping document](../audits/2026-09-18-c02-c03-outcome-horizon-scoping.md), which stopped short of implementation specifically to do the reference audit first. This is that audit's result and the build it authorized.

## The audit, completed

The scoping doc listed 201 non-test references to `SignalOutcome` across 12 modules as the
open risk. All 201 were read, not sampled. **Every single one** falls into exactly two patterns:

1. Filtered on `is_correct.is_not(None)` / `pct_return.is_not(None)` — the maturity signal.
2. A point lookup by exact `signal_id` — already returns nothing for an unresolved signal,
   identical to today's behaviour.

**None** expects or reads a pending row. Two examples that mattered most:

- `ml-prediction/features/builder.py` — double-filtered on `is_correct.is_not(None)` AND
  `exit_date.is_not(None)`. Safe.
- **`outcomes.py`'s own dedup guard** (`evaluated_ids`/`evaluated_sighd`, built from which
  `signal_id`s already have a row) — this is the one that would have broken. Inserting a
  pending row at signal time would make this guard treat the signal as "already evaluated"
  and **permanently skip its real primary-window resolution** — exactly backwards.

This flips the original plan. The scoped fix was "add a pending state to `SignalOutcome`,
patch ~156 unaudited call sites." The audited answer is a **new, additive table that nothing
existing reads**, which satisfies the acceptance criterion by construction rather than by
patching: `SignalOutcome`'s population semantics are completely unchanged, so all 201
references are — provably, not assumed — unaffected.

## What was built

**New table, `signal_outcome_horizons`** (`SignalOutcomeHorizon` in `shared/db/models.py`).
One row per `(signal_id, window_days)`, resolved independently of every other window on the
same signal and independently of `signal_outcomes`' own primary-window row.

| Column | Purpose |
|---|---|
| `window_days` | Which window this ONE row resolves — 5, 10, 20, or the style's own primary hold |
| `horizon_unit` | C03: explicit per row (`'calendar_days'` today), not inferred from context |
| `is_primary_window` | Whether `window_days` equals this (horizon, direction)'s own primary |
| `status` | `pending` / `resolved` / `missing_price` / `skipped` — an explicit state, per the scoping doc's requirement 2, rather than inferred from NULLs (inferring from NULL is how the original ambiguity arose) |

**Resolution logic** lives in `evaluate_signal_outcomes()` itself, as a new Phase 3, reusing
the *exact* `_lookup_outcome_price`/`_window_return` closures the existing primary-resolution
logic already trusts — no parallel price-lookup implementation to drift against the one
`signal_outcomes` itself uses (the same discipline `options_income_backtest.py`'s own
docstring names: *"a backtest of a parallel implementation measures the parallel
implementation"*).

**The candidate cutoff** was widened with an explicit `5`-day floor
(`min(..., min(...), 5)`) — not currently a behaviour change (SELL SHORT's own 5-day hold
already made 5 the effective minimum), but a guard against that floor silently rising if
`_SELL_OUTCOME_HOLD_DAYS["SHORT"]` is ever changed.

## Verified: the central claim, live

| | Before | After (Phase 3 added) |
|---|---:|---:|
| SWING BUY, 5-day resolved | 0 (of 279 actionable) | resolves independently, no wait for the 14-day primary |
| LONG BUY, 5-day resolved | 0 (of 880 actionable) | resolves independently, no wait for the 28-day primary |
| GROWTH BUY, 5-day resolved | 0 (of 369 actionable) | resolves independently, no wait for the 14-day primary |

Test-proven directly: a LONG BUY signal 6 days old gets a **resolved** 5-day row
(`is_correct`, `pct_return` populated) while its 28-day primary row is **pending**, in the
same run, from the same entry fill.

## What it deliberately does NOT do

- **Does not touch `signal_outcomes`.** Same columns, same population timing, same meaning.
- **Does not delisting-loss-score auxiliary windows.** The *primary* row's own 5/10/20-day
  columns never applied that special case either (`_window_return` takes no `is_delisted`
  parameter) — this table stays consistent with that established precedent rather than
  inventing a stricter rule for auxiliary windows. Test-covered and sabotage-verified.
- **Never rewrites a resolved row.** Scoping doc requirement 4. Sabotage-verified: removing
  the guard lets a later run with different prices silently overwrite an already-resolved
  outcome — caught immediately.
- **Does not wire `calibration.py`/AUD-A11 to consume the new data.** The scoped ask was
  making the data *available*; a new `GET /signals/horizon_coverage` endpoint (analytics.py)
  reports coverage by `(horizon, direction, window_days, status)`, which is enough to verify
  the fix landed. Teaching calibration to actually use the newly-available early windows is a
  natural next step, not done here — AUD-A11 remains correctly labelled (strength, not
  probability) until that calibration work happens on a real, matured sample.

## A bug found and fixed while building this, twice

`evaluate_signal_outcomes()`'s own `today = date.today()` carried the exact
[AUD-T409](../incidents/utc-vs-et-date-boundary.md) UTC/ET boundary bug — the same class
found in market-data the day before, now confirmed in signal-engine too, where it's
considerably more widespread (`calibration.py` alone has ~20 occurrences). Fixed the **one**
call site this build depends on (`_today_et()` added to `signals_shared.py`, matching the
market-data helper's name and rationale) — the other ~28 occurrences in signal-engine are
mostly lower-severity lookback-window cutoffs, not maturity/weekend decisions, and are
recorded as expanded scope in the T409 incident doc rather than swept here.

**Then wrote the exact same bug fresh**, in the new `horizon_coverage` endpoint's own
`since or date.today() - timedelta(days=30)` default — caught and fixed before it shipped,
specifically because a test was written to pin it (`test_horizon_coverage_default_lookback_
uses_et_aware_today_not_naive`), sabotage-verified to fail on the naive version.

## Tests

22 across two files: 12 in `test_t410_signal_outcome_horizons.py` (the acceptance criterion,
idempotency, the delisting-consistency precedent, the SELL-SHORT window-collapse edge case,
grace-window timing, the endpoint's wiring and its own date-boundary correctness) plus
existing `outcomes.py` regression suites re-run clean (38 total, no changes to their
behaviour). Sabotage-verified in three directions: removing the never-rewrite guard,
delisting-loss-scoring an auxiliary window, and the fresh date-boundary bug in the new
endpoint — all three caught immediately.

## Migration

`scripts/migrations/014_create_signal_outcome_horizons.sql` — purely additive, `CREATE TABLE
IF NOT EXISTS`, touches no existing table, column, or index.
