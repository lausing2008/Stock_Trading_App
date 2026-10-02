# Production model inventory — the CM finding and three narrowed conclusions (M14)

**NOT THE FIRST RUN.** The baseline is
[2026-10-01 production model and capability inventory](2026-10-01-production-model-and-capability-inventory.md),
which also ran the capability matrix across twelve services and found two defects in this
tooling. This document is the follow-up: the `CM_long` finding, and three conclusions of mine
that were too broad.

**2026-10-02.** Read-only: 1,313 artifacts loaded, nothing written, no flags changed. Run with
the real `_compute_oos_suppression`, injected rather than reimplemented.

## Headline

| | |
|---|---|
| Artifacts | **1,313** (1,313 distinct hashes — no duplicates) |
| Suppressed | **1,218 (92.8%)** |
| Unsuppressed | **95 (7.2%)** — *artifacts on disk, not confirmed serving models* |
| Read errors | **0** |
| Rule version | `r02-2026-09-28` |

**The fleet is in sync with its own rule.** `would_suppress: 0`, `would_unsuppress: 0` — a
resweep today would change nothing.

**NARROWED.** I first wrote that this showed the stale-suppression problem was closed. It shows
the fleet agrees with **today's rule** — not that the rule detects every unsuitable model.
`CM_long` is the proof of that distinction: perfectly consistent with the rule, and serving on
no evidence at all. Consistency and adequacy are different claims.

**Both invariants hold.** No invalid model is unsuppressed, and no resweep would unsuppress one.

## Why models are suppressed

| Reason | Count |
|---|---|
| `cv_auc_below_0.52` | 552 |
| `overfit_gap_magnitude` | 324 |
| `dead_recall` | 244 |
| `evaluation_not_out_of_sample` | 98 |

## Validity coverage

**97.0%.** 39 artifacts carry `evaluation_valid: None` — written before R02, so the property was
never measured. Counted separately rather than folded in with "valid": *never measured* and
*measured and fine* are different facts.

## The finding: a model serving with no quality evidence at all

`random_forest/CM_long.joblib` — trained 2026-09-28, **unsuppressed**, contributing at live
fusion weight:

```
auc 1.0   recall 1.0   precision 1.0   n_test 12
cv_auc_mean  None      overfit_gap  None      evaluation_valid  true
```

A perfect score on **twelve rows**, with no cross-validation metric and no overfit gap.

It is not suppressed because **every condition is unevaluable**, not because it passed any:

- `cv_auc_mean < 0.52` cannot fire on `None` — and this is the *primary* quality gate;
- `abs(overfit_gap) > 0.10` cannot fire on `None`;
- dead recall is trivially passed by a model that predicts every positive;
- the evaluation is marked valid.

**It is serving because nothing could measure it.** That is the opposite of what an unsuppressed
flag is taken to mean.

### Scope

- **4 artifacts** have both `cv_auc_mean` and `overfit_gap` absent.
- **185 artifacts (14%)** have `overfit_gap: None`, so the symmetric trust check is inert for
  them regardless of suppression state.
- Of **160** artifacts with a degenerate AUC (exactly 0.0 or 1.0), **159 are correctly
  suppressed** — this is the only one that is not.

### Not fixed here, deliberately

Suppressing on absent `cv_auc_mean` would stop four models contributing. That is a decision
about live signal, and the same `None`-means-unknown reasoning that *correctly* refuses to
mass-suppress for a missing `evaluation_valid` points the other way here — only four artifacts
are affected, and `cv_auc` is the primary gate rather than a secondary property. The population
is now reported by the inventory so the decision rests on evidence rather than on this argument.

## The constraint behind all of it

**1,311 of 1,313 artifacts have `n_test < 50`**, and the one above has 12.

This is the binding constraint already recorded in the quarterly checklist — not a new finding,
but now measured across the whole fleet rather than inferred.

**NARROWED.** "The lever is data, not rule-tuning" is incomplete, and loosening thresholds to
raise the serving count would certainly promote noise — but *more rows* is not automatically the
fix either. More rows may simply add **correlated** observations, which raises the count without
adding independent evidence. What actually needs investigating, before anyone buys more history:

- history retrieval — how much is actually fetched per symbol, and what fails;
- feature attrition — rows lost between load and fit (this is what M13's ledger measures);
- label availability — how many rows have a mature label at training time;
- split allocation — how 70/80/90 divides what survives;
- **independent event counts** — how many of the remaining rows are genuinely independent.

A 92.8% suppression rate is what a fleet trained on splits this small looks like when the rule is
applied honestly. Which of the five causes above dominates is unmeasured.

## What this does not establish

**95 unsuppressed artifacts are not 95 serving models.** That is a directory count. Legacy and
horizon-specific filenames coexist, and which artifact a consumer actually selects at inference
is a separate question this inventory never asked. Active consumer selection must be counted
separately before any statement about what is contributing to live signal — including anything
about `CM_long`'s actual influence.

The inventory says what the artifacts *claim* and whether the rule is applied consistently. It
says nothing about whether any serving model is any good — that needs forward outcomes, not
stored metrics.

## Three states, not one

The `CM_long` case forced a distinction the tooling did not previously make:

| Question | Answered by | For `CM_long` |
|---|---|---|
| **Validity** — was the evaluation free of known leakage? | `evaluation_valid` | true |
| **Evidence sufficiency** — were enough independent observations and diagnostics available? | `n_test`, `cv_auc_mean`, `overfit_gap` | **no** |
| **Promotion eligibility** — did it beat a baseline? | forward outcomes | **unknown** |

`evaluation_valid: true` answers only the first. The inventory now reports
`serving_on_insufficient_evidence` with the specific missing diagnostics.

**An insufficiency reason is not an accuracy claim.** Any production restriction on `CM_long`
must say *insufficient evidence*, never that the model was shown to be inaccurate — nothing has
shown that.

### Before changing anything about CM

1. Resolve which artifact path a consumer actually selects for that symbol and horizon.
2. Measure its real ensemble weight and the fallback behaviour if it abstains.
3. Compare current inference against an abstaining/neutral CM in **shadow**, before any
   restriction.
