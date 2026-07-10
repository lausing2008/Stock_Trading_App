# Implementation prompt: conviction-based position scaling enhancement

Paste this into a Claude Code session at the start of each phase. Run phases in
order; each is scoped to fit in one focused session. Do not skip ahead to a
later phase in the same session even if it seems fast — the acceptance
criteria at the end of each phase are what keeps this from becoming an
unreviewable pile of changes.

---

## Context to give Claude at the start of every session

```
I have an existing algorithmic trading platform with these modules already
built and working: technical analysis, AI signal generation, market regime
engine, decision engine, ML prediction, email alerts, model training,
strategy optimizer, portfolio manager, paper trading, backtesting.

I'm adding a new capability: conviction-based position scaling (adding to a
position on a pullback ONLY when independent evidence still supports the
original thesis, gated through a meta-labeling model and a thesis-persistence
check, not just "price dropped so buy more").

Reference architecture: [attach the Word/PDF doc or paste the section
relevant to this phase]
Reference code (starting scaffolding, not final): [attach
triple_barrier_labeling.py / thesis_persistence_gate.py / meta_labeling_gate.py
as relevant to this phase]

Before writing code: read my existing [module name] implementation first and
tell me how it currently structures [data schema / signal output / trade log]
so we integrate cleanly instead of duplicating logic. Ask me before assuming
a schema I haven't shown you.
```

---

## Phase 0 — Codebase orientation (do this once, before Phase 1)

**Goal:** Claude understands your actual codebase well enough that later
phases don't invent conflicting schemas.

**Ask Claude to:**
- Read and summarize the existing trade log / position schema (however you
  currently store entries, fills, position state).
- Read and summarize the existing backtesting engine's trade simulation loop
  — specifically whether it currently supports multiple entries into the same
  position (tranches) or only single entry/exit pairs.
- Read and summarize the output format of your current ML prediction model
  and AI signal module (what fields, what probability/confidence scale).
- Read and summarize your regime engine's output format.
- Produce a short written summary of gaps between what exists and what the
  reference architecture assumes, before any code is written.

**Acceptance criteria:** You get a written gap analysis, not code. Review it
yourself before moving to Phase 1 — this is the cheapest point to catch a
wrong assumption.

---

## Phase 1 — Backtesting engine: path-dependent multi-tranche support

**Why this is first:** every later phase's validation depends on the
backtester being able to simulate scaling-in correctly. Building the
meta-model before this exists produces backtest results you can't trust.

**Goal:** Extend the backtesting engine so a single logical position can
consist of multiple tranches (entries), each with its own entry price,
timestamp, and size, with:
- A running weighted-average cost basis recalculated after each tranche.
- Per-tranche and whole-position P&L tracking.
- Realistic slippage modeling on each tranche (later/smaller tranches likely
  fill worse than the first).
- Support for re-evaluating exit barriers (profit target / stop-loss / time
  limit) against the *current* weighted average cost basis, not the original
  entry price.

**Deliverables:**
- Updated backtesting engine code with multi-tranche position objects.
- Unit tests covering: single-tranche position (must match old behavior
  exactly — regression test), two-tranche add reducing average cost basis
  correctly, three-tranche position hitting stop-loss on the blended basis,
  slippage applied per tranche.
- A short written note on any backtesting API changes that affect strategies
  currently using the single-entry assumption.

**Acceptance criteria:** All existing single-entry backtests produce
identical results to before (no regression). New multi-tranche test cases
pass with cost-basis math you've manually verified by hand for at least one
example.

---

## Phase 2 — Triple-barrier labeling pipeline

**Reference code:** `triple_barrier_labeling.py`

**Goal:** Build the pipeline that turns your historical trade log into
labeled training data for the meta-labeling model.

**Tasks:**
- Adapt `compute_barriers`, `label_single_event`, and `build_labeled_dataset`
  to your actual price history storage (however you currently fetch OHLC
  data — file, database, API).
- Extend labeling to multi-tranche events specifically: for every historical
  point where an add *could* have happened (not just where one *did*), label
  whether adding was the better choice versus holding versus exiting. This
  requires re-running the barrier simulation from each candidate add point
  using the Phase 1 multi-tranche backtester.
- Store the labeled dataset in a format you can version (e.g., dated parquet
  files) so you can compare label distributions across retraining cycles.
- Write a sanity-check report: label balance (% add-correct vs not),
  distribution across regimes, distribution across time periods — flag if
  any period or regime is wildly overrepresented.

**Acceptance criteria:** You have a labeled dataset with a written sanity
report, and can point to at least 3 spot-checked examples where you manually
agree with the label given the price path.

---

## Phase 3 — Meta-labeling gate: training pipeline

**Reference code:** `meta_labeling_gate.py`

**Goal:** Train and validate the meta-labeling classifier.

**Tasks:**
- Adapt `FEATURE_COLUMNS` and `compute_features_for_event` to pull real
  values from your regime engine, primary ML model, and technical analysis
  module outputs (per the Phase 0 gap analysis).
- Implement the walk-forward training loop referenced in the architecture
  doc: rolling window train/validate splits, never a random shuffle.
- Train `MetaLabelingGate` on each window, collect out-of-sample predictions,
  and compute Sharpe ratio, max drawdown, and hit rate on the validation
  folds — not just classification accuracy.
- Run `feature_importances()` and report the ranking. Flag it explicitly if
  `current_drawdown_pct` dominates — that means the model has re-learned
  naive averaging down and the feature set or labels need revisiting before
  proceeding.
- Save the trained model with versioning (date, training window, validation
  metrics) so you can roll back if a later retraining is worse.

**Acceptance criteria:** A written validation report with walk-forward
Sharpe/drawdown/hit-rate per fold, the feature importance ranking with your
sign-off that it isn't just rediscovering price-based averaging, and a saved,
versioned model artifact.

---

## Phase 4 — Thesis persistence gate integration

**Reference code:** `thesis_persistence_gate.py`

**Goal:** Wire the rules-based circuit breaker into your live decision flow.

**Tasks:**
- Adapt `ThesisSnapshot` fields to match what your regime engine, primary
  signal, and technical analysis modules actually output.
- Add snapshot capture at entry time to your existing entry/order-placement
  code path.
- Add a scheduled or event-driven re-evaluation call to `ThesisPersistenceGate.check()`
  for every open position, wired to run *before* the meta-labeling gate is
  even called (cheap check first, expensive model second).
- Decide and implement what happens on each `recommendation` value
  (`allow_add`, `hold_only`, `consider_exit`) in your decision engine —
  this is a business logic decision, not just plumbing, so confirm the
  mapping with me before finalizing.

**Acceptance criteria:** A paper-traded position going through entry, an
adverse price move, and a re-evaluation shows the correct snapshot diff and
recommendation in logs. At least one test case where a regime flip correctly
triggers `consider_exit`.

---

## Phase 5 — Position sizing and portfolio integration

**Goal:** Connect the meta-model's `suggested_size_multiplier` to your
existing position sizing / portfolio manager code.

**Tasks:**
- Add portfolio-level caps: max tranches per name, max sector exposure,
  volatility-adjusted base tranche size.
- Make sure the sizing engine only receives a sizing request *after* both the
  thesis persistence gate and meta-labeling gate have approved — confirm this
  ordering is enforced in code, not just convention.
- Add circuit-breaker logging: every time a cap blocks or reduces a suggested
  add, log why, so you can audit whether caps are firing sensibly over time.

**Acceptance criteria:** A paper-traded multi-tranche scenario respects the
tranche cap and sector exposure cap, with clear logs showing each gate's
decision in sequence (regime → signal → meta-label → thesis check → sizing).

---

## Phase 6 — Shadow deployment and monitoring

**Goal:** Run the new pipeline in shadow mode against live data without it
controlling real (or even paper) capital yet.

**Tasks:**
- Add a shadow-mode flag that runs the full pipeline, logs what it *would*
  have done, but leaves existing decision logic in control of actual paper
  trades.
- Build a simple comparison report: shadow-mode decisions vs. what the
  existing system actually did, over a rolling window.
- Add model-decay monitoring: track the meta-model's live prediction
  distribution vs. its training-time distribution, and alert if they drift
  meaningfully (this is your signal that a retrain is due).

**Acceptance criteria:** A running shadow-mode report you can review weekly
before deciding whether to let the new pipeline start controlling paper
trades for real.

---

## Things to remind Claude of in every phase

- Preserve backward compatibility with existing single-entry strategies
  unless explicitly told to migrate them.
- Don't let the meta-model be the only thing standing between a bad add and
  real capital — the thesis persistence gate is a deliberate, cheaper,
  rules-based backstop and should stay that way, not get folded into the ML
  model "for simplicity."
- Ask before assuming a data schema, file format, or API contract that
  wasn't shown to it in-session.
- Write tests alongside code, not after — especially for the cost-basis math
  in Phase 1, since a silent error there corrupts every phase built on top.
