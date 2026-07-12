# Design: Promotion Gates for meta_trainer / position_scaling_gate

**Status:** Implemented 2026-07-12. Written to close out the remainder of
`SELFIMPROVE-PROMOTION-GATES-INCOMPLETE` (the promotion_gate.py/min_entry_score scheduling piece
was already split out and shipped separately as `SELFIMPROVE-PROMOTIONGATE-SCHEDULED`, 2026-07-12
— see that commit for the smaller, purely-additive fix). This document covers the two remaining,
harder gaps: `train_meta_model()` (ml-prediction) and `walk_forward_train()`/
`train_and_save_position_scaling_gate()` (market-data), both of which unconditionally overwrite a
live deployed model artifact with no comparison against what's currently running.

**Decisions from review (2026-07-12):**
1. §2.3's `MIN_AUC_IMPROVEMENT = 0.0` (reject only if strictly worse) — **confirmed**, with an
   explicit note to revisit once real `promotion_rejected`/`promoted` log volume accumulates.
2. §3.4's staged rollout for position_scaling_gate — **confirmed**: ships as shadow-log-only
   first (always saves regardless of the gate's verdict, just logs what it WOULD have done).
   Real enforcement (actually skipping the save) is a deliberate follow-up, not part of this pass.
3. §4 Q3 (where a rejected promotion surfaces) — **confirmed: both** `admin-health.tsx`'s
   `JOB_META` (consistent with every other calibration job's status already living there) AND a
   promoted/rejected history section on `signal-tuning.tsx` (more detail, since these are the
   first model-artifact-level, not threshold-level, entries on that page).

---

## 1. Why these two are a different, harder problem than promotion_gate.py's

`promotion_gate.py`'s gap was pure scheduling — the validation logic already existed
(`evaluate_and_record()`'s walk-forward EV-lift check), and the function was already safe to run
unattended because it explicitly never writes to live config. Scheduling it was zero-risk.

These two are different in a way that matters:

- **They already retrain and save unconditionally today.** Adding a gate is a real behavior
  change to a path that currently always succeeds (in the sense of "always produces a new
  file") — a gate can now cause a scheduled job to correctly do nothing, which is new behavior
  that itself needs to be observable, not just correct.
- **The artifact being gated is a live model file, not a Redis-cached scalar.** A bad promotion
  gate that never lets a new file through would silently freeze the model forever with no
  obvious symptom (unlike a threshold gate, where the current value is always visible and
  comparable in the dashboard).
- **The two components have very different real-world stakes right now**, which changes how
  much caution the design needs:
  - `train_meta_model()`'s output (`meta_model.joblib`) feeds `predict_meta()`, which IS
    consumed by the live prediction ensemble blend (`trainer.py`'s
    `predict_latest_ensemble_three`) — a regression here changes real signal generation.
  - `position_scaling_gate.joblib` is **only ever loaded in shadow mode**
    (`paper_trading_engine.py`'s position-scaling shadow-mode block, confirmed via
    `_check_position_scaling_gate_drift`'s own docstring and the module's own comment: "Saving
    a new model file has no effect on any live/paper decision by itself"). A regression here
    only degrades a shadow-mode log line, not a real trade. Lower stakes, and per the module's
    own docstring, this whole component doesn't even have a trustworthy training-data volume
    yet ("~12 real scale-in events... nowhere near enough for a trustworthy walk-forward
    split") — so a promotion gate here is guarding something not yet fully real to begin with.

This asymmetry means the two should NOT get an identical gate — `meta_trainer` needs a real
promotion gate with teeth; `position_scaling_gate` needs one, but can afford a softer rollout
given its shadow-only blast radius and its own documented immaturity.

---

## 2. `train_meta_model()` — proposed gate

### 2.1 What already exists to build on

`bundle["auc"]` is already computed and saved into the joblib bundle at every train
(`meta_trainer.py` line ~310). `predict_meta()` already reads it back and applies a hard `0.55`
inference-time floor (line ~361) — so there is already a notion of "is this bundle good enough to
use," just not "is this bundle better than what it's replacing."

### 2.2 The gate

Immediately before the `os.replace(tmp_path, META_MODEL_PATH)` atomic write:

```python
# Load the CURRENTLY DEPLOYED bundle's AUC (if one exists) before overwriting it.
previous_auc = None
if META_MODEL_PATH.exists():
    try:
        previous_bundle = joblib.load(META_MODEL_PATH)
        previous_auc = previous_bundle.get("auc")
    except Exception as exc:
        log.warning("meta_trainer.previous_bundle_unreadable", error=str(exc))
        # A corrupt/unreadable existing bundle is NOT a reason to refuse a new one — see §2.4.

MIN_AUC_IMPROVEMENT = 0.0  # see §2.3 for why this starts at 0, not a positive margin

if previous_auc is not None and auc < previous_auc - MIN_AUC_IMPROVEMENT:
    log.warning(
        "meta_trainer.promotion_rejected",
        new_auc=round(auc, 4), previous_auc=round(previous_auc, 4),
        n_samples=len(records),
    )
    return {
        "trained": True, "promoted": False, "n_samples": len(records),
        "auc": round(auc, 4), "previous_auc": round(previous_auc, 4),
    }

# ... existing os.replace() block, unchanged ...
log.info("meta_trainer.promoted", new_auc=round(auc, 4), previous_auc=previous_auc)
return {"trained": True, "promoted": True, "n_samples": len(records), "auc": round(auc, 4)}
```

`trained: True, promoted: False` is deliberately distinct from today's only failure shape
(`trained: False` for insufficient data) — a caller (or a future dashboard) needs to tell "we
didn't have enough data to even attempt a retrain" apart from "we retrained, but the result was
rejected," which are very different operational situations.

### 2.3 Why the improvement margin should start at 0.0, not a positive buffer

The obvious instinct is "require the new AUC to beat the old one by some safety margin (e.g.
+0.01)," mirroring `tune_style_profiles()`'s EV-lift-over-baseline pattern. Two reasons to NOT do
that here, at least initially:

1. **AUC on a held-out validation slice is noisy at this sample scale.** The validation split is
   `20%` of up to 20,000 rows (capped by the query's own `LIMIT 20000`), and the model retrains
   monthly — a tiny, arbitrary positive margin picked without real variance data would be
   security-theater, not a real bar. Requiring `new >= old` (not `new > old`, and not `new > old
   + margin`) is the correct MINIMUM bar: never accept a model that's measurably worse, but
   don't invent a margin with no empirical basis for what "measurably" should mean yet.
2. **A margin that's too strict has an asymmetric failure mode.** If the bar is too high, the
   model can never update again — every future retrain fails the same margin check forever,
   silently freezing a monthly retrain pipeline that's supposed to keep improving. This is
   exactly the "gate never lets anything through" failure mode flagged in §1 as the main new risk
   this design introduces. Starting at `MIN_AUC_IMPROVEMENT = 0.0` (reject only if strictly
   worse) is the conservative choice given that risk.

Once a few months of real `meta_trainer.promotion_rejected` / `meta_trainer.promoted` log volume
exists, revisit whether a small positive margin is justified by the actual observed AUC variance
— this is explicitly a "tune after observing," not a "guess a number now" decision, same lesson
already learned the hard way for `promotion_gate.py`'s worst-trade-regression tolerance (that
value — 10.0pp — was carried over from the original design doc's own example, not independently
derived; don't repeat that pattern here with an invented AUC margin).

### 2.4 Why an unreadable/corrupt previous bundle should NOT block a new one

If `META_MODEL_PATH.exists()` but `joblib.load()` raises (corrupt file, incompatible
sklearn/xgboost version after an environment upgrade, truncated write from a prior crash — this
codebase already documents `os.replace()`'s atomicity guard specifically because of a prior race,
see RACE-001), the correct behavior is to log a warning and proceed with the new bundle, not to
treat "can't read the old one" as "reject the new one." A promotion gate that fails closed on its
own read error would turn a corrupted file into a permanent retrain freeze — worse than the bug it
was built to prevent. This mirrors `hard_rejects.py`'s macro-blackout check failing open on
exception (same principle: a monitoring/gating mechanism's own failure should never be able to
block a decision path more thoroughly than a genuine negative result would).

### 2.5 What this does NOT change

`predict_meta()`'s existing `auc < 0.55` inference-time floor is untouched and still the last line
of defense — this gate only prevents a WORSE model from replacing a BETTER one; it says nothing
about whether either model clears the absolute bar `predict_meta()` already enforces
independently. The two checks are complementary, not redundant: this gate is relative
(new-vs-old), `predict_meta()`'s is absolute (new-vs-fixed-floor).

---

## 3. `position_scaling_gate` — proposed gate

### 3.1 What already exists to build on

`walk_forward_report()` already computes `mean_hit_rate` and
`mean_realized_return_across_folds` across all valid folds — this is the report the module's own
docstring says is used for "report, then decide" today. `train_and_save_position_scaling_gate()`
also already stores `training_mean_act_probability` in the saved bundle's `metadata` (added for
the drift-check job, T241-AUDIT-WALKFORWARD-VALIDITY) — so there's already a real precedent in
this exact module for "read back the previously saved bundle's stored metadata as a baseline,"
which the drift-check job (`_check_position_scaling_gate_drift`) already does today for a
different purpose (comparing LIVE shadow verdicts against the training-time baseline, not
comparing two successive trainings against each other).

### 3.2 The gate

Insert immediately before `final_gate.save(save_path, metadata={...})` in
`train_and_save_position_scaling_gate()`:

```python
import os
previous_report = None
if os.path.exists(save_path):
    try:
        previous_bundle = PositionScalingGate.load(save_path)  # or a raw joblib.load if metadata-only access is cheaper
        previous_metadata = joblib.load(save_path).get("metadata", {})
        previous_report = previous_metadata.get("walk_forward_report")
    except Exception as exc:
        log.warning("position_scaling_gate.previous_bundle_unreadable", error=str(exc))

new_hit_rate = report.get("mean_hit_rate")
previous_hit_rate = (previous_report or {}).get("mean_hit_rate")

# Only compare if BOTH the new and previous reports have a real hit rate to compare —
# an all-folds-skipped report (report["all_folds_skipped"] is True) has no hit_rate at all,
# and should neither block promotion (nothing to disprove the new model with) nor count as
# a pass (nothing proved it either) — see §3.3.
promoted = True
if new_hit_rate is not None and previous_hit_rate is not None and new_hit_rate < previous_hit_rate:
    promoted = False
    log.warning(
        "position_scaling_gate.promotion_rejected",
        new_hit_rate=new_hit_rate, previous_hit_rate=previous_hit_rate,
    )

if promoted:
    final_gate.save(save_path, metadata={..., "walk_forward_report": report, ...})
else:
    log.info("position_scaling_gate.retrain_kept_prior_model", new_hit_rate=new_hit_rate, previous_hit_rate=previous_hit_rate)

return {
    "trained": True, "promoted": promoted,
    ...  # existing fields unchanged
}
```

### 3.3 Why "insufficient data to compare" should default to PROMOTED, not rejected

Unlike `meta_trainer` (which has a real, large `signal_outcomes` table today),
`position_scaling_gate`'s own module docstring already states its real training data is only
~12 historical events — nowhere near `MIN_SAMPLES_PER_SPLIT=15` for even one fold. This means
`walk_forward_report()` returning `all_folds_skipped: True` (no `mean_hit_rate` at all) is the
EXPECTED, common case today, not a rare edge case. If the gate defaulted to "reject when there's
nothing to compare," it would block every single retrain indefinitely until real scale-in volume
accumulates — which, per the module's own docstring, requires a whole separate candidate-event-
mining data-engineering task not yet built. A gate that always rejects in the interim is
equivalent to freezing the shadow-mode model forever, which defeats the entire point of a WEEKLY
retrain job (the more candidate events get mined into existence over time, the better this model
should get — a permanently-frozen gate can never observe that improvement). Given the shadow-only
blast radius (§1), the safe default here is "promote when there's nothing conclusive to compare,"
mirroring `meta_trainer`'s §2.4 principle (a gate's own inability to compare should never be
treated as equivalent to a real negative result) but taken one step further because the stakes
here are genuinely lower.

### 3.4 Recommended staged rollout (given this component's own admitted immaturity)

Given position_scaling_gate's shadow-only status and the module's own docstring already flagging
its training data as not yet trustworthy, don't flip this gate straight to "skip the save" in one
step. Suggested staging:

1. **Week 1-4: shadow-log the gate's decision without acting on it.** Compute `promoted` as
   above, log it (`position_scaling_gate.promotion_would_have_rejected` when `promoted=False`),
   but always still call `final_gate.save(...)` regardless — exactly the same "shadow mode before
   trusting it" discipline already applied to the position-scaling gate ITSELF
   (`paper_trading_engine.py`'s shadow-mode block never acts on a verdict either). This produces
   real log volume on how often the gate WOULD fire, without any risk of it freezing the file.
2. **After reviewing 4+ weeks of shadow-logged decisions:** if the gate's reject rate looks sane
   (not rejecting every single week, which would suggest the comparison itself is broken) and a
   human has actually looked at a few rejected-vs-accepted walk-forward reports side by side to
   sanity-check the hit_rate comparison is measuring something real, THEN flip it to actually
   skip the save on rejection.

This two-step rollout is the one piece of this design that's a genuine judgment call rather than
something derivable purely from reading the existing code — flagging it explicitly rather than
silently picking step 2 immediately, since `meta_trainer`'s gate (§2) is being proposed as a
direct, non-staged change precisely because its data volume and stakes are both already real
today, whereas position_scaling_gate's aren't yet.

---

## 4. Decisions (resolved 2026-07-12)

1. **`meta_trainer`'s `MIN_AUC_IMPROVEMENT = 0.0`** — confirmed. Reject only if strictly worse
   (`new_auc < previous_auc`); no positive margin on day one. Revisit once real
   `promotion_rejected`/`promoted` log volume exists to see whether the data supports tightening it.
2. **`position_scaling_gate`'s rollout** — confirmed staged. This pass ships shadow-log-only:
   the gate computes and logs `promoted`/`would_have_rejected` on every retrain, but
   `final_gate.save(...)` always runs regardless of the verdict. Flipping to real enforcement
   (skip the save on reject) is an explicit, separate follow-up after a review window, not part
   of this implementation.
3. **Where a rejected promotion surfaces** — confirmed both. `admin-health.tsx`'s `JOB_META`
   gets an entry for each retrain job's promoted/rejected outcome (consistent with how every
   other calibration job's status already lives there), and `signal-tuning.tsx` gets a
   promoted/rejected history section for both models — the first model-artifact-level (not
   threshold-level) entries on that page.
