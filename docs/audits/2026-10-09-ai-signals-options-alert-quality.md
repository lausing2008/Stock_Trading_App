# AI Signals and Options Alerts: quality audit and next improvements

Audit date: 2026-10-09 America/Los_Angeles. Production read at 2026-10-10 01:20–01:23 UTC; revision `b21197c0`, 15/15 containers healthy. This report distinguishes that deployed baseline from the local changes accompanying this audit. No alerts, trades, historical rewrites or production captures were triggered by the audit.

## Assessment

The platform provides useful research observations, but **reliable profitable options opportunities are not demonstrated**. Flow direction is an aggressor-side inference, AI strength is a heuristic score, and the displayed historical rates concern underlying price moves. Neither is an option strategy return. Some recent US AI cohorts improved; the broader history is weak and the measurement has unresolved defects. A conclusion that the current alerts are profitable, useless, or should simply be inverted would exceed the evidence.

The immediate improvement is stock-level triage followed by an explicit alert review. A trade review needs exact legs, contemporaneous quotes, entry/exit policy, costs, deliverables and portfolio constraints. The current flow review does not supply those facts.

## Evidence and method

- [Read-only production probe](evidence/2026-10-09-ai-options-alerts/production-probe.py) and [sanitized results](evidence/2026-10-09-ai-options-alerts/production-results.json). Queries use read-only transactions; no recipients, credentials or message bodies are collected. The probe records a failed `rows_received` query explicitly; no conclusion about empty coverage rows relies on it.
- Traced AI model retrieval → per-style fusion → persistence → historical calibration → conviction/transition checks → direct email paths. Inspected the actual SignalCard and signal-filter rendering.
- Traced provider flow/chain adapters → candidate construction → candidate ledger → notification selection → eligibility partition → performance API → review page. Inspected fixed-contract capture/resolution and the new opt-in notification helper separately.
- Executed the real generator with deterministic external inputs; exercised real provider adapter functions with controlled responses; used the existing isolated real-SQLite request-handler probe for persistence and pagination. This is software verification, not performance evidence.
- No signed-in production browser session was available. Production database reads establish the stored facts, not what a user's browser rendered.

## Measured results

AI primary outcomes over the trailing 180 calendar days, **US only**:

| Style | BUY n | BUY hit rate | BUY mean underlying move | SELL n | SELL hit rate | SELL mean underlying move |
|---|---:|---:|---:|---:|---:|---:|
| SHORT | 3,464 | 43.62% | −0.735% | 1,341 | 41.69% | +0.725% |
| SWING | 3,042 | 41.81% | −1.630% | 1,122 | 43.49% | +0.534% |
| LONG | 2,340 | 49.15% | −1.358% | 1,139 | 42.32% | +1.347% |
| GROWTH | 3,390 | 42.21% | −1.801% | 667 | 38.98% | +1.368% |

These are stored underlying results, not independently reconciled returns. Positive raw return is adverse to a SELL thesis. Primary BUY windows are 7/14/28/14 calendar days; SELL uses its own shorter windows. A hit requires clearing the configured directional 0.5% hurdle; that hurdle is not a transaction-cost model. The table spans historical implementations and includes the 17 timing anomalies below. Counts are evaluated outcome rows, not the denominator of every emitted or delivered signal: the evaluator normally inserts the primary outcome only once its window matures. Zero recorded censoring therefore does not establish complete coverage.

The last 30 days differ: US BUY SHORT 51.69% (563 rows, 19 dates), SWING 58.01% (231, 12 dates), GROWTH 60.07% (268, 13 dates). LONG has 55 resolved rows from only **one date**. These are overlapping exposures, not independent trials. Recent improvement merits prospective testing, not an increased-risk recommendation. HK remains separate; its recent BUY cohorts are materially weaker, and US-only options planning must not silently inherit pooled HK calibration.

### Why the displayed AI strength is usually low

The stored field called `confidence` is `abs(fused_score - 0.5) * 200`. A fused score of 0.59 therefore renders as 18/100. It is not an estimate that the call has an 18% chance of success. The latest production cross-section confirms that near-neutral output is normal: US median strength is 19.50 SHORT, 17.48 SWING, 19.74 LONG and 20.08 GROWTH; HK medians are 16.91, 12.62, 19.31 and 12.95. Raising those numbers by presentation or a multiplier would manufacture certainty.

The low values have identifiable causes. Among 151 current US symbols, the stored low-OOS-quality flag is active on 114 SHORT, 123 SWING, 126 LONG and 127 GROWTH rows. The compression cap fired on 79, 48, 51 and 41 respectively; the SWING weekly gate fired on 14 and the SWING ADX reducer on 40. HK has the same pattern with lower medians and 23–27 of 42 names carrying low-OOS flags. These counts describe the current engine state; they do not prove each reducer improves predictions.

Production history also rejects the premise that a larger strength number is automatically better. The broad 180-day cohorts above are weak even though they contain the higher bands. The safe improvement is to make the decomposition visible, repair provenance and evaluate versioned prospective cohorts. It is not to relax safeguards until the number looks reassuring.

Flow, all stored resolved 10-calendar-day rows, under the real `flow-elig-2` partition:

| Direction | Original | Eligible | Excluded | Dates | Symbol/date groups | Contract-weighted hit rate | Equal symbol/date-group hit rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bullish | 1,031 | 432 | 599 | 20 | 150 | 52.08% | 56.00% |
| Bearish | 1,059 | 398 | 661 | 21 | 160 | 36.93% | 32.50% |

Bullish exclusions: 516 expired before alert, 83 same-day expiry. Bearish: 586 and 75. The largest eligible symbol/date group contains 13 contracts. Changing the weighting changes the headline; neither weighting supplies independent observations or an options profit estimate. This audit does not substitute these all-history figures for the page's separately selected lookback.

Squeeze 10-day underlying hit rates: short squeeze 25.0% (16 resolved), ignition 50.0% (6), gamma-unwind calls 49.68% (155), puts 45.24% (294). These are different mechanisms and small or correlated cohorts; no shared "squeeze probability" is justified.

Production fixed-contract ledger: **0 captures, 0 resolutions**. Notification outbox: **0 rows**. Existing legacy jobs still use direct send paths, so empty outbox does **not** mean no historical email was sent. It does establish that the new opportunity outbox path has no recorded production use.

## Findings, ranked by decision impact

| ID | Priority | Finding and consequence | Status / acceptance |
|---|---|---|---|
| AQ01 | P1 | `_fetch_ml_data` invented AUC 0.55 when every quality metric was missing. A response without validation evidence acquired positive fusion weight. | **Fixed locally:** zero-weight sentinel, explicit unavailable/invalid status, no reported AUC for missing evidence. Tests cover missing, null, nonfinite/out-of-range and real zero. |
| AQ02 | P1 | `generate_all_signals` copied SWING's model/AUC/agreement/probability-map into every style's reasons, while fusion used each style's own model. Production MU has identical stored AUC 0.59375 across all horizons despite different weights and probabilities. Calibration consumes this provenance. | **Fixed locally:** each style overwrites all model metadata, including nulls when absent. Real generator test uses four different model responses. Historical stored provenance is not repaired. |
| AQ03 | P1 | The AUC-scaled weight floor never saturated: at AUC .95, SHORT's .10 floor became .45 despite its .30 profile cap. This contradicts the comment that AUC ≥.60 earns the full floor. | **Fixed locally:** scale capped at 1.0. Real fusion test checks high-AUC SHORT weight. The existing deliberate minimum-floor precedence over a lower administrative cap is unchanged. No assertion of improved prediction follows. |
| AQ04 | P1 | `_adj_close` fills individual missing adjusted closes with raw closes; indicators also use raw high/low with adjusted close. A constant raw series `[100,100,100]` and partial adjusted series `[50,NULL,50]` becomes `[50,100,50]`: invented movement from a basis change. AI and flow outcome evaluators independently use `Price.close`, without the observation system's adjustment-evidence contract. | **Open:** use one explicit, evidenced OHLC basis per window; reject mixed bases, retain unresolved states, share adjustment evidence with outcomes. Test splits, dividends, partial factors and benchmark-only events. Do not silently rewrite historical returns. |
| AQ05 | P1 | AI calibration includes **17 outcomes with entry_date = signal_date** despite today's T+1-close convention. It pools historical model/rule versions, and `_calibrated_win_rate` silently falls back from a market bucket to pooled markets. Only a 30-row floor is enforced; no distinct-date requirement. | **Open:** immutable capture/resolver/model identity, timing eligibility with exclusions, explicit market fallback metadata, pending/censored denominator, date-cluster uncertainty and point-in-time benchmark. Preserve the original historical display values. |
| AQ06 | P1 | `_record_options_flow_alert_outcome` records every candidate **before** per-user cooldown, ranking and email cap. AI signal outcomes also describe generated signals rather than the delivered conviction subset. Neither is a clean denominator for "alerts you received". | **Open:** immutable candidate → decision → accepted/delivered identity and separate performance pools. Provider acceptance and inbox delivery remain distinct. Compare delivered selection against the full candidate set and a same-date baseline. |
| AQ07 | P1 | Flow accepts a parseable provider timestamp from a 48-hour discovery window without a maximum event-age gate. Signal alerts verify price recency but do not independently require the selected signal's timestamp to be current. Fresh prices do not refresh old model output. | **Open:** separate event, quote, signal and input-session clocks. Freeze age limits in policy and abstain on unknown/future/stale time for entry alerts. Keep risk-reducing notifications separately labelled. Boundary tests must reach the real send-selection path. |
| AQ08 | P1 | `get_historical_option_chain_result` treated `None` as a complete empty terminal page and silently dropped malformed rows while claiming complete coverage. | **Fixed locally:** only an actual list establishes a page; malformed contract rows leave retained valid rows explicitly incomplete. Prior coverage claims need a provenance-aware repair; the audit did not invalidate all 266 complete claims or assert they are all wrong. |
| AQ09 | P1 before activation | The new opportunity gate uses boolean size/capital assertions before calculating quantity. It does not bind the calculated size to numerical displayed size or buying power. The enqueue helper accepts a caller-supplied actionable verdict; it does not verify maximum loss against the budget and truncates quantities with `int`. | **Open; channel remains inactive:** bind evidence IDs, exact integral quantity, per-leg size, required cash/collateral, total loss and expiry to one server decision. Test forged/mismatched verdicts and fractional quantities. No producer currently calls the helper in production code. |
| AQ10 | P2 | Prospective strategy retry was validated against a newly assigned current timestamp before existing-capture lookup. A valid capture retried after entry was rejected. | **Fixed locally:** retrieve identical frozen capture before applying the clock rule to a new capture. Real DB probe proves retry succeeds and a different late capture fails. |
| AQ11 | P1 product boundary | The fixed-contract ledger is long call/put, supplied-quote, scheduled-close simulation only. Contract identity/deliverable evidence is asserted by its caller, trigger rules are descriptive, and there are no production captures. No multi-leg, assignment or broker-fill evidence exists in this ledger. | **Open:** O04 has infrastructure, not a completed live strategy demonstration. Add canonical contract validation and one prospective price-evidenced trace, then separately test spreads and assignment. Never relabel underlying studies as this ledger. |
| AQ12 | P2 | SignalCard displays "Confidence" and "Bullish" percentages although the values are distance-from-neutral and fused scores; the factor detail calls every model XGBoost. Signal Filters and several decision pages repeated the probability framing. | **Fixed locally on the principal decision surfaces:** SignalCard, Signal Filters, Rankings, Opportunities, stock summary/model panel, Learn and Signal Quality now use strength/directional scores on `/100`. Signal Quality no longer compares a strength-band midpoint with hit rate as though it were a calibration target. Historical rates name the underlying and pooled-history limitation. Administrative and legacy filter names still require a compatibility-aware terminology migration. |
| AQ13 | P2 | Flow Review is one historical contract alert. There was no full-filter stock grouping, and its company-research link supplied a query parameter the destination ignored. | **Fixed locally:** all-page server stock groups, mixed directions visible, counts/dates/recency, distinct "Review alert", symbol-aware research navigation. No trade suitability claim is added. |
| AQ14 | P2 | Existing performance API exposes rates even for tiny eligible cohorts, while the email calibration applies count/date floors. Both use the same partition, but different display eligibility. A source's reported side is still not opening/closing identity, dealer inventory or an independently verified trade. | **Open:** share cohort status as well as partition; expose insufficient history and evidence scope in every consumer. Preserve descriptive counts without decorating thin rates as predictive quality. |
| AQ15 | P1 presentation | The backend stores `breadth_compression` as a boolean event flag, while SignalCard treated it as a numeric multiplier. JavaScript coerced `false` to zero, so an explicitly inactive flag could render as “Breadth Compressed −100%.” | **Fixed locally:** render only when the flag is exactly `true`; describe the reducer without inventing a percentage. Tests cover both boolean states and the full strength explanation. |

Freshness qualification: 193 symbols have stored signals (151 US, 42 HK). The only latest signals older than three calendar days in this read belong to SKHYV, all HOLD and already carrying a stale warning. This read does not demonstrate that a stale BUY was actually emailed. `ml_oos_suppressed` is a neutralization/compression flag, not proof of zero fusion weight; the audit does not misclassify every positive weight beside it as a defect.

## Decision-page improvements

Implemented locally: stock overview → filtered historical alerts → selected alert review → symbol-specific Quality & Value research. Stock summaries aggregate the entire filtered ledger before the 25-row pagination; at most 50 symbols are shown, ordered by last alert. Direction filters affect the group counts and are disclosed. No premium totals are netted into invented buying pressure. A mixed group stays mixed even when most contracts point one way.

Next page delivery should show one stock/as-of/horizon at a time, with:

1. **Decision status:** research / waiting for confirmation / blocked / eligible for manual trade review. Name the blockers and the evidence that would clear each one.
2. **Thesis and opposing evidence:** AI direction, observed flow, squeeze context and event risk with independent timestamps and shared-source grouping. Do not count several contracts or reused provider data as independent votes.
3. **Trade alternatives:** exact legs/expiry and long/short exposures, bid/ask and sizes, timestamp, limit-price assumption, fees, payoff, break-even, loss bound, Greeks and event scenario. Include abstain and stock-only comparison where appropriate.
4. **Entry and exit:** price/volatility confirmation, invalidation, time stop, quote deadline, expiry/exercise handling. A touched daily high/low cannot establish trigger order; ambiguous bars remain unresolved unless finer data resolves them.
5. **Risk and evidence:** account-specific quantity only after current capital and positions are supplied; separate theoretical bounded payoff from operational assignment/execution exposure. Keep the proposed 0.25% per-trade / 1% aggregate loss limits labelled provisional planning defaults.
6. **Performance:** current-policy prospective fixed-contract net results, provisional/replay pools separately, n and distinct dates, unresolved count, exclusions, costs, benchmark and drawdown. Show "unmeasured" where the data is absent.

## Prioritized implementation and acceptance plan

| Sequence | Deliverable | Completion test |
|---|---|---|
| 1 | Common outcome/evidence contract for AI and flow; quarantine the 17 timing anomalies from corrected calibration | Mixed-basis and same-day-entry witnesses fail eligibility; originals remain readable; pending + resolved + excluded totals reconcile |
| 2 | Frozen source/model/policy identity and freshness gates on actual alert selectors | Fresh price + stale signal abstains; 48h-old flow cannot become a current entry notice; future/unparseable event times abstain |
| 3 | Durable candidate/decision/delivery ledger for existing signal and flow channels | One candidate accepted/omitted/deferred/failed is traceable without guessing from a Redis counter; no provider call in tests |
| 4 | One real prospective long-option trace plus canonical identity and numerical risk gates | Record before entry; resolve the same contract; missing quotes remain unresolved; correct stock direction with losing option is retained |
| 5 | Stock decision panel and structure comparison, then bounded spreads | Same frozen evidence powers API/page/notification. No eligible structure produces explicit abstain. Net P&L includes every leg and costs |
| 6 | Performance validation before enabling an opportunity channel | Prespecified holdout windows and alert budget; date/symbol-cluster uncertainty; matched baseline; costs/slippage sensitivity; no tuning on the evaluation cohort |

All strategy families remain in the playbook scope, but they do not share activation readiness. Long options and defined-risk verticals precede calendars/diagonals, income with collateral, volatility structures, 0DTE and assignment-sensitive positions. Covered calls and cash-secured puts remain bullish/income exposures; bearish input cannot rank them into a bearish recommendation. Gamma/short-squeeze and dark-pool evidence are context inputs, not substitutes for priceable contracts or a risk budget.

Monitor alert usefulness directly: duplicate alerts per stock/session, source and delivery lag, percent reviewed/dismissed by reason, valid entry windows remaining on arrival, blocked-candidate reasons, and net outcomes of the actually eligible/delivered cohort. Improving precision by sending fewer alerts must retain the denominator and misses, otherwise silence can appear to be accuracy.

## Source boundaries

[OCC/OIC general information](https://www.optionseducation.org/referencelibrary/faq/general-information) explains why open interest requires opening/closing information; volume and a bid/ask classification alone do not establish it. [NYSE's open-close data product](https://www.nyse.com/data-products/catalog/open-close-volume-summary) treats opening/closing, buy/sell and participant category as separate dimensions. [FINRA on assignment](https://syndication.finra.org/content/trading-options-understanding-assignment) describes early exercise and obligations on short legs even within multi-leg positions. These support the evidence boundaries; they do not validate this platform's predictions.

## Verification and deployment status

Local verification completed for the audit changes:

- Signal-engine provenance and missing-quality witnesses: 8 focused tests pass; the complete service suite passed earlier in this change set with 576 passed and 1 skipped.
- Market-data provider coverage and fixed-ledger request path: 19 focused tests plus the real SQLite handler probe pass; the complete service suite passed earlier with 5,298 passed and 18 skipped.
- Frontend: 571 tests pass, typecheck is clean, and the production build completes across 73 pages. Focused cases cover strength decomposition, the boolean breadth boundary, stock flow grouping and symbol-specific research navigation.
- A local headless-browser fixture exercised stock grouping, mixed direction, server symbol filtering, selected-alert review, the Quality & Value deep link, and distinct error/empty states. It used an unsigned client-only fixture, which produces an expected SSR/auth hydration mismatch; there was no signed-in production browser verification.

Production figures above describe revision `b21197c0` before these local changes. No software test or deployment establishes improved predictive performance.
