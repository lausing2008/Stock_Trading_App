# Production model inventory — first read-only run (M14)

**2026-10-02.** Read-only: 1,313 artifacts loaded, nothing written, no flags changed. Run with
the real `_compute_oos_suppression`, injected rather than reimplemented.

## Headline

| | |
|---|---|
| Artifacts | **1,313** (1,313 distinct hashes — no duplicates) |
| Suppressed | **1,218 (92.8%)** |
| Unsuppressed | **95 (7.2%)** |
| Read errors | **0** |
| Rule version | `r02-2026-09-28` |

**The fleet is in sync with its own rule.** `would_suppress: 0`, `would_unsuppress: 0` — a
resweep today would change nothing. That is the first direct evidence that
AUD-ML3-STALESUPPRESSION is actually closed, rather than reported closed.

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
but now measured across the whole fleet rather than inferred. A 92.8% suppression rate is what a
fleet trained on splits this small looks like when the rule is applied honestly. **The lever is
data, not rule-tuning**, and loosening thresholds to raise the serving count would simply
promote noise.

## What this does not establish

The inventory says what the artifacts *claim* and whether the rule is applied consistently. It
says nothing about whether the 95 serving models are any good — that needs forward outcomes, not
stored metrics.
