# Design: Self-Improving Signal/Model Loop (Tune → Backtest → Tune)

**Status:** Design only — nothing in this document is implemented yet. Action items are tracked
in `improvements.tsx` under Tier 233. This is a design + architecture proposal per user request,
to be reviewed before any implementation work begins.

**Goal:** stop tuning signal/ML parameters against proxy metrics (AUC, in-sample expected value)
in isolated, manually-triggered endpoints, and instead close the loop — tune against realistic
backtested P&L, gate promotion to production behind a walk-forward validation check, and repeat
automatically on a schedule, with full history so regressions are visible and reversible.

---

## 1. What already exists (inventory — do not rebuild these)

The system already has real, working pieces of this loop. The design below is a **meta-layer**
that connects them, not a replacement:

| Piece | Where | What it does today | Gap |
|---|---|---|---|
| ML hyperparameter tuning | `ml-prediction/training/tuner.py`, `POST /ml/tune_all` | Optuna search per symbol/style/horizon, optimizes **AUC** via `TimeSeriesSplit` CV | Tunes a classification proxy metric, not P&L. A model can have great AUC and mediocre trading expected value (e.g. confident-but-wrong on the highest-conviction trades). |
| Signal threshold calibration | `signal-engine`, `GET /signals/outcomes/calibrate`, `POST /signals/outcomes/calibrate/apply` | Sweeps `buy_threshold` per horizon against real `SignalOutcome` rows, picks the value maximizing win_rate × avg_return (real expected value) | Already the right objective! But: manually triggered, no walk-forward split (sweeps and reports over the same window), no automatic promotion gate, threshold-only (doesn't touch ML weight caps, TA weights, or paper-trading gate thresholds in the same pass). |
| TA/ML weight calibration | `signal-engine`, `POST /signals/calibrate/ta-weights`, `POST /signals/calibrate/ml-weight` | Adjusts blend weights based on recent accuracy | Same manual-trigger, no-backtest-gate pattern as above. |
| Signal-level outcome tracking | `SignalOutcome` table, `POST /signals/outcomes/evaluate` (scheduler, post-close) | Real forward-return tracking per signal: entry/exit price, `pct_return`, `is_correct`, multi-window (5d/10d/...) returns | This is the ground truth the whole loop should be built on — already solid. |
| Trade-level outcome tracking | `PaperTrade` table + paper trading engine | Real closed-trade P&L, exit reason, hold days, per-portfolio equity curve | Second ground-truth source; captures gate/sizing effects that `SignalOutcome` alone can't (a good signal can still lose money on bad sizing or a bad exit rule). |
| DSL backtest engine | `strategy-engine/backtest/engine.py` | Rule-based backtest: fixed fee/slippage bps, entry/exit rule evaluation over historical OHLCV | Built for **user-defined DSL strategies**, not for evaluating a candidate change to the production signal/ML pipeline itself. Also known to have its own indicator-computation bug (T232 audit: RSI formula diverges from `technical-analysis`'s canonical implementation). |
| Data quality checks | `market-data/scheduler.py`, `_DQ_CHECKS` | Staleness monitoring across rankings/signals/prices/outcomes every 2h | Not tuning-related directly, but the safety net any automated loop needs to trust its own inputs. |

**Bottom line:** the ingredients for outcome-driven tuning exist and one piece
(`outcomes/calibrate`) is already doing the right thing (optimizing real expected value, not a
proxy). What's missing is orchestration: a walk-forward validation step, a single harness that
can backtest a *candidate parameter set* (not just DSL strategies) against held-out history, a
promotion gate, and a scheduler to run the loop periodically instead of by hand.

---

## 2. Design principles

1. **Optimize what you actually care about.** Every tunable parameter in this loop should be
   evaluated against realized P&L / win-rate on held-out data — never against a proxy metric
   like AUC alone. `outcomes/calibrate` already does this for thresholds; ML tuning does not yet
   for hyperparameters. Bring ML tuning's objective in line with the rest.
2. **Walk-forward, never in-sample.** Every "tune against outcome data" step must split
   chronologically: tune on window N, validate on window N+1 (never seen during tuning), and
   only promote if the validation-window result holds up. `outcomes/calibrate` today sweeps and
   reports over the *same* window — that's look-ahead bias baked into the recommendation, even
   though the underlying metric (expected value) is correct. Fix the validation methodology, not
   the metric.
3. **Never auto-promote silently.** Every parameter change this loop proposes needs to pass a
   backtest gate (see §4) before touching production config. No exceptions, including
   scheduled/automatic runs — an automatic loop that can silently make trading worse without a
   trace is strictly worse than the manual status quo, however slow.
4. **One home for "what changed and did it help."** Every tune, whether manual or automatic,
   writes to a single history table so regressions are traceable and revertible without grepping
   logs across 4 services (the current state for `_should_enter()`/decision-engine drift
   incidents this session already proved how expensive that is to reconstruct after the fact).
5. **Reuse the existing paper-trading engine as the backtest execution model where possible.**
   Building a *third* backtest engine (after the DSL one and whatever this loop needs) repeats
   the exact duplication pattern already found and partially fixed this session (regime,
   style-params, dual-scorer). If the paper-trading engine's gate/sizing logic can run against
   historical data in a dry-run mode, that's a stronger backtest than a hand-rolled fixed-fee
   simulator, because it tests the ACTUAL production decision path, not an approximation of it.

---

## 3. Proposed architecture

```
                         ┌─────────────────────────────┐
                         │   Tuning Orchestrator        │   NEW — a scheduled job (weekly,
                         │   (new: market-data          │   Sunday, matching the existing
                         │    scheduler job, or a new   │   outcomes/calibrate/apply cadence)
                         │    lightweight service)      │
                         └───────────┬─────────────────┘
                                     │ 1. pulls current SignalOutcome + PaperTrade history
                                     │ 2. proposes candidate parameter sets (see §3a)
                                     ▼
                         ┌─────────────────────────────┐
                         │   Backtest Harness           │   NEW — the missing piece.
                         │   (new: shared module or a   │   Runs the REAL paper-trading gate/
                         │    new backtest-engine       │   sizing/exit logic against historical
                         │    service)                  │   price+signal data with a candidate
                         └───────────┬─────────────────┘   parameter set substituted in.
                                     │ walk-forward: train window vs. held-out validation window
                                     ▼
                         ┌─────────────────────────────┐
                         │   Promotion Gate              │   NEW — compares candidate vs.
                         │   (rules, not ML)              │   current-production backtest result
                         │                                │   on the SAME held-out window.
                         └───────────┬─────────────────┘
                          pass │             │ fail
                               ▼             ▼
                  ┌─────────────────┐   ┌──────────────────┐
                  │ Apply to prod    │   │ Log + discard,    │
                  │ config + write   │   │ write to history  │
                  │ to Tune History  │   │ table anyway       │
                  │ table            │   │ (so "we tried X    │
                  └─────────────────┘   │  and it didn't      │
                                        │  help" is visible)  │
                                        └──────────────────┘
```

### 3a. What gets tuned, and by which existing mechanism

| Parameter class | Tuning mechanism | Change needed |
|---|---|---|
| ML model hyperparameters (XGBoost etc.) | `ml-prediction`'s existing Optuna tuner | Add a **secondary objective**: after Optuna picks the best-AUC params, run the resulting model through the new backtest harness and report expected value alongside AUC. Don't replace AUC-based CV (it's still useful for avoiding overfit) — add the backtest as a second gate before the tuned model is actually deployed. |
| Signal confidence thresholds per style/horizon | `signal-engine`'s existing `outcomes/calibrate` | Add walk-forward split (§2 point 2) — tune on the first 2/3 of the lookback window, validate on the last 1/3, only report/apply if the validation-window expected value also improves. |
| TA/ML blend weights | `signal-engine`'s existing calibration endpoints | Same walk-forward fix as above. |
| Paper-trading gate thresholds (`min_kscore`, `min_ta_score`, `min_volume_z`, etc.) | **New** — nothing tunes these today; they're hand-set constants in `_DEFAULT_CONFIG`/`_STYLE_OVERRIDES` | This is the biggest net-new piece. These gate thresholds directly determine which candidates ever reach a trade, so they have first-order impact on realized win-rate — but nothing today systematically searches for better values. Proposed: Optuna (or a simpler grid/random search, given the moderate parameter count) using the new backtest harness as the objective function directly. |
| Position sizing multipliers (regime, confidence, research, consensus bands) | **New** — same story as gate thresholds | Same approach — these are currently hand-tuned constants (some explicitly acknowledged as "rescaled" in code comments, e.g. `sizer.py`'s T232-DE2 note) with no systematic search. |

### 3b. The Backtest Harness — the one genuinely new core component

This is the piece that makes everything else trustworthy. Design:

- **Input:** a candidate parameter set (any subset of: ML model version, signal thresholds, TA/ML
  weights, paper-trading gate thresholds, sizing multipliers) + a historical date range.
- **Execution:** replay the ACTUAL `_scan_for_entries`/`_monitor_positions` decision logic
  (or decision-engine's, once the dual-scorer debt from Part 10 of the Tier 232 audit is
  resolved and one side is canonical) against historical `Price`/`Signal`/`Ranking` rows, with
  the candidate parameters substituted in, producing a synthetic equity curve exactly like a real
  `PaperPortfolio` would generate — not a simplified proxy simulator.
- **Why not reuse `strategy-engine`'s backtest engine as-is:** that engine is built for
  user-authored DSL rules (`{"indicator": "rsi_14", "operator": "<", "value": 35}`), a
  fundamentally different and much simpler decision model than the multi-layer gate/scoring/
  sizing pipeline this loop needs to validate. It also has its own known indicator-divergence bug
  (different RSI formula than the canonical `technical-analysis` implementation) that would
  quietly corrupt any backtest built on top of it. Building the harness around the REAL trading
  engine's own functions (parameterized to accept overrides instead of reading global
  `_DEFAULT_CONFIG`) avoids inheriting that bug and, more importantly, tests the actual code path
  that runs in production — the strongest possible guarantee that a backtest result predicts
  live behavior.
- **Output:** win rate, expected value per trade, Sharpe/Sortino/max-drawdown on the synthetic
  equity curve, broken out per style/market (US/HK) since (per this session's earlier findings)
  performance characteristics differ meaningfully between them.
- **Where it lives:** proposed as a new module under `shared/` (e.g. `shared/backtest/`) rather
  than a new service — it needs direct, in-process access to `paper_trading_engine.py`'s
  functions (parameterized), and going through an HTTP hop for every historical bar replayed
  would be far too slow for a walk-forward sweep across many candidate parameter sets. A shared
  library that both `market-data` (real trading) and a new orchestrator job can import avoids
  yet another duplicated implementation of the same trading logic — exactly the failure mode this
  session spent most of its time fixing (regime, style-params, dual-scorer).

### 3c. Promotion Gate — rules, deliberately not another model

Given this session's repeated finding that blind parameter changes without outcome validation are
exactly the risk to avoid (see the dual-scorer tech-debt writeup), the promotion gate should be
simple, auditable rules, not another tunable model:

1. Candidate must show **positive expected-value lift** on the held-out validation window
   (not just the training window — walk-forward, per §2).
2. Candidate must **not reduce trade count below a minimum sample size** for the style/market
   being tuned (an "improvement" from 3 lucky trades is not a real signal).
3. Candidate must **not increase max drawdown** beyond some tolerance (e.g. 10% relative) even
   if expected value improves — a strategy that wins bigger but occasionally blows up further
   is not unambiguously better.
4. Candidate must pass on **both** SignalOutcome-based and PaperTrade-based backtests where
   applicable (they can disagree — a good signal with bad sizing looks fine in one and bad in
   the other; require agreement before promoting).
5. All four checks, pass or fail, get logged to the Tune History table (§3d) with the full
   before/after numbers — never just "applied" or "rejected" with no evidence trail.

### 3d. Tune History — one home for "what changed and did it help"

New table, proposed name `tune_history`:

```
id, run_id, ts, parameter_class (ml_hyperparams | signal_threshold | ta_ml_weight | gate_threshold | sizing_mult),
style, market, old_value (jsonb), new_value (jsonb),
backtest_window_start, backtest_window_end,
train_window_expected_value, validation_window_expected_value,
promoted (bool), promotion_gate_failures (jsonb array of which of the 4 checks failed, if any),
triggered_by (scheduled | manual)
```

This directly addresses a pattern that cost real diagnostic time this session: CAL-1 (an earlier
Tier 232 finding, a corrupted Redis-cached threshold from a bad Sunday calibration run) went
undetected until a live production check found it, because the calibration endpoint didn't leave
an audit trail distinguishing "applied because it passed a gate" from "applied because nothing
checked." A `tune_history` table with the full backtest evidence for every attempted change,
success or failure, closes that gap structurally rather than relying on someone noticing a
Redis key looks wrong.

### 3e. Orchestrator — where does the loop run?

Recommend: a new scheduled job in `market-data/scheduler.py` (matching the existing
`run_data_quality_checks`/`outcomes/calibrate/apply` pattern — Sunday, off-market-hours), NOT a
new service. Reasoning: the orchestrator's job is "call existing endpoints in the right order,
apply the promotion gate, write history" — plumbing, not a new bounded context. Adding an entire
new service for this would repeat exactly the "too many services, unclear boundaries" question
this session's parallel architecture-audit workflow is investigating in parallel with this
design. If that audit concludes market-data itself needs to be split, this job should live in
whichever successor service ends up owning the scheduler.

---

## 4. Phased rollout (do not build this all at once)

**Phase 1 — walk-forward fix (lowest risk, immediate value):** Fix `outcomes/calibrate`'s
in-sample bias by adding a train/validation split. This alone makes the *existing* calibration
mechanism trustworthy without building anything new. Ship and observe for a few calibration
cycles before Phase 2.

**Phase 2 — Backtest Harness for gate thresholds only:** Build the harness (§3b) scoped
narrowly to the paper-trading gate thresholds (`min_kscore`, `min_ta_score`, `min_volume_z`,
etc.) — the biggest net-new tunable surface with the clearest, most isolated effect (a gate
threshold either lets a candidate through or doesn't; no interaction with ML retraining). Prove
the harness produces backtest numbers that plausibly match live paper-trading results for the
SAME historical period before trusting it for anything else.

**Phase 3 — Promotion gate + Tune History table:** Wire the harness's output into the 4-check
gate (§3c) and the history table (§3d), still manually triggered (no scheduler yet). Run it
by hand for a few cycles, review the history table, confirm the gate is catching bad candidates
(deliberately feed it an obviously-bad parameter set as a negative test).

**Phase 4 — Extend to ML hyperparameters and sizing multipliers:** Only after Phases 1-3 are
proven, extend the same harness+gate+history pattern to ML tuning's secondary objective and the
sizing multiplier search — these have more moving parts and more ways to overfit than gate
thresholds, so they should be the last, not first, thing this loop touches.

**Phase 5 — Scheduler automation:** Only after a human has watched Phase 4 run manually enough
times to trust it, wire it into the weekly scheduled job. Until then, this stays a manually
triggered pipeline, matching the "never auto-promote silently" principle in a stronger form —
never auto-RUN silently either, until proven.

**Explicitly out of scope for now:** using an LLM or a meta-model to propose candidate parameter
sets (as opposed to grid/random/Optuna search over a known parameter space). That's a reasonable
future extension once the harness and gate are proven, but it adds a layer of opacity
("why did the model propose THIS value") on top of a system this design is specifically trying
to make more auditable, not less.

---

## 5. Open questions for review before implementation starts

1. Should the Backtest Harness replay `_scan_for_entries` directly (testing the real, current
   gate pipeline including all its known dual-scorer debt) or a cleaned-up reference
   implementation? Recommend the former for Phase 2 — the goal is validating changes to the
   system that actually exists, not a hypothetical cleaner one — and revisit once the
   dual-scorer tech debt (Tier 232, `T232-DL-DUALSCORER-DEBT`) is resolved.
2. How much historical data is actually available for a meaningful walk-forward split per
   style/market combination? HK data volume and quality was already flagged this session as
   thinner than US (T232-DATA1: LONG horizon has 8 outcome rows total). Phase 1 may need to
   start US-only, or with a relaxed minimum-sample-size, for HK.
3. Where does compute for a walk-forward Optuna sweep over gate thresholds run, and how long can
   it take before it competes with the production scheduler's other jobs on the same box? This
   needs a resource/timing budget before Phase 2 starts, not after.
