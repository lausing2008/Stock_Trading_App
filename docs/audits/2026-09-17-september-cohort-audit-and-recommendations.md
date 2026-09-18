# September results, remaining issues, and recommended fixes

**Audit date:** 2026-09-17, America/Los_Angeles.  
**Production observations:** 2026-09-17 at 23:52:49 and 23:54:49 UTC.  
**Local revision:** `4b885e6`; inspected trading-engine files match the production hashes from the T400 follow-up.  
**Scope:** read-only production data analysis, current source review, recommendations. No application fixes, configuration changes, broker calls or production writes were performed. `CLAUDE.md` was not modified.

Related reports: [system audit and roadmap](2026-09-17-system-audit-and-trading-roadmap.md), [production verification and A17](2026-09-17-production-verification-audit.md).

## 1. Answer: some recent evidence is better, but it is not uniform

**Older aggregate results should not be used as the verdict on the current implementation.** Several fixes landed during September, so the month itself contains multiple versions. The new data supports a more specific assessment:

- **Recent US paper entries are encouraging but very few.** Three trades entered from September 9 onward have one closed winner and two open positions, with approximately +$599.51 combined realized P&L and stored open-position marks. That is not a realized portfolio return or a demonstrated win rate.
- **September's earlier entries still dominate its losses.** Two September 3 trades account for about 88% of the month's net realized US entry-cohort loss. They should remain in the ledger, with their version/incident context, rather than being treated as evidence about every later fix.
- **Several SELL signal groups have improved descriptively.** US SHORT/SWING/GROWTH SELL and HK SWING/GROWTH SELL groups look stronger than their August records. Their short observation windows and the change in outcome-recording semantics prevent a causal claim that a particular fix created the improvement.
- **The resolved SHORT BUY groups are still weak.** US and HK September results are worse than their August records on the stored outcome metric. These observations are mainly from September 3–9, not the latest September 15–17 signals.
- **Recent SWING/LONG/GROWTH BUY performance is not yet available in the outcome table.** An additional delay in collecting their short-window outcomes is explained below. Missing outcomes do not mean those signals failed.
- **Engineering issues remain.** The settlement-counter regression A17, incomplete fix-effectiveness tracking, mixed evaluation horizons, options valuation/execution limitations, and broker lifecycle gaps still need work.

The next step is to repair the measurement pipeline and test a frozen current version, rather than either condemning the new version using all historical losses or promoting it from a handful of recent gains.

## 2. Method and interpretation

Production SQL ran with database-enforced read-only transactions, repeatable-read isolation, an eight-second statement timeout and a one-second lock timeout. The audit retrieved all **128 stock paper-trade records** in the snapshot, selected aggregate signal cohorts, fix-tracking metadata, options counts and price-head dates. Trade rows contained no user identities or broker credentials. Aggregation was performed locally.

Four distinctions matter:

1. **Entry cohort versus exit month:** judging September entries is different from summing everything that closed in September, some of which entered earlier.
2. **Before/after source changes versus verified deployment cohorts:** September 9 is an exploratory boundary after the September 7–8 fix cluster in Git. There is not a complete immutable deployment/configuration history attached to each trade, so it is not a certified post-deployment cutoff for every fix.
3. **Closed versus still open:** fast losers and slower winners can resolve at different speeds. Report unresolved positions alongside closed results, without assuming open marks are final profits.
4. **Signal label versus tradable profit:** signal returns are forward price changes under the evaluator's rules. They are not actual stock/option execution returns after spread, fees, stops, sizing and portfolio constraints.

The stock join identified **108 US/USD** and **20 HK/HKD** trade records. This supports keeping the native currencies separate; the report does not add USD and HKD P&L.

No statistical significance is claimed. Signals share dates and stocks; a few hundred rows over four to six dates are not a few hundred independent market experiments. August rows also lack the frozen first-actionable state present in the September cohort, so the month comparison is descriptive, not a clean treatment/control experiment.

## 3. Paper trading: what changed this month?

### 3.1 US results grouped by entry month

P&L below is the sum of stored closed-trade P&L, not return on account equity. Profit factor is gross winning P&L divided by the absolute sum of losing P&L.

| Entry month | Entered | Closed | Still open | Closed winners | Closed win rate | Realized P&L, USD | Profit factor |
|---|---:|---:|---:|---:|---:|---:|---:|
| June | 37 | 37 | 0 | 15 | 40.5% | +384.07 | 1.177 |
| July | 36 | 36 | 0 | 10 | 27.8% | −2,715.28 | 0.465 |
| August | 23 | 22 | 1 | 7 | 31.8% | +383.25 | 1.253 |
| September through the snapshot | 12 | 8 | 4 | 1 | 12.5% | −1,498.10 | 0.047 |

This does not show a month-wide improvement in realized September entries. However, it also does not isolate the latest fixes.

### 3.2 Split September around the major September 7–8 source changes

| US entry cohort | Entered | Closed | Open | Realized P&L | Estimated open-position P&L | Combined trade P&L estimate |
|---|---:|---:|---:|---:|---:|---:|
| September 1–8 | 9 | 7 | 2 | −$1,572.17 | Approximately −$469.97 | Approximately −$2,042.14 |
| September 9 onward | 3 | 1 | 2 | +$74.07 | Approximately +$525.44 | Approximately +$599.51 |

The later cohort comprises DT entered September 9, NOW entered September 11, and SPCX entered September 15. NOW closed with a stored +$74.07 result; DT and SPCX remained open. There is only **one resolved trade**, so reporting “100% win rate after the fixes” would be misleading.

Open-position estimates use stored `current_price`, remaining shares, entry price, accumulated realized partial P&L and entry commission. They exclude future exit costs and do not establish quote freshness, executable liquidation value, portfolio cash returns or an independently reconciled equity curve. Across all September US entries, the corresponding combined estimate is approximately **−$1,442.63**.

For comparison, grouping by **exit month** instead gives 15 US trades closed in September, five winners, −$1,366.62 and profit factor 0.426. That is a different population; it should not be substituted for the entry-cohort table.

### 3.3 Earlier losses need their incident context

SNOW and DELL were entered on September 3 and closed with stored losses of **$986.48** and **$325.63**, respectively. Their combined $1,312.11 is **87.6% of the September entry cohort's net realized loss**. This percentage uses net loss, not the sum of all losing trades.

The existing [incident record](../incidents/self-tuning-job-performance-bugs.md) specifically discusses those trades and a gap/chasing-filter blind spot. September's Git history includes later anti-chasing and partial-bar corrections, as well as September 7–8 changes to entry gates, sizing and cross-market exit handling. This supports separating historical incident cohorts from later entries. It does not prove that every later version would have avoided the trades or that the remaining strategy is profitable.

**Recommendation:** retain these losses in the actual ledger. Add incident and version tags, then report both the original record and a separately validated counterfactual replay. Do not delete them or present an “excluding bad trades” result as actual performance.

### 3.4 HK and options do not yet answer September profitability

HK entry cohorts improved from nine losing June trades to four winners out of six in July and three winners out of four in August. July and August stored P&L were **+HKD 5,210.44** and **+HKD 1,256.97**. Those are small historical samples, predominantly GROWTH, not proof of a stable edge.

September has **one HK entry, still open**, so it has no resolved September win-rate estimate. The options-income engine has **six open positions and no closed positions**, with the earliest expiry on September 25. The corrected options equity accounting is an engineering improvement, not evidence of profitable settled trading.

## 4. Signal quality: BUY and SELL need separate conclusions

### 4.1 Exact meaning of the metric

The stored `return_5d` evaluator targets **entry date plus five calendar days**, then uses the next available daily close within its allowed lookup window. Entry is the first close strictly after the frozen signal date. Therefore this is not necessarily five exchange trading sessions or an intraday actionable fill.

The hit flag requires an underlying move greater than **+0.5% for BUY**, or less than **−0.5% for SELL**. The mean returns below are raw price returns, with the sign reversed for SELL to show favorable bearish movement as positive. The 0.5% hit hurdle does not mean that transaction costs have actually been subtracted from those average returns.

Sources: [outcomes evaluator](../../services/signal-engine/src/api/outcomes.py), [horizon and hurdle constants](../../services/signal-engine/src/api/signals_shared.py).

### 4.2 August versus resolved September signals

| Market / style / direction | August n / hit rate | September n / hit rate | September directional mean return | September signal dates represented |
|---|---|---|---:|---|
| US SHORT BUY | 1,559 / 38.68% | 174 / 25.86% | −2.674% | September 3–8; 4 distinct dates |
| HK SHORT BUY | 458 / 36.03% | 74 / 22.97% | −5.306% | September 3–9; 6 dates |
| US SHORT SELL | 120 / 36.67% | 103 / 61.17% | +0.477% | September 3–10; 6 dates |
| US SWING SELL | 100 / 40.00% | 59 / 64.41% | +0.972% | September 3–8; 4 dates |
| US GROWTH SELL | 83 / 44.58% | 44 / 56.82% | +1.090% | September 3–8; 4 dates |
| HK SWING SELL | 51 / 29.41% | 43 / 67.44% | +1.933% | September 3–9; 6 dates |
| HK GROWTH SELL | 39 / 23.08% | 35 / 60.00% | +1.849% | September 3–9; 6 dates |
| US LONG SELL | 103 / 41.75% | 40 / 45.00% | −0.917% | September 3 only |
| HK LONG SELL | 61 / 21.31% | 35 / 60.00% | +1.034% | September 3–6; 3 dates |

The stronger SELL results may partly reflect the sampled market direction. A useful next test compares them against an unconditional bearish baseline and a simple trend filter over the **same stocks and dates**, after realistic implementation costs. The result does not authorize shorting stocks or buying puts: those are different instruments and execution policies.

### 4.3 Higher confidence still does not reliably mean a better BUY

For the frozen September US SHORT BUY cohort:

| Stored confidence band | Resolved rows | Hit rate | Mean forward return |
|---|---:|---:|---:|
| Below 10 | 11 | 45.45% | +0.126% |
| 10 to below 20 | 48 | 22.92% | −2.682% |
| 20 to below 40 | 94 | 26.60% | −2.522% |
| 40 or above | 21 | 19.05% | −4.802% |

The below-10 and 10–20 bands each represent a single signal date, so the table is not a basis for selecting a threshold or reversing the confidence scale. It does show that interpreting higher scores as empirically safer BUYs is unsupported in this sample.

US SHORT SELL shows a more promising pattern: the 40+ group has 58 rows, a 65.52% hit rate and +1.162% directional mean return, versus 45 rows, 55.56% and −0.405% for the 20–40 group. That pattern should be tested on later untouched dates rather than immediately promoted.

**Solution:** keep separate calibration models or appropriately pooled estimates for direction, style and market. Preserve raw score and final probability as different fields. Evaluate reliability, economic payoff, coverage and uncertainty together. Fit calibration on independent matured data; increasing sample count alone does not remove dependence or selection bias. [scikit-learn calibration guidance](https://scikit-learn.org/stable/modules/calibration.html).

## 5. Data improved, but the evaluation pipeline has important gaps

### C01 — Fix-effectiveness snapshots are absent, and the endpoint omits the date filter

**Observed in production:** two registered fixes are due for remeasurement after their configured 14-day interval, with **zero snapshots**:

| Fix | Recorded fixed time, UTC | Domain |
|---|---|---|
| `AUD-SIGNAL3-EVALSELECTIONBIAS` | September 3, 01:57:54 | AI signal |
| `AUD-DECIDE1-LOWGATECONFIG` | September 3, 03:19:40 | Decision making |

The selected Redis job status last records `recheck_fix_effectiveness` as `ok` on **September 14 at 22:05 UTC**, before those fixes became due. The inspected scheduler registers a weekday 18:05 ET job. This is an observed absence of later status/snapshots; it does not establish whether a scheduler misfire, restart, registration issue or another failure caused it.

**Observed in source:** [fix_effectiveness.py](../../services/signal-engine/src/api/fix_effectiveness.py) defines `_compute_ai_signal_win_rate_metrics(session, since=None)`, but `take_fix_snapshot` calls it without `since`. The current call therefore computes the available full history instead of the intended post-fix slice. The endpoint also rejects non-`ai_signal` domains, so the registered decision-making fix has no implemented snapshot calculator.

**Solution:**

1. Use the fix's effective deployment timestamp and frozen decision timestamp to select the post-fix cohort. A date-only fallback should be labelled because it can include earlier decisions on deployment day.
2. Preserve pre-fix, transition, post-fix and immature cohorts separately, split by market/style/direction and metric definition.
3. Implement the decision-making calculator, or expose that domain as unsupported and exclude it from successful dispatch counts.
4. Return attempted/successful/failed/unsupported counts and persist a durable last-success timestamp. Add startup catch-up and an overdue-snapshot alert.
5. Verify scheduler registration and due-job status through read-only diagnostics before invoking any snapshot-writing endpoint.

**Acceptance:** with identical pre-fix history, adding or changing older rows cannot alter a post-fix snapshot; no unsupported-domain failure produces a misleading completed recheck; each due supported fix gets an auditable snapshot or explicit failure.

### C02 — Short-window outcomes for longer BUY styles wait unnecessarily for the primary horizon

**Observed:** the September 3-onward frozen cohort has **3,239 actionable signals** and **607 represented resolved five-day outcomes**, up from the earlier report's 184 resolved rows. In this query, resolved outcome direction and confidence match the associated frozen values. That is improved traceability, not a guarantee that all other historical input/price assumptions are correct.

Coverage by direction/style is uneven:

| Frozen BUY cohort since September 3 | Actionable signals, US + HK | Stored resolved five-day outcomes |
|---|---:|---:|
| SHORT | 520 | 248 |
| SWING | 279 | 0 |
| LONG | 880 | 0 |
| GROWTH | 369 | 0 |

The remaining 359 resolved outcomes are SELL rows across styles. It would be wrong to say “607 examples are enough for the SWING BUY model”: none in that slice currently supplies its needed evidence.

The [evaluator](../../services/signal-engine/src/api/outcomes.py) creates an outcome row after its primary exit target matures, then computes the auxiliary 5/10/20-day windows. BUY primary windows are 7/14/28/14 calendar days for SHORT/SWING/LONG/GROWTH. Consequently, an already observable five-day result for a longer BUY style can remain absent until the longer primary window closes. Recent primary outcomes can also legitimately remain immature because entry is delayed to the next available session.

**Solution:** create a pending outcome record at the immutable signal event or valid evaluation entry, then resolve each horizon independently when its own inputs exist. Preserve primary-horizon maturity separately. Record missing-price, immature, skipped and resolved states distinctly.

**Acceptance:** a LONG BUY can acquire a five-day outcome while its 28-calendar-day primary outcome remains pending, without inventing the later result or altering the original signal. Repeated evaluation is idempotent, uses only then-available data and does not change completed outcomes without an explicit correction record.

### C03 — Calendar-day and trading-session horizons need explicit names

The outcome evaluator's `return_5d` uses calendar days; the ML/style language elsewhere often refers to trading sessions. A probability calibrated on one target is not automatically calibrated for the other.

**Solution:** store `horizon_unit`, target session/date, actual entry/exit sessions and label-version identity. Introduce explicit session-based metrics for new experiments while retaining legacy calendar-based results for reproducibility. Do not silently relabel old data. Tests must include weekends, US/HK holidays and missing sessions.

### C04 — Most active price heads are recent; one active symbol needs a data-quality hold

Among active, non-delisted stocks, **137 of 138 US symbols** and **42 of 42 HK symbols** have a daily-bar head dated at least September 16. All have some daily history. The exception is **SSNLF**, whose latest stored daily bar is **November 7, 2025**.

This is evidence of broad head-date coverage, not a full audit of missing sessions, corporate actions, provider accuracy or intraday quote freshness. A stale head alone is not proof of delisting.

**Solution:** mark SSNLF's execution/recommendation data as stale and investigate provider support, instrument status and session expectations. Use an eligibility gate tied to a documented data contract. Preserve a reasoned data-quality hold; do not infer delisting or manufacture bars. Add completeness, anomaly and freshness reporting for each forecast input.

**Acceptance:** stale critical price data cannot yield an executable new-entry playbook; UI and APIs distinguish unavailable data from a neutral/low-conviction forecast.

## 6. Remaining engineering issues and exact next actions

| Priority | Issue/status | Recommended solution | Completion evidence |
|---|---|---|---|
| First, before the next income expiry | **A17 remains in the inspected production-matching source.** The settlement result overwrites the integer counter. | Separate `settlement_result` and `settled_count`; enforce transaction rollback on failure; report batch failures accurately. | Behavior tests for zero/one/multiple positions, missing prices and injected failure; integer count and correct cash/position state. |
| First measurement batch | **C01: the fix-verification system does not yet answer whether recent fixes helped.** | Apply the actual cutoff, support or explicitly reject domains, restore durable scheduled remeasurement. | Post-fix snapshot unaffected by pre-fix rows; due supported fixes have snapshots or actionable errors. |
| Next measurement batch | **C02/C03: missing short-window feedback and inconsistent horizon semantics.** | Independent outcome resolution and versioned calendar/session targets. | Mature early windows populate without waiting for the full style hold; no future data. |
| Before trusting options equity/drawdown | Liability deduction is fixed, but marks can be stale or intrinsic-only. | Persist mark source/time; validate ask/underlying freshness; flag incomplete valuation; profile and batch the quote lookup. | Entry is economically equity-neutral apart from costs/spread; stale marks visibly lower valuation confidence. |
| Before weight promotion | Options selection study still lacks live capital/position constraints and purged label boundaries. | Replay feasible portfolios with label-maturity cutoffs and a fresh holdout. | Identical admissible trades under paper/replay and dependence-aware net results. |
| Before real automation | Broker entries/exits still lack a complete fill-driven, durable lifecycle. Earlier same-day DB inspection showed only unauthorized sandbox linkage. | Explicit modes, stable order intents, fill ledger, entry/partial-exit/full-exit reconciliation, protection and crash recovery. | Broker-paper fault tests pass with no unexplained positions/cash or duplicate economic orders. |
| Data quality | SSNLF is active with a stale price head. | Data-quality hold and provider/instrument investigation. | Stale data cannot authorize new exposure; supported inputs recover with documented provenance. |

A04's original liability omission, A05's earlier-close substitution and A13's shell failure masking have received T400 fixes. Those should not remain on the backlog as wholly unimplemented. Their remaining acceptance work and the A17 regression should be tracked explicitly.

## 7. Recommended next experiment for signals and playbooks

1. **Freeze a dated current baseline.** Record code commit, deployed artifact, model/calibration IDs, resolved configuration, universe, data-provider timestamps and cost model. Keep the existing SWING stop-width A/B separate; its earlier snapshot had zero trades.
2. **Repair and instrument the evaluator before optimizing.** Resolve C01–C03 and publish cohort counts with maturity states. Verify that paper exits cannot overwrite the meaning of a forward signal label.
3. **Prioritize diagnosing SHORT BUY.** Examine the resolved losing cohort by market regime, market/sector relative strength, gap/extension, earnings proximity, spread/liquidity and entry timing. Test one filter at a time against the same opportunities. Avoid raising the confidence threshold merely because the score is called confidence.
4. **Evaluate the promising SELL cohorts first as risk information.** Test whether a SELL state usefully avoids new long exposure or improves a predefined long-exit rule, with a matched incumbent. Evaluate short-stock and bearish-option execution as separate strategies with their own costs and constraints.
5. **Compare market-adjusted value.** A bearish call during a broadly falling market needs to beat a simple market/sector rule on the same dates. Likewise, a profitable long portfolio should be compared with the appropriate exposure-matched alternative.
6. **Use later untouched dates for validation.** The cohort tables above are now research/development evidence. Do not tune on them and then call performance on the same dates out of sample. Track stock/date clustering and retain more than one market condition.
7. **Review on a fixed cadence.** Each review should show net expectancy, payoff distribution, drawdown, costs, coverage, data quality, unresolved outcomes and operational failures—not only hit rate. Promote only when both statistical and execution evidence support it.

A useful dashboard would offer `All history`, `By entry month`, `Since deployed fix`, and `Current frozen experiment`, with market, style and direction filters. Each view should show the cutoff/version, total opportunities, open/pending counts, realized/unrealized separation, and whether the metric is signal-level or portfolio-level. This directly answers whether the system is improving without hiding old losses or blending them into every current-version result.

## 8. Limits and preserved evidence

This audit does not establish that the latest system is profitable or unprofitable over a representative future period. The newest stock cohort is small, options have no closed forward trades, and most current longer-horizon BUY outcomes are pending or not independently materialized. It also does not establish that worse SHORT BUY results were caused by a particular code change.

Current-source inspection and production hashes confirm A17 is still present in the inspected version; the executable reproduction remains in the [production verification report](2026-09-17-production-verification-audit.md). No production expiry path was invoked here. Earlier local test timeouts and external claims about their cause were not rerun in this cohort audit.

Temporary evidence was saved locally in `/tmp/stockai-september-cohort-results.jsonl` and `/tmp/stockai-september-cohort-details.jsonl`; these are not durable repository artifacts. This report preserves the relevant aggregates, cohort definitions, source paths, limitations and acceptance criteria. Production mutations and code fixes remain outside this audit's performed work.
