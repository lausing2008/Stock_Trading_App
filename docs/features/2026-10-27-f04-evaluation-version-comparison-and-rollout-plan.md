# F04 — Evaluation versions, reporting allocation comparison and rollout plan

Date: **October 27, 2026, America/Los_Angeles**. Status: **design only; F04 remains held on `f04-threshold-report-purge`**. Source baseline: `c88fdc6f`. No training, inference-policy change, artifact replacement, suppression change or deployment is part of this document.

Related: [F04 in the full audit](../audits/2026-10-26-little-rzy-ai-signals-full-review.md), [initial impact probe/results](../audits/evidence/2026-10-26-rzy-ai-intraday-review/f04-impact.json), [deadlock rollout](../audits/2026-10-27-signal-upsert-delivery-deadlock-review.md).

## Decision

Do not redeploy the isolated gap patch or choose between validity and a larger test slice as if they were alternatives. **Versioned evidence is required in either case.** Compare reporting allocations offline, measure actual routing/coverage, then promote a bounded set of qualified artifacts under an explicit serving policy.

Separate three controls:

1. **Evaluation validity:** what protocol, data and independent dates support a metric?
2. **Replacement eligibility:** may this candidate replace the currently selected artifact?
3. **Serving eligibility:** may this artifact contribute to a prediction under today's policy?

A legacy `evaluation_valid=True` cannot answer all three. An invalid candidate must not destroy the last usable artifact, but that does not entitle an old artifact to remain “v2-validated” indefinitely. Keep both artifacts and select explicitly; never solve the problem by overwriting a good model with a suppressed candidate solely to change a flag.

## What is known, and what must be refreshed

The original read-only inventory covered **1,435 artifacts**, including **932** labelled `holdout`. Applying the proposed gap to their stored test sizes made **590** fail the 10-row reporting floor. **38 of 109 unsuppressed artifacts** were in that group. These are results of that captured inventory, not current live-model counts. Row sufficiency is necessary, not sufficient: the probe could not establish class support after purging.

Current trainer behavior (`training/trainer.py`) preserves an incumbent with a truthy `metrics.evaluation_valid` when the candidate is invalid. It does not compare protocol versions. Single and ensemble inference use artifact suppression flags, with three-model → two-model → single-model fallback at the signal-engine caller. Artifacts can therefore be present or unsuppressed without being the exact set contributing to a given request.

Before fitting anything, refresh a **read-only inventory** with artifact identity/hash, family, saved/requested style, horizon, training time, observed evaluation fields, suppression/reason, row/class counts where stored, and resolver path. Report separately:

- Stored artifacts and flags.
- Potential eligible contributors per requested stock/style and per API path.
- Observed actual contributors from prediction provenance, where available.
- Missing/ambiguous provenance; never infer exact live contribution from a filename or suppression flag.

Do not invoke training endpoints, update bundles, clear caches or call prediction APIs that may fetch external features merely to collect this inventory. If exact contribution cannot be recovered from existing data, add a separately reviewed metadata-only provenance phase first.

## Evaluation contract

Proposed protocol identity: **`daily_directional_threshold_report_v2`**, with the exact definition and implementation hash frozen before use. A new protocol record is additive and immutable; existing v1 metrics remain historical evidence under their original labels.

| Field | Required meaning |
|---|---|
| Artifact/run identity | Artifact SHA-256, model family, stock/market, requested and saved style, feature/label versions, horizon in trading bars, trainer/source/config/input hashes |
| Protocol identity | Protocol ID/version and evaluator code hash; evaluation type (`retrospective_split`, `outer_walk_forward`, `forward_frozen_artifact`) |
| Information boundaries | Feature dates and known-at times; each label's actual maturity date; all fit, early-stop, probability-calibration, threshold-selection and reporting ranges |
| Purge evidence | Intended and actual exclusions at every boundary, including threshold → report; no partial-gap success; actual maturity checks rather than an unaudited counter alone |
| Sample accounting | Loaded/surviving rows, dead-zone exclusions, split/purge rows, class counts, predicted-action counts, distinct dates; every excluded/insufficient population retained |
| Metrics | Metric name/unit, exact cohort, valid/invalid/unknown status, reason and uncertainty; AUC, precision and probability calibration have distinct validity fields |
| Policy decision | Serving/replacement policy version, decision/reason, selected artifact IDs and fallback path; separate from the evaluation itself |

Required statuses are **`valid_for_protocol`**, **`invalid_for_protocol`**, and **`unknown_or_legacy`**. Missing metadata is unknown, not passed. Invalid is reserved for evidence that fails a defined requirement. A manually added version field cannot retroactively validate old metrics.

`n_report_purge_rows >= horizon` is useful evidence, but check actual label maturity too. Dead-zone filtering and missing bars mean surviving row positions and exchange sessions are not interchangeable. Threshold-selected precision/accuracy and threshold-independent AUC must not be conflated: F04 directly weakens the former; any claim about the latter also needs its own train/calibration/report boundaries.

The current **10 reporting rows** remain a feasibility floor for comparison, not statistical proof. Preserve existing quality gates; do not lower them to recover coverage. Promotion additionally needs adequate class/action support and uncertainty. Unsupported/inconclusive cells remain visible and unqualified.

## Incumbent and serving policy

Store research candidates **outside the production model directory**, for example in a separate research artifact root with a run manifest. Recursive artifact scans must not accidentally discover and serve candidates. Do not overwrite production paths during the comparison.

| Incumbent | Candidate | Required decision under strict v2 |
|---|---|---|
| Qualified under the required protocol | Invalid/unknown candidate | Preserve selected incumbent; retain rejected candidate for research. |
| Legacy/unknown | Qualified candidate passing promotion gates | Eligible for explicit replacement/canary, with frozen rollback identity. |
| Legacy/unknown | Invalid/unknown candidate | Neither is v2-qualified. Preserve files; strict serving uses the declared fallback. Do not “fix” status by replacing one with the other. |
| Qualified incumbent | Qualified candidate | Compare under the frozen promotion contract; newest is not automatically better. |
| Missing/corrupt | Any candidate | Missing incumbent is not a validation credit for the candidate. Serve only qualified evidence or fallback. |

During an initial metadata/shadow stage, **serving remains under the existing policy**, with legacy status disclosed; that stage is not a v2 activation. Strict v2 starts only at the reviewed canary. No silent grace period in which legacy artifacts are presented as v2-passed.

Enforce the same serving policy in individual predictions, both ensemble paths and signal-engine's fallback chain. Excluded models must not contribute to probability, thresholds, quality averages or agreement votes. Cross-style fallback is not coverage for the requested horizon. If no eligible contributors remain, return explicit ML-unavailable evidence and ensure fusion gives ML zero weight; a fabricated neutral probability with a retained quality score/floor is not an honest substitute. Preserve any usable TA signal with the fallback reason visible.

For rollback, prefer the prior qualified artifact/policy pair. If none exists, use the declared TA-only fallback. Restoring legacy serving requires a separate, explicit policy choice and must retain its legacy label; it must not rewrite evaluation history.

## Comparison design

### Stage 0 — feasibility and routing, no fits

Refresh inventory and compute exact split sizes for these preregistered arms:

| Arm | Inner allocation: fit / early-stop / calibration / final test | Final threshold/report rule | Role |
|---|---|---|---|
| A | 70 / 10 / 10 / 10 | Existing unpurged halves | Diagnostic legacy-recipe comparator only; cannot qualify as v2 |
| B | 70 / 10 / 10 / 10 | Full horizon purge, as the held F04 patch | Correctness baseline |
| C | **60 / 10 / 10 / 20** | Same full purge as B | One larger-reporting candidate; reduced fit allocation measured explicitly |

Percentages apply to the same chronologically surviving input cohort before the documented gaps. Include every existing inter-stage gap. Do not compute C from a raw-history length while computing B after dead-zone filtering. Record minimum fit/early-stop/calibration support and every reason an arm cannot run. Do not add more allocations after looking at outcomes without a new registered experiment.

This stage establishes conditional row-feasibility estimates and potential routing loss, **not performance**. Stored metadata alone cannot show the new class balance, a rebuilt common-label cohort or model skill. Use exact frozen labels when available; otherwise report unknown until the offline study and recalculate feasibility when the cohort is rebuilt.

### Stage 1 — paired offline training and quality study

Freeze a read-only input snapshot, feature availability, corporate-action basis, universe, code, model defaults, seed and date folds. All arms receive the same opportunity population and point-in-time feature policy. Fit any data-dependent label/dead-zone threshold on the earliest training prefix shared by all arms in that fold, then freeze it for every arm; fitting it on each arm's different sample would change the targets as well as allocation. Every CV/feature-selection step must stay inside that arm's permitted fit prefix rather than retaining a hardcoded 70% boundary. Preserve missing-data exclusions. No Optuna search, changing feature sets or relaxed suppression gates in this experiment.

**Common outer reporting dates are mandatory.** Comparing each arm only on its own last slice would mix period effects with allocation effects. Use whole chronological outer folds: each fold reserves a common latest reporting interval for all arms and purges development labels that mature into it. Inside the earlier development prefix, apply each arm's allocation and complete selection procedure, including its inner reporting-validity requirement. Score frozen results on the same outer dates without using that window to select thresholds.

This is a nested research comparison, not a claim that the production fractions cover the same absolute number of rows: the inner fractions apply to the development prefix. Keep Stage 0's production-layout feasibility counts separate. Report outer research results even for an inner-ineligible arm, but do not turn those results into automatic qualification. A refitted Arm A is a legacy-*recipe* comparator, not a reproduction of an old production artifact unless all its original inputs are preserved.

Historical windows already examined are **retrospective evaluation**. Reserve a final new period after the study freeze and keep it untouched; subsequent forward observations provide separate confirmation. For overlapping multi-day horizons, purge using actual label maturity at every fit/early-stop/calibration/threshold/report boundary. Split whole issue sessions; examine shared-date and repeated-symbol dependence.

Historical outcome feedback, macro/fundamental features and any cached enrichments require known-at evidence. Use the identical eligible feedback contract in all arms. If that cannot be reconstructed, label a restricted/base-only sub-study explicitly; do not present it as a full production-pipeline comparison.

### Pilot and resource boundary

Proposed pilot: **12 stock/style cells**, covering all eight US/HK × four-style combinations plus four affected/sparse-history cases, selected before outcomes. Up to three model families × three arms × three chronological folds = **324 training jobs maximum**, less where a cell is unsupported. Each job includes internal CV and a final fit: with five CV folds this can mean **1,944 base-model fits**, plus auxiliary calibration work. Budget actual internal work, not only top-level calls. No substitutions chosen for favourable returns.

Run on an isolated worker outside production. Proposed ceiling: one concurrent fit, four GiB memory and a two-hour pilot wall budget, with per-fit timeout and incremental results. Confirm resource affordability from a small benchmark before approving the actual run. Stop with incomplete results if limits are reached; never relax evidence to finish. **This document does not start or approve those fits.** Scaling to all artifacts requires the pilot's measured cost and a separate bounded execution plan.

## Metrics and decision rules

Report by requested horizon, market and family, plus an explicitly weighted portfolio/opportunity view:

- **Validity/attrition:** qualified/invalid/unknown counts and exact reasons; classes, dates and predicted positives; sensitivity to dead-zone filtering and the longest horizon.
- **Training quality:** fit/early-stop support, CV versus common-outer AUC, overfit gap, log loss/Brier score, calibration/reliability, precision/recall and base rates at the frozen threshold. Label the retained directional/dead-zone cohort explicitly; its quality metrics are conditional, not an unconditional probability of profit. Also report coverage of all eligible opportunities, including excluded small-move windows, without inventing binary training labels for them. Include uncertainty; a target precision in training is not a delivered win rate.
- **Routing/coverage:** proportion of requested stock/style decisions with a v2-qualified contributor; actual contributors and weights; ensemble → single → TA-only transitions; cross-style and missing-model fallbacks; score/decision changes. Artifacts are not independent live decisions.
- **Strategy consequences:** when point-in-time candidate/exit/cost evidence exists, replay the full frozen signal policy with identical thresholds/risk/entry/exit/cost rules. Report net expectancy, drawdown, turnover and opportunity coverage. Without that evidence, restrict the conclusion to model quality/coverage; do not claim higher trading returns.
- **Cost and operations:** fit wall time/memory, artifact/storage growth, failures and inference latency under the chosen resolver.

Before scores are exposed, preregister the non-inferiority tolerances, primary quality endpoint, uncertainty method and acceptable coverage loss. These cannot be selected after seeing the comparison. Require all v2 correctness gates regardless of coverage. If B/C are statistically inconclusive or materially worse, retain research status; a larger slice is not presumed superior.

Use paired comparisons on common dates with session-block uncertainty and symbol sensitivity; account for all tested arms. Never select the best result from many cuts and call it independent validation. [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

## Rollout gates

| Gate | Change allowed | Required evidence |
|---|---|---|
| 0 — inventory/design | Read-only capture and agreed contracts | Current artifact/routing inventory; review of this plan; no serving effect |
| 1 — protocol implementation | Research-only versioned evaluator and candidate registry | Real split/label-maturity tests; no production-path writes; old flags cannot satisfy v2 |
| 2 — offline comparison | Approved bounded fits | Hashed snapshots/folds/results, all attrition and paired quality/coverage report; frozen decision criteria |
| 3 — shadow resolution | Metadata-only qualified-artifact resolver, **zero applied candidate weight** | Exact endpoint/fallback paths and hashes; live versus proposed coverage/differences; no accidental candidate discovery |
| 4 — bounded canary | Explicit stock/style cohort selects qualified artifacts | Compatible consumers, existing risk/quality gates, agreed fallback, session-level monitoring and tested rollback; individual and ensemble paths agree |
| 5 — staged expansion | Increase the reviewed cohort | Adequate matured quality evidence and no operational/coverage regression; no automatic rollout on sample count alone |

Canary means shadow/paper evidence first, not enabling broker trading. Predeclare cohort, duration, activity requirements and abort criteria. A few quiet sessions cannot establish forecast quality for LONG; let the actual labels mature. F03's five-session observation study is useful operational context but does not validate 20-session labels or replace this study.

Deploy code and artifact selection as separate controlled steps, under the host lock. Do not rebuild ml-prediction from the held branch and let its normal scheduler publish models before the new replacement/serving contracts are in place. Preserve old artifact hashes and an explicit selected-manifest version; promote atomically per stock/style/family and reject mixed/incomplete versions. Test interrupted promotion and readers in flight.

## Required acceptance tests

- The original 40-session F04 overlap example fails under v1 and is purged under v2. All earlier boundaries also honor actual label maturity; equality, missing bars and dead-zone gaps are covered.
- Too few reporting rows/classes yields invalid/unknown evidence, never an in-sample pass. Tiny samples stay visible. Changing a legacy boolean or adding a version string alone cannot qualify an artifact.
- Invalid candidate preserves a qualified incumbent; legacy incumbent cannot block all v2 progress as “already valid”; two unqualified artifacts do not create a qualified selection. No invalid candidate overwrites the only saved rollback.
- All individual/ensemble/fallback endpoints enforce the same selected protocol. Zero contributors has zero ML influence; wrong-horizon fallback and missing quality scores are disclosed.
- Research candidates cannot be discovered by production scanners. Mixed cache/manifest versions, corrupt files, duplicate promotion, interrupted writes and rollback are tested.
- Frozen evidence/output hashes are reproducible. No point-in-time feedback or outer evaluation data leaks into feature/threshold selection. The comparison reports every predeclared cell, including failures.

## Deliverables and next action

First deliverable after plan review: a **fresh read-only artifact/routing inventory** with an exact proposed protocol schema and replacement/serving truth table. Then implement the research-only evaluator/registry, run the bounded comparison when its budget and criteria are approved, and review the results before any serving-policy activation.

The held F04 branch remains the reference gap implementation. It is not yet a complete production release. There is **no claim here that a larger test slice, versioning alone or suppression alone increases win rate or returns**.
