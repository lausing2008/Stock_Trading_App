# Consolidated US options opportunities: design and delivery plan

Date: 2026-10-08. **Proposed implementation; no runtime changes in this delivery.**

Companion documents: [audit and evidence](../audits/2026-10-08-options-squeezes-dark-pool-review.md), [strategy playbooks](2026-10-08-options-opportunity-playbooks.md).

## Product outcome

One concise answer to: **What opportunity is worth investigating, why now, which strategy fits, what could invalidate it, and what can I lose?** Evidence expands below the answer. All styles remain available, but strategies graduate separately as their data and measurement become adequate.

US options only initially. HK stock direction screens remain available, but no US chain, 100-share multiplier, calendar or exercise assumption may silently apply to HK instruments. Buying an option is not automatically appropriate merely because the underlying has a bullish/bearish label.

Example card layout, using placeholders rather than a fabricated live recommendation:

> **XYZ — bullish continuation watch · 5–20 trading sessions**  
> Candidate: bull call debit spread; confirmation not yet met.  
> Why: price approaching resistance; relative volume elevated; independently sourced catalyst.  
> Counterevidence: option premium already prices a larger move than the conservative scenario.  
> Trigger: completed break/retest under policy P; invalidate on close below L.  
> Legs/expiry: shown only with valid identity and quotes; quote time and source beside price.  
> Maximum position loss: $… including modeled costs; planned exit loss is a separate estimate.  
> Status: research only / actionable under configured limits / blocked, with specific reason.  
> Performance: unmeasured until eligible outcomes exist.  
> Expand: sources, contradictory evidence, payoff, Greeks, fill assumptions and audit history.

Place this reusable component in Quality & Value's single-symbol view and the existing stock Options tab. A consolidated shortlist can live within the existing options surface. Keep income portfolios, calculators and performance pages as specialist drill-downs; avoid a second scanner or duplicate business logic.

## Existing parts to consolidate

| Existing owner | Retain | Change / new shared responsibility |
|---|---|---|
| Market-data provider adapters and archive | Fetching, caching, quota controls, contract history | Typed availability, source timestamps, complete-window claims, option deliverables and quote quality |
| Flow, squeeze and dark-pool jobs | Mechanism-specific discovery and raw evidence | Emit normalized candidates into one opportunity pipeline; stop independently inventing strategy recommendations |
| `options_strategies.py` | Tested payoff arithmetic and valid vertical/collar construction | Registry of strategy eligibility, objective, directions, legs, risks and lifecycle |
| Income engine | Capital locking, idempotent paper intent, CC/CSP settlement | Reuse eligibility/quotes; label current simulation assumptions; preserve existing paper behavior until separately validated |
| LEAPS/performance tooling | Fixed-contract tracking, bid/ask replay, missing mark disclosure | Common contract resolver and mark/execution policy, not copied backtests |
| Research observation system | Frozen inputs, session policies, resolver fingerprint/supersession | Adapt to option legs and strategy cash flows; do not force stock-return records to pretend to be option trades |
| Existing email service | Transport, templates, unsubscribe/settings | One opt-in opportunity channel with durable per-recipient lifecycle and budgets |
| Decision/risk services | Account, portfolio, market and existing guardrails | Explicit options permissions, economic exposure, settlement/assignment and correlation checks |

The options pipeline may reuse the thirteen-bucket stock context. Its contract/liquidity/portfolio checks are **additional mandatory gates**, not another weighted stock score. Permanent unknowns in company durability can coexist with a price watch; an unknown option deliverable cannot coexist with a reliable maximum-loss claim.

## Proposed data contracts

### Evidence item

Store `evidence_id`, instrument ID, event ID, event time, publication time, received time, market session, source/provider, source family, raw reference/hash, normalized values/units, method version, evidence type and scope. Persist `availability_reason`, freshness policy, completeness basis and errors.

Examples of types: completed price/volume, option trade event, contract OI snapshot, reported short interest, borrow availability/fee, provider GEX estimate, off-exchange print, earnings event, portfolio holding.

Group by source/event for independence while retaining **every distinct finding and contradiction**. The same UW sweep reused in flow, pressure and a gamma narrative is one underlying event, not three votes. Preserve contradictory directions; disagreement can lower support or produce no trade rather than be hidden by averaging.

### Contract and quote

Contract identity includes provider symbol plus normalized root/underlying ID, call/put, strike, expiry, last trading instant, venue/currency, exercise and settlement style, multiplier, deliverable, adjustment memo/reference and supported status. Ordinary US equity contracts commonly use 100 shares; this must be data, not a universal constant. Exclude adjusted/nonstandard contracts from promotion until their deliverables are parsed.

Quote includes bid, ask, sizes where available, quote timestamp, source event timestamp, receipt timestamp, underlying price/time, trade conditions, delay/entitlement flag and market status. Model Greeks include pricing inputs and units. Zero IV or zero premium must not stand for unavailable.

Archive completeness is an explicit claim: requested/received expiries and strikes, pages/cursors, event window, source guarantee if any, and `partial/response_only/verified`. A successful HTTP response is not a completeness guarantee. Do not manufacture a page loop until the provider's contract is checked.

### Opportunity

Proposed fields:

```text
opportunity_id / parent_episode_id / version
symbol, market, instrument_id, thesis_kind, direction, volatility_view
observed_at, horizon_sessions, origin = prospective | replay
three_main_factors[], strongest_counterevidence, evidence_ids[]
confirmation_rule, invalidation_rule, time_stop, event_policy
strategy_id, objective, legs[], contract_ids[], quote_snapshot_ids[]
price_assumptions, max_debit_or_min_credit, payoff_at_expiry, scenario_grid
max_economic_loss, collateral, risk_budget, proposed_quantity
eligibility_checks[], blocking_reasons[], source_completeness
support_quality, predictive_confidence = null unless validated
state, expires_at, policy_fingerprint, frozen_input_digest
```

An opportunity may have several eligible strategy alternatives. Each alternative has distinct cash flows and outcome records; all share the same thesis/evidence parent. A stock/setup rank orders research attention, never implies a calibrated success probability.

### Lifecycle and measurement

Suggested states: `discovered → research_watch → confirmed → eligible_for_notification → notified → expired/invalidated`, with separate `blocked` reasons and `simulated_open/closed` positions. A delivered notification is not an executed trade. User execution, if later imported, gets its own broker execution IDs and actual fills.

Preserve discovery even when not eligible, so the system can measure filters and missed opportunities without backfilling winners. Freeze candidate selection, alternatives, inputs and policy before outcomes. Revisions append; invalid originals remain discoverable but excluded from eligible aggregates.

## Gate order

1. **Data and identity:** market/contract support, event age, quote freshness, completed price bars, action/deliverable coverage. Unknown event age blocks “fresh” urgency.
2. **Thesis:** defined direction or volatility proposition, horizon, confirmation/invalidation; strongest counterargument visible. A gamma/short/dark-pool flag alone remains a watch.
3. **Strategy fit:** directional exposure and volatility exposure match thesis; objective matches income/speculation/hedge. A bearish view cannot select a bullish CSP by default.
4. **Economics:** valid leg structure, bid/ask consistency, expiration match where required, net debit/credit after cost assumptions, scenario sensitivity. High IV rank alone is not evidence that selling vol has positive expected value.
5. **Account:** holdings, freely coverable shares, existing short calls, cash and buying power, approval level, concentration, settlement/assignment capacity and risk budget.
6. **Notification:** opt-in, preferred styles/horizons, material transition, deduplication, cooldown and recipient budget. Data-fault notices are not opportunity alerts.

Do not combine these gates into a compensating score: a strong squeeze reading cannot make an expired contract tradable. Optional evidence can be missing if it does not invalidate the selected strategy; the missingness remains visible.

## Accuracy experiments

“Accuracy” has at least four meanings and must be reported separately:

| Measure | What to measure |
|---|---|
| Data correctness | Identity/expiry failures, stale quotes, missing sessions, incomplete chains, corporate-action mistakes |
| Directional research | Fixed-horizon signed/excess stock returns, matched controls, false-break rate, time-to-confirmation |
| Option strategy performance | Net P&L, return on capital at risk, loss distribution, drawdown, fill/assignment and opportunity costs |
| Notification utility | Valid opportunities delivered, duplicates, latency, recipient volume, acted-on rate where explicitly reported |

Do not tune thresholds to maximize a single historical win rate. A system with frequent small wins and rare large losses can have negative expectancy.

### Experiment families

- **Short squeeze:** dated SI + price/volume versus price/volume alone; add borrow fee/availability only with timestamps and source coverage. Label observed price behavior, not “forced covering proved.” Stratify by liquidity, market capitalization, regime and extension; no unverified short-volume substitution.
- **Gamma:** expiry/OI concentration alone versus provider GEX context and versus price-only trigger. Separate near-expiry from longer DTE. Freeze dealer-sign/model assumptions and test sensitivity. Never treat max pain as a target by definition.
- **Flow:** timestamped ask/bid imbalance and identified sweep clusters versus chain call/put composition. Separate opening/closing known/unknown, single versus suspected multileg, 0DTE versus swing, and delayed discovery.
- **Dark pool:** activity magnitude/participation and post-print response versus matched symbol/time controls. Measure the relative threshold's marginal selectivity. Prove incremental value over the price trigger; large notional alone is not a trade.
- **Income:** CC versus owning stock; CSP versus cash and a specified limit-entry policy. Separate premium received from total economic return. Include assignment, dividends, financing and reinvestment assumptions.
- **Volatility strategies:** implied move/term structure/skew against a forecast made before the event, and realized **strategy** cash flows afterward. Realized underlying movement exceeding a fixed percentage is not sufficient.

### Statistical protocol

Version policies, separate research/development/holdout periods, purge overlapping label windows and embargo adjacent samples where leakage is plausible. Cluster uncertainty by symbol and event/date; contracts on one earnings day are not independent trials. Correct for trying many thresholds and strategies. Keep prospective and retrospective replay reports separate. Show unresolved/invalid/excluded counts and reasons beside every performance table.

Use Brier/reliability diagnostics only for genuinely probabilistic predictions, with a proper horizon and event definition. Publish confidence intervals and effective cohort size; a minimum raw count such as 30 is not a universal validation gate. In sparse cohorts publish “unmeasured,” not a precise percentage. A calibrated chance of positive return still does not establish favorable expected dollars.

Promotion requires reproducible held-out and then prospective results on a defined universe and execution policy, reasonable robustness to worse fills/costs, and no unresolved measurement defect. Sample-size targets must be derived from the minimum economically meaningful improvement and observed dependence/variance, not chosen after seeing the result.

## Execution and outcome policy

- Separate source observation, decision availability, alert acceptance, possible order time and actual fill time.
- For live notification eligibility, use timestamped quotes and an explicit maximum acceptable debit/minimum credit. Do not promise a fill at mid, bid or ask.
- For simulation, record conservative buy-at-ask/sell-at-bid assumptions and additional fee/slippage scenarios; include size/market-depth limitations. Intraday strategies require intraday option quotes; EOD prices cannot prove an intraday stop or entry.
- Freeze the actual legs. Do not reselect the nearest ATM contract every day.
- Use exact exchange sessions and contract trading cutoffs. Missing marks remain missing. A holiday does not silently change the specified horizon.
- If target and stop both occur within one bar and ordering is unknown, mark ambiguous or use the predeclared conservative ordering; do not select the favorable path.
- Mark open shorts using a defensible liquidation liability; separate theoretical model marks from observed bid/ask and actual execution.
- Include dividends and exercise/assignment cash flows, splits, mergers and adjusted deliverables. A spread's terminal maximum loss does not eliminate interim assignment/financing exposure.
- Persist resolver and adjustment-method versions; corrected results supersede rather than overwrite. APIs/UI/calibration query the same eligible version.
- Total strategy performance and underlying directional studies have separate denominators, units and benchmarks.

## Notification design

Reuse `email_service.py` but add explicit `options_opportunities` opt-in with selected strategies. User requested all styles in this design; this document does not change their production subscriptions or send messages.

Suggested default settings for a future opt-in: three new opportunity emails per US session plus a digest; user-adjustable; material risk/invalidations of followed positions handled separately. This is a noise-control proposal, not a trading-frequency target. No quota forces the system to fill slots.

Dedup by thesis episode + instrument + direction + horizon + policy, with child strategy IDs. Merge squeeze, flow and dark-pool evidence into one card when they support the same idea. A new evidence source is an update, not automatically another urgent trade. Opposing evidence triggers downgrade/invalidation, not contradictory unqualified emails.

Store per recipient: considered, opted-out, blocked, deferred, queued, sender-accepted, delivery-confirmed if provider supports it, failed, expired. Sender acceptance is not proof of inbox delivery. Claim durable idempotency keys around an outbox; retry failures without duplicate mail. Distinguish candidate counts from actual messages and from trades.

Alert content, in order: direction/strategy/horizon; three factors; biggest risk; exact legs and quote age; trigger/limit/invalidation/time stop; maximum loss and capital; eligibility and measurement status; deep link to evidence. Never use “high chance” unless an appropriately calibrated probability is present.

## Delivery phases and acceptance

Effort below is indicative engineering scope, not a delivery promise. Implement each phase with real route/store tests and a migration-upgrade test from an existing schema, not only a fresh database.

| Phase | Work | Acceptance / dependency |
|---|---|---|
| 0: correctness containment | O01 direction fit; O02 missing premium; O03 validity cohorts; O04 label/version legacy measurement; O05 quote/contract eligibility | Reproduction witnesses fail under old logic and pass after fixes; old outcomes retained; no live strategy-size claims from stale data |
| 1: shared inputs | One chain adapter, flow evidence types, source dates, completeness, calendar/deliverables, O10 missing-price fallback | Real provider-shape fixtures; truncated/empty/error distinct; route/source labels reconcile; provider chain-cap hypothesis resolved or visibly partial |
| 2: one opportunity flow | Registry, parent episode, frozen strategy alternatives, compact UI, portfolio gates | US symbol produces summary + blocked/watch/eligible state from same stored evidence; bearish fixture never receives bullish primary |
| 3: strategy shadow cohorts | Parallel research for every family; start full execution-quality capture where available | Prospective candidates persist before outcomes; contract-specific P&L; replay is labelled; no calibrated performance at inception |
| 4: opt-in alerts | Durable outbox, user style/risk settings, dedup/digest, trigger and invalidation updates | Opt-out/failure/duplicate/delayed/stale tests through real request and sender adapter; no default enrollment; account constraints enforced |
| 5: earned promotion | Per-strategy evaluation and broader coverage | Cost-sensitive held-out + prospective evidence, source health, budget and user suitability; strategies may remain watches independently |

Suggested practical sequencing within phase 3: price-confirmed debit spreads and covered-position hedges first, CC/CSP lifecycle next, intraday squeeze/0DTE when timestamped option data is ready, then calendars/diagonals/condors/straddles. **All families are in scope from discovery; sequencing reflects implementation dependencies, not a decision to ignore any style.**

### Test matrix that must exercise production code

- SELL/no holding; neutral/no thesis; rich IV/earnings uncertainty; insufficient cash; shares already pledged; position near concentration cap.
- UW archive with absent last trade; valid zero volume; partial expiries; response cap; market closed; stale quote; crossed quote; zero bid; adjusted deliverable.
- Expiry yesterday/today; last trading instant already passed; US holiday/early close/DST; event time unknown/future/delayed.
- Missing stock/option/benchmark mark; split in stock only, benchmark only, or adjusted option; dividend and early short assignment.
- Same provider event in three buckets; distinct findings from one source retained; opposing flow at a later time; multileg uncertainty.
- Multi-leg quotes at incompatible timestamps; crossed spread economics; unequal expiries; unknown multiplier; fees erase apparent payoff.
- No calibration record; invalid legacy rows excluded; old resolver retained; corrected resolver changes fingerprint; repeated resolver idempotent.
- Outbox duplicate/retry; preference lookup failure for new opt-in channel; user unsubscribes before send; accepted-versus-delivered distinction.
- Actual API→persistence→render flow; production migration on populated tables; builds outside Next.js page test paths.

## Operational checks and unresolved choices

Monitor source lag and coverage by symbol and strategy; candidate rejection counts by gate; quote-age and complete-chain distributions; same-day expiry and invalid identity rates; duplicate/delivery lag; option mark coverage; calibrated-versus-unmeasured cohorts; risk-budget violations. A job that returns zero candidates must distinguish empty universe, missing evidence, filtered candidates and errors.

Account size and existing positions are still needed for real quantities. Current provider intraday quote entitlement, quote sizes, corporate-action deliverables and full-chain completeness require verification. Start with existing subscriptions and caching; no new vendor purchase or quota expansion is authorized by this plan. Do not translate current heuristic scores into percentages to avoid that work.
