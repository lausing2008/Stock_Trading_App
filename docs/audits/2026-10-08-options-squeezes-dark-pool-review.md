# Options, short/gamma squeezes and dark-pool review

Date: 2026-10-08. Scope: US options, all strategy families. Documentation and read-only review; no trading rules, subscriptions, orders or deployments changed.

## Decision

Consolidate discovery and explanations, but **do not promote the current alert hit rates into probabilities of profitable option trades**. The platform has useful ingredients and real contract history. It does not yet have one consistent path from an observed event to a suitable, currently priced option structure, a delivered alert, and the strategy's net outcome.

Prioritize correctness and contract eligibility before adding more signals. Four immediate problems are a bullish structure selected for a bearish view, option-flow premium silently becoming zero through the chain adapter, historical expired-contract candidates contaminating calibration, and multiple outcome conventions that still measure underlying moves using calendar-day windows.

The user's scope is **all styles, US options first**: intraday squeezes, directional swings, income, hedging and volatility. Provisional risk policy: 0.25% of account net liquidation value per trade; 1% combined open risk. These are conservative design defaults, not calibrated optima. Account value, holdings and buying power remain unknown; no actionable quantity can be produced yet. Detailed policies are in the playbooks.

- [Consolidation architecture, delivery plan and acceptance gates](../features/2026-10-08-options-opportunity-consolidation-plan.md)
- [Strategy playbooks and notification examples](../features/2026-10-08-options-opportunity-playbooks.md)
- [Reproducible evidence and limitations](evidence/2026-10-08-options-review/README.md)

## Review method and boundaries

Reviewed provider adapters, archived chains, flow snapshots, short-interest and gamma consumers, dark-pool collection, strategy construction, income paper engine, LEAPS and income backtests, alert selection/delivery/calibration, outcome evaluators and major UI surfaces. Consulted prior audits as historical evidence, then checked present source and bounded production aggregates. Local HEAD at review: `4d3f4997`; [source digests](evidence/2026-10-08-options-review/source-digests.json) identify reviewed files independently of concurrent work.

Verification levels are deliberately separate:

- **Reproduced:** actual pure strategy builder exercised with deterministic inputs.
- **Measured:** read-only production SQL around 2026-10-08 07:11 UTC; no recipient identities extracted.
- **Source-confirmed:** executed paths inspected but not necessarily exercised through authenticated production routes.
- **Hypothesis:** needs a provider-contract or runtime check; not reported as a proven defect.
- **Historical:** earlier measured audits, explicitly dated, not assumed to be current.

Ran the market-data options/squeeze/dark/gamma/LEAPS filename-selected suites: **1,050 passed, three deprecation warnings**. A focused eight-file run also passed 205 tests; it overlaps the larger run and is not an additional independent 205. These passes do not establish correctness of uncovered branches or predictive performance. No authenticated browser test, real fill reconciliation, full provider-entitlement audit, or exhaustive security/load audit was performed. User's concurrent observation-system edits were left intact.

## What exists and should be reused

| Capability | Current owner / entry point | Assessment |
|---|---|---|
| Historical chain and Greeks | `unusual_whales.get_historical_option_chain`, `uw_option_chain`, `option_chain_history` | Useful settled-session archive; not an intraday executable quote feed |
| Chain/flow/game-plan routes | `market-data/src/api/routes.py` | Rich UI data, but adapter semantics and source dates need consolidation |
| Actual provider flow events | `scheduler.check_options_flow_alerts`, UW `get_flow_alerts` | Ask/bid imbalance, sweeps, contract identity, filtering and delivery accounting exist |
| EOD flow | `options_flow_snapshot.compute_options_flow` | Separate yfinance aggregation; diverges from migrated UW route |
| Short squeeze / ignition | `check_short_squeeze_alerts`, `check_squeeze_ignition_alerts` | Short-float, momentum, relative-volume and freshness controls; weak measured cohorts |
| Expiry/OI watch and GEX | `check_gamma_unwind_alerts`, `get_gamma_exposure`, `gex_snapshots` | OI proxy and provider GEX are distinct; neither proves observed dealer inventory |
| Dark-pool activity | `check_dark_pool_alerts`, `dark_pool_prints` | Persists prints; size/relative/recency filters; appropriately nondirectional outcome |
| Structure arithmetic | `options_strategies.build_strategy_matrix` | Long call, covered call, protective put, CSP, collar, bull-call/bear-put debit spreads; pricing safeguards exist |
| Income paper portfolio | `options_income_engine`, `api/options_income.py` | CC/CSP selection, collateral, locks/idempotent intent, settlement and mark-quality machinery |
| Contract history performance | `api/_t411_ivhv.py`, `OptionsPerformanceTable.tsx` | Fixed call/put identities over a window; midpoint-mark performance, not realized fills |
| LEAPS studies | `backtest/leaps_backtest.py` | Ask-entry/bid-exit and roll comparisons; reusable contract tracking, not a universal strategy engine |
| Income replay | `backtest/options_income_backtest.py` | Reuses actual selector; explicitly omits fees/slippage and early assignment |
| Stock signal integration | `signal-engine/src/generators/signals.py` | Options sentiment directly affects fused stock signal; semantic errors can propagate beyond Options UI |
| Delivery | market-data `scheduler.py`, `email_service.py`, shared alert preferences | Existing transport and accounting should be reused; new opportunity type must be opt-in |
| User surfaces | `/options-flow`, `/options-flow-alerts`, `/short-squeeze`, `/squeeze-alert-performance`, `/options-income`, guides/calculator, stock Options tab | Keep specialist detail; add one concise opportunity summary rather than another independent scanner |

## Production observations

### Alert records and their existing underlying-return metrics

These are stored candidate/outcome records, **not delivered-email counts**. “5d” below is the legacy evaluator's calendar-day-plus-forward-bar convention, not five trading sessions. Returns use underlying prices and lack the new strategy-level execution/adjustment contract. Do not infer option profitability, causality, or robust predictive skill.

| Family / direction | Records | Non-null 5d returns | Stored hurdle hit rate | Mean raw underlying return |
|---|---:|---:|---:|---:|
| Short squeeze | 17 | 16 | 12.50% | −4.858% |
| Squeeze ignition | 6 | 6 | 33.33% | −5.260% |
| Gamma unwind calls | 190 | 161 | 39.75% | −0.457% |
| Gamma unwind puts | 340 | 316 | 44.30% | +1.263% |
| Options flow bullish | 1,128 | 1,085 | 55.21% | +2.784% |
| Options flow bearish | 1,192 | 1,109 | 30.21% | +3.629% |
| Dark-pool activity | 1,075 | 927 | 64.62% absolute-move hurdle | Not a directional strategy |

Bearish rows' raw positive return is an underlying rise, not a profitable short. Dark-pool success means an absolute move exceeded 2%, not that a purchased straddle overcame its premium. Cohorts overlap symbols/days; apparent sample size overstates independent evidence. Short-squeeze/ignition samples are particularly small. No matched controls or cost-adjusted strategy results were computed in this audit.

Flow cohorts contain **1,102 contracts expired before `fired_date`** (516 bullish, 586 bearish), and **1,300 expired before the evaluator's entry date**. They span 28 alert dates. Recent check: **0 expired-before-alert among October 1–7 records**, but 45 of those 168 candidates expire on their alert date. Therefore distinguish historical contamination from current behavior. Same-day contracts may be legitimate intraday research; next-day entry cannot measure their trade.

### Data and paper coverage

| Measure | Observed |
|---|---|
| Flow snapshots | 2,040 rows / 85 symbols; newest label Oct 7 |
| Game-plan snapshots | 795 / 74; newest label Oct 7 |
| GEX snapshots | 756 / 71; newest label Oct 7; no null gamma flips in these rows |
| Recent chain coverage, queried Oct 1 onward | 140 symbols: 101 latest Oct 1, two Oct 5, 37 Oct 6 |
| Recent symbol/session slices | 255; **225 contain exactly 500 rows**, maximum 500 |
| Crossed positive bid/ask in recent slice | Zero observed; does not establish other quality dimensions |
| Database statistics estimates | ~1,094,397 chain rows; ~536,386 dark-pool prints; approximate, not exact counts |
| Income positions | Four closed CSPs, seven open positions (one CC, six CSPs); none of the open positions past expiry |
| Closed income stored P&L | $1,987 summed across four rows; unverified model P&L, not brokerage profit or proof of edge |

Snapshot `as_of`/computation date does not prove quote freshness. A full-history chain aggregate hit a 30-second statement timeout; the transaction rolled back, and subsequent checks used bounded windows. No exact all-history chain count is claimed.

## Findings and corrective direction

### O01 — High: strategy recommendation can contradict the stated direction

**Reproduced.** [`options_strategies.py`](../../services/market-data/src/services/options_strategies.py), `_recommend` (~513), prioritizes IV and ownership. With `signal=SELL`, no shares, IV rank 80 and valid bearish and bullish spreads, primary is `cash_secured_put`. The same happens for HOLD and BUY. A CSP carries bullish downside exposure. A bearish debit spread is available but not selected for this scenario. [Witness](evidence/2026-10-08-options-review/strategy-witness.py).

This is a builder finding, not proof a particular live email recommended that exact trade. The game-plan route also calls construction with `signal=None` (~4754), so a default income answer must not masquerade as thesis-specific advice.

**Fix plan:** require objective and compatible directional/volatility exposure before ranking; separate income intent from bearish speculation; require cash/holdings/permission/assignment willingness. Unknown direction permits neutral research or no recommendation, not invented bullish conviction. Acceptance: SELL/no shares can choose an eligible bearish structure or abstain, never a bullish primary through IV fallback.

### O02 — High: migrated chain makes route premiums/whales zero

**Source-confirmed end-to-end data transformation.** `uw_option_chain._to_chain_row` (~67) sets `last_price=0.0` because this archive has no last-trade field. `routes._uw_option_chain` copies it into `lastPrice`. `get_options_flow` (~3430) computes `premium = volume * lastPrice * 100`, then `is_whale = premium > 500_000`. Thus UW-derived unusual rows have zero premium/false whale flags regardless of activity. The pressure score consumes the whale count.

Replacing zero with midpoint would produce a *marked turnover estimate*, not actual paid premium or a single whale trade. Contract daily volume can aggregate many unrelated trades. Preserve unavailable premium as null; use actual tape-event premium where licensed/available and a separately named estimated turnover otherwise. Add a route test using the real UW adapter's shape; existing yfinance-shaped fixtures miss this mismatch.

### O03 — High: historical invalid contracts still feed calibration

**Measured + source-confirmed.** `scheduler._build_options_flow_alert_calibration` (~5502) selects all non-null 10d hit flags for a direction, without contract-validity, resolver-version or supersession filters. The 1,102 expired-before-alert records remain in the table. Current event parsing/dedup improvements do not repair historical eligibility. Recent clean dates do not prove the full calibration is clean.

**Fix plan:** immutable eligibility verdicts and corrected outcome versions; keep old records visible but exclude invalid cohorts from performance. Freeze expiry/last-trading-instant checks at capture. Separate same-day event research from an actual intraday contract simulation. Preserve counts for invalid, unresolved, expired, suppressed and delivered states. Do not overwrite old rows to make the history look better.

### O04 — High: existing outcomes cannot validate options-profit alerts

**Source-confirmed.** `scheduler.evaluate_*_alert_outcomes` (~6842–7235) loads D1 `Price.close`; entry is first stored bar at/after next calendar day. Targets are `entry_date + timedelta(days=window)`, with `_squeeze_outcome_lookup_price` allowing a later bar within a grace period. Existing non-null prices are skipped; no per-outcome resolver fingerprint is visible in these table schemas. Benchmark, option bid/ask, fees, assignment and adjustment basis are absent from this path.

The email already labels flow metrics “Underlying directional hit rate” and explicitly says not option P&L (`email_service.py` ~2102); retain that correction. The remaining issue is measurement capability and validity, not alleging that every email calls it option profit.

**Fix plan:** retain descriptive event-study returns separately; reuse the session-aware/versioned observation principles, then add a true fixed-leg strategy ledger. Missing scheduled prices remain unresolved. Check corporate actions for stock, option deliverables and benchmark. A profitable underlying move must not count as a profitable call automatically.

### O05 — High: settled quotes and incomplete eligibility are insufficient for actionable alerts

**Source-confirmed.** Income selection accepts chains up to five calendar days old (`_INCOME_MAX_CHAIN_STALENESS_DAYS`, ~134), reprices moneyness using current stock price, and uses archived bid premium. Its SQL requires bid/delta/OI, but does not gate selection on a valid ask, spread width, quote timestamp/size or contract deliverable. `_next_earnings_by_symbol` explicitly lets absent calendar entries pass. Matrix `_leg` accepts undated last trades (reproduced), assumes a 100-share multiplier and has no per-leg timestamp. The route does disclose settled `chain_as_of`; that is useful but not execution validation.

**Fix plan:** distinguish `research_candidate` from `actionable`. Timestamped two-sided quotes, expiry/last-trade time, deliverable, market status, spread/size, capital and event-calendar coverage are mandatory for the latter. “No event known” and “verified no event during holding window” differ. Archive data remains useful for EOD research.

### O06 — High: flow semantics and independent copies create misleading agreement

**Source-confirmed.** `options_flow_snapshot.compute_options_flow` still fetches yfinance and duplicates call/put sentiment thresholds, while the route uses UW. It aggregates `volume × lastPrice × 100` and labels large contract aggregates whales. The source explicitly admits independent implementations. A partial expiry-fetch failure continues without a coverage field. `signal-engine/generators/signals.py` (~2456) consumes these bullish/bearish-style labels and changes the fused stock signal.

Calls can be sold or hedge another position; put activity can hedge stock. Call-heavy volume establishes composition, not bullish intent. Ask/bid-classified flow is stronger evidence about execution aggressor but still does not establish opening/closing, the whole spread, or portfolio intent.

**Fix plan:** one normalization/aggregation owner; separate tape events from chain activity; nulls remain unknown; persist attempted/received expiries and source sessions. Rename chain-derived direction to call-heavy/put-heavy and test the incremental predictive contribution before applying any stock-signal bonus. Group flow, options pressure and gamma evidence by shared underlying source so they are not three independent confirmations.

### O07 — Medium: GEX/short-interest provenance is insufficient for strong positioning claims

**Source-confirmed.** `unusual_whales.get_gex_levels` (~715) chooses `data[0]` and stores wall/flip/magnet/date, without expiry selection, dealer-sign assumptions or model version. API prose says “real dealer gamma”; a provider calculation is not independently observed dealer inventory. The expiry/OI alert is explicitly a proxy and should retain that distinction. `get_short_interest_uw` drops `market_date` even though `ShortInterestData` has it.

**Fix plan:** explicit expiry aggregation/ordering; model and units, source timestamp, dealer-position assumption, sensitivity under alternative signs; separate borrow-market observations from dated reported SI. Never infer squeeze probability from proximity to a flip/max-pain level alone. Cboe's capacity-aware research illustrates why gross option volume and dealer net exposure are different measurements. [Cboe](https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact)

### O08 — Medium: dark-pool alert frequency is not evidence of useful trade selection

**Source + historical measurement.** The current relative threshold remains 5× median print size, with $1M absolute floor, 14-day baseline and 20-print minimum. The September 30 audit measured only **15/9,710** absolute-qualified prints rejected by the relative bar; do not claim that rate was remeasured today. Current production has 1,075 outcome records over Sept 2–Oct 7; the 64.62% absolute-move rate lacks an eligible-symbol baseline and says nothing about paying options premium.

**Fix plan:** measure marginal selectivity, event-time unusualness, clustered prints, participation versus normal dollar volume, price reaction and matched controls. Event baselines must precede the event. Treat activity as context until it adds held-out net strategy value. A large off-exchange print does not establish buyer intent, institutional identity or even ATS execution without venue evidence. FINRA distinguishes ATS and non-ATS OTC trading. [FINRA](https://syndication.finra.org/content/where-do-stocks-trade)

### O09 — Medium: archived chain completeness is unestablished

**Measured anomaly, cause unconfirmed.** 225/255 recent symbol-session slices contain exactly 500 rows; maximum 500. `get_historical_option_chain` (~2119) makes one request and exposes neither pagination nor coverage metadata. This is evidence to investigate a response cap, **not proof the provider supports pagination or that all chains are truncated**.

**Fix plan:** check current provider specification/entitlement and a known large chain against returned contract/expiry inventory. Persist request window, pages, terminal cursor or completeness guarantee, expiry/strike counts and errors. Until established, “full chain” and absence-of-contract conclusions are unsupported.

### O10 — Medium: snapshot fallback references an undefined variable

**Source-confirmed conditional defect.** `options_game_plan_snapshot.compute_options_game_plan_snapshot` (~136) calls `t.history(...)` when `_goal_current_price` returns None, but no `t` is bound in the function/module. The broad outer exception converts failure to no snapshot. This is not evidence of a current outage; 795 stored snapshots show the ordinary branch runs.

**Fix plan:** explicit shared price adapter with age/provenance, test the real missing-current-price branch. Preserve chain source date separately from snapshot computation date; the present game-plan store labels rows by the computation day's `as_of`.

### O11 — Medium: delivery consolidation and suitability are still missing

**Source-confirmed architecture gap.** Several alert families have their own recipient/symbol/contract cooldown and calibrated cohorts. Flow candidate records are written before recipient delivery. Existing preference helpers treat absence as subscribed and lookup failure as fail-open. That legacy policy must not silently enroll users in a new financial-opportunity channel.

**Fix plan:** one opportunity identity with multiple contributing signals, durable per-recipient delivery status, explicit subscription, requested strategy styles and risk gates. Keep essential existing risk alerts separate. Daily budgets apply to opportunity notifications, not to silently dropping position-risk escalations. Reuse sender transport; do not build another email service.

### O12 — Medium: income and LEAPS evidence should not be generalized to all strategies

**Measured + source-confirmed.** Four closed income rows are too few to validate profitability. The income replay explicitly omits early assignment, fees, slippage and impact; current-universe survivorship is disclosed. LEAPS ask/bid replay is useful but a quote is not a fill. The performance table uses midpoint marks and its explanation attributes a rising-stock/falling-call case to time decay alone; IV, quote changes and dividends can also matter.

**Fix plan:** show attribution only when calculated; model assignment and financing/dividend cash flows, contract adjustments, full mark coverage and execution uncertainty. Compare CC with stock ownership, CSP with cash plus an explicit stock-entry alternative, and hedges with the unhedged portfolio. Optimize risk-adjusted **net** outcomes, not annualized premium yield or raw hit rate.

## Priority order

1. **P0 before opportunity promotion:** O01–O05; stop invalid contracts/quotes/contradictory direction entering the actionable queue and quarantine invalid measurement cohorts. Preserve current history.
2. **P1 before expanding coverage:** O06–O10; shared source semantics, dates, completeness and provenance; repair fallback and add integration tests that use actual adapters.
3. **P1 before new notifications:** O11; explicit opt-in, portfolio constraints, consolidated identity and delivery ledger.
4. **P2 before probability/edge claims:** O12 and strategy-level prospective outcomes; benchmark, fees, uncertainty and independent holdout validation.

No gain guarantee follows from these changes. The measurable goal is fewer invalid/noisy opportunities and demonstrably better net outcomes when sufficient prospective evidence exists. “No eligible opportunity” is a useful result, not a reason to lower the gates.

## External reference checks

- [FINRA: short interest versus short-sale volume](https://syndication.finra.org/content/short-interest-what-it-what-it-not): position inventory and transaction volume are different; do not substitute one for the other.
- [OCC/OIC: options assignment](https://www.optionseducation.org/referencelibrary/faq/options-assignment): short American equity options can be assigned before expiry; dividend timing matters. These risks apply even inside a spread.
- [Cboe: dealer positioning](https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact): source-specific positioning evidence is required; gross flow alone does not identify net dealer exposure.
- [FINRA: where stocks trade](https://syndication.finra.org/content/where-do-stocks-trade): off-exchange and dark-pool labels are not interchangeable venue/intent proofs.
- [OIC strategy guide](https://www.optionseducation.org/getmedia/68305977-b772-41c8-bf1d-3d405725b3cf/options-strategies-quick-guide-2025.pdf): strategy payoff reference for the playbooks, not performance evidence.

These references support market mechanics, not the proposed thresholds, expected returns, or current provider entitlements.
