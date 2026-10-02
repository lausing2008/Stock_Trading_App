# Training sample-size trace: storage, filtering and evaluation

Date: October 1 PDT; read-only production snapshot October 2, 2026 03:48 UTC. No retraining, resweep, model mutation, policy change or deployment. This is a targeted CM/MU trace, not a fleet-wide causal attribution.

## What production establishes

| Artifact | Saved training rows | Calibration rows | Saved `n_test` | Actual final report rows | Evidence |
|---|---:|---:|---:|---:|---|
| CM LONG Random Forest, trained Sep 28 | 218 | 11 | 12 | Not recorded in selected metadata | AUC/precision/recall 1.0; CV AUC/gap absent; unsuppressed; validity true |
| MU GROWTH XGBoost, trained Oct 1 | 289 | 26 | 27 | **14** | CV AUC .352; test AUC .077; suppressed; validity true |
| MU LONG XGBoost, trained Oct 1 | 285 | 21 | 21 | **11** | CV AUC .570; test AUC .100; suppressed; validity true |
| Legacy MU SWING LightGBM, trained Sep 7 | 287 | 31 | 32 | Not recorded in selected metadata | Unsuppressed; evaluation-validity metadata absent |

Both CM and MU have **755 stored daily bars**, spanning September 28, 2023 through October 1, 2026 in the current five-year query. This does not prove all 755 existed or were usable at each historical training time. The expected `MU_swing.joblib` filename was absent; the legacy `MU.joblib` exists. This probe did not establish runtime selection.

**Important metric distinction:** current trainer stores `n_test=len(X_test)` before its threshold/report subdivision, but computes final AUC/precision/recall on `y_test_report` when held-out reporting is possible. `threshold_report_rows` is the relevant final metric denominator. Therefore “almost all artifacts have n_test below 50” does not describe their exact reporting sample; it can be substantially smaller. CM's older artifact cannot be assigned today's split semantics without checking its producing code version.

## Code path explaining the pressure

Current `services/ml-prediction/src/training/trainer.py` and `features/builder.py`:

1. `_load_prices` asks for up to five calendar years of stored D1 rows. It does not fetch missing vendor history during training.
2. `train_model` excludes today's bars, then builds features and forward labels.
3. `build_features` drops rows missing required daily features, rows whose horizon-forward label is unavailable, and **dead-zone rows** whose absolute future return falls below the fitted threshold. Optional fundamental/weekly/sector/outcome/options NaNs are explicitly allowed; missing optional features do not automatically drop every row.
4. The outcome-augmentation/dedup path can change the usable base population and add weighted training observations. M13's new ledger measures this augmentation branch, not the complete raw-price-to-final-evaluation funnel.
5. Base rows are allocated approximately 70% training, 10% early stopping, 10% calibration and 10% test. Each later block loses an affordable embargo, targeting SHORT=5, SWING=10, GROWTH=15 and LONG=20 rows.
6. Test rows are split again into threshold-selection and final reporting halves when enough reporting rows/classes remain. Missing support triggers a labeled in-sample fallback, which current validity logic disallows for promotion.

Illustration, not measured artifact reconstruction: with 400 usable base rows, each 10% block has 40 rows. A full 15-row GROWTH gap leaves 25 test rows, of which 13 report performance. A full 20-row LONG gap leaves 20, of which 10 report. Small final samples can thus arise even without a broken ingestion job.

## What remains unmeasured

Exact loss attribution for historical CM/MU fits requires saved stage counts or a faithful replay of their original point-in-time inputs. Current storage and saved final metrics do not establish how many rows were lost to warm-up, missing required fields, unavailable labels, dead-zone filtering, dedup, or incomplete history at that time. Do not subtract current 755 rows from `n_train` and call the difference feature loss: training deliberately receives only one slice, and augmentation may change that count.

Likewise, absent CV AUC may reflect skipped single-class training/validation folds rather than no CV execution. The code permits both. CM's actual fold sequence has not been reconstructed. `evaluation_valid=true` does not establish adequate class support or independent evidence.

## Next instrumentation and design work

Persist one base-training ledger alongside the augmentation ledger:

- Requested history window, source coverage, raw/unique bars, session gaps and completed-bar cutoff.
- Required-feature eligibility by reason, optional-feature coverage, horizon label availability and dead-zone inclusion.
- Sequential first-exclusion counts for reconciliation; overlapping diagnostic reasons stored separately, never summed as disjoint losses.
- Pre/post dedup base count and separately weighted augmentation count, with actual label availability at the training cutoff.
- Every chronological slice before/after embargo, its actual dates, and class counts.
- Threshold-selection count and **final report count/date range**, not only `n_test`.
- Each CV fold's training/validation class counts, skipped reason and usable AUC status.
- Unique sessions/events/symbols and overlap diagnostics; rows are not automatically independent bets.

The target also needs explicit interpretation: dead-zone training learns direction **conditional on a sufficiently large future move**. Inference cannot know in advance which future moves qualify. Evaluate operational prediction on the full eligible opportunity population too, or introduce a separately validated move/abstention model. Do not label the conditional probability as unconditional chance of a profitable trade.

Potential improvements to compare, rather than activate from this audit:

1. Extend verified, adjusted historical coverage where actually missing; distinguish older regime relevance and corporate-action quality from raw row count.
2. Use walk-forward/out-of-fold development predictions for calibration/threshold research while preserving an untouched, adequately sized final period and label-availability purges. Do not reclaim rows by removing leakage protection.
3. Set evidence requirements by final reporting support, classes and uncertainty. Replace ambiguous `n_test` presentation with separate total, threshold and report counts; retain legacy field semantics for compatibility.
4. Consider pooled/hierarchical models for sparse per-symbol horizons only as a controlled study with group/time leakage controls and point-in-time universes.
5. Continue CM current-versus-neutral shadow evaluation after establishing serving path and weight. No suppression rule was changed here.

## Evidence

[Read-only probe](evidence/2026-10-01-training-sample-probe.py) and [captured production metrics](evidence/2026-10-01-training-sample-results.json), including artifact hashes. Current source was inspected to explain mechanisms; no full historical training reproduction or forward performance claim is made.
