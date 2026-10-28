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

Extend the existing **M14 tooling in `services/ml-prediction/src/training/suppression_inventory.py`**, rather than implementing a parallel inventory/decision function. Reuse `build_inventory()` with the real suppression-rule function injected, preserve `evaluation_valid is None` as unknown, and preserve unreadable-artifact accounting. Add protocol, capture and routing fields around those existing results. M14 reports artifacts on disk; it does not establish actual runtime selection.

Before fitting anything, refresh a **read-only inventory** with artifact identity/hash, family, saved/requested style, horizon, training time, observed evaluation fields, suppression/reason, row/class counts where stored, and resolver path. Report separately:

- Stored artifacts and flags.
- Potential eligible contributors per requested stock/style and per API path.
- Observed actual contributors from prediction provenance, where available.
- Missing/ambiguous provenance; never infer exact live contribution from a filename or suppression flag.

Do not invoke training endpoints, update bundles, clear caches or call prediction APIs that may fetch external features merely to collect this inventory. If exact contribution cannot be recovered from existing data, add a separately reviewed metadata-only provenance phase first.

### Moving inventory and immutable study capture

**The production trainer still publishes with the unpurged threshold/report split until v2 is explicitly activated.** F04 was reverted; normal post-close scheduling continues to post `/ml/train_all_horizons` (`market-data/services/scheduler.py`, `post_close` branch around line 913; queue construction in ML `api/routes.py`, around line 451). This is the continuing status quo, not a new regression introduced by the study. A read-only study does not pause that scheduler or freeze production artifacts.

Capture `capture_started_at`/`capture_completed_at`, per-artifact read times, artifact path/role, file identity and **SHA-256 of the exact bytes whose metadata was loaded**. Read/copy the bytes once and inspect that immutable copy; hashing a path and subsequently reopening it can observe two different atomic replacements. Store research copies outside the production root. Recheck the directory/file identities for added, removed or replaced artifacts and record changes explicitly, with unknown/incomplete status where needed. Preserve unreadable entries in denominators; retries must not silently erase them.

Unless an atomic filesystem snapshot exists, call this a **rolling capture window**, not one atomic fleet snapshot. Its immutable research manifest is a fixed study input; it is not proof that the whole fleet was serving those versions simultaneously. Later production replacements never update the frozen study cohort. Refresh live coverage separately at each reviewed stage and retain all inventory versions.

### Production writer boundaries

Explicit production writers include all `/ml/train*` and `/ml/tune*` endpoints (including ensemble, all-horizon and meta variants), normal scheduled training, and **`POST /ml/resweep_suppression?dry_run=false`** (`api/routes.py`, around line 670). The last endpoint rewrites `oos_suppressed` from stored metrics without retraining. It defaults to dry-run and is admin-callable; no scheduled caller was found in the reviewed service sources.

- **Inventory/offline study:** no study-initiated production training, tuning, artifact edits or suppression resweep. Place unscheduled/admin writer operations, including suppression resweep, under the study's documented change moratorium. Ordinary pre-existing post-close training continues as the explicit exception and is handled by versioned rolling captures above. Do not describe the live fleet as frozen. A genuinely global writer pause would require a separately reviewed operational change.
- **Canary:** no unaccounted writer may alter selected canary artifacts or suppression flags. Freeze admin suppression resweeps and manual writer actions; fence, pause or redirect scheduled publication for selected canary paths, or use a qualified immutable root/manifest inaccessible to legacy writers. Other cohorts may retain legacy scheduled training. Verify enforcement before activation; a plan's moratorium is not an implemented permission block. An emergency writer action pauses/rebaselines the canary.
- Stage 0 should use M14's pure read-only calculation; it has no need to call even the resweep endpoint's dry-run route. No production pause, ACL change or resweep is performed by this plan revision.


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

### Reconcile M14 axes and the two floors

Use qualified names in the combined report so M14's conceptual “eligibility” is not confused with runtime permission:

| Existing concept | Combined-report meaning/name |
|---|---|
| M14 validity | `legacy_evaluation_validity`: the stored Boolean/unknown, retained as historical evidence; alongside the new `protocol_evaluation_validity` |
| M14 sufficiency | `m14_evidence_sufficiency`: the existing `evidence_sufficiency()` result and missing diagnostics, unchanged |
| M14 documentation's eligibility question | `performance_evidence_eligibility`: acceptable measured performance versus its stated baseline; do not invent a pass from `unsuppressed` or imply M14 already implements the future paired promotion study |
| Candidate replacement eligibility | `replacement_authorization`: whether the selected candidate may replace the incumbent under a versioned policy |
| Runtime serving eligibility | `runtime_contribution_authorization`: whether the resolved artifact may contribute to this request |

Keep both existing floors visible and **do not change either** in Stage 0:

| Check | Existing floor and denominator | What it establishes |
|---|---|---|
| Trainer reporting computation | At least **10 post-subdivision/post-purge reporting rows**, with two reporting classes | Whether the held-out reporting path can compute its metrics; not adequate evidence by itself |
| M14 evidence sufficiency | **`n_test >= 50`**, plus required `cv_auc_mean` and `overfit_gap` diagnostics | Existing visibility/sufficiency classification; not a promotion threshold or runtime authorization |

M14 currently reads the stored **`n_test`**, which for the modern holdout path is the test slice **before** threshold/report subdivision; it is not `n_metric_rows`. Fifty test rows therefore do **not** mean fifty independent reporting observations. Keep `n_test`, threshold/purge/report counts and actual metric denominators separate. Do not silently substitute `n_metric_rows` into M14 or invent a new 50-report-row gate in this study.

For example, 45 total test rows with horizon 5 can leave 20 reporting rows under the held gap rule: computationally feasible but M14-insufficient. Fifty test rows with horizon 20 can leave 15 reporting rows: M14 may be sufficient if diagnostics exist, yet that is still only 15 reporting observations and not promotion evidence. Missing counts remain unknown; class support and protocol validity remain separate axes.

Preserve existing quality gates; do not lower them to recover coverage. Promotion additionally needs the already specified adequate class/action support and uncertainty. Unsupported/inconclusive cells remain visible and unqualified.

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

The **proposed strict-v2 target behavior** is to enforce the same serving policy in individual predictions, both ensemble paths and signal-engine's fallback chain. Excluded models must not contribute to probability, thresholds, quality averages or agreement votes. Cross-style fallback is not coverage for the requested horizon. With zero qualified contributors, the target contract is explicit ML-unavailable evidence and zero ML influence, preserving usable TA with a visible reason.

**Open Stage 0 verification, not an established defect:** trace what currently happens when no model qualifies. Test missing artifacts, all-suppressed artifacts, one qualified contributor and wrong-horizon fallback across individual/two-model/three-model endpoints and signal-engine's extraction/fusion. Record returned probability, quality/AUC provenance, suppression/status flags and actual applied weight. Determine whether a neutral probability retains a quality score/floor or is correctly excluded; do not assume the failure occurs. Use isolated fixtures/read-only stored provenance, not new production prediction/provider calls. Any confirmed behavior change is designed and reviewed separately.

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

Freeze a read-only input snapshot, feature availability, corporate-action basis, universe, code, model defaults, seed and date folds. All arms receive the same opportunity population and point-in-time feature policy. Fit any data-dependent label/dead-zone threshold on the earliest training prefix shared by all arms in that fold, then freeze it for every arm; fitting it on each arm's different sample would change the targets as well as allocation. Every CV/feature-selection step must stay inside that arm's permitted fit prefix rather than retaining a hardcoded 70% boundary.

**Two independent code sites must change in the research evaluator.** In the reviewed trainer, `_train_rows = int(len(df) * 0.70)` and `compute_label_threshold(df.iloc[:max(_train_rows, 60)], ...)` at **lines 828–829** choose the dead-zone threshold from the *raw* price frame, before feature/label filtering. This is separate from **`split_train = int(len(X) * 0.70)` at line 1057**, and the later 80/90 split boundaries. Changing only `split_train` for Arm C can leave its label threshold informed by rows beyond its actual 60% fit cutoff. The `max(..., 60)` floor must also remain within the permitted common training cutoff; it is not an exception to the no-look-ahead rule.

Use a preregistered common time cutoff for label-threshold estimation, map raw-bar dates to the retained feature dates, and verify it precedes every arm's actual fit end. Do not fit on “60% raw rows” and assume that equals “60% retained rows.” If the predeclared cutoffs cannot satisfy this after filtering, mark the cell unsupported rather than repeatedly changing thresholds until it passes. Freeze one label/dead-zone definition per fold for all arms.

This common-prefix correction moves **A and B as well as C away from the current production labelling recipe**. Arm A is therefore only the stated refitted legacy-*split* comparator on common research labels, not an exact current-production retrain. Archive the production recipe's original label metadata separately; do not attribute the difference solely to reporting allocation.

Preserve missing-data exclusions. No Optuna search, changing feature sets or relaxed suppression gates in this experiment.

**Common outer reporting dates are mandatory.** Comparing each arm only on its own last slice would mix period effects with allocation effects. Use whole chronological outer folds: each fold reserves a common latest reporting interval for all arms and purges development labels that mature into it. Inside the earlier development prefix, apply each arm's allocation and complete selection procedure, including its inner reporting-validity requirement. Score frozen results on the same outer dates without using that window to select thresholds.

This is a nested research comparison, not a claim that the production fractions cover the same absolute number of rows: the inner fractions apply to the development prefix. Keep Stage 0's production-layout feasibility counts separate. Report outer research results even for an inner-ineligible arm, but do not turn those results into automatic qualification. A refitted Arm A is a legacy-*recipe* comparator, not a reproduction of an old production artifact unless all its original inputs are preserved.

Historical windows already examined are **retrospective evaluation**. Reserve a final new period after the study freeze and keep it untouched; subsequent forward observations provide separate confirmation. For overlapping multi-day horizons, purge using actual label maturity at every fit/early-stop/calibration/threshold/report boundary. Split whole issue sessions; examine shared-date and repeated-symbol dependence.

Historical outcome feedback, macro/fundamental features and any cached enrichments require known-at evidence. Use the identical eligible feedback contract in all arms. If that cannot be reconstructed, label a restricted/base-only sub-study explicitly; do not present it as a full production-pipeline comparison.

### Pilot and resource boundary

Proposed pilot: **12 stock/style cells**, covering all eight US/HK × four-style combinations plus four affected/sparse-history cases, selected before outcomes. Up to three model families × three arms × three chronological folds = **324 training jobs maximum**, less where a cell is unsupported. Each job includes internal CV and a final fit: with five CV folds this can mean **1,944 base-model fits**, plus auxiliary calibration work. Budget actual internal work, not only top-level calls. No substitutions chosen for favourable returns.

**Historical runtime prior — code comment, not a new measurement:** the `AUD-ML3-STYLESTARVATION` comment in [ML `api/routes.py`, line 451](../../services/ml-prediction/src/api/routes.py#L451) records approximately **326 training runs per 30 minutes** for a serial queue of about 173 symbols × four styles × **two model families**. Neither this review nor the user performed a new timing measurement. Each queued job calls `train_model`, which includes configured five-fold `TimeSeriesSplit` CV before the final fit, so this is top-level training-job throughput, not throughput per individual CV fit.

That historical rate corresponds to about **5.5 seconds per job** and, arithmetically, **324 jobs ≈ 1,789 seconds / 29.8 minutes**. It is only a planning prior: the study includes three families, different samples and a potentially slower isolated worker. No original timing log or current replication is supplied. CV is already included—do not multiply this runtime estimate by six again. The larger 1,944 figure above describes base-model fit operations, not 1,944 separately timed 5.5-second jobs. **The required research-worker benchmark remains mandatory; the historical comment does not establish that the pilot will finish within two hours.**

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
| 0 — inventory/design | Extend M14 read-only reporting and agree contracts | Timestamped/hash-bound inventory, both floors and mapped axes, no-model-contributor trace; no serving effect |
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
- Frozen evidence/output hashes are reproducible. No point-in-time feedback or outer evaluation data leaks into feature/threshold selection. Assert the raw-frame dead-zone fit at lines 828–829 stays inside every arm's fit cutoff independently of the retained-row split. The comparison reports every predeclared cell, including failures.
- M14 unknown validity, the 50-total-test-row sufficiency flag and the 10-report-row computation floor retain their distinct meanings. Atomic artifact replacement during capture cannot mix a hash from one version with metadata from another; unreadable/changed artifacts remain visible. Admin suppression resweep and scheduled publication cannot mutate a selected canary outside the approved writer policy.

## Deliverables and next action

First deliverable after plan review: an **M14-based, timestamped and hash-bound read-only artifact/routing inventory** with an exact proposed protocol schema and replacement/serving truth table. Then implement the research-only evaluator/registry, run the bounded comparison when its budget and criteria are approved, and review the results before any serving-policy activation.

The held F04 branch remains the reference gap implementation. It is not yet a complete production release. There is **no claim here that a larger test slice, versioning alone or suppression alone increases win rate or returns**.


## October 27 review disposition

All five review points are incorporated into the operating specification above: the raw-frame label fit is a separate required research change; M14 is reused with explicit axis/floor mapping; suppression resweep is named in writer controls; the live legacy publisher and rolling capture semantics are explicit; and the historical production-throughput code comment is a prior for the required worker benchmark. The unverified no-eligible-model behavior is a named Stage 0 investigation. These are documentation corrections, not a change to the study arms, existing gates or F04 hold. **Stage 0 has not started.**


## Guarded trainer deployment and nightly publication

**Selected policy: Option 1.** Deploying the new trainer does not activate v2 fleet-wide. Outside explicitly enabled cohorts, scheduled live training continues the existing v1 recipe, retention and serving behavior, clearly labelled legacy/unqualified under v2. V2 evaluation and fitting write only to a candidate root outside all live discovery paths. This preserves the existing defect temporarily for legacy cells; it does not claim those cells have been repaired.

Proposed controls (not implemented): `ML_V2_PUBLICATION_ENABLED=false` by default plus a versioned exact cohort manifest keyed by stock, horizon and model family. The scheduler respects these controls when building requests. Enforcement also belongs in the ML service's queue worker, artifact writer and selection resolver: a scheduler flag alone cannot protect manual endpoints, queued jobs or retries. Client-supplied protocol/root values cannot grant publication authority. An unknown control value or unreadable manifest denies v2 publication without silently changing the declared legacy policy.

Each queued job freezes its requested protocol, intended destination, cohort and policy identity. Recheck current authorization at execution and immediately before atomic publication/selection. A stale job must not cross protocols or publish after its cohort is disabled. Use a fenced publication mechanism so disabling a cohort cannot race a previously checked writer into updating the selected artifact. For enabled cohorts, scheduled training may produce v2 candidates; only the same versioned qualification/promotion gate may update their selected manifest. Never publish merely because fitting succeeded. Legacy jobs cannot overwrite an enabled cohort's qualified selection; its immutable root and writer policy enforce that boundary.

Activation requires the publication flag **and** explicit cohort membership **and** qualification. Disabling publication stops new promotions; it does not delete artifacts or silently switch an already-selected cohort back to v1. Serving rollback is a separate explicit, tested selection change to a pinned qualified artifact or the declared fallback. Record these independent control states in release evidence.

Mandatory regression tests use the actual nightly enqueue → worker → writer path with isolated roots: flag off cannot write/select v2 in the live root; v1 behavior remains unchanged for non-enabled cells; flag on with an empty/nonmatching cohort still cannot promote; valid enabled candidates pass the promotion gate; rejected candidates cannot replace selection; old queued jobs/manual endpoints/retries cannot bypass controls; revocation during execution fences publication. Hash live artifacts and selection manifests before/after every denied case. Test restart/default configuration and compare selected-path hashes after deployment. This is a **code-release gate**, not a canary-only requirement.

## Consolidated release sequencing

The [completion and release plan](2026-10-27-rzy-ai-completion-and-release-plan.md) proposes shipping the complete guarded correction with purged baseline B before allocation-C optimization finishes. This does not waive protocol, retention, routing, coverage or canary gates, qualify legacy artifacts, or activate a production cohort. Stage 0 has not been executed by this documentation update.

**Status update (2026-10-28).**
- **Stage 0 complete.** [Inventory and no-qualified-model trace](../audits/2026-10-28-f04-stage0-inventory-and-no-model-trace.md).
  The no-eligible-model behaviour is now established: all-suppressed ensembles emitted a weighted
  bearish 0.45.
- **Fixed and deployed** as `f5beb232`: [replay and deployment record](../audits/2026-10-28-f04-ml-unavailable-replay.md).
  Suppressed models carry no ML weight; unavailable ML soft-fails the conviction gate's ML layer.
- **Still open and still held:** F04's versioned evaluation, purged baseline B and nightly-publication
  fencing, and the two-model quality calculation (S0-3).
