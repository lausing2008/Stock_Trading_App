# Little RZY / AI Signals — complete remaining solutions and release sequence

Date: **October 27, 2026, America/Los_Angeles**. Scope: F01–F12 from the [full review](../audits/2026-10-26-little-rzy-ai-signals-full-review.md), the requested Intraday horizon, and the related persistence, measurement and collection gaps. Source baseline: `0a3525dd`. Status: **consolidated solution and execution plan; the remaining implementations are not claimed complete**.

## Delivery decision

Use three deployable batches. Build independent components without waiting for every future session to mature. Ship guarded research capabilities when their correctness tests pass; activate each new sender, model selection or trading capability only when its own acceptance evidence exists.

**F04's isolated-patch hold becomes a bounded engineering checklist, not an indefinite wait for the best model.** The complete versioned correction can ship with the purged baseline. The larger-reporting experiment addresses coverage/quality and need not delay deploying correct evaluation code. Existing legacy artifacts must not silently count as passing that new protocol.

No deployment, training or broker activation occurs merely because this document exists. Existing opt-ins, risk-reducing alerts and the current guarded calibration endpoints remain subject to their explicit contracts.

## Current state, reconciled

- **F01 deployed:** the calibration projection contains `exit_date`; strict evidence remains enforced.
- **F02 partly complete:** daily-input provenance, provisional labels and calibration exclusion are deployed. The immutable completed-close record deferred in the original F02 text **was subsequently deployed in the F03 observation phase**. Provider final-close evidence and correctly attributed new-cohort readers remain distinct work.
- **F03 observation deployed:** occurrences/opened/closed snapshots and daily passive collection exist. Receipts and receipt-attributed outcomes are not built. Current `content_changes` is full-content churn, not confidence churn.
- **Persistence repair deployed:** market-data `8dc84150` uses canonical signal-row write order. The immediate normal job committed 401 metadata rows; full active-session evidence remains open. No database retry was added.
- **F04 implementation held/reverted from prod:** the gap patch is preserved on `f04-threshold-report-purge`; the currently running trainer still uses the old unpurged final split. Scheduled legacy retraining continues. The original 38 affected unsuppressed artifacts are a captured risk count, not current serving coverage.
- **F05–F11:** remaining solutions below have not been implemented by this plan. **F12 is contained** by the existing hard research-only promotion guard; it is not an active tuning writer.

## Full solution by finding

| Item | Complete solution | Completion evidence |
|---|---|---|
| **F01 — calibration projection** | Retain the deployed real-query fix and strict timing/evidence checks. Use the same explicit predicate for each new cohort. | Positive verified-cohort query-to-bucket tests plus invalid/missing/provisional controls on SQLite and PostgreSQL. Zero verified production evidence must still yield no rate. |
| **F02 — daily inputs / closed state** | Keep moving daily estimates, visibly provisional. Keep completed-session calibration separate. Link the already-built completed-close snapshot to the exact venue session/bar and actual ingestion-evidence status; do not turn calendar closure into provider finality. | US/HK forming/lunch/weekend/holiday/early-close/missing-session tests; no yesterday-bar capture for today's session; unknown finality stays unknown. |
| **F03 — attribution** | Durable frozen send snapshots and per-recipient receipts, followed by receipt-specific outcomes. Dual-write old fields for compatibility, then cut readers over by cohort. No historical delivery backfill. | Concurrent real PostgreSQL paths; multiple same-day directions/confidences; distinct-recipient dedup; durable intent before send; crash/unknown handling; separate generated, delivered and completed-close outcomes. |
| **F04 — ML validation** | Ship the gap together with protocol versions, label-fit boundaries, version-aware incumbent selection, candidate isolation and consistent serving resolution. Use purged allocation B as the correctness baseline; investigate C separately. | Checklist below; current hash-bound inventory and actual routing impact; no old Boolean can satisfy v2; no suppressed/unknown contributor regains weight through another endpoint. |
| **F05 — causal simulated fills** | Freeze detection/order-readiness time. Idealized historical replay and causal forward simulation use distinct modes. Forward fills require an executable observation after readiness; never retroactively fill at an earlier next-bar open. | Delayed/batched/restarted scans cannot fill before readiness. If only OHLC is available, use the first eligible later bar open conservatively; no invented within-bar fill. Missing execution evidence still blocks execution-grade paper. |
| **F06 — Intraday** | Add a true same-session horizon with completed 5m triggers, available completed 15m context, entry expiry, max hold and session-end management. Keep strategy/source separate from horizon and from the daily SHORT model. | Registry/schema/API/UI coverage, timestamped outcomes and session-clock tests; no fallback into SWING/daily ML. Research first; operational/execution readiness gates remain explicit. |
| **F07 — corrected-data survivorship** | Maintain immutable as-observed decisions/fills and a separately versioned corrected-data replay. Freeze cohort membership at observation; watchlist removal or a rebuilt chart cannot remove an actual historical decision from its original cohort. | Revision/generation, watchlist removal and unresolved/cancelled/closed lifecycle tests. Current reconstructed view and original forward record reconcile without deleting losses. |
| **F08 — research controls/statistics** | Fold-local, ex-ante control strategies; complete cost/exit policies; common dates; purged labels; date-block and symbol-dependence checks; trial registry and untouched future data. Keep old matched-duration controls only as retrospective diagnostics. | Frozen reproducible inputs/outputs, negative/planted-edge checks, complete exclusions/nonfills, paired incumbent comparisons and uncertainty. No best-cell promotion. |
| **F09 — data/latency** | Commit-safe correction tracking, incremental per-stream state, fair scheduling and independent position management. Record quote/provider availability and entitlement evidence; split close→availability→detection→queue→acceptance. | Full-session coverage/latency denominators, old-revision/deletion detection, source-switch/gap tests, bounded resource/lease behavior. A healthy container or quiet-window zero is insufficient. |
| **F10 — horizon semantics** | Version prediction window, evaluation window/unit, entry convention and managed hold separately. Add aligned 5/10/20/15-session forecast studies for daily styles, preserving old calendar-day cohorts. SELL-exit and short entry are distinct actions. | Model/schema/UI labels agree on the named question; holiday/maturity tests; forecast hit rate cannot be presented as net managed-trade return. |
| **F11 — policy lineage** | Immutable resolved policy snapshot/hash per candidate, including override source/expiry, actual in-memory weights, feature/basis/time and artifact identities. Capture rejected candidates as well as admitted ones. | Exact replay of accepted/rejected candidates using the frozen effective policy; policy expiry/change starts a new segment. Redis's current values cannot rewrite historical policy. |
| **F12 — legacy tuning** | Keep the hard guard; keep diagnostics labelled research-only. Replace legacy promotion with the qualified v2 pipeline rather than re-enabling unsafe bodies. Unreachable legacy code cleanup is not a prerequisite to shipping correctness fixes. | Integration checks prove every guarded endpoint cannot mutate Redis, artifacts or promotion state. Only explicitly qualified new promotion paths can select artifacts. |

## F04 — complete release solution

The detailed [comparison/versioning plan](2026-10-27-f04-evaluation-version-comparison-and-rollout-plan.md) remains authoritative. The practical deployment path is:

1. **Read-only Stage 0:** extend M14, not a second inventory. Copy/read artifact bytes once outside the production model root; hash and inspect those same bytes. Record capture interval and per-artifact time/identity, unreadable/replaced entries, exact requested/resolved horizon and endpoint/contributor information where known. Normal legacy publication can continue; the inventory is a rolling capture, not an atomic frozen fleet.
2. **Trace the no-qualified-model path:** use isolated fixtures through individual/ensemble/caller extraction and fusion, recording probability, quality provenance, suppression and applied weight. It is a question to establish, not an assumed current bug.
3. **Implement protocol v2:** full final threshold/report purge and every earlier label-maturity boundary; explicit status/version and metric denominators. Correct the raw-price label-threshold fit (`trainer.py:828–829`) separately from retained-row `split_train` (`:1057`). Common research labels must come from a permitted earliest prefix, never an arm's later early-stop/report period.
4. **Fix the incumbent freeze:** preserve files, but compare evidence under the required protocol. A legacy pass is unknown under v2. Invalid candidates never overwrite the last qualified rollback; neither an invalid candidate nor a legacy incumbent becomes qualified just because the other fails. Store candidates outside the live discovery paths.
5. **Unify serving resolution:** individual, two-model, three-model and caller fallbacks must use the same explicit qualification. No wrong-horizon substitute counts as that horizon's coverage. Empty qualified sets produce explicit ML-unavailable evidence with zero ML contribution, rather than a retained quality score earning a fusion floor.
6. **Make fallback safe at the whole-policy level:** TA-only display may remain useful, but missing ML must not silently soft-pass a new-entry gate or create a new actionable BUY that the incumbent policy blocked. Compare eligibility as well as scores; preserve risk-reducing exits. Version and test the fallback policy.
7. **Fence nightly publication before deploying the trainer:** adopt Option 1. Non-enabled cells retain the exact live v1 behavior, disclosed as legacy; v2 fitting writes only to the isolated candidate root. A default-off publication control and exact cohort manifest must be enforced by the scheduler **and** ML worker/writer/selection paths, including queued/manual work. Training success never grants promotion. Revocation must fence in-flight publication; disabling promotion does not silently roll back serving. The [detailed writer policy](2026-10-27-f04-evaluation-version-comparison-and-rollout-plan.md#guarded-trainer-deployment-and-nightly-publication) and actual nightly-path negative tests are mandatory before any guarded trainer deploy.
8. **Qualify baseline B first:** the unchanged nominal 70/10/10/10 allocation with a genuine final purge is the default correctness baseline. Retrain only into the candidate root, apply existing quality gates and the new structural proof, then use a bounded selected cohort. Do not globally suppress every legacy artifact simply by tagging it unversioned. Conversely, do not call the still-legacy cells fixed or v2-qualified.
9. **Use C for recovery/optimization:** compare 60/10/10/20 on common outer reporting dates and common labels. It is optional coverage/quality research, not a prerequisite to deploying the protocol/selection code. B/C promotions depend on evidence, not a desire to retain the previous artifact count.

The old “38” number is not the total strict-versioning impact: **every unversioned contributor** needs qualification under the requested protocol. Measure actual selected cells/ensembles before choosing canary size. Stored artifacts, unsuppressed artifacts and effective stock/horizon decisions have different denominators.

Keep the trainer's **10 actual reporting-row/two-class computation floor** distinct from M14's **50 `n_test` rows plus diagnostics** evidence flag. Neither alone grants promotion. Do not change these gates to obtain a desired coverage percentage.

**F04 code-release exit checklist:** default-off, server-enforced nightly publication/cohort fencing; protocol and label boundaries; version-aware retention; isolated candidate storage; consistent inference/fallback behavior; current inventory/coverage; real regression tests and immutable selected/rollback identities. Once complete, deploy the guarded code. Enabling each selected cohort is a separate measured transition. Historical, leakage-controlled model validation is not a claim of future profitable trading, and need not wait for a 60-session profit study just to repair the algorithm.

## F03, policy and measurement implementation

Keep occurrence identity by continuous actionable direction within the venue session. Neutral HOLD/WAIT ends the run; confidence changes do not create a new trade occurrence. The opened and completed-close records already exist.

Add immutable send snapshots, receipt identity and per-receipt outcomes. Commit claimed send intent before the provider call, send without an open database transaction, then settle using a fenced owner/attempt token. A crash after acceptance becomes `unknown`; no blind resend. Recheck consent and current eligibility before sending. Snapshot/hash identity and retries reuse the original content. Distinct recipients are counted once. Unknown receipt state remains visible.

Delivered daily studies anchor to the first eligible close **strictly after that receipt's acceptance**, and are not execution fills. Two recipients accepted in different sessions can have different outcomes. Generated, completed-close, informational and delivered populations remain separate; legacy daily-row delivery flags cannot be backfilled into exact receipts.

Implement the sender/reader code behind default-off activation flags while observation continues. The **five-complete-session rule remains an activation/capacity acceptance gate**; it need not prevent development and local integration tests. Record this sequencing explicitly rather than treating flags as permission to enable early. Preserve dual-write compatibility and compare daily reports before reader cutover.

Add a new, versioned measurement period for precise confidence/probability changes. Preserve v1 full-content churn. Store the previous comparable score/probability, raw deltas and a separately declared materiality convention; reasons-only changes are a distinct measure and may be meaningful. Missing starting values are unknown, not reconstructed from the first opened snapshot. Do not pool v1 and v2 counters.

For a real persistence failure rate, record **unique stock-batch attempts, eventual commits, exhausted failures and SQLSTATE retry attempts** separately. Existing daily-row counts cannot provide that rate. Keep canonical row order. Any later `40P01` retry uses a database-only unit, fresh transaction/rollback, bounded attempts and frozen inputs; it must not repeat model/provider/email/research work or overwrite a newer generation.

## RZY forward execution, data and research

Separate the **as-observed operational ledger** from reconstruction. Corrections can rebuild chart structures but cannot rewrite actual order/fill history, original risk or observation times. An open position's management cannot pause because a setup stream is rebuilding. Cancellations and nonfills have no trade P&L; unresolved exposure remains visible.

A forward order becomes eligible only after data availability, detection and order readiness. When using bar-only research, never fill at the open of a candle that began before readiness. Structural close invalidation schedules a genuinely subsequent eligible exit; protective barriers use the available execution basis. At session end, a halted/unfilled position is still exposure, not a fabricated close.

Use configured data providers first, with explicit timestamp, delay, entitlement, basis and short-borrow status. No new paid feed/subscription is assumed. Where facts are unavailable, ship the research overlay with precise blockers. Do not set `execution_eligible=True` merely to make the simulator produce trades. Any assumed-execution research cohort is separately named.

Bring the shared corporate-action/adjustment window evidence up to date per symbol and provider coverage. The absence of a recorded action is not proof of complete coverage. Use the same declared price/total-return convention for features, labels and outcomes; preserve actual-current-price execution units. Unknown/incomplete evidence remains excluded from claims of verified performance. This is cross-cutting work needed before higher-return claims, not a cosmetic confidence adjustment.

For incremental scans, first provide **commit-safe, per-series change tracking** covering accepted price/volume/basis/session changes and deletions, with canonical writer lock order and same-snapshot version/data reads. A global sequence allocated before commit is not by itself a safe consumer watermark: transactions can commit out of order. Audit every managed writer and maintenance path. Until coverage is proved, retain full fingerprint/history fallback. Then cache indicators/state, process only safe append work, and replay the affected segment on corrections.

Use fair/coalesced per-stream scheduling, bounded CPU/memory, renewable fenced leases and separate priority for existing-position risk. A stale backlog must expire visibly rather than emit late “enter now” alerts. Record expected-versus-evaluated opportunities; acceptance/inbox delivery and detection/execution latency stay distinct.

Repair the research framework with fold-local ex-ante controls, point-in-time universe snapshots, complete execution costs, gap censoring and common-date paired comparisons. Use date-block uncertainty plus symbol sensitivity; preserve the tested-variant registry. Keep the historical −0.093R result labelled under its original code/data/assumptions. Removing the line exit already worsened that study; do not make it the default on speculation.

## Intraday completion

Implement the [existing Intraday specification](2026-10-26-intraday-and-signal-performance-plan.md): a central horizon contract, immutable same-session occurrences/outcomes, completed 5m trigger plus available completed 15m context, session VWAP, explicit entry/stop/target/expiry, maximum hold and session-end handling. Start with deterministic, correctly labelled rules; daily ML is not an Intraday model.

Stock detail should show a concise plan and steps, with entry range, stop, targets, net planned R/R, expiry, data age and invalidation. Detailed evidence is expandable. Keep the selected rules/LLM/RZY source and immutable saved snapshot explicit. Planned R/R is not probability of success. Saving does not revalidate an expired proposal or submit an order.

Keep long and short directions separate. Short research can exist with a borrow blocker; it cannot become executable by symmetry. New Intraday notification subscriptions are explicit opt-ins. Live data targets must account for the current two-minute settlement floor: deploy delayed research if needed, but do not call it a sub-minute actionable service until a validated finality/availability path meets that contract.

## Deployable batches

| Batch | Contents | Deployment and activation |
|---|---|---|
| **A — trustworthy evidence and F04 safeguards** | M14-based capture/trace; guarded nightly writer/cohort routing; protocol and label-boundary implementation; candidate/selection/retention controls; policy provenance; accurate counters/collection coverage; new receipt/outcome schemas and Intraday-compatible readers where shared changes are required | Pin and test the release. Apply additive migrations once under lock; rebuild affected shared consumers sequentially. Deploy research/metadata functionality with new senders, strict-model cohorts and Intraday producers off until their specific gates pass. |
| **B — complete daily/RZY workflows** | F03 durable sender/receipts/readers; causal forward simulator and immutable ledger; independent management; commit-safe scan acceleration; explicit forecast/managed-trade semantics; Intraday research generator, card/overlays and opt-in drafts | Activate receipt paths after observation/capacity/consistency checks. Activate qualified model cohorts progressively. Keep execution-grade flags false where data/borrow facts are unknown. Capture before/after source/counter identities. |
| **C — validated coverage and performance** | B/C allocation results; model cohort expansion; fold-local RZY/AI research comparisons; execution-ready paper and calibrated displays where sufficient; later Intraday ML if justified | Mature the relevant outcomes. Enable only supported cohorts/capabilities; maintain rollback/fallback and published uncertainty. This is not an automatic real-order release. |

Some work in B can be developed alongside A; neither a larger allocation winner nor future profitability is required to deploy guarded correctness code. A complete-session/label-maturity requirement cannot be replaced by a deployment date.

Prefer one coordinated additive shared-schema release to repeated all-service rebuilds, but keep service-local patches independently deployable. PostgreSQL enum values require an explicit migration and compatible readers before any INTRADAY row is written. ORM `create_all()` does not upgrade existing columns/enums. Flags must prevent accidental new-value production during rollout. Do not deploy an isolated historical branch over later fixes; integrate reviewed changes onto the current release.

## Acceptance and rollout record

Batch A must include the real-path F12 integration test proving the research-only guard prevents Redis, artifact and promotion writes; the existing containment statement is not a substitute for that test.

Each batch needs exact source/data/policy versions, behavioral tests, real PostgreSQL concurrency/migration gates, affected suites, negative controls, rollback images and a per-finding status record. A feature is **implemented**, **deployed**, **enabled** and **evidence-qualified** at different times; report those states separately.

Use the host deployment/backup lock, save old logs before recreation, serialize startup/schema work, monitor actual disk utilisation and pressure, and preserve the backup window. Do not repeat an email or expensive research action as a transaction retry. Confirm all declared containers, source hashes, authentication/ownership and the chosen normal jobs after rollout; healthy containers alone do not establish signal accuracy.

Collections should be immutable and atomic, use explicit successful-run cursors, expose command/log coverage and failures, retain release identities of both writers, and support bounded retry/catch-up without overwriting manual evidence. Failure counters need complete coverage or an unknown label. Current cron installation remains; improve the collector without creating a duplicate schedule.

**Ready-to-start execution order:** Stage 0 capture and no-model trace → v2 split/label/retention/resolver tests → first deployable evidence/safeguard batch → qualified-cohort canary and F03 sender integration → causal RZY/Intraday workflows → expanded validation. Keep legacy calibration writers guarded throughout.

The objective is to remove implementation defects quickly and make results measurable. Higher win rates and returns remain hypotheses to test; no setting, model or healthy deployment can guarantee them.

**Status update (2026-10-28).**
- **Stage 0 complete.** [Inventory and no-qualified-model trace](../audits/2026-10-28-f04-stage0-inventory-and-no-model-trace.md).
  The no-eligible-model behaviour is now established: all-suppressed ensembles emitted a weighted
  bearish 0.45.
- **Fixed and deployed** as `f5beb232`: [replay and deployment record](../audits/2026-10-28-f04-ml-unavailable-replay.md).
  Suppressed models carry no ML weight; unavailable ML soft-fails the conviction gate's ML layer.
- **Still open and still held:** F04's versioned evaluation, purged baseline B and nightly-publication
  fencing, and the two-model quality calculation (S0-3).
