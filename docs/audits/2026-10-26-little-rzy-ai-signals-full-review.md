# Little RZY and AI Signals — correctness, performance and Intraday review

Date: **October 26, 2026, America/Los_Angeles**. Reviewed source baseline: **`6113ce52`**. Status: **review and plan only; no application/configuration changes, retraining, calibration, deployment, orders or test emails**. The implementation proposal is [the Intraday and performance plan](../features/2026-10-26-intraday-and-signal-performance-plan.md).

## Verdict

Neither system has evidence sufficient to promise higher win rates or returns. Little RZY implements a coherent, deterministic research pattern, but the historical evaluation found **no demonstrated net edge**. AI Signals has substantial safeguards, but its displayed outcome calibration contains a newly reproduced defect, its daily feature inputs lack a completed-session contract, and its delivered-alert outcome attribution is too coarse. **INTRADAY has not been implemented**: the database enum and generator still support only SHORT, SWING, LONG and GROWTH.

The right sequence is correctness → data/execution timing → immutable measurement → limited preregistered experiments → unseen forward validation. Increasing displayed confidence, increasing target R/R, lowering filters to produce more alerts, or picking the best historical win-rate cell is not evidence of improvement.

## Scope and evidence

Reviewed the RZY specification; engine, data adapter, workflow, generation/rebuild handling, sender, simulator journal, evaluators and timing; AI daily inputs, fusion, model validation, thresholds, outcome/calibration population, delivery attribution and horizon persistence; relevant UI labels and previous audits. This is a deep review of these paths, not a certification of every service, broker integration or historical model artifact.

Evidence is in [the review evidence directory](evidence/2026-10-26-rzy-ai-intraday-review/):

- Existing targeted suites: **48 AI tests and 118 RZY tests passed**. These are targeted checks, not fresh full-repository or PostgreSQL integration runs. The saved logs are historical evidence from this review, not a claim of a new run against later HEAD. A pre-commit check found subsequent changes to `market-data/src/api/routes.py` and `market-data/src/services/scheduler.py` relative to the pinned baseline; no tests were rerun for this documentation-only commit.
- Three inspection witnesses use unchanged shipped functions/statements: actual SQL query/eligibility on disposable SQLite, actual RZY engine next-open fill, and the trainer's threshold/report split. They demonstrate the findings below without editing application code. The calibration witness's first harness attempt used a column collection where SQLAlchemy required a joinable table; the corrected harness uses actual tables and completes successfully.
- Production reads were aggregate-only, in READ ONLY transactions with statement timeouts, plus allowlisted Redis values and source hashes. No mutation endpoint was called. The existing [capture script](evidence/2026-10-21-ai-signals-horizons/capture_runtime.py) was reused.
- At **2026-10-27 00:30 UTC / October 26 17:30 PDT**, all nine inspected production files matched the pinned `6113ce52` baseline. The working-tree scheduler differed because concurrent reconciliation work was being edited; subsequent function-level AST hashes also matched for the three reviewed functions (`check_signal_alerts`, `check_little_rzy_workflows`, `drain_little_rzy_outbox`). This is scoped parity, not an assertion that the entire release has zero drift. Concurrent index-page commits and calendar/reconciliation edits are outside this audit and were preserved. Source hashes in the manifest are pinned to the reviewed baseline, not those later working-tree edits.
- Later follow-up at **05:55 UTC / October 26 22:55 PDT** classified the eight forward simulator observations as cancelled and read the post-close job record. These are separate point-in-time observations, not one atomic platform snapshot.

### Current measurements

| Population | Observation | Meaning / limit |
|---|---|---|
| AI resolved outcomes, trailing 180 days | **25,843**: 21,520 unknown/legacy, 4,148 unverified, 175 provisional, **0 verified** | Stored directional/hurdle outcomes; not verified executable returns. The zero verified population is a data-evidence limitation independent of F01. |
| Effective bull BUY baseline keys | SHORT **.72**, SWING **.81**, LONG **.69**, GROWTH **.72** | Watchdog keys currently override calibration keys .57/.61/.55/.65. Runtime applies regime deltas. These are scores, not win probabilities. |
| ML cap keys | Global **0**; SHORT .40, LONG .15, GROWTH .65, no SWING override | Zero cap does not switch ML off: quality-scaled floors remain intentional. Do not misclassify this as the old cap bug. |
| Latest recorded threshold promotions in captured history | October 23 and earlier | The current research-only guard prevents new legacy tuning writes; existing keys still have their normal expiration behavior. |
| RZY journal | 755 rebuild, 3 bootstrap, 1 imported/late, **8 forward observations** | Follow-up: all eight forward observations are `CLOSED`, `cancelled=true`; they are not eight executed trades or eight losses. |
| October 23 RZY session scans | 76; mean **287.0s**, maximum **619.6s** | Different session/release/workload from October 26. |
| October 26 RZY session scans | 120; mean **167.3s**, maximum **539.7s** | Lower observed average, but not a controlled estimate of a particular code change's speedup. Still unsuitable for a minute-scale readiness claim. |
| Scan counters, October 26 | 1,247 `too_late_to_mail`, 10 queued | Counter includes historical/rebuilt emailable events and several event types. **Not 1,247 missed fresh entries**, not a miss percentage, and queueing is not delivery. |
| October 26 evidence capture | No `capture.json` found in the expected destination at the 00:30 UTC read | Capture completion was not established here. Later post-close job status was `ok` at 20:34:35 UTC, but that alone does not verify capture artifacts or 95% per-symbol coverage. |

### Historical return evidence

The [corrected October 20 evaluation](../2026-10-20/little-rzy-evaluation-rerun.md), on its recorded snapshot and code version, reported **692 trades, 24.9% win rate, −0.093R mean net return**. Difference versus matched random timing was **+0.001R**, with symbol-clustered 95% interval **−0.066R to +0.073R**. Costs averaged approximately .094R. Removing the line-close exit raised target hits but worsened mean net return to **−0.200R** and cumulative-R drawdown from **74.8R to 174.9R**.

These are historical research results with assumed execution/borrow, a survivors-only universe and retrospective holdouts. This review did not rerun the full historical study on today's code/data. The evidence rejects a claim of demonstrated edge; it does not prove that no future variant could work. Its already-examined periods must not become a new “unseen” test set.

## Findings and recommendations

Priority: **P1** must be resolved before trusting the affected performance output or activating Intraday execution; **P2** is necessary research/product hardening. “Confirmed” means reproduced or directly established by source, not necessarily an observed production loss.

### F01 — P1, confirmed: confidence-calibration projection rejects every eligible outcome

**Status (2026-10-26): FIXED.** The query now selects `SignalOutcome.exit_date`; the eligibility predicate is unchanged. `__meta__.verified_ineligible_count` exposes verified rows the strict contract still rejects. Regression: `services/signal-engine/tests/test_f01_calibration_query_to_bucket.py` (real SQLite query to published buckets, plus missing-exit, inverted, unverified and same-day controls; all three failed `0 == 35` before the fix). Rerun on current code: signal-engine 958 passed / 1 skipped, RZY 118 passed, ml-prediction trainer 75 passed. Production has 0 verified outcomes, so no published rate changes until verification exists.

**Path:** `services/signal-engine/src/api/signals_shared.py:246–281`; `src/api/calibration_safety.py:evidence_eligible`.

The SQL projection includes signal and entry dates but omits `SignalOutcome.exit_date`. The shared predicate requires `signal < entry <= exit`. A SQLAlchemy Row without `exit_date` returns the helper's default `None`, so every verified row fails. This also prevents the new delivered-subset buckets from forming. Zero currently verified production rows masks the defect; merely refreshing corporate-action evidence will not repair publication.

**Witness:** [calibration-projection-witness.json](evidence/2026-10-26-rzy-ai-intraday-review/calibration-projection-witness.json): a complete stored fixture is eligible; the actual unchanged query/function counts one verified row and zero eligible rows. This loss occurs before minimum sample/date checks.

**Proposed solution:** include every required field in the real projection, retain strict eligibility, and make rejection reasons distinguish missing projected fields from genuinely missing stored data. Add real SQLite/PostgreSQL query-to-bucket tests with at least 30 verified observations over at least five dates, plus invalid timing controls. Do not weaken the predicate to make the UI green.

### F02 — P1, confirmed input-contract gap: AI daily indicators can consume an unfinished day

**Status (2026-10-27): PROVENANCE AND LABELLING BUILT; no signal behaviour changed.** Intraday readings still move. Each generation records `reasons.input_contract` (`generators/input_contract.py`, `f02-input-v1`): last bar date, `forming`/`completed`/`future`/`not_a_session`, basis `provisional_intraday` or `session_closed`, venue's last completed session, completed sessions missing, whether the last weekly bucket is partial, and `close_ingest_verified: null` (no ingest time is stored, so a closed session is a necessary condition for a final bar, not proof). It is in the shared reasons, so the first-fire snapshot freezes it. Confidence calibration admits only `session_closed` first fires; provisional, `unknown` and legacy `unrecorded` rows are counted in `__meta__.input_basis_counts` and excluded. Tests: `test_f02_daily_input_contract.py` (real calendar: US mid-session/after close/weekend/missing sessions/early close, HK lunch, future and non-session bars, unreadable frames) and basis controls in `test_f01_calibration_query_to_bucket.py`; signal-engine 970 passed. **Not yet built:** the separate frozen completed-close snapshot. It needs a stored, immutable record per daily occurrence, which is the same structure F03 designs, so it is specified there rather than bolted onto the mutable `reasons` column.

**Path:** `generators/signals.py:_fetch_prices`, `_check_price_staleness`, `generate_all_signals`; market-data `api/routes.py:get_prices`.

The generator requests the last 400 daily bars from the generic prices endpoint. That endpoint includes today's rows and exposes no completion/ingest-verification gate. The generator calculates TA from the returned frame directly; its age test is `>3` UTC calendar days, with exception fallback to “not stale.” This can consume a partial daily OHLCV or treat missing completed sessions as fresh. A newly stamped Signal does not establish the freshness/completeness of its feature bars. Weekly resampling can also incorporate the current partial week.

Real-time provisional daily estimates may be a valid separate product, but they are not interchangeable with completed-daily training features or completed-session Game Plans. The path currently lacks that explicit distinction.

**Proposed solution:** daily AI horizons consume the shared venue-specific completed-session/per-symbol ingestion contract; record cutoff, completeness, basis and feature availability in each signal. Keep any provisional live estimate clearly labelled, separately evaluated and excluded from completed-close calibration. Test mid-session US/HK, weekends/holidays, missing sessions, failure/unknown records and future timestamps. A staleness score penalty alone is not eligibility.

### F03 — P1, confirmed attribution weakness: delivered outcomes attach to a mutable daily row

**Status (2026-10-27): observation phase DEPLOYED (`1be6c90c`, all 12 backends, verified); sender/receipt slice not started.** Occurrences per venue session, `opened` and `completed_close` snapshots and the first-wins completed-close record (F02's remainder) are written beside the existing columns. Nothing reads them except a churn report. Receipts and delivered outcomes wait for ≥ 5 observed sessions. See [design §12](../features/2026-10-27-f03-signal-occurrence-and-delivery-attribution-design.md#12-observation-phase--built-2026-10-27-not-deployed). Deploying needs all 12 images; the held F04 trainer change is reverted from `prod`.

**Path:** shared `Signal` first-fire fields; `signals_shared.py:_stored_signal_for_style`, `_build_confidence_calibration`; scheduler `check_signal_alerts`; `outcomes.py:evaluate_signal_outcomes`.

The live daily Signal row changes repeatedly. Outcomes score its **first** BUY/SELL snapshot; delivery marks the row ID of the **current** selected signal. The calibration reader then joins that row's strongest delivery state to the first-fire outcome. Example: first SELL at 10:00 is never mailed; later BUY at 13:00 is mailed on the same row. The eventual first-SELL outcome can enter the “sender accepted” cohort even though that thesis was not the delivered one. Confidence changes within one direction have the same issue. The recipient count accumulates acceptances, so it is not necessarily a distinct-recipient count.

**Proposed solution:** immutable candidate/decision occurrences and delivery receipts keyed to occurrence, snapshot and recipient. Score exactly the delivered direction/levels/time; separately retain generated-first-fire outcomes. Acceptance: multiple same-day transitions and changing confidence cannot cross-attribute outcomes; duplicate send/retry does not inflate distinct recipients. Do not retrospectively infer exact delivery identity for legacy rows.

### F04 — P1, confirmed conditional validation defect: no label purge at the final ML split

**Status (2026-10-27): HELD — preserved on branch `f04-threshold-report-purge`, reverted from `prod`** so `prod` is the release intended for deployment. It stays held until evaluation-version and incumbent-retention behaviour are resolved; enlarging the test slice alone does not fix those. Original commit `28f232bd`: `_threshold_report_bounds()` puts a `horizon`-row gap between the threshold-selection and reporting halves; a slice that cannot afford it falls to the suppressed in-sample fallback. Regression: `services/ml-prediction/tests/test_f04_threshold_report_purge.py` (8 of 9 fail without the gap).

**Measured impact (read-only, all 1,435 production artifacts, [f04-impact.json](evidence/2026-10-26-rzy-ai-intraday-review/f04-impact.json)).** My earlier estimate ("mainly LONG below ~600 rows") was wrong: after dead-zone filtering most artifacts split far fewer rows. Of 932 `holdout` artifacts, **590 (63%) could no longer reach the 10-row reporting floor** under the purge: SWING 196, GROWTH 206, LONG 144, SHORT 44. **38 of the 109 currently unsuppressed artifacts are among them** (SWING 15, GROWTH 14, SHORT 5, LONG 4). 375 artifacts predate the mode field. Row gate only; class support of the new reporting rows is unknown without retraining.

**Second-order effect that blocks deployment as-is.** R02's incumbent rule keeps an existing artifact when the new candidate's evaluation is invalid and the incumbent's is `evaluation_valid`. All 590 are marked valid under the un-purged split, so after deployment their retrains would be refused and these artifacts would be **frozen, not suppressed** — including the 38 that serve. Options: (a) treat an incumbent without `n_report_purge_rows` as not valid for the incumbent rule, so the purged candidate replaces it (38 serving models become suppressed); (b) enlarge the test allocation so the purge is affordable (changes training data); (c) keep the fix undeployed until (a) or (b) is chosen.

**Path:** `services/ml-prediction/src/training/trainer.py:1405–1432`.

The trainer has real embargoes between train/early-stop/calibration/test and suppresses insufficient evaluation evidence. However, it then divides the final test slice into threshold-selection and reporting halves with **no purge between those halves**. A threshold-selection example's future label can extend into the reporting period. The branch is labelled `holdout` based on row count/classes, not label-window separation.

**Witness:** [ml-threshold-boundary-witness.json](evidence/2026-10-26-rzy-ai-intraday-review/ml-threshold-boundary-witness.json) executes the shipped split assignments on consecutive-session examples. The last five-session threshold label matures after the first reporting feature date. This establishes a reachable overlap, not leakage in every historic artifact. Threshold-selected accuracy/precision are affected; this finding alone does not invalidate threshold-independent AUC.

**Proposed solution:** split by actual label maturity and information availability, purge/embargo at **every** selection/reporting boundary, record exact dates/counts, and suppress insufficient artifacts. Evaluate on all eligible opportunities, not only the retained positive/negative dead-zone labels. Test boundary equality, missing bars, filtered rows and small samples.

### F05 — P1 before simulator activation: next-open fills are not causal to live detection

**Path:** RZY `little_rzy.py:Engine._positions`; `rzy_data.py:prepare_bars`; `rzy_journal.py:record_positions`.

The engine fills at the next bar's open. The adapter waits two minutes after a candle closes, plus scan/queue delays. The journal calls an incremental trigger forward when seen within timeframe +3 minutes. For a 5m trigger closing 10:05, detection at 10:07 still qualifies, while the modelled next-open fill is 10:05. There is no condition requiring fill availability after detection or order readiness.

**Witness:** [rzy-fill-clock-witness.json](evidence/2026-10-26-rzy-ai-intraday-review/rzy-fill-clock-witness.json) uses the existing fixture and actual engine: modelled fill precedes assumed detection by **120 seconds**. Live stored-price streams remain `execution_eligible=False`, so this is a blocked activation defect, not evidence of actual broker orders or current paper profits.

**Proposed solution:** keep idealized historical replay separate from causal forward simulation. Freeze `data_available_at`, `detected_at`, `order_ready_at` and first eligible quote/bar. Fill only on a subsequent executable observation; use bid/ask, delay, spread, slippage, partial fills and nonfills. A missed entry remains missed. Test delayed scans, reconnect/restart and backlogged trigger+fill processed together.

### F06 — P1 feature gap: neither SHORT nor an RZY 5m chart is the requested Intraday horizon

**Path:** shared `SignalHorizon`; `generate_all_signals`; `little_rzy.py:_positions`; `rzy_data.py:break_indices`; signal API style lists and frontend types.

There is no INTRADAY enum/generator/model/outcome path. SHORT predicts five daily bars. RZY supports intraday candle sizes but preserves state across complete adjacent sessions and has no mandatory session-end liquidation or entry cutoff. That is not a violation of an explicit existing day-end rule—the rule is absent—but it does not meet a same-session trading requirement. Adding an enum alone would route some consumers into daily logic or a SWING fallback (`routes.py:886–888`).

**Proposed solution:** implement the separate horizon contract in the companion plan: session IDs, completed 5m triggers/15m context, causal entries, expiry, maximum hold and end-of-session management, timestamped outcomes, independent alert preferences and explicit unsupported-style errors. Keep RZY strategy identity distinct from AI source/horizon.

### F07 — P2, confirmed research-cohort limitation: corrections can remove forward results

**Path:** `rzy_journal.py:summarize`, `paper_report`.

Scores exclude superseded generations and use the current watchlist/configuration over a 90-day reporting window. That is disclosed and useful for a current reconstructed view. It is not an immutable record of decisions that were actually observed: a later revision or watchlist change can remove a winning or losing observation from the aggregate. Rebuilt replacements are correctly not relabelled forward, but the original forward denominator still changes.

**Proposed solution:** two separately named views: immutable **as-observed forward decisions/fills** and **corrected-data replay**. Freeze universe/policy membership at observation, retain revision lineage, and report all censoring/cancellation/supersession flows. Original actual executions must never disappear because a chart was rebuilt. Freeze a cohort before comparing return improvements.

### F08 — P2, confirmed measurement limitation: controls and uncertainty are not a promotion test

**Path:** `rzy_evaluation.py:matched_controls`, `summarize`, `report`.

Random controls are conditioned on the real trade's realized holding duration, draw from the entire segment, and do not reproduce the structural line exit. Period reporting splits real trades by entry time after controls have already been sampled. Thus an earlier-period trade's control may lie in a later period. The report correctly calls these retrospective diagnostics, but these are not independent out-of-sample comparisons of a tradable control strategy. Symbol clustering does not cover shared-date market shocks; mean-R intervals and minimum detectable effect still use by-trade calculations. Small directional slices and many variants further limit inference.

**Proposed solution:** retain the descriptive control, add ex-ante control strategies with matching feasible entry/cost/exit policies, restrict controls within each fold/session stratum, purge crossing labels/trades, and use date-block/multiway uncertainty checks. Report trial counts and freeze a final unseen period. Keep survivorship and unavailable-data exclusions explicit. No new optimizer should promote a winner from the old snapshot.

### F09 — P1 operational readiness remains open: latency, freshness and execution evidence

**Path:** `rzy_workflow.py:scan`, `rzy_data.py`, `rzy_monitoring.py`, scheduler `check_little_rzy_workflows`.

The latest observed scan average is almost three minutes with a nine-minute maximum. Alphabetical serial processing and full history/revision preparation remain. The fixed scan-start data cutoff becomes increasingly old for later symbols. Unknown provider delay and unavailable short borrow remain actual blockers. New write observations are valuable, but best-effort telemetry is not a transactional correction watermark or proof of exchange availability. Research price bars alone lack spread, executable quotes and order acknowledgement.

**Proposed solution:** validate the preserved full-session report first; measure close → availability → detection → notification separately with all eligible opportunities in denominators. Design bounded incremental processing only with reliable correction/deletion tracking or mandatory fingerprint/fallback. Add per-stream fairness, stale-work coalescing and separate trade management from expensive setup scans/rebuild pauses. The 600-second workflow lease needs renewal/fencing or proven bounded duration before parallel workers are introduced; a historical scan exceeded that lease, but this review did not establish an actual overlap.

Do not turn unknown execution flags to true or merely increase expiry to conceal lateness. Production-data readiness and profitable edge are independent gates.

### F10 — P2, confirmed semantic mismatch: prediction, score outcome and hold duration differ

**Path:** trainer `_HORIZON_BY_STYLE`; `signals_shared.py` hold tables; Game Plan and paper style policies.

| Horizon | ML target (daily bars) | BUY primary evaluation (calendar days) | SELL primary evaluation (calendar days) |
|---|---:|---:|---:|
| SHORT | 5 | 7 | 5 |
| SWING | 10 | 14 | 7 |
| LONG | 20 | 28 | 10 |
| GROWTH | 15 | 14 | 7 |

Different windows can be legitimate distinct products; they cannot be advertised as the same validated forecast. Outcomes use next-session-close entries and a **0.5% hurdle**, not an actual trade's stop/target/net return. SELL also needs to distinguish exiting a long from opening a borrow-dependent short. GROWTH is a strategy family as well as a horizon.

**Proposed solution:** version prediction window, evaluation unit/window, entry convention and managed maximum hold separately. Preserve legacy history under its original convention; add aligned 5/10/20/15-session forecast studies and independent managed-trade results. Rename SHORT in UI to “Short-term” to avoid confusion with short-selling.

### F11 — P2, confirmed activation limitation: existing overrides and policies are not immutable per decision

**Path:** dynamic threshold/ML readers; daily first-fire reasons and `SignalOutcome`; `calibration_safety.py`.

Research-only decorators now block all eight legacy mutation endpoints, including watchdog. That is a real improvement. They neither clear old Redis keys nor stop keys expiring back to fallback values. First-fire reasons retain useful model/context data, but there is no complete, immutable resolved policy version that lets an auditor replay all thresholds, gates and expiry transitions for each candidate. Generated BUY/SELL-only outcomes also cannot evaluate candidates a loosened policy would newly admit.

**Proposed solution:** snapshot the complete resolved policy and source/version per immutable candidate, including rejected candidates and input availability; expose override sources/expiry. Research comparisons must replay the whole decision and execution pipeline against the effective incumbent. Any deliberate policy change needs its own version and promotion record, not an interpretation of a Redis cap.

### F12 — P2, contained legacy defects: disabled tuning bodies must not simply be re-enabled

**Path:** `calibration.py:outcomes_calibrate_apply` and `calibration_safety.py:research_only`.

Behind the hard guard, the BUY body indexes `train_bucket[0]` before handling an empty training set after purging. The SELL body still lacks the BUY evidence filter and session purge, uses an inclusive threshold different from runtime's strict SELL comparison, and compares to a default rather than the full effective incumbent. Other legacy searches omit full costs/exits. **These bodies do not currently execute through the guarded endpoints**, so they are not active automatic-tuning failures.

**Proposed solution:** keep the guard. Replace the research/promotion architecture and its exact population/baseline contracts before activation; add empty/purged-set, equality, negative-EV, regime/market and policy-version tests. Do not remove decorators as a shortcut.

## What is implemented coherently and should be retained

- RZY confirmed pivots are available only after right-side candles complete; anchors/geometry freeze, equality handling is deterministic, unknown sequence history is labelled. With right=7, confirmation alone costs seven bars (35 minutes on 5m; seven hourly candles can span sessions). That is a strategy tradeoff, not a repainting defect.
- Completed/settled-bar filtering, expected latest-bin freshness, missing-bar segmentation, native/derived source choice, generation-aware IDs and same-occurrence mail dedup have explicit code/tests. Rebuild pauses do not pretend to be live signals.
- Paper execution requires actual boolean eligibility and shortability/borrow; default stored bars block fills. Stops precede targets when OHLC order is unknown; gaps, friction, commissions and borrow are modelled. These conservative assumptions still need causal live availability (F05).
- Email subjects clearly say research; WATCH_ONLY is not an entry, a triggered setup is not a fill, and paper events are not broker orders. Preferences are rechecked at send time. Invalidated/expired renderers do not by themselves promise subscribed lifecycle emails.
- Simulator cancellations/unresolved trades do not become fabricated P&L. Superseded/history views and paper/data limitations are disclosed.
- AI uses style-specific fusion and filters; ML quality suppression and the shared cap/floor calculation are present. The UI calls confidence **Signal strength** and distinguishes the composite score from a calibrated profit probability.
- Calibration now uses strict evidence for visible diagnostics, whole-date purging in repaired paths, shared fusion maths, and a non-toggleable promotion guard. The October 21 audit's active-writer findings are therefore **partly superseded**; F01 and remaining legacy limitations are today's findings.

## Improving performance without optimizing the wrong objective

Use net expectancy, drawdown, tail loss, capital/exposure, turnover and execution feasibility alongside win rate. A strategy winning 70% at +0.3R and losing 30% at −1R has **−0.09R before costs**; one winning 40% at +2R and losing 60% at −1R has **+0.20R before costs**. Neither example predicts this platform's results. RZY's existing stop/target-only experiment already illustrates why more target hits need not improve return.

Recommended research order:

1. **Causal data/latency and cost filters:** evaluate first actually available fills, measured spread/impact and gap/no-fill behavior. Report coverage loss. These improve fidelity; they may lower reported returns while making them more believable.
2. **Liquidity/time-of-day and regime separation:** one preregistered time-normalized volume/spread filter; trend-continuation and range-reversal remain separate strategies. Compare accepted and rejected opportunities, not just the winners.
3. **RZY context/confirmation:** test one higher-timeframe alignment variant; separately test a shorter pivot-confirmation window with all pivots still causal. Quantify earlier entry versus false triggers. Do not scan all combinations and select the best cell.
4. **Exit/target experiments:** retain the line-exit baseline. Test a single time stop or narrowly specified volatility-normalized invalidation variant only after entries are measurable. Smaller targets may raise wins while cutting expectancy; larger R/R may reduce realized target frequency. Partial profits require exact weighted quantities/costs.
5. **AI ablations and calibration:** compare TA-only, ML-only, current fused policy, and simple regime/context baselines on identical frozen opportunities and costs. Keep horizons/markets/directions separate; use reliability, Brier/log loss and all-candidate base rates before calling any score a probability. Adding another LLM is not a substitute for this evidence.
6. **Portfolio policy:** enforce one combined risk budget per symbol across timeframes/sources, correlated-exposure limits and realistic capital constraints. Multiple RZY/AI confirmations from the same underlying bars are not independent votes.

Choosing many rules on the same historical sample raises false-discovery risk; the experiment registry and untouched final period in the plan address that risk. See [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf). Execution modelling must also distinguish last-trade prices from bid/ask fills and stop/limit instructions from guaranteed outcomes; see [SEC Investor.gov, Types of Orders](https://www.investor.gov/introduction-investing/investing-basics/how-stock-markets-work/types-orders).

## Review outcome and next action

Proceed with the companion implementation plan only after review. F01–F04 address measurement/data defects; F05/F06/F09 are prerequisites to a credible same-session simulator. F07/F08/F10/F11 underpin improvement claims. F12 stays contained. Current research use remains reasonable with the stated limits; **this audit does not approve either system for autonomous trading or identify a proven profitable setting**.


## F01/F02 production rollout — October 27, 2026

**Deployed signal-engine only from `7084299d`**, using the existing remote deployment script under the host-wide lock, pinned to the reviewed commit. Previous signal-engine image retained as `stockai-signal-engine:rollback-before-7084299d`; old logs and identity saved under `/home/ec2-user/deploy-evidence/2026-10-27-f01-f02-signal-engine/`. Fresh full signal-engine suite before rollout: **970 passed, 1 skipped**.

Independent post-deploy checks: **15 containers healthy; only signal-engine restarted; its `src` and `shared` hashes match the release**. The ml-prediction image ID, start time, `src` and `shared` hashes are identical to preflight. All other services' source hashes are also unchanged. No trainer deployment or retrain was initiated. F04 remains explicitly **held**, including its incumbent-retention/evaluation-version decision.

The loaded calibration builder executed against production PostgreSQL in a READ ONLY transaction: **26,021 resolved rows**, 26,004 timing-eligible, **0 verified**, **0 eligible**, **0 published buckets**. Seventeen same-day entries were excluded. `input_basis_counts` is empty because that counter applies after the verified/timing gate; it is not a count of all historical provisional inputs. The strict gate remains intact. Pure fixed-clock checks against the loaded F02 implementation return provisional for US mid-session/HK lunch and session-closed after US close, with `close_ingest_verified=None`. These are fixed fixtures, not new generated production signals or independently verified closing prices.

**Partial-release drift is deliberate and recorded, not zero:** eleven backends retain the older shared calendar relative to this repo. market-data also has pre-existing service-source drift; ml-prediction has intentional trainer/source drift. These differences existed in preflight and were not expanded into an unapproved all-service deployment. Signal-engine now contains the corrected calendar; other services were left untouched under the explicit scope. A coordinated calendar rollout remains separate and must preserve the F04 hold.

Deployment ended with about **25 GB free** and sampled I/O wait **3.1%**. [Source/image/start identities, actual query metadata, test log and verification script](evidence/2026-10-27-f01-f02-deployment/). F01 is deployed; F02 provenance and completed-session calibration eligibility are deployed, while the immutable completed-close record remains F03 design work. F03 review decisions and pre-build corrections are recorded in its [design addendum](../features/2026-10-27-f03-signal-occurrence-and-delivery-attribution-design.md#11-review-decisions-and-pre-build-corrections-october-27-2026).
