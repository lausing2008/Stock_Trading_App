# Deep review: signals, options alerts, event intelligence and entry decisions

Date: **2026-10-01**. Review baseline: `8381809b72644e9a9f77138ec57e0693036ecfa4` plus explicitly observed working-tree state. Scope: signal generation, authoritative decision gates, options-flow selection/delivery, option strategy construction, news flags, prediction suppression and the existing measurement/automation roadmap.

**Conclusion:** prioritize decision consistency, freshness, event identity and economically valid plans before loosening entry thresholds or adding another model. Eight concrete defects/limitations were reproduced offline using current production functions or exact AST-extracted blocks. Four relevant files match running production containers. This confirms affected code presence, not the number of real alerts/trades impacted. The review establishes no profitable edge and no optimal thresholds.

## 1. Findings, ranked

| ID | Severity | Finding | Evidence / scope |
|---|---|---|---|
| SR-01 | P1 | Authoritative decision engine still vetoes new signals using stale conviction records | Exact hard-reject function; runtime file matches |
| SR-02 | P1 | Timezone-less serialized timestamps bypass the decision engine's signal-age restriction | Exact hard-reject function; route/API trace; runtime files match |
| SR-03 | P1 | Options contracts omitted by cooldown or email cap are marked seen without being sent | Exact recipient loop; runtime scheduler matches |
| SR-04 | P1 | Same-contract options direction depends on provider row order, not newest event | Exact candidate loop; runtime scheduler matches |
| SR-05 | P2 | Standalone decision engine omits HK securities holidays | Exact hard-reject function; paper scheduler has a separate protective guard |
| SR-06 | P1 | Option strategy recommender can select a debit spread with negative maximum profit | Pure strategy module executed with synthetic inconsistent quotes; local code |
| SR-07 | P1 | Collar combines different expiries but reports fixed single-expiry payoff bounds | Pure strategy module; normal put/call selection windows differ; local code |
| SR-08 | P2 | Old adverse news can be stamped as a fresh risk event on ingestion | Exact flag writer; signal decay uses ingestion timestamp; writer matches runtime |

Severity is prioritization for correctness and potential decision impact, not evidence of realized loss. Options playbook findings concern displayed/generated advice; this pass did not establish that automated options-income execution consumes that strategy matrix.

### SR-01 — Conviction fix stops before the authoritative consumer

Locations: `services/decision-engine/src/api/core/hard_rejects.py::check_hard_rejects` (final conviction block); `services/market-data/src/services/paper_trading_engine.py::_scan_for_entries`; `scheduler.py::_store_conviction`.

Paper trading now ignores a conviction record when its evaluation timestamp predates the signal, and prefers `gate_passed`. It subsequently calls the authoritative decision engine. That engine still reads `conv_gate:{symbol}:{style}` and rejects on `signal == BUY` and `sent is False`, without comparing timestamp/input identity or using the explicit gate field.

**Witness:** a September 30 failed cache record rejects an otherwise eligible October 1 14:55 UTC signal: `Conviction gate failed: old signal failed`. Removing the cache admits the same fixture. Thus a local stale-cache fix does not ensure end-to-end parity.

**Fix:** one shared conviction decision contract with immutable signal/input identity, market, horizon and policy version. All readers validate the same identity and expiry, recomputing required policy or returning explicit unavailable status. The producer currently records evaluation time, not immutable signal identity; comparing timestamps is only partial protection. A later evaluation of an old signal is still not a decision about a newer signal.

**Acceptance:** actual paper→DE path with older/newer/equal/malformed records, explicit gate versus legacy field disagreement, and email-provider outage; fixed inputs must yield the same gate verdict independently of notification state.

### SR-02 — Signal age can be displayed correctly but not enforced

Locations: `hard_rejects.py::check_hard_rejects` signal-age block; `services/decision-engine/src/api/routes.py` around lines 126–137 and 199–219; `services/signal-engine/src/api/routes.py` timestamp serialization.

The hard-reject function normalizes naive datetime objects but not naive ISO **strings**. Subtracting a parsed naive string from aware UTC `now` raises; the broad exception handler silently skips the gate. The route separately normalizes its display timestamp but passes the original `sig_ts` to the gate. Signal API timestamps use `row.ts.isoformat()`, which can produce the naive form for naive database UTC columns.

**Witness:** `2026-09-20T15:00:00` passes on October 1; the identical instant with `+00:00` is rejected as **264 hours old**, exceeding 72 hours.

**Fix:** parse once to a canonical aware UTC instant and pass that value to every consumer. Explicitly distinguish missing/invalid/future timestamps and reject or abstain for required freshness evidence. Use exchange-aware policy for long-horizon signals rather than treating a parse error as approval.

**Acceptance:** datetime and string, naive documented UTC, `Z`, offsets, malformed/future values; verdict and displayed age must agree through the route, not only the helper.

### SR-03 — Unsent options candidates consume seen state

Location: `scheduler.py::check_options_flow_alerts`, recipient loop, approximately lines 5597–5673.

`send_ok` begins true. After cooldown filtering and email truncation, `resync_set = current_chains if send_ok ...` marks **all candidates** seen. When every candidate is on cooldown, no email is attempted yet all become seen. When a payload is capped, omitted contracts become seen too. Cooldowns are also claimed before the cap, temporarily suppressing omitted groups.

**Witnesses:** an existing cooldown produces zero sender calls but `seen=['c1']`; cap=1 with two different-symbol candidates sends only `c1` but records `seen=['c1','c2']`. Repeated snapshots can keep those candidates suppressed while they remain present. This is not proof of permanent loss for every contract; resync and expiry can eventually change eligibility.

**Fix:** distinguish observed, deferred, intentionally omitted, queued and accepted states. Advance accepted/delivered-purpose identity only for the exact payload accepted. If omission is deliberate product policy, record it as suppression, not delivery; otherwise retain eligible fresh deferred events. Integrate with M20 after its first family is verified, without replaying obsolete historical flow.

**Acceptance:** cap overflow, active cooldown with no sender call, one cooldown shared by several contracts, failed send, rotating candidate set and retry expiry. Reconcile generated→eligible→queued→accepted with explicit suppression reasons.

### SR-04 — Contract identity collapses distinct flow events

Location: `scheduler.py::check_options_flow_alerts`, `candidates[row.option_chain] = ...` near line 5549; UW adapter preserves multiple rows and their `created_at`.

Each row overwrites the prior event on the same option contract. No timestamp comparison or documented aggregation selects the winner. The contract can exhibit buying and selling at different times.

**Witness:** the same two call-contract events yield **bearish** when newest bullish is first and older bearish is last; reversing list order yields **bullish**. This proves order dependence, not the ordering of every current UW response.

**Fix:** preserve provider event identity and timestamps. Define either newest-valid-event selection with deterministic tie rules, or a documented time-window aggregation that retains opposing evidence. Reject/quarantine missing/invalid times for actionable use. Show event age and classified-premium coverage; the 48-hour feed window is a discovery lookback, not an intraday action TTL.

**Acceptance:** arbitrary row permutations, duplicated pages, opposing prints, corrected events, same contract across sessions and stale current underlying quotes. Direction and outcomes must be invariant to transport ordering under the chosen policy.

### SR-05 — HK holiday entry verdict differs by caller

Location: `hard_rejects.py` market-open block. It checks NYSE holidays for non-HK markets but only weekday/time ranges for HK. `_bar_is_incomplete` and the paper scheduler already use shared HK calendar logic, so the shared calendar exists but this consumer does not use it.

**Witness:** at October 1, 2026 11:00 HKT, an otherwise eligible HK request passes the hard-reject function. October 1 is an HK securities-market holiday. [Official HKEX 2026 schedule](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/SEHK/2025/ce_SEHK_CT_075_2025.pdf).

**Fix:** central market/session predicate for each actual instrument venue, including holidays/lunch/early close as applicable. Test standalone route and paper caller parity. Scope matters: this is an invalid standalone decision verdict; the separately guarded paper scheduler is not proven to trade on HK holidays.

### SR-06 — Invalid spread economics can become the primary recommendation

Location: `services/market-data/src/services/options_strategies.py::_mid`, `_leg`, `build_strategy_matrix` bull-call and bear-put blocks, `_recommend`.

Spread construction checks only positive debit, not debit below strike width. `_mid` also accepts positive crossed quotes and falls back to an undated last price when one side is absent. Such snapshots can be inconsistent across legs. The recommendation function selects an available strategy without screening its computed maximum payoff.

**Witness:** underlying 100; call strikes 100/105; quote mids 12/2 → debit 10 for width 5. The module computes **maximum profit −$500**, yet selects `bull_call_spread` as primary for a BUY/no-shares/normal-IV request.

**Fix:** validate contract identity, expiry, multiplier, finite positive prices, uncrossed spread and quote as-of before pricing. For an actionable debit vertical require economically valid net debit below width after costs; otherwise no valid plan. Separate illustrative mid/last marks from executable estimates and size limits. The maximum expiry payoff is bounded by strike width less debit. [OIC bull-call-spread definition](https://www.optionseducation.org/strategies/all-strategies/bull-call-spread-debit-call-spread).

**Acceptance:** missing/crossed/stale legs, mismatched contracts, adjusted multiplier, debit equal/above width, fees making a marginal plan invalid, and valid control. No invalid structure may be selected as primary.

### SR-07 — Different-expiry collar given single-expiry bounds

Location: `options_strategies.py::build_strategy_matrix`, collar block around line 234. `services/market-data/src/api/routes.py` selects protective puts at 25–60 DTE and calls at 14–45 DTE. The matrix combines them without requiring equal expiry, then reports a fixed max profit/loss and breakeven.

**Witness:** stock 100, put strike 95/premium 2 expiring November 20; call strike 110/premium 1 expiring October 16. Reported max profit is $900 per 100 shares. But if the short call expires worthless while stock is 100, and stock later reaches 120 at put expiry, the unchanged position earns $1,900 before fees. There is no longer a call capping that later upside. Other paths require assignment/remaining-leg analysis; a single common-expiry diagram does not describe this structure.

**Fix:** require matched expiry for a static collar payoff summary, or explicitly model a staggered-expiry strategy with intermediate valuation/assignment, remaining legs and separate scenario horizons. Standard collar construction illustrates matched expiries; alternative rolling strategies require their own lifecycle assumptions. [CME collar explanation](https://www.cmegroup.com/education/courses/option-strategies/collars).

**Acceptance:** common-expiry control, earlier call expiry, earlier put expiry, assignment, and stock/remaining-leg positions at each event. This is an advice/payoff-model defect, not evidence of an actual automated collar fill.

### SR-08 — Delayed news arrival resets perceived economic age

Locations: `services/news-intelligence/src/services/storage.py::_mark_hot`; `services/signal-engine/src/generators/signals.py` hot-news decay around lines 2505–2523.

The writer stores publication time but sets `ts=now` and a fresh two-hour TTL. Signal decay reads `ts`, not publication time. A first-seen old material-negative story can therefore apply a full fresh compression. No age validation occurs in the inspected writer. Existing unrelated-positive resolution is a separate already-open risk.

**Witness:** adverse story published September 1, ingested October 1, receives October 1 `ts` and the ordinary hot flag. This is a controlled delayed-input case; no production frequency is claimed. Normally timely feeds reduce exposure but do not enforce the contract.

**Fix:** retain event occurrence, publication, receipt and revision timestamps separately; define explicit policy for delayed material information. Do not blindly discard old-but-newly-disclosed risk, but label and evaluate it as delayed evidence rather than a just-released catalyst. Link ongoing material events and their resolutions; do not clear persistent issuer risk merely because two hours elapsed or an unrelated positive article appeared.

**Acceptance:** delayed first discovery, source republication, historical recap, material correction and event-linked resolution. Distinguish transient reaction timing from unresolved fundamental risk.

## 2. Existing issues that remain strategically important

These are not all new defects and must not be double-counted as new audit closures.

- **Confidence is strength, not win probability.** Signal confidence is `abs(fused−0.5)*200`. It does not establish a 70% chance of profit. Corrected September studies show sparse upper bands and dependent samples. Keep the existing calibration hold; use separate target-specific calibrated probability and uncertainty.
- **Options-flow “calibration” is a historical row-weighted hit rate.** `_build_options_flow_alert_calibration` pools resolved 10-day outcomes by direction, with count/date floors but no symbol/session equal weighting or explicit study window. Contract rows can reuse one underlying move. Candidate outcomes are recorded before recipient delivery; they are not email or trade win rates. Rename honestly and use versioned cohorts before promotion.
- **Flow side does not fully establish investor intent.** Classified ask/bid imbalance is useful evidence, not proof of opening/closing or standalone directional exposure. Hedges and multi-leg activity remain possible. The classifier's imbalance denominator excludes unclassified premium; include coverage and abstain when support is weak.
- **News gate policy is incomplete.** LONG is exempt from the transient negative-news compression, while serious issuer events can affect every holding horizon. Separate long-lived event risk from intraday sentiment; do not simply remove the exemption and apply one arbitrary multiplier everywhere.
- **Suppressed-model handling requires consumer-level verification.** The ensemble can fall back to all models when every model is suppressed; downstream suppression/compression exists. This review does not establish a new leakage bypass from that branch alone. Inventory actual consumer behavior and unavailable-model coverage before claiming suppression solves inference reliability.
- **Current inactivity has multiple causes.** The recent read-only [portfolio follow-up](2026-09-30-paper-inactivity-followup.md) found recovery markers in 2/5, zero candidates in HK SWING 9, and drift/filter rejection elsewhere. Fixing one reader or loosening a threshold does not repair all these paths.
- **M15/M20 remain scoped.** Recent reservation tests improve concentration correctness; fresh marks, broker pending exposure/assignment and M20 scheduler/cutover remain separate work. Concurrent modifications to those modules were left untouched. Their reported fixes are not re-certified by this review.
- **MU-01 needs actual evidence.** This review does not claim the provider→event→committed EPS mapping has been resolved. MU-02 phase delivery and event attribution remain distinct from a complete reliable earnings intelligence system.

## 3. How to improve prediction and decision quality

### Establish distinct questions

| Product | Defined question | Required evaluation |
|---|---|---|
| Market regime | What is the expected market return/risk distribution over a stated horizon? | Point-in-time benchmark returns, volatility/drawdown, regime support; no regime labels defined using future returns |
| Stock direction | Will this stock outperform its chosen benchmark over N completed sessions? | Mature availability-aware labels, calibrated probability, discrimination and coverage |
| Entry setup | Is there an executable entry now with favorable expected payoff under a fixed exit policy? | Quotes, confirmation, invalidation, costs, fill probability and net expectancy |
| Options playbook | Which feasible structure fits direction, timing, volatility, capital and holdings? | Real contract prices, spreads, Greeks, expiry/assignment lifecycle and scenario P&L |
| Event impact | What changed versus expectations, when was it known, and is the remaining reaction actionable? | Facts/consensus timestamps, revision identity, post-availability performance and market-relative reaction |

Never substitute underlying directional accuracy for options profitability. IV, time to expiry and other pricing inputs affect premium; a correct direction can still produce a losing option. [OIC options pricing](https://prd-web.optionseducation.org/optionsoverview/options-pricing).

### Calibrate and validate without mining the same losses

Freeze market/horizon/direction/target/entry timing first. Train only with labels actually available at the cutoff; purge overlapping label windows at each split and group repeated event/contract observations. Separate training, calibration, policy selection and untouched forward evaluation. Record model suppression, feature missingness and abstentions as part of coverage.

Compare probabilities with a cohort base-rate model using reliability plots, Brier/log loss and discrimination metrics. A better calibration curve does not create signal information; proper scoring metrics reflect multiple components. [Scikit-learn calibration documentation](https://scikit-learn.org/stable/modules/calibration.html).

Do not infer optimal confidence, R:R or anti-chase thresholds from this review. Define an economically material improvement and risk tolerances before evaluating candidate policies; retain uncertainty and multiple-comparison controls. Keep September as the retrospective diagnosis, and the post-fix prospective period separate.

## 4. Entry timing: test conditional setups, not “buy the bottom”

Recommended research hypotheses, not ready-to-enable strategies:

| Setup | Entry evidence | Invalidation / experiment |
|---|---|---|
| Trend pullback and recovery | Higher-horizon trend; completed-bar reclaim of a defined support/VWAP/reference; spread and liquidity pass | Stop anchored to measured invalidation; compare first signal versus confirmed recovery on the same baseline events |
| Breakout/retest | Prior resistance fixed before breakout; time-of-day-normalized volume; acceptance or retest | Expire after a bounded interval/extension; compare immediate breakout versus retest including unfilled/missed moves |
| Earnings continuation | Verified result AND guidance versus pre-release expectations; sustained post-release response | Avoid chasing initial spike by default; compare after-hours versus next-session confirmation with their distinct costs/coverage |
| Flow-supported entry | Fresh, sufficiently classified flow plus independent price confirmation and coherent horizon | Ablate flow versus identical price setup; no “call equals buy” or 48-hour-old print treated as a fresh trigger |

Each plan needs immutable reference prices, creation/expiry times, entry condition, maximum acceptable price/spread, stop/invalidation, targets, time exit and no-trade conditions. Distinguish full daily bars from partial-session bars; raw volume-so-far versus full-day average cannot establish abnormal activity. Measure time-of-day volume profiles before reintroducing a skipped liquidity filter.

Evaluate timing from the first executable price after the decision was available. Compare net expectancy, drawdown, MAE/MFE, fill rate, delay cost, time in cash and missed opportunities. A retest rule may improve trade hit rate while missing the strongest moves; portfolio equity is the deciding comparison.

## 5. Practical options and news improvements

**Options:** separate a market-flow observation from an actionable plan. Require current contract quotes, quote age, liquidity/open interest, spread, expiry, multiplier, holdings/collateral feasibility and a scenario grid for underlying price, time and IV. Do not present model delta as a calibrated win probability. Keep no-trade as a valid recommendation. Match spread expiries or model multi-expiry risk explicitly; price buys/sells from realistic sides and allow unfilled limit orders rather than assuming mids.

**News/earnings:** publish sourced facts promptly, with interpretation as a later version. Use issuer/fiscal-event identity, materiality/category and publication/receipt age. Maintain unresolved event state rather than one symbol-level last-writer flag. Distinguish expected versus surprising facts, positive earnings versus negative guidance, firm-specific shock versus market movement, and an old recap versus new disclosure. Jev can be an additional classification input after baseline evaluation exists; it cannot repair broken delivery or event identity by itself.

**Decision architecture:** one versioned policy result should be shared across alert, standalone decision and execution consumers. Product recommendations can use account-independent context, but executable approval must recheck actual holdings, reservations, quotes and risk immediately before submission. Avoid an ever-growing collection of reader-specific exceptions that disagree on identical inputs.

## 6. Recommended implementation and experiment order

| Order | Work | Completion evidence |
|---|---|---|
| 1 | SR-01/SR-02 shared identity and timestamp enforcement | Route and paper/DE integration tests; stale/unparseable evidence cannot silently bypass or reinstate a gate |
| 2 | SR-03/SR-04 options event selection and send accounting | Permutation-invariant event choice; exact payload accounting; retained deferred work; no obsolete replay |
| 3 | SR-06/SR-07 quote/payoff validity | Real structure functions reject invalid spreads and distinguish unequal expiries; UI labels research estimates correctly |
| 4 | SR-05/SR-08 calendar and news-time contracts | All consumers agree on trading sessions; delayed events retain honest age and event-resolution policy |
| 5 | Finish M20 first-family wiring and M15 bounded verification | Outbox failure/cutover evidence, exposure reconciliation, fresh-mark shadow coverage; deployment remains explicit |
| 6 | Complete Milestone A/B baseline and outcome lineage | Definition matches query grain; decisions/alerts/fills/outcomes linked and counts reconciled |
| 7 | Run one paired prospective entry-timing experiment | Same baseline stream, separate paper state, frozen fees/fills/exit rules, independent cluster support and risk comparison |
| 8 | Add UW/Jev/calibration interventions one at a time | Incremental net value versus the frozen control; costs, abstentions and failures included |

Primary economic metric: treatment minus control marked-equity return on equal starting capital over the same fixed window, with realistic execution and provider costs. Guardrails: drawdown, tail loss, concentration, exposure, unresolved orders and stale marks. Secondary metrics: net expectancy, hit rate, profit factor, calibration, opportunity coverage and event-to-acceptance latency. Win rate is not the optimization objective by itself.

Continue the [measurement framework](../features/2026-09-30-measurement-framework-and-improvement-backlog.md) rather than adding a parallel dashboard/registry. Its existing work IDs remain authoritative; this review supplies concrete new evidence and acceptance cases.

## 7. Verification, boundaries and artifacts

- [Executable witnesses](evidence/2026-10-01-decision-alert-review-probes.py) and [captured results](evidence/2026-10-01-decision-alert-review-results.json). They execute exact AST-extracted functions/blocks with frozen clocks and fake dependencies, plus the pure options module. They assert defective current behavior; they are **not** a green release acceptance suite. The first harness run failed for missing AST source locations; corrected with `ast.fix_missing_locations` before successful execution.
- [Runtime hash comparisons](evidence/2026-10-01-review-runtime-hashes.json): decision hard rejects/routes, market-data scheduler and news storage matched local files via read-only SSH. This is not a full-container drift check. Options strategy module and signal-generator runtime hashes were not checked.
- Scope included source traces into prediction suppression and existing audits; it did **not** include retraining, complete model-artifact validation, a full security audit, browser review, every service/test, a new performance backtest, or proof of exact historical user impact. “Deep review” here is concentrated on the requested signal-to-decision paths, not certification of the entire repository.
- Existing user/Claude changes in exposure and test modules were preserved. No source fixes, production writes, jobs, sends, flags, commits, deployments or CLAUDE.md changes were made.
- As the working tree is being edited concurrently, evidence hashes identify the reviewed versions; line numbers can move. Rerun corrected-behavior tests after remediation rather than treating later source changes as already reviewed.
