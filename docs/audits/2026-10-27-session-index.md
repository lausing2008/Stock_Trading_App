# Session index — 2026-10-26/27: AI signals F01–F04, signals deadlock, RZY timeframe guide

START HERE for this session. Each item says what was built, what is deployed and what is still
open, and links the document that holds the detail. Source review:
[RZY/AI full review](2026-10-26-little-rzy-ai-signals-full-review.md). Tracker tier **475**.

## Status at a glance

| Item | State | Release | Detail |
|---|---|---|---|
| F01 calibration projection | Deployed, signal-engine only | `7084299d` | [review §F01, rollout](2026-10-26-little-rzy-ai-signals-full-review.md#f01-f02-production-rollout--october-27-2026) |
| F02 daily input contract (provenance + calibration eligibility) | Deployed, signal-engine only | `7084299d` | same |
| F03 signal occurrences — observation phase | Deployed to all 12 backends; collecting | `1be6c90c` | [F03 design §11–§12](../features/2026-10-27-f03-signal-occurrence-and-delivery-attribution-design.md) |
| F04 final-split label purge | **Held**; preserved on branch `f04-threshold-report-purge`, reverted from `prod` | revert `a82e2401` | [F04 plan](../features/2026-10-27-f04-evaluation-version-comparison-and-rollout-plan.md) |
| Signals upsert vs alert-delivery deadlock | Fixed and deployed, market-data only | `8dc84150` (deployed, recorded `c88fdc6f`) | [deadlock review](2026-10-27-signal-upsert-delivery-deadlock-review.md) |
| Little RZY timeframe guide + diagrams, tracker tier 475 | Deployed, frontend only | `1b0dfd6c` | [little-rzy.tsx](../../frontend/src/pages/little-rzy.tsx) |

## 1. F01 / F02 — calibration and the daily input contract

- **F01.** The calibration query omitted `SignalOutcome.exit_date`, so the strict timing predicate
  rejected every verified row. The query now selects it, and the predicate is unchanged.
- **F02.** Every generation records `reasons.input_contract`: last bar date and status, basis
  `provisional_intraday` / `session_closed`, missing sessions, and a partial weekly bucket.
  Calibration admits only `session_closed` first fires.
- **Deployed** to signal-engine only, from `7084299d`: 15/15 healthy, 970 tests passed.
- **Production state.** 26,021 resolved rows, 0 verified, so 0 published buckets. That is
  correct: the gate is intact and there is no verified evidence yet.

## 2. F03 — signal occurrences, observation phase only

- **What it records.** One occurrence per continuous BUY/SELL run, per stock and horizon, within
  one venue session. The session comes from F02's `last_bar_date`.
  - Each run gets an `opened` snapshot and counts its generations and content changes.
  - The first completed-session generation of each day is frozen (`completed_close`); it can be
    HOLD or WAIT.
  - Nothing changes sending, gating, outcomes or calibration.
- **Safety.**
  - An advisory lock per stock and horizon is taken in the generator's fixed horizon order.
  - A partial unique index allows at most one open run.
  - A SAVEPOINT isolates the observer, so a failure cannot lose the signals upsert. This was
    proven on PostgreSQL with a real SQL error.
  - Kill switch: `F03_OBSERVATION=0`, then recreate signal-engine.
- **Deployed** `1be6c90c` to all 12 backends under the host lock, market-data first.
  - 0 source mismatches.
  - The running trainer is byte-identical to F04's parent.
  - 0 observer failures.
- **Collection.**
  - `collect_session.py` runs at 23:00 UTC on weekdays. The user installed the cron wrapper; it
    takes the host lock and preserves the first capture (`84300fc4`). My own cron install was
    refused by the session's permission policy.
  - Target: ≥ 5 complete sessions per market. The US 2026-10-27 deploy session is excluded, and
    so is any session in which signal-engine is recreated.
- **First, excluded capture (US 10-27).** A shape check only:
  - 154 occurrences over 3,243 generations.
  - Close reasons: 0 `direction_change`, 79 `to_wait`, 27 `to_hold`.
  - 624 completed closes.
  - About 3.4 MB of storage.
- **Measurement limit (`e8851a43`).** `content_changes` is not "confidence changes": the hash
  covers all the reasons, which drift every refresh. Intermediate confidence values were never
  stored, so a versioned counter is needed before confidence churn can be measured.
- **Evidence cutover.** The market-data deadlock fix starts a new F03 evidence segment; do not
  pool across it.

## 3. F04 — held

- **Release.** Preserved on `f04-threshold-report-purge`. `a82e2401` reverts only the trainer and
  its test on `prod`; no later commit depended on it.
- **Why it stays held.** The impact study found 590 of 932 holdout artifacts below the
  reporting-row floor after the gap, and 38 of 109 unsuppressed ones affected. Also, incumbent
  retention keeps any artifact whose old `evaluation_valid` flag is true. Deploying the gap fix
  alone would freeze artifacts that were validated under the defective split.
- **Plan (`fb05a25b`).** Separate evaluation-validity, replacement and serving rules; a read-only
  routing inventory; arms A/B/C (70/10/10/10 unpurged, purged, and 60/10/10/20) on common
  outer dates; a bounded pilot; shadow, then canary.
- **Review of the plan** (this session, code-checked against `prod`):
  1. **Arm C leaks through the dead-zone threshold.** It is fitted on the first 70% of the raw
     frame (`trainer.py:828`), beyond arm C's 60% fit end. Name it as a required change,
     separate from `split_train` at `:1057`.
  2. **Reuse the M14 inventory.** `training/suppression_inventory.py` already separates
     validity, sufficiency and eligibility, and treats `None` as unknown. Reconcile its 50-row
     evidence floor with the plan's 10-row report floor, and its "eligibility" with the plan's
     "serving eligibility".
  3. **Freeze `POST /resweep_suppression`** during the study and canary. It rewrites production
     artifacts; no scheduler calls it.
  4. **Hash the inventory.** Training re-posts every post-close (`routes.py:451`), so artifacts
     change throughout. Timestamp and hash the inventory, and state that the current trainer
     keeps publishing under v1 until v2 ships.
  5. **Pilot budget is plausible.** About 326 runs per 30 min measured, so 324 jobs is about
     30 min. Still benchmark the research worker.
  6. **Unverified here:** signal-engine's "no eligible model" behaviour. Check it in Stage 0.

## 4. Signals upsert vs alert-delivery deadlock

- **Cause** (user diagnosis, `2aba2e14`). Generation upserts a stock's horizons
  SHORT → SWING → LONG → GROWTH in one transaction. `check_signal_alerts()` updated the selected
  rows in dict insertion order, so each could hold a row the other wanted. Before F03 this
  happened 2–15 times a day; none of the deadlocks involved F03. The victim's whole batch rolls
  back.
- **Fix (`8dc84150`, market-data).**
  [`ai_signal_delivery_write.py`](../../services/market-data/src/services/ai_signal_delivery_write.py)
  reads `(id, stock_id, horizon)` without locks, then updates in (stock, generation rank) order.
  The values written and the single commit are unchanged, and no retry was added.
- **Tests.**
  - A drift test pins the rank to `generate_all_signals()`.
  - A PostgreSQL race uses the shipped upsert, the live F03 observer and the real helper, with
    overlap proven via `pg_blocking_pids()`. Canonical order: both writers commit. Reversed order
    (negative control): 40P01.
- **Deployed** by the user, market-data only: 15/15 healthy, and the startup alert job wrote 401
  rows. Zero deadlocks so far, but the window was after the US close, so this is not yet evidence.
  The daily F03 capture counts deadlocks per session.
- **Open:** a database-only retry (step 2) needs its own review; never retry the email job.

## 5. Little RZY — timeframe guide and diagrams

- **Location.** A collapsible "Reading the timeframes" section on `/little-rzy`.
- **Facts verified in code.**
  - One engine per symbol and timeframe (`rzy_data.MINUTES`: 5m, 15m, 1h, 1d); 15m and 1h are
    aggregated from completed 5m bars.
  - Nothing compares timeframes.
  - Every rule is counted in bars, so the same defaults span 35 min (5m pivot confirmation) up to
    7 sessions (1d).
- **Table.** Per timeframe: role, pivot wait, armed expiry, Bollinger window, cost per trade and
  evaluation sample. 5m costs 0.117R per trade and 15m 0.069R; every timeframe shows no edge, and
  1h and 1d have too few trades.
- **Diagrams** (inline SVG, readable on phones via horizontal scroll):
  1. One session drawn in 1d, 1h, 15m and 5m bars, with the 7-bar pivot wait shaded.
  2. A long setup drawn to the engine's geometry: line through A and C; D is the gap from the
     highest high to that line; target is that high plus D. The trigger is a completed candle on
     the line closing above it and above the prior high. The stop is below C, the trigger low or
     the line, minus 0.25 ATR. A close below the line cancels.
  3. Top-down order: daily/1h direction, 15m setup, 5m timing; skip when they disagree.
- **Labelled as untested.** Combining timeframes is a reading practice: not implemented and not
  measured, and no timeframe has beaten random entry timing.

## Frontend deployment record (2026-10-28 04:08 UTC)

- **Scope.** The only changes since the running release `adb1a795` were this session's two
  frontend commits: `8288f1d0` and `1b0dfd6c` (3 files, `little-rzy.tsx`, `improvements.tsx`, one
  test).
- **Before.** Fresh frontend suite: 992 passed, `tsc` clean.
- **Deploy.** `scripts/deploy_frontend.sh` under the host lock, about 4.5 min, rc=0.
  - The previous image is kept as `stockai-frontend:rollback-before-1b0dfd6c`; the new one is
    tagged `release-1b0dfd6c`.
- **Verified.**
  - 15/15 containers healthy.
  - 0 static chunks contain `localhost:8000`.
  - The served `little-rzy-6b84d167b4f7a09d.js` contains the guide, and the improvements chunk
    contains tier 475.
  - `/`, `/little-rzy`, `/improvements` and `/api/health` all return 200.
  - 33 GB free.

## F04 Stage 0 (2026-10-28) — inventory and no-qualified-model trace

[Report](2026-10-28-f04-stage0-inventory-and-no-model-trace.md), read-only.
- **Inventory.** 1,433 artifacts hash-bound, 0 qualified under v2. F04 split: 590 of 934 holdout
  below the 10-row floor (40 unsuppressed). M14 sufficiency is unreachable (median `n_test` 23).
- **Main finding.** All-suppressed ensembles emit a bearish 0.45 with a "measured" stored AUC and
  real fusion weight. Production, last 3 days: 435 signals; 74 SELL signals had
  suppressed-but-weighted ML. The fix is F04 step 5/6 (empty qualified set → zero ML weight),
  which is a live behaviour change awaiting approval.

## F04 Stage 0 fix (2026-10-28) — held for review

[Replay report](2026-10-28-f04-ml-unavailable-replay.md). Suppressed models now carry no ML
direction or quality weight. Code is on branch `f04-ml-unavailable` (`e1494f8a`), **not deployed**.
- **Coverage.** Frozen-input replay of 796 cells: 792 compared, 4 failed (SSNLF, no price data).
  Control reproduced the old pass exactly.
- **Effect.** 30 signal changes, all in all-suppressed cells. 5 new BUYs, all gate-failed; 7 new
  SELLs; 0 lost BUYs or SELLs; 0 newly actionable entries.
- **Decision pending.** 7 BUY rows move from tier full → near.

## Open items

- F03: read ≥ 5 complete sessions per market, then design receipts. Add a versioned confidence
  counter if confidence churn matters.
- Deadlock: confirm on full US/HK sessions after `8dc84150`; review the database-only retry
  separately.
- F04: fold the plan review into Stage 0, then build the read-only inventory. Keep it held.
- RZY: if multi-timeframe alignment is wanted as a rule, it needs its own replay experiment
  before it is shown as anything more than a reading practice.
