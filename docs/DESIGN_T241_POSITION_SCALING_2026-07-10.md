# T241 — Conviction-Based Position Scaling: What It Is, How It Fits In, How To Use It

**Status:** All 6 phases from the original design doc are built and deployed. Currently
**SHADOW MODE ONLY** — the feature evaluates real positions and logs what it would do, but
has never placed a real order and cannot touch real capital until a portfolio is
explicitly switched to a live mode that does not exist yet (see §5).

Reference docs: `Improvements/Position_Scaling/AI_Investment_Position_Scaling_Architecture.pdf`
(the original architecture doc) and `Improvements/Position_Scaling/implementation_prompt.md`
(the phase-by-phase build spec this feature followed). Tracker entries: search
`frontend/src/pages/improvements.tsx` for `T241`.

---

## 1. What problem this solves

The paper trading engine already has one way to add to a winning position: **scale-in**
(`paper_trading_engine.py`, the `SCALE_IN` block inside `_scan_for_entries()`) — a fixed
25%-of-position add, triggered once a position is already up 5%+ with confidence ≥60. That
mechanism is deliberately untouched by T241 and keeps working exactly as before.

T241 targets the **opposite, harder case**: a position that has moved *against* you — a
pullback below your cost basis — and a fresh BUY signal is still firing on it. Naively
"buying more because it dropped" is a martingale: it makes your biggest bet exactly when
the trade is going worst, and a single regime-shift event can produce outsized losses. The
alternative this feature builds is **conviction-based averaging-in**: add to a losing
position only when (a) a trained model, looking at real market/signal context, thinks this
specific pullback is worth backing, AND (b) a separate, deterministic rules check confirms
the *original reasons you bought this stock* haven't broken.

---

## 2. Where it sits in the existing pipeline

```
Signal fires (BUY, price below cost basis, position already open)
        │
        ▼
┌─────────────────────────────┐
│ Position-Scaling Gate        │  "Is this pullback worth backing, and how much?"
│ (trained classifier)         │  → act_probability, suggested_size_multiplier
└──────────────┬───────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Thesis Persistence Gate      │  "Do the ORIGINAL reasons I bought this still hold?"
│ (rules-based circuit breaker)│  → allow_add / hold_only / consider_exit
└──────────────┬───────────────┘
               │
               ▼
   [ Phase 5/6: SHADOW MODE — logs the verdict, resolves it against the real
     subsequent price ~20 days later, never places a real order ]
               │
               ▼
   [ NOT BUILT YET — a future "live" mode would place a real add here,
     sized via sizer.py / paper_trading_engine.py, only once shadow data
     has been reviewed and trusted ]
```

This is a hierarchical gate, not two independent votes averaged together — the model has
to say "act," AND the rules-based thesis check has to still agree, before anything could
ever proceed to sizing. The thesis-persistence gate exists specifically so a silently-decaying
model doesn't leave the system with zero protection: even if the trained classifier drifts
or degrades, the deterministic circuit breaker still catches an obvious thesis break (a
regime flip, a broken support level, signal confidence collapsing, the stock underperforming
its own sector after previously leading it).

**Every part of this is currently READ-ONLY with respect to real trading.** No code path
exists today that lets this feature place an order, touch `portfolio.current_cash`, or
change `PaperTrade.shares`. The only thing it writes is log lines and Redis-backed shadow
records for later review.

---

## 3. The pieces, in build order

| File | What it does |
|---|---|
| `services/market-data/src/backtest/multi_tranche_engine.py` | Pure position-math: given a list of tranches (fills), computes the weighted-average cost basis and evaluates ATR-based profit/stop barriers against it. No DB, no live wiring — a simulation library. |
| `services/market-data/src/backtest/triple_barrier_labeling.py` | Given a historical candidate "could we have added here," runs two parallel simulations (with the add, without it) through the engine above and labels whether adding would have been the better choice. This is how training labels are generated. |
| `services/market-data/src/backtest/candidate_event_mining.py` | Finds the actual historical candidate events in the DB (real BUY signals arriving below an open position's cost basis, across all 4 trading horizons), computes the real feature set for each (drawdown depth, sector correlation, realized-vol percentile, regime, etc.), and orchestrates the full mine → label → train → save pipeline. Also has the LIVE counterpart used for real-time shadow predictions, `compute_live_features_for_position()`, built to reuse the exact same math as the offline training path. |
| `services/market-data/src/backtest/position_scaling_gate.py` | The trained classifier itself (`PositionScalingGate`) — a calibrated gradient-boosted model that outputs `act_probability` and a smoothly-scaled `suggested_size_multiplier`. Has `save()`/`load()` for persisting a trained model to disk (`joblib`, atomic writes). |
| `services/market-data/src/backtest/thesis_persistence_gate.py` | The rules-based circuit breaker (`ThesisPersistenceGate`). Snapshots what justified the original entry (`ThesisSnapshot`); on every re-check, diffs the current state against that snapshot and recommends `allow_add` / `hold_only` / `consider_exit` based on how many original conditions have broken. |
| `services/market-data/src/services/paper_trading_engine.py` | The live wiring. Inside `_scan_for_entries()`, right after the existing scale-in block, there's a shadow-mode block (search `T241-P5-SHADOW`) that: computes live features, loads the saved model, gets a prediction, runs the thesis check, logs the verdict, and records it for later resolution. Also has `resolve_position_scaling_shadow_verdicts()` (search `T241-P6`), which checks old pending verdicts against what actually happened. |
| `services/market-data/src/services/scheduler.py` | Three scheduled jobs: weekly retrain (`_retrain_position_scaling_gate`, Sundays 04:00 UTC), daily verdict resolution (`_resolve_position_scaling_shadow`, 05:00 UTC), and weekly model-decay drift check (`_check_position_scaling_gate_drift`, Sundays 04:30 UTC). |
| `services/market-data/src/api/paper_portfolio.py` | `GET /paper-portfolio/position-scaling-shadow` — the comparison report endpoint. |
| `frontend/src/pages/paper-portfolio.tsx` | The "Position Scaling" tab, which displays that report. |

---

## 4. How to use it today

### 4.1 Checking whether a model exists and what it looks like

The model file lives at `{model_dir}/position_scaling_gate.joblib` (production: `/data/models/`).
It does not exist until `train_and_save_position_scaling_gate()` has run at least once — either
via the weekly scheduled job, or manually:

```python
# Run inside the market-data container
from db import SessionLocal
from common.config import get_settings
from pathlib import Path
from backtest.candidate_event_mining import train_and_save_position_scaling_gate

settings = get_settings()
save_path = str(Path(settings.model_dir) / "position_scaling_gate.joblib")
with SessionLocal() as session:
    result = train_and_save_position_scaling_gate(session, save_path)
print(result)  # n_candidates, n_stocks, walk_forward_report, feature_importances, training_mean_act_probability
```

This takes ~2-6 minutes (mines ~1200+ real historical candidate events across all 4 trading
horizons, labels them, trains, and saves). As of the last real run (2026-07-10, after fixing a
training-data-leakage bug found in a deep audit — see the `T241-AUDIT-WALKFORWARD-VALIDITY`
tracker entry): 1227 candidates, 112 stocks, 84.9% mean walk-forward hit rate, `current_drawdown_pct`
still the single largest feature (35.4%) but no longer dominant — `sector_correlation` (18.0%) and
`realized_vol_percentile` (13.7%) both carry real, validated weight.

### 4.2 Enabling shadow mode on a portfolio

**This is the only "on" switch that exists today.** It never places a real order — it only
starts logging what the gate *would* have done, for later review.

```python
# Run inside the market-data container, or via a DB migration/admin action
from db import SessionLocal, PaperPortfolio
from sqlalchemy import select

with SessionLocal() as session:
    p = session.execute(select(PaperPortfolio).where(PaperPortfolio.id == <portfolio_id>)).scalar_one()
    cfg = dict(p.config or {})
    cfg["position_scaling_mode"] = "shadow"   # default is "off"
    p.config = cfg
    session.commit()
```

Once enabled, every scan cycle (roughly every 5 minutes during market hours) that finds an
open position sitting below its cost basis with an active BUY signal will:
1. Compute live features for that position.
2. Load the saved model and get a prediction.
3. Run the thesis-persistence check against the position's original entry snapshot.
4. Log a `paper.position_scaling_shadow` line with the full verdict.
5. Record ONE verdict per symbol+portfolio+calendar-day (deduped — this was a real bug,
   fixed in the audit pass, that used to record ~60-80 near-identical verdicts per day per
   position) into Redis (`ps:shadow:pending`), to be checked against the real outcome later.

To turn shadow mode back off, set `position_scaling_mode` back to `"off"` the same way.

### 4.3 Reading the shadow-mode comparison report

Two ways to see it:

- **UI**: the "Position Scaling" tab on the Paper Portfolio page (`/paper-portfolio`) — shows
  pending/resolved counts, overall hit rate, would-act-specific hit rate, and full verdict
  tables with symbol, act probability, thesis recommendation, and (once resolved) the
  subsequent return and whether the prediction was correct.
- **API**: `GET /paper-portfolio/position-scaling-shadow?limit=200` (requires a logged-in
  user session — same auth as every other paper-portfolio endpoint).

A verdict moves from "pending" to "resolved" once its ~20-day holding window has passed (the
daily `_resolve_position_scaling_shadow` job handles this automatically) — at that point it
gets an `outcome_correct` flag based on whether the position's actual subsequent return matched
what the `would_act` prediction implied.

**Important interpretation caveat, from the audit:** the shadow hit rate measures something
real but narrower than the training-time hit rate — it's a point-in-time check ("did the stock
move up more than 0.5% by day ~20"), not the full barrier-based counterfactual used in training.
There's also no baseline shown yet for "what hit rate would a model that always says no achieve
on these same candidates" — a genuinely high shadow hit rate should be read cautiously until
that baseline exists. Don't treat the shadow hit rate alone as sufficient grounds to go live;
review the actual verdicts (the "Resolved Verdicts" table) for whether the calls make sense.

### 4.4 Model-decay drift monitoring

Runs automatically every Sunday at 04:30 UTC (right after the weekly retrain). Compares the
average `act_probability` of shadow verdicts from the last 7 days against the model's own mean
predicted probability on its training set (stored at training time). If they've drifted apart
by more than 0.15 (absolute probability), it logs a `position_scaling_gate.drift_detected`
warning — the actionable response is "an earlier-than-scheduled retrain may be warranted,"
nothing more automated than that today.

---

## 5. What does NOT exist yet (do not assume otherwise)

- **No "live" mode.** There is no config value, flag, or code path that lets this feature place
  a real add, adjust `portfolio.current_cash`, or change `PaperTrade.shares`/`entry_price`. Going
  live would require: (a) deciding how the model's `suggested_size_multiplier` composes with the
  existing sizing multipliers in both `sizer.py` (decision-engine) and `paper_trading_engine.py`'s
  inline sizing — these are two independently-diverged implementations today, not mirrors of each
  other, and any new multiplier has to be added to both in the same change; (b) an explicit,
  deliberate decision to trust the shadow data collected so far.
- **No automatic promotion from shadow to live.** Nothing watches the shadow hit rate and flips a
  portfolio over on its own. Turning shadow mode on, and any future decision to go live, are both
  manual, explicit actions.
- **No portfolio currently has shadow mode enabled** as of this writing (2026-07-10) — it was
  briefly turned on for 3 portfolios during development to verify the wiring worked, then
  explicitly turned back off. Starting to collect real shadow data is a decision that hasn't
  been made yet.

---

## 6. Known limitations to keep in mind

- The mined candidate universe (~1200 events) is still small by ML standards; the weekly retrain
  will grow it over time as more real signals accumulate.
- `existing_position_pct_of_portfolio` is currently an inert placeholder feature (a constant
  0.05 in training) — it's not yet computed from real portfolio-sizing context, so it carries
  zero predictive weight today. The live-inference path computes a real (if not fully correct —
  see the audit's finding #4) value, but since it's not what the model was trained on, this
  divergence is currently harmless.
- `regime_is_favorable` carries very little predictive weight (~1-3% feature importance) in the
  current model — it's the weakest of the design doc's originally-envisioned "criticality-ranked"
  components, likely because the current feature is a single categorical regime label rather
  than the confidence-weighted regime vector the original architecture doc's section 3.1 assumed
  (a genuine 4-state HMM posterior does exist elsewhere in this codebase, `hmm_regime.py`, but
  isn't wired into this feature).
