# StockAI production verification audit

**Audit file date:** 2026-09-17 (America/Los_Angeles).  
**Verified source baseline:** `cb00e42`; T400 follow-up to the original audit at `985d12e`.  
**Scope:** authorized read-only production verification and local regression checks.  
**Related report:** [System audit and trading roadmap](2026-09-17-system-audit-and-trading-roadmap.md).

This separate audit records the production findings previously appended to the system report. Finding IDs A01–A16 refer to that report; A17 is the additional settlement regression identified here. The observations retain their original timestamps; moving this record does not imply a new production scan.

**Same-day addition:** section 6 records the subsequent September cohort findings and prioritized solutions, using production observations at 23:52:49–23:54:49 UTC. The full supporting analysis is in the [September cohort audit](2026-09-17-september-cohort-audit-and-recommendations.md).

**Feedback review:** section 7 reviews Claude's open-item feedback, independently verifies shared-code drift and options positions, and distinguishes pending outcomes from work that should be completed before those outcomes arrive.

**Observation window:** 2026-09-17, approximately 23:39–23:42 UTC (16:39–16:42 America/Los_Angeles). This audit supersedes the original report’s runtime-unknown statements only for the specific checks listed here.

The user supplied production SSH access after the original report. Checks used SSH, `docker ps`, Git revision reads, selected file hashes, aggregate SQL and allowlisted Redis job-status reads. Database connections enforced `default_transaction_read_only=on`, repeatable-read isolation, an eight-second statement timeout and a one-second lock timeout. The transaction itself reported `read_only=on`. No broker APIs, application run-step endpoints, schedulers, migrations or administrative mutations were invoked. No credential values, personal user records or broker account identifiers were returned.

## 1. Deployment and broker mode

All 15 containers reported healthy: 12 backend services, frontend, PostgreSQL and Redis. This confirms their configured health checks at observation time, not correctness of predictions or execution.

The host checkout initially reported `985d12e3` and later `cb00e42c` while separate T400 work was landing. Files inside the running market-data container matched the local final checkout for these paths:

| File | SHA-256 prefix, matching local/container |
|---|---|
| `services/paper_trading_engine.py` | `1ee087a710ea35f0` |
| `services/options_income_engine.py` | `a0bcbb5e06838122` |
| `services/scheduler.py` | `11aa4eb7bf59de58` |

These are file-level checks, not proof that every module loaded by every worker, image, model artifact and runtime flag matches the repository. The latest options equity record also provides behavioral evidence consistent with the liability fix.

There were **11 active stock paper portfolios**: ten without a broker link and one with a link. The broker-connections table contained **one active `etrade_sandbox` connection, marked unauthorized**. Across all 128 stored stock trades, **zero had a broker order ID**; four closed US records had a non-null broker-error field. Error contents were not retrieved.

**Interpretation:** no current production-money connection or recorded executed broker order was found in the inspected tables. A01–A03 remain blockers before enabling real execution, but this snapshot does not establish an ongoing live-money exposure. It also does not prove anything about external brokerage activity outside these application records.

## 2. Current stock paper records and the SWING experiment

The following are aggregate **stored closed-trade** results across the retained history and strategy/configuration versions, not a fresh backtest or return on total portfolio equity:

| Market inferred from symbol | Closed trades | Winners | Win rate | Sum of stored P&L, native ledger units | Profit factor |
|---|---:|---:|---:|---:|---:|
| US | 103 | 33 | 32.0% | −3,446.06 | 0.666 |
| HK | 19 | 7 | 36.8% | −4,295.99 | 0.671 |

US units are presumed USD and HK units presumed HKD from the market conventions; the query did not verify an explicit currency field on every trade. **Do not add these P&L values together.** The source rows span entries beginning June 16 for US and June 25 for HK. There were also five open US stock trades and one open HK stock trade; unrealized results are excluded above.

Style slices were US GROWTH 46 closed / 34.8% wins / −1,232.25; US SWING 57 / 29.8% / −2,213.81; HK GROWTH 15 / 46.7% / +2,314.68; HK SWING four / 0% / −6,610.67. These small, overlapping and differently sized paper samples do not support selecting a winning style simply from the positive HK GROWTH sum. Dollar-weighted P&L and mean percentage return can differ because trade sizes differ.

Portfolio **891** exists, is active, was created on September 16, and has `stop_pct_override=0.925` and `atr_stop_mult_override=2.5`. The price multiplier corresponds to a 7.5% fixed stop distance. It had **zero trades** at the snapshot. Other sampled US SWING portfolios had no explicit values for those override keys. This verifies experiment configuration, not outcomes or complete matching of every control variable/runtime overlay.

The recent signal-outcomes query found **2,154 stored resolved primary outcomes** with signal dates in the previous 30 days: 995 SHORT BUY, 165 SHORT SELL, 268 SWING BUY, 178 SWING SELL, 154 LONG SELL, 252 GROWTH BUY and 142 GROWTH SELL. It did not establish that these rows satisfy the clean/frozen cohort criteria needed for calibration. Do not replace a historical clean-sample threshold with this raw count. Missing or unresolved signals not yet represented in that table were not counted.

## 3. Options status and fixes now present

Separate T400 work is described in [its review record](2026-09-17-audit-review-and-t400-fixes.md). Direct source inspection confirms:

- **A04 core fix:** equity now subtracts a short-option liability based on an archived ask, with an intrinsic-value fallback.
- **A05 date fix:** the engine and backtest require the expected settlement session instead of substituting any previous close. A separate regression in the engine is documented below.
- **A13 shell fix:** the Makefile accumulates failures across services and exits nonzero if any required service failed. The previously noted `main`/`dev` workflow triggers remain a separate release-coverage issue.
- **A14 fixture update:** research tests now reflect the implementation's documented missing-data behavior; the local follow-up passes.

Production had **one active options-income portfolio**, initial capital **250,000**, with **six open cash-secured puts and no closed positions**. Entries were September 16–17; expiries ranged from September 25 to October 30. No open position was overdue. Thus there is still **no resolved forward options-income track record** in these tables; the historical contract backtest is a different source of evidence.

The latest options curve, dated September 17, contained:

```text
Cash                          66,467
Reserved collateral          186,600
Implied short liability        3,087
Reported equity              249,980

66,467 + 186,600 − 3,087 = 249,980
```

Open premium receipts totaled 3,067. The observed curve therefore no longer simply adds the full premium to starting equity. The 20-unit difference from initial capital is not a validated strategy return or independently priced broker equity. Earlier curve rows were not re-audited or corrected by this follow-up.

**Remaining valuation limits:** `_latest_option_ask` takes the latest archived ask without a freshness bound; an intrinsic-only fallback can omit substantial time value. The function returns a mark-source label, but the snapshot loop does not persist that label or its quote time. Add mark timestamp/source/quality and flag insufficient marks rather than treating fallback equity as comparable to a complete liquidation valuation. This makes A04's central omission fixed while leaving a broader mark-quality task open.

All 13 sampled income-universe symbols had a latest archived chain date of **September 16**. The chain-capture Redis status recorded `ok` at 22:45:22 UTC on September 17; this is consistent with capturing the previous session's archive and is not by itself a missed-current-day diagnosis. It remains unsuitable to treat that archived bid as a synchronous executable September 17 quote (A06).

The income-step status recorded `ok` at 23:05:44 UTC with a duration of **344 seconds**. The deployed `_latest_option_ask` makes one contract lookup per position; the inspected index list had no index beginning with `option_symbol`. Profile that query and consider a batched lookup or suitable composite index in an approved migration. The 344-second measurement alone does **not** prove those lookups caused the duration; no expensive production `EXPLAIN ANALYZE` or backtest was run here.

## 4. A17 — P1: T400 settlement counter is overwritten by the price/date tuple

**New finding; source-confirmed and locally reproduced against `cb00e42`.** In `settle_expired_positions`, the numeric variable `settled` is initialized to zero, then reused for `_settlement_close(...)`, whose successful return is a `(price, session_date)` tuple. After mutating cash and position fields, the code executes `settled += 1`.

The isolated source-function probe supplied one expired position and a valid settlement result. It produced:

```text
TypeError: can only concatenate tuple (not "int") to tuple
position.stage after exception: closed
portfolio cash after exception: changed from 100 to 10,100
session.commit calls inside the settlement function: 0
```

These were fake in-memory objects, not production records. The deployed file hash matches the inspected source. No production settlement was invoked, and the earliest currently open income expiry was September 25, so this check does not establish a historical settlement incident.

**Impact:** the first valid expiry settlement raises after mutating ORM objects but before the intended commit/count return. The caller catches the exception without an explicit rollback, so later operations may flush or commit partial state. Multiple expired positions may not all be processed. If the last attempted lookup is missing, the same variable reuse can also return `None` rather than the advertised integer count. An `ok` scheduler status is not sufficient to detect this class of inner failure.

**Solution:** use distinct variables such as `settlement_result` and `settled_count`; keep the result count an integer in every path. Ensure the transaction boundary either commits the intended settlement set or rolls back failed mutations, and reports partial batch failure accurately. This audit documents the fix but does not implement it.

**Required acceptance tests:** execute the actual settlement function with zero, one and multiple expired positions, mixed available/missing session closes, and a failure after the first mutation. Assert integer return values, processed-position count, cash conservation, atomic commit/rollback behavior and correct retry behavior. The current source-string checks for the new helper do not catch this regression.

## 5. Follow-up test results and interpretation

At `cb00e42`, local sequential checks produced:

| Check | Result |
|---|---|
| Entire research-engine suite | **81 passed** |
| Options-income test file | **54 passed** |
| API gateway suite | No completion within the follow-up's 45-second limit |
| Decision-engine suite | No completion within 45 seconds |
| Event-intelligence suite | No completion within 45 seconds |
| A17 isolated settlement probe | Reproduced the tuple/integer error described above |

The separate T400 review reports successful runs of the three previously timed-out suites in its environment. That is useful additional evidence, but its explanation that concurrency alone caused this audit's timeouts is **not established**: this follow-up ran those suites one at a time and still reached its limits. Environment differences or test behavior need diagnosis before assigning a root cause. No claim of a production outage follows from these local timeouts.

The passing options test file alongside the reproduced A17 failure reinforces the need for behavior-level settlement tests. No application source was changed by this follow-up, no production state was repaired, and `CLAUDE.md` was left untouched by this audit.

## 6. September improvements, remaining issues, and recommended solutions

**Observation time:** September 17, 23:52:49–23:54:49 UTC. Local revision `4b885e6`; inspected trading-engine hashes matched production. These results come from the subsequent read-only cohort analysis, not a new scan performed when this section was added.

### 6.1 Recent fixes should be evaluated separately from older losses

September contains multiple implementation versions. All-history results alone cannot establish whether the newest fixes helped. Group trades by entry date, show unresolved positions, and distinguish deployment cohorts from calendar months.

| US entry cohort | Entries | Closed / open | Realized P&L | Estimated open-position P&L | Combined trade P&L estimate |
|---|---:|---|---:|---:|---:|
| September 1–8 | 9 | 7 / 2 | −$1,572.17 | −$469.97 | −$2,042.14 |
| September 9 onward | 3 | 1 / 2 | +$74.07 | +$525.44 | +$599.51 |

The later cohort is encouraging but has only **one closed trade**. Open marks are not realized profits, do not establish executable liquidation prices, and exclude future exit costs. September 9 is an exploratory boundary after the September 7–8 source-fix cluster, not a verified deployment cutoff for every change.

Across all September US entries, eight trades had closed with one winner and **−$1,498.10 realized P&L**. SNOW and DELL, both entered September 3, account for **87.6% of that net loss**. Existing incident notes associate those trades with a gap/chasing-filter blind spot. Keep those losses in the ledger, tagged with incident/version context; do not use their removal to claim actual profitability.

September HK trading has one open entry and no closed trades. Options income has six open positions and no closed trades. Neither supports a resolved September win-rate conclusion.

### 6.2 Signals show mixed improvement

| Signal cohort | August hit rate | Resolved September hit rate | September sample |
|---|---:|---:|---:|
| US SHORT BUY | 38.68% | 25.86% | 174 |
| HK SHORT BUY | 36.03% | 22.97% | 74 |
| US SHORT SELL | 36.67% | 61.17% | 103 |
| US SWING SELL | 40.00% | 64.41% | 59 |
| US GROWTH SELL | 44.58% | 56.82% | 44 |
| HK SWING SELL | 29.41% | 67.44% | 43 |
| HK GROWTH SELL | 23.08% | 60.00% | 35 |

These are the stored five-calendar-day forward-price hit flags, using a directional 0.5% hurdle—not actual net trading win rates. September observations in these groups cover only four to six distinct signal dates, chiefly September 3–10. They do not measure the newest September 15–17 signals. August lacks September's frozen first-actionable state, so this is a descriptive comparison, not a controlled estimate of a fix's impact.

SELL groups merit further investigation against a same-date bearish baseline; a declining market could explain part of the improvement. SHORT BUY remains weak in the resolved sample. Higher BUY confidence does not show reliable improvement, so simply raising the confidence threshold is not an evidence-backed solution.

The frozen cohort has grown to **3,239 actionable signals and 607 resolved outcomes**, compared with the earlier reported 184 resolved rows. However, the 607 comprise 248 SHORT BUY and 359 SELL outcomes. Recent SWING/LONG/GROWTH BUY groups still have no stored resolved five-day outcomes. Sample growth must be assessed within each intended prediction target.

### 6.3 Additional issues found in the cohort review

**C01 — Fix-effectiveness tracking cannot yet reliably answer whether fixes helped.** Two September 3 fixes are due for their configured 14-day recheck and have zero snapshots. The selected job status last reports success on September 14. In source, `take_fix_snapshot` calls `_compute_ai_signal_win_rate_metrics(session)` without its available `since` parameter, so a snapshot includes full history instead of just post-fix observations. The registered decision-making domain also lacks a supported snapshot calculator.

**Solution:** apply the effective deployment/frozen-decision cutoff; separate market, style, direction and maturity; implement or explicitly flag unsupported domains; add durable catch-up and overdue alerts. Verify that changing pre-fix rows cannot change a post-fix snapshot. Investigate the missing scheduled recheck without assuming a cause from the status alone.

**C02 — Early outcome feedback is delayed for longer BUY styles.** The evaluator waits for the primary hold window before inserting a row and calculating its auxiliary 5/10/20-day returns. A shorter window can therefore be observable but absent from the table while the primary outcome remains immature.

**Solution:** create a pending evaluation record and resolve each horizon independently. Preserve separate pending/resolved/missing-price states, immutable signal identity, and idempotent updates. Test that a LONG BUY receives its early result without fabricating the later primary result.

**C03 — Horizon definitions differ.** Stored `return_5d` targets calendar days, while some model/style descriptions refer to trading sessions.

**Solution:** persist the horizon unit, target and actual sessions, and label version. Introduce explicitly named session-based metrics without silently relabelling legacy calendar-based data. Test weekends, US/HK holidays and price gaps.

**C04 — One active stock has stale daily data.** Price heads dated at least September 16 exist for 137/138 active non-delisted US stocks and 42/42 HK stocks. SSNLF's latest daily bar is November 7, 2025. Recent head dates do not prove complete or accurate history, and SSNLF's staleness is not proof of delisting.

**Solution:** apply a data-quality eligibility hold to stale inputs and investigate provider/instrument coverage. Block executable new-entry playbooks when critical data fails its freshness contract, while displaying the reason clearly.

### 6.4 Prioritized implementation and validation plan

1. **Fix A17 before the next income expiry.** Separate the settlement tuple from the integer count; define commit/rollback boundaries and partial-batch failure reporting. Add behavioral tests for zero, one and multiple expired positions, missing prices and injected failures.
2. **Repair post-fix measurement (C01).** Use explicit cutoff/version information, restore scheduled snapshots and expose unsupported domains. Publish pre-fix, transition, post-fix and unresolved cohorts separately.
3. **Resolve horizons independently (C02/C03).** Improve feedback availability without treating immature outcomes as losses or changing metric definitions invisibly.
4. **Validate a frozen current playbook.** Diagnose SHORT BUY by regime, gap/extension, earnings proximity and entry timing. Test promising SELL information separately for avoiding long exposure or managing exits before considering separate short/options strategies. Change one variable at a time and evaluate later untouched dates.
5. **Finish options valuation and replay controls.** Liability deduction is now present, but mark timestamps, stale/intrinsic fallback disclosure, executable quote eligibility, capital-constrained selection and purged outcome windows remain necessary.
6. **Complete broker order management before real automation.** Require explicit modes, stable intents, actual-fill accounting, partial/full-exit reconciliation and crash recovery. The earlier same-day snapshot found only unauthorized sandbox linkage; no live enablement was performed.

The dashboard should offer all-history, entry-month, since-deployed-fix and frozen-experiment views. Each needs opportunity counts, unresolved outcomes, realized/unrealized separation, currency, costs and data-quality status. This makes recent improvement visible without hiding historical losses or promoting an unproven small sample.

Full cohort tables, formulas, source references and acceptance criteria are preserved in the [September cohort audit and recommendations](2026-09-17-september-cohort-audit-and-recommendations.md). This section documents the work; it does not claim the recommended fixes have been implemented.

## 7. Review of Claude's open-item feedback and recommended actions

**Review date:** September 17, America/Los_Angeles. The new production database snapshot was taken at **2026-09-18 00:03:29 UTC**, which is still September 17 locally. This review used read-only file manifests, bounded read-only SQL, and local source/history inspection. No images were rebuilt, containers restarted, orders submitted, application code changed, or `CLAUDE.md` modified.

### 7.1 Assessment of the feedback

The recommendation to replace persistent shared-code drift with durable image builds is sound. The admission that several findings were not examined is also important: **unexamined does not mean resolved or disproved**.

Three qualifications change the action plan:

1. **Drift is not the only confirmed open issue.** A17 remains in the inspected source, and the fix-effectiveness measurement problems in section 6 remain observable. A07, A10 and A11 also have concrete source evidence.
2. **Waiting for September 25 does not replace settlement readiness work.** The first three options positions belong to an earlier selection state and violate some current admission rules. Their settlement will provide useful forward paper observations, but cannot validate the current strategy's reported 69% backtest win rate.
3. **All-put selection is only partly explained by available evidence.** Both strategies are enabled. Current concentration limits explain why one comparable covered-call purchase would be excluded, but do not explain all six puts. Historical candidate and rejection records are missing from the inspected income path.

### 7.2 Shared-code drift: independently confirmed, with low immediate observed impact

A per-file SHA-256 comparison of all Python files under `shared/` found:

| Services | Result against host/local shared source |
|---|---|
| market-data, api-gateway | All checked shared Python files match |
| signal-engine, ml-prediction, research-engine, ranking-engine, strategy-engine, technical-analysis, portfolio-optimizer, event-intelligence, news-intelligence, decision-engine | Exactly two differing paths each: `db/models.py` and `db/__init__.py` |

The older `models.py` content matches revision `cdca5958`; the old `db/__init__.py` matches historical content at `f135018b`. The model difference is 112 added lines for the three options-income tables, and the initializer difference adds their imports/exports. Other checked shared Python files matched. This supports the additive-change explanation and narrows the finding beyond a generic whole-directory warning.

The host checkout reported `cb00e42`, while local HEAD was `4b885e6`. These two revisions differ only in two audit documents; host shared files exactly matched local shared files. Thus there is a harmless documentation-only Git revision difference at this snapshot, while the **container shared-code drift is real and separate**. This does not contradict an earlier statement that Git matched at a different observation time.

No direct options-income model use was found in the other services' application source. Both older schema files are internally consistent there. That makes the immediate observed compatibility risk low. However, `db/__init__.py` eagerly imports the model exports: mixing the new initializer with an old model file could fail on package import. Treat the shared package as one versioned unit, rather than copying individual files.

**Recommended durable remediation:**

1. Pin the intended application revision and record the existing image IDs for rollback. Verify the host has resources for builds without starving running ingestion, prediction or trading jobs; the quoted 15–30 minutes is an estimate, not a measured duration here.
2. Build immutable images for the ten affected services from that same revision, including the complete shared package. Ensure the two already-current services also have a recorded image/source identity; a file match alone is not a complete image-provenance check.
3. Recreate services in a controlled sequence, starting with a service that has no critical scheduled work. Check shared imports, health, authorization and representative read paths before proceeding. Preserve singleton ownership of scheduled jobs and account for restart/catch-up behavior.
4. Compare per-file manifests across all 12 backends, verify image IDs/revisions, and confirm that the checked source is part of the built image. Rehearse or otherwise validate recreation from those images so runtime-only hotfixes are not mistaken for durable deployment.
5. Roll back the affected image if compatibility/smoke checks fail. Do not rely on a partial shared-file copy as the final repair.

Do not permanently silence the ten drift warnings. If a short transitional exception is necessary, make it exact to the service, path and expected hash, with an owner and expiry.

**Drift-checker improvement:** [check_deploy_drift.sh](../../scripts/check_deploy_drift.sh) labels its comparison “local git HEAD” but hashes the local working tree. It also prints `docker cp`/restart advice. Compare against a pinned committed/build manifest, expose a dirty-tree distinction, report differing relative paths, and recommend a durable rebuild for the normal fix. This avoids both false attribution and advice that recreates the documented deployment problem.

This section is a reviewable deployment plan, not a record of a deployment. No rebuild was performed during this audit.

### 7.3 Review of the previously unexamined findings

| Finding | Current evidence/status | Recommended next step |
|---|---|---|
| **A07 — options run-step concurrency** | Source still has read/check/write entry and settlement paths without a portfolio lock/idempotency boundary. Scheduler and admin routes reach the same function. A real concurrent database reproduction has not been run. | Add a two-worker PostgreSQL test; serialize portfolio cash/position mutation and make entry/settlement idempotent. Roll back failures before reusing a session. |
| **A10 — exit replay cutoff** | Rechecked RSI and double-top queries still fetch latest signal rows without an `as_of` predicate. | Route all historical reads through a cutoff-aware context. Assert that adding future data cannot change earlier exits. |
| **A11 — confidence semantics** | Rechecked formula remains `abs(fused - 0.5) * 200`. Section 6's recent BUY cohorts do not establish monotonic outcome quality. | Keep strength distinct from probability; calibrate the declared target using independent matured outcomes, with market/direction/style support shown. |
| **A12 — overlapping scorers and decision consistency** | An architectural/evaluation concern, not a single newly reproduced exception. Compression-cap sign protection and a later weekly gate already exist. | Use paired ablations and one versioned decision contract; preserve hard vetoes. Do not claim every score layer is defective or remove them without evidence. |
| **A14 — verification gaps** | Research fixture disagreement is resolved in the local 81-pass follow-up. The options test file passes despite A17. Three local suites still had bounded timeouts; another review reports passing them elsewhere. | Add behavioral settlement tests. Compare pinned test environments and capture per-test stacks/timing before assigning a timeout cause. Do not label a local timeout a production outage. |
| **A15 — operational reporting/deployment** | Shared drift is now directly verified. Options run-step still catches internal failures, and the fix-recheck record is stale/missing due snapshots. | Return business-level batch results, persist useful-output timestamps, implement restart recovery and pin code/schema/model/config versions. |
| **A16 — tracker evidence** | Duplicate `aud-c2-calibrator-leakage` IDs remain in `improvements.tsx`. Tracker completion still does not establish runtime promotion or measured trading value. | Enforce unique IDs and separately track implemented, tested, deployed, enabled and measured status with evidence links. |

For **A01–A03**, deferring live deployment until a durable order lifecycle exists is appropriate. “Only bites with a live broker linked” is too narrow: the same bookkeeping and reconciliation errors can affect an authorized broker-paper/sandbox workflow as well. Real-money consequences depend on live linkage; broker-paper correctness should be tested now as part of readiness.

**A06 and A08/A09 remain open.** The exact fixes are still executable-quote separation, capital/occupancy-constrained replay, and training-label maturity/purging. The reported +0.83 percentage-point contract-study uplift should stay labelled provisional, not converted into expected live portfolio performance.

### 7.4 September 25: first observations, not validation of a 69% win rate

The first three open positions do expire September 25. Their entry records show:

| Position | Entry date | Entry-to-expiry calendar days | Recorded stock price | Put strike | Entry OTM cushion |
|---|---|---:|---:|---:|---:|
| AMD put | September 16 | 9 | 493.410004 | 500.00 | **−1.34%: already ITM** |
| TQQQ put | September 16 | 9 | 69.269997 | 68.50 | **1.11%** |
| PLTR put | September 16 | 9 | 173.309998 | 162.50 | 6.24% |

The current selector requires at least **14 days to expiry** and at least **2% OTM cushion**. All three fail the current minimum DTE; AMD also fails current moneyness, and TQQQ fails the cushion floor. This is a verified mismatch between stored historical entry inputs and today's rules. It does not prove the current selector bypassed its checks: the entries can predate those checks, and their exact decision/configuration snapshots were not persisted.

**Implications:**

- Label them as an earlier entry cohort, preserving the actual ledger. Do not count them as proof of the current selector's forward win rate.
- Their expiry tests the **paper settlement/accounting path**. These are synthetic paper positions, not evidence of real broker execution or assignment handling.
- A17 must be repaired and behaviorally tested before relying on this run. The current counter regression prevents treating the date as “nothing to do until then.”
- Even three wins would not validate 69% accuracy. Under a simplifying independent 69%-win model, three wins occur about **32.9%** of the time. These positions share expiry and market exposures, so independence is itself questionable.
- Compare later forward outcomes with a matched strategy, entry-rule version and cost model. An all-CSP cohort is not automatically comparable with a historical CC/CSP selection study.

The September 17 TSLA, META and NVDA puts have 15, 29 and 43 calendar days to expiry and recorded cushions of 10.13%, 10.11% and 10.95%. They pass these particular current filters. That limited check does not establish their original quote freshness, full eligibility or profitability.

### 7.5 Why all six positions are puts: verified constraints, unresolved historical selection

Production portfolio 2 has both `COVERED_CALL` and `CASH_SECURED_PUT` enabled, initial capital **250,000**, one contract per position, a **25%** collateral concentration limit, one position per symbol, ten maximum positions, and three entries per day. Current cash is **66,467**. Thus a put-only strategy configuration is not the explanation.

Under those current settings, the per-position collateral ceiling is **62,500**. Using each position's stored underlying entry price, the stock purchase required for a one-contract synthetic covered call would have been:

| Symbol | Actual put collateral | Hypothetical covered-call stock cost | Under current 62,500 concentration ceiling? |
|---|---:|---:|---|
| AMD | 50,000 | 49,341.00 | Yes |
| TQQQ | 6,850 | 6,927.00 | Yes |
| PLTR | 16,250 | 17,331.00 | Yes |
| TSLA | 33,000 | 36,720.00 | Yes |
| META | 61,000 | 67,864.00 | **No** |
| NVDA | 19,500 | 21,898.00 | Yes |

This provides a concrete reason a comparable META covered call would be excluded while its put could pass **under the current configuration**. It does not explain all six positions. Nor does affordable stock establish an eligible call contract: quote availability, delta, DTE, earnings, cushion, yield, ranking, prior holdings and daily budget also matter. Historical cash/configuration and the full rejected candidate set at each entry are not available in the inspected income records.

The statement that covered calls dominated the ranked picks is therefore treated as Claude's observation, not independently verified historical selection evidence. Re-running today's ranking would answer a different question and cannot reconstruct a missing original decision snapshot.

**Recommended solution:** make the selection stage a pure function returning both accepted entries and rejection reasons. Use the same function in paper execution and portfolio replay. Persist a bounded per-run decision record with:

- Candidate rank, symbol, strategy/contract, score components and rule version.
- Quote/underlying timestamps, chain date and event-data availability.
- Pre-decision cash/reservations, existing symbols, remaining daily budget and relevant portfolio limits.
- The first blocking rule and relevant values, such as `collateral 67864 > limit 62500`.
- Selected quantity, resulting reservations and a link to the eventual position/fill.

Then report counts by strategy through `eligible → ranked → affordable → selected → filled`, rather than judging strategy mix from ranking alone. Do not force a 50/50 call/put allocation merely to balance the screen. If separate strategy performance is the question, establish comparable dedicated paper cohorts with predefined risk/capital budgets and record the opportunity sets.

### 7.6 What is genuinely time-dependent, and what should happen now?

| Item | Must wait for | Work available now |
|---|---|---|
| Options first expiry | Actual expiry-session prices and eventual paper outcomes | Repair A17, test settlement/rollback, record mark quality, tag old-rule entries, specify the matching evaluation cohort |
| SWING A/B portfolio 891 | Enough comparable opportunities and matured trades | Confirm experiment settings, instrument eligibility/rejections, freeze controls and measure opportunity rate; snapshot still shows **zero trades** |
| Confidence calibration | Sufficient independent matured outcomes for the actual target | Repair C01–C03, resolve early horizons independently, measure reliability/support, preserve a future holdout |
| Shared-code drift | A scheduled, authorized deployment window | Prepare pinned images, resource/compatibility checks, rollout/rollback plan and manifest acceptance checks |

Mid-October is a checkpoint, not a model-readiness guarantee. The latest same-day cohort has 607 resolved frozen-state outcomes across different directions/styles and only a few dates in important slices, while longer BUY styles have no stored resolved five-day coverage. A calendar date or a total count alone cannot determine calibration readiness.

### 7.7 Recommended order of work

1. **Settlement integrity:** fix/test A17 and transaction failure handling before the first expiry.
2. **Measurement integrity:** correct the fix-date filter and scheduled snapshots; independently resolve outcome horizons and identify strategy/version cohorts.
3. **Selection explainability:** record accepted/rejected options candidates and the capital/eligibility checks that produce the put-only portfolio.
4. **Durable deployment:** rebuild and sequentially recreate the ten drifted services with compatibility checks and rollback evidence. Do not normalize permanent drift alerts.
5. **Controlled evaluation:** repair replay cutoffs and options portfolio/label methodology; let frozen stock/options paper experiments accumulate representative outcomes.
6. **Broker automation:** complete and fault-test the durable order/fill lifecycle before authorizing real-money execution.

The first four items contain actionable engineering work today. Waiting for market outcomes is necessary for evaluation, but it is not a reason to leave the evaluation or settlement machinery incomplete.
