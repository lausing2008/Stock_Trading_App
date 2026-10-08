# US options opportunity playbooks

Date: 2026-10-08. **Proposed research and notification policies, not live trade recommendations.** All major strategy families requested by the user are covered. An unlimited set of strike/ratio combinations is not implicitly supported.

Read with the [audit](../audits/2026-10-08-options-squeezes-dark-pool-review.md) and [implementation plan](2026-10-08-options-opportunity-consolidation-plan.md). Current tooling supports only some construction/measurement paths; “documented” does not mean “implemented” or “profitable.”

## Start with the objective

| Your objective / evidence | First structures to compare | Avoid inferring |
|---|---|---|
| Bullish move within a defined horizon | Long call; bull call debit spread; bull put credit spread where eligible | Call-heavy volume guarantees buying or a breakout |
| Bearish move | Long put; bear put debit spread; bear call credit spread where eligible | A CSP is bearish because it uses a put |
| Own shares, want protection | Protective put; matched-expiry collar; appropriately sized put spread | A price stop guarantees a floor through a gap |
| Willing to sell owned shares at a chosen price | Covered call | Premium is free income or protects against a large stock decline |
| Willing and able to buy shares lower | Cash-secured put; later a managed wheel | High premium means attractive risk-adjusted return |
| Large move, direction uncertain | Long straddle/strangle, after checking priced move and IV sensitivity | A 2% stock move makes long volatility profitable |
| Range-bound and compensated for selling volatility | Defined-risk iron condor or iron butterfly | Price near a gamma wall guarantees a pin |
| Relative term-structure thesis | Calendar/diagonal | Different expiries share a simple vertical payoff diagram |
| Long-horizon bullish exposure | LEAPS call or LEAPS debit spread | Long expiry removes time decay, volatility or funding risk |
| Short/gamma squeeze evidence | Price-confirmed long call/debit spread or simply watch | A squeeze indicator establishes forced covering or dealer inventory |
| Large off-exchange prints only | Watch until independent price/thesis confirmation | Dark-pool print = institutional accumulation = buy calls |

No eligible strategy is an acceptable answer. All styles may generate research watches immediately after data normalization; each needs its own eligibility and validation before actionable notification.

## Provisional risk policy requested by the user

The user specified **US options, all styles**, and asked for an initial risk limit. Proposed settings, pending actual account information:

- **Per new speculative trade:** maximum economic loss, including modeled costs, no more than **0.25% of current account net liquidation value**.
- **Aggregate new options opportunity risk:** maximum economic losses summed conservatively no more than **1% of account equity**. Do not assume correlated technology/squeeze positions diversify one another. Account-wide existing risks must also be checked; this is not a replacement for portfolio limits.
- **Quantity:** floor of the smallest risk-budget, buying-power and exposure capacity divided by per-contract requirements. If one contract exceeds the limit, quantity is **zero**. Never round up to create an opportunity.
- **No naked calls, uncovered short puts, unbounded ratio spreads or borrowed financing** in the initial actionable channel. These remain educational/advanced-policy items.
- **CC/CSP risk:** reserve the shares/cash and count the substantial equity downside, not the premium or a stop-loss estimate. A $100-strike standard CSP receiving $2 still has approximately $9,800 downside if the stock goes to zero, before costs; it does not fit a $250 trade-loss budget. Do not call it safe just because assignment is affordable.
- **Hedges on existing holdings:** compare the existing portfolio with the hedged portfolio, check residual portfolio loss and fund the hedge debit. Do not double-count the same shares as two new positions, and do not silently exempt a new income position from the limit by calling it a hedge. A strict hedging objective may justify different approval rules, to be specified separately.
- **Unknown account value, holdings, permissions or cash:** research economics only; quantity and actionable suitability remain unknown.

These thresholds are design judgments chosen to limit early experimentation, not empirically optimized advice. They deliberately reject many one-contract income trades in smaller accounts. The answer is to abstain or investigate a properly eligible defined-risk alternative, not quietly raise the risk limit.

Example arithmetic only: with $100,000 equity, per-trade risk is $250 and combined risk $1,000. A standard 100/105 bull call spread costing $2/share with $3 total modeled round-trip fees has $203 maximum loss, $297 maximum expiry profit and approximately $102.03 break-even. One contract fits the per-trade ceiling; two do not. Actual quotes, fills, approvals and concentration still decide eligibility.

A planned stop is an instruction, not a guaranteed maximum loss. Gaps, halts, illiquidity and assignment can bypass it. Short American options can be assigned before expiry; a spread does not remove the short leg's obligation. [OCC/OIC assignment reference](https://www.optionseducation.org/referencelibrary/faq/options-assignment)

## Common checklist for every playbook

1. Name the direction **and** volatility view, horizon, objective, confirmation and strongest counterevidence.
2. Verify current source timestamps and complete price sessions. Separate dated SI, previous-session OI and live trades.
3. Verify actual contract identity, deliverable, multiplier, expiration/last trading instant and exercise/settlement rules.
4. Require compatible, fresh two-sided leg quotes for actionable pricing. Show spreads, delay and size limits; reject crossed, missing or unacceptable liquidity. Midpoints are estimates, not fills.
5. Check earnings, dividends, known binary events, macro events where relevant and explicit event-calendar coverage. Unknown calendar is not “no events.”
6. Confirm ownership/cash, approvals, risk budget and correlated exposure. Show maximum loss and capital separately.
7. Specify an acceptable entry limit, price invalidation, time stop, event exit and assignment/expiration management before notifying.
8. Freeze inputs, strategy alternatives, cost/fill assumptions and policy version. Record no-fill, expiry, invalidation and missing-outcome states.

Initial holding/DTE ranges below are research starting points for testing, not optimized parameters. A selected contract must live beyond the intended strategy horizon unless an explicit expiry-settlement policy is the thesis.

## Playbook 1 — Bullish or bearish directional swing

**Thesis:** an independently supported directional move over roughly 5–20 trading sessions. Start investigating 21–60 calendar-DTE contracts, adjusted for event timing and the actual horizon.

**Evidence:** completed price breakout/breakdown or retest, relative strength/weakness versus an appropriate benchmark, volume quality and a sourced catalyst if relevant. Options flow can corroborate only when event timing/side interpretation are explicit.

**Structures:** bullish long call or bull call debit spread; bearish long put or bear put debit spread. Choose an outright option only if its premium/IV sensitivity and necessary move are acceptable. A spread trades away some upside/downside participation for a lower debit; it is not automatically superior.

**Entry:** completed confirmation under a versioned rule, acceptable bid/ask and limit price, reward scenarios that still make sense after costs. Proposed scenarios cover flat, favorable, adverse and IV contraction paths; a favorable expiry payoff alone is not an expectancy estimate.

**Invalidation/exit:** underlying loses the confirmed level; catalyst reverses; target reached; predefined time expires without progress; event boundary or liquidity deterioration. Check option liquidation price, not just the stock stop.

**No trade:** missing direction, stock extended beyond the allowed entry distance, imminent event outside policy, premium already requires an unsupported move, one contract over budget or stale chain.

**Measure:** actual fixed-leg net P&L, benchmark-relative underlying move, theta/vega sensitivity and no-fill count. Existing matrix supports debit spreads/long calls; long-put recommendation and direction-compatibility need explicit work. O01 must be fixed before promotion.

## Playbook 2 — Credit verticals for a directional or boundary thesis

**Structures:** bull put credit spread for bullish/above-support; bear call credit spread for bearish/below-resistance. Same-expiry legs and parsed deliverables. Begin studying roughly 21–45 DTE; test rather than assume that interval has edge.

**Evidence:** a defendable price boundary plus compensation for volatility/event risk. IV rank alone does not establish expensive options: compare implied versus a predeclared realized-volatility/scenario forecast and skew.

**Economics:** standard vertical expiry maximum profit = credit × multiplier minus costs; maximum loss = (width − credit) × multiplier plus costs. Quote mismatches can manufacture attractive credits; gate both legs and timestamps.

**Entry/exit:** place a defined limit only after thesis confirmation. Close or reduce under the predeclared boundary/time/event rule; reserve operational capacity for short assignment. Do not keep widening or rolling merely to avoid recognizing a loss. Each roll realizes the old trade and starts a new risk decision.

**No trade:** event uncertainty, inadequate credit after costs, broad spreads, unstable short-strike boundary, unknown settlement or short-leg coverage. Treat early assignment/pin risk separately from terminal payoff.

**Status:** proposed family, not implemented by the current debit-spread matrix. Requires credit-spread construction, lifecycle and tests before alerts.

## Playbook 3 — Short-squeeze continuation

**Thesis:** a confirmed upward price move might continue while short positioning contributes potential demand. High reported short interest is background susceptibility, not proof shorts are buying now. Short-sale volume is not short interest. [FINRA](https://syndication.finra.org/content/short-interest-what-it-what-it-not)

**Evidence:** dated SI/float and days-to-cover, timestamped borrow observations where available, price/relative-volume confirmation, float and liquidity quality, catalyst and current extension. SI, borrow fees and availability have different time scales and cannot be presented as a single current fact.

**Structures:** small defined-risk long call or bull call spread where quote quality permits; compare to no trade. No automatic naked short calls to fade a squeeze. Avoid far-OTM “cheap” lottery contracts selected solely by low dollar premium.

**Entry:** observe a predeclared breakout/retest after sufficient opening price formation; state whether the observation is intraday or completed-session. Fresh **intraday option** quotes are mandatory for an intraday alert; M5 stock bars plus an EOD chain are inadequate.

**Invalidation/exit:** loss of breakout base, collapse in confirming volume/participation, event reversal, time stop or exit before the planned overnight exposure. Halts and gap risk are modeled as limitations, not repaired by a tight stop.

**No trade:** extended move, unreliable float/SI date, untradeable spread, halt, absent borrow coverage portrayed as confirmation, or unvalidated urgency. Current short-squeeze cohort has 16 resolved legacy 5d observations and a 12.5% stock hurdle rate; that does not justify a profitable-squeeze badge.

**Measure:** distinct squeeze episodes, first actionable alert timing, MFE/MAE, option entry/exit quotes, halt/no-fill outcomes, net loss tails, and price-only control. Do not count every contract/day as an independent squeeze.

## Playbook 4 — Gamma/expiry acceleration or stabilization

**Thesis A:** under a stated dealer-position model, hedging might amplify a confirmed move. **Thesis B:** a different inventory state may dampen movement. The same gross OI cannot prove either without a sign assumption.

**Evidence:** expiry-specific OI and Greeks, source date, modeled exposure, price relative to relevant levels, independent price/volume confirmation and model sensitivity. Max pain and gamma walls are context, not destinations or guaranteed barriers. Provider-calculated GEX must carry its assumptions; the free OI concentration path remains a proxy.

**Structures:** directionally compatible debit spread/long option after confirmation for acceleration. A condor/pin strategy is a separate volatility thesis with separate requirements; never infer “sell premium” only from a positive-GEX label.

**Entry/exit:** freeze the expiry aggregation and confirmation rule; invalidate when price behavior contradicts the thesis, the level/model changes materially, or the relevant expiry passes. Near-expiry behavior requires intraday monitoring and quote data.

**No trade:** stale OI/Greeks, unknown dealer model, unverified ordering of per-expiry rows, weak liquidity, or no independent directional trigger. Do not claim real-time dealer holdings from a provider estimate.

**Measure:** OI-only versus GEX-enriched versus price-only strategies by DTE and regime. Cboe's capacity-aware analyses illustrate why gross volume and net dealer gamma are distinct. [Cboe](https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact)

## Playbook 5 — Options-flow corroborated opportunity

**Thesis:** a timely and interpretable trade cluster supplies incremental evidence, not a command to copy another trader.

**Evidence:** event age, contract, strike/expiry, premium, bid/ask classification, sweep/multileg flags, volume/OI and next available OI change. OI changes arrive later and cannot retroactively become a known-at-alert input. Ask-side call activity may be a close or hedge; directional interpretation stays qualified.

**Structures:** choose fresh contracts suited to *our* horizon and budget; do not blindly copy the printed contract. If the event's contract is expired, it is historical context only. A 0DTE sweep cannot be scored as a next-day option entry.

**Entry/exit:** independent price confirmation, recent usable quotes, limit price and normal invalidation/time rules. Opposing later flow revises the evidence; it does not create simultaneous unqualified buy and sell advice.

**No trade:** unknown/old event time, balanced or missing side data, suspected package whose complete legs are unknown, extreme extension, thin chain or already-expired contract.

**Measure:** strategy P&L separately from underlying directional hit rate; compare to price-only and matched nonalert events. Quarantine O03 legacy invalid records before any calibration claim.

## Playbook 6 — Dark-pool/off-exchange activity

**Thesis:** unusual off-exchange activity may identify a price area or event worth watching. It does not reveal the portfolio intent of the counterparties.

**Evidence:** execution and report timestamps, corrections/cancellations and trade conditions where available, venue classification, clustered notional relative to that symbol's prior distribution and ordinary dollar volume, and subsequent price response. Delayed print time is not a real-time support test. Off-exchange trading includes more than dark ATS venues. [FINRA](https://syndication.finra.org/content/where-do-stocks-trade)

**Structures:** none from the print alone. After an independent directional trigger, use the directional playbook. If investigating long volatility, compare the actual option premium and horizon to a separate volatility forecast.

**Invalidation:** price fails to hold/reclaim the proposed area; print corrected; supposed cluster proves duplicated or routine; trigger expires. A print's reported price is not automatically support/resistance.

**No trade:** only large notional, missing timestamps/conditions, no incremental information beyond normal activity, or no price confirmation.

**Measure:** conditional option strategy outcomes and alert-load reduction, not “stock moved 2% either way.” The current 64.62% absolute-move statistic cannot determine whether a straddle pays.

## Playbook 7 — Covered call

**Objective:** earn premium while deliberately accepting sale of already owned, unencumbered shares at the selected strike. A synthetic buy-write is a new equity-risk trade and must be labelled separately.

**Evidence:** ownership/cost basis for context, willingness to sell, issuer/event outlook, dividend calendar, quote liquidity and acceptable capped upside. Initial research window roughly 21–45 DTE; compare expiries rather than maximizing annualized premium.

**Entry:** covered share count after existing calls, valid multiplier/deliverable, chosen sale price and conservative premium estimate. Reject overselling, even when another page shows the same shares available.

**Exit/management:** accept assignment only if planned; close/roll before a relevant dividend or event when warranted by the policy. A roll is two transactions with realized P&L and new exposure, not free repair. Track stock and call together.

**Risk:** most stock downside remains. Premium yield is not total return. Early assignment can forfeit the dividend or planned holding period; tax effects depend on the account and are outside this simulator.

**Measure:** stock+option+dividend net P&L versus owning the stock, drawdown, forgone upside, fees and assignment frequency. Existing income paper engine closes at expiry and liquidates residual stock; that is not the same as holding a long-term covered-call portfolio.

## Playbook 8 — Cash-secured put and wheel

**Objective:** commit cash to purchase a stock at a chosen effective price, with genuine willingness to own it through a decline. A bearish speculation objective is incompatible.

**Entry:** verified cash reservation, stock suitability, acceptable assignment price, event-calendar coverage, positive liquidity and thesis-aligned downside scenarios. Begin examining 21–45 DTE. Delta is a sensitivity/model input, not a guarantee of assignment probability or profit.

**Exit/management:** close before invalidation or accept planned assignment; do not mechanically sell another put when the business thesis breaks. The **wheel** continues from assigned shares to covered calls only after reassessing ownership and sale willingness. Preserve one continuous stock/option cash ledger through assignments and rolls.

**Risk:** cash-secured does not mean limited to the premium; stock can approach zero. A subsequent covered call cannot erase the economic loss in assigned shares. The provisional risk limit may rule out most standard single-stock CSPs.

**Measure:** total cycle P&L and time/capital employed, versus cash and a stated alternative stock-entry policy. Existing CSP paper engine liquidates assigned stock at expiry; it is **not an implemented wheel**.

## Playbook 9 — Protective put, put-spread hedge and collar

**Objective:** reduce risk in an existing holding, not maximize standalone option hit rate.

**Structures:** protective put for a floor over its life; put spread for a limited band of protection; matched-expiry collar when selling upside is acceptable. The short put in a put-spread hedge means protection stops increasing below that strike; disclose residual tail loss.

**Entry:** actual position size and risk horizon, coverage ratio, strike floor/cap, quote quality and premium budget. Do not describe a staggered-expiry collar using a single fixed terminal payoff.

**Exit:** hedge horizon ends, exposure is sold, thesis changes or protection is rolled under a new decision. Monitor early assignment of a collar's short call; synchronize changes in the stock position with option coverage.

**Measure:** hedged versus unhedged portfolio drawdown, cost of insurance and forgone upside. A put expiring worthless can be a successful insurance purchase; do not grade it only on standalone win rate.

## Playbook 10 — Long straddle / strangle and event volatility

**Thesis:** future movement/volatility is underpriced relative to a forecast made before the trade. Direction may be unknown, but the volatility thesis must be explicit.

**Structures:** same-expiry long ATM call+put (straddle), or OTM call+put (strangle). A strangle reduces debit but requires more movement at expiry. Long options have bounded premium risk, but both can decay simultaneously.

**Entry:** event timing, implied move and term structure/skew, liquid synchronized legs, predeclared IV-crush scenarios. Current expected-move helpers alone do not establish value.

**Exit:** specified before/after-event or time-based policy; model immediate post-event option marks and IV change, not just terminal stock movement. Intraday stop/target requires intraday option data.

**No trade:** thesis is merely “something will happen,” expensive implied move unsupported by forecast, no same-contract outcome coverage or no liquidity.

**Measure:** two-leg P&L after spreads/costs versus a defined baseline; separately attribute movement, volatility change and time where the model permits. No automatic earnings straddles without a validated event-specific policy.

## Playbook 11 — Iron condor, iron butterfly and defined-risk butterflies

**Thesis:** a bounded distribution/range is adequately compensated by option prices. This is a short-volatility/range thesis, not simply lack of directional evidence.

**Structures:** iron condor = put credit spread plus call credit spread; iron butterfly concentrates short strikes near the center with wings; debit butterflies have their own bounded expiry payoff. Validate strike order, ratios, matching expiries and positive economics after four-leg costs.

**Entry:** stable regime hypothesis, explicit range/vol forecast, liquid wings, absence or deliberate modeling of binary events, sufficient credit/payoff after realistic fills. Begin evaluating 21–45 DTE structures, not assuming a universal window.

**Exit:** range invalidates, volatility thesis fails, predefined time/expiration boundary or target reached. Do not double risk by adding size to defend a tested wing. Multi-leg terminal bounds do not remove assignment and pin risk.

**No trade:** “inside range” is the only evidence, costs consume edge, legs are stale or crisis/event tail risk is unmodeled.

**Measure:** net P&L, drawdown/tail losses and dependency of both sides, not sum of independently assumed spread win rates. Current matrix lacks these builders and lifecycle paths.

## Playbook 12 — Calendars, diagonals and covered LEAPS combinations

**Thesis:** relative volatility/time structure and price location across expiries create an opportunity. Long back-month option plus short front-month option is not equivalent to a same-expiry vertical.

**Entry:** both expiry surfaces, dividends/rates, Greeks, scenario values at the short expiry, assignment capacity and after-expiry plan. Calendar value depends on the remaining long option's IV; simple terminal width-minus-debit arithmetic is invalid.

**Management:** short-leg assignment can leave stock exposure; closing/rolling the short must be modeled alongside the long. A so-called poor man's covered call is a diagonal, not literal share coverage. It needs broker approval and a complete early-assignment policy.

**No trade:** no term-structure evidence, ratio/deliverable mismatch, unmodeled short-expiry event or no capital for an assignment scenario.

**Measure:** the complete sequence of option/stock cash flows and remaining marks, including every roll's spread/fee. Advanced research until the multiexpiry engine and risk policy exist.

## Playbook 13 — LEAPS directional exposure

**Thesis:** durable directional exposure over months, with a defined premium budget and known loss if wrong. Compare long call with a long-dated debit spread and outright shares.

**Entry:** long-horizon business/valuation case plus risk review, liquid contract, acceptable delta/IV/expiration, concentrated-exposure check. A near-term squeeze is not automatically a LEAPS thesis.

**Management:** periodic scheduled review, thesis invalidation, IV/Greek and liquidity changes, predefined roll policy. Preserve the actual contract identity through the study. Rolls pay another spread and realize the previous position.

**Risk:** total premium loss, time decay, volatility repricing, gaps, contract adjustments and opportunity cost. Leverage is explicit; low cash outlay is not low economic exposure.

**Measure:** bid/ask-aware contract P&L versus stock exposure and a budget-matched alternative, with dividend differences disclosed. Existing LEAPS replay is a useful foundation, but its selected capture universe and execution assumptions limit conclusions.

## Playbook 14 — Advanced/unbounded structures and 0DTE

**0DTE is a horizon, not a strategy.** It can use debit spreads, long options or defined-risk credit spreads, but requires its own market-time/quote/fill/expiry policy and prospective dataset. Keep it in research until intraday option quotes, monitoring and last-trade/settlement rules are verified. Do not use next-day closes to assess it.

**Naked short calls/puts, short straddles/strangles, uncovered ratios and covered puts backed by short stock:** document mechanics, but initial actionable eligibility is false. Unknown account capacity and potentially very large/unbounded loss are incompatible with the requested provisional risk policy. A fully protected backspread may later be considered only after exact payoff, ratios, assignment and cash stress are modeled.

**Boxes, conversions/reversals, synthetic stock and financing/arbitrage claims:** advanced study only. Borrow, rates, exercise style, dividends, capital, execution synchronization and settlement dominate small apparent edges. No “risk-free return” label from a calculator.

All of these can appear in learning material without becoming suggested trades. Later activation requires a separate tested policy and account permission, not just another dropdown option.

## Exit policy template

Every eligible trade must fill these fields before alerting:

| Field | Required content |
|---|---|
| Entry | Confirmation condition, valid time window, maximum debit/minimum credit and quote-age bound |
| Invalidation | Underlying/volatility/evidence condition; handling of gaps/halts |
| Economic risk | Full modeled maximum loss, capital reserved, residual equity risk and assignment exposure |
| Profit management | Predeclared target or staged exit rule; no retrospectively optimal target |
| Time stop | Exact session/time or remaining-DTE boundary |
| Event policy | Earnings/dividend/macro treatment and uncertainty |
| Expiration | Planned close/settlement, broker cutoff assumptions, pin/assignment handling |
| Rolling | Explicitly close old trade and evaluate new one with updated budget |
| Missing data | Block entry; label existing-position valuation uncertainty; escalate monitoring fault |

Avoid universal “take profit at 50%, stop at 2× credit” rules until tested for that strategy/universe/execution policy. Such rules can be candidates in a versioned experiment, not hidden truths.

## Notification playbooks

### Research watch

> **Bullish squeeze watch — confirmation pending**  
> Price/volume setup present; dated short interest adds context.  
> Main risk: move already extended and IV elevated.  
> Watching: break/retest of the specified level within the stated session window.  
> Strategy alternatives: call/debit spread; contracts unpriced until fresh quotes.  
> No position quantity; no option-profit probability available.

### Eligible strategy candidate

> **Confirmed bullish setup — defined-risk spread candidate**  
> Exact contract IDs, expiry, buy/sell legs, quote timestamp and source.  
> Maximum debit, maximum loss/capital, proposed quantity under configured account limits.  
> Three factors and strongest opposing evidence.  
> Invalidate below the named level; time/event exit specified.  
> Measurement label: prospective strategy cohort, sample/uncertainty, or “unmeasured.”

This template is not a live recommendation. Production cannot fill its actionable fields from an EOD chain alone.

### Update / invalidation

> **Prior opportunity invalidated** — confirmation level failed / quote became stale / event risk changed.  
> The prior alert is retained, with updated status and time.  
> If the user recorded a position, show its relevant risk/exit policy; do not assume the alert was traded.

### Daily digest

Group by directional, squeeze/gamma, income, hedge and volatility. Show only changed/high-priority eligible ideas plus research watches the user selected. Include “no eligible opportunities” and top blockers when appropriate. Avoid five emails for the same stock because five modules observed the same event.

## What must be learned before claiming this earns money

The current stock-alert cohorts, four closed income paper trades and LEAPS studies do not establish a profitable universal options system. Profitability must be measured per strategy with exact legs, fees/spreads, mark coverage, assignment, benchmark and unresolved counts. Every playbook can lose. The purpose of this plan is disciplined selection and measurable improvement, with useful abstention when evidence or trade economics are inadequate.

Strategy-mechanics reference: [OCC/OIC options strategy guide](https://www.optionseducation.org/getmedia/68305977-b772-41c8-bf1d-3d405725b3cf/options-strategies-quick-guide-2025.pdf). The proposed entry windows, risk percentages and notification policies are project design judgments, not claims made by that guide.
