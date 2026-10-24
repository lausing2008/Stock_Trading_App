# Strategy comparison: selection review and next implementation

**Production update:** covered-call candidate comparison is deployed in `e08dd338` after explicit public-push authorization. The [deployment report](2026-10-23-covered-call-candidates-deployment.md) records TSM results, source/build verification and remaining market-aware ranking work. Local-only descriptions below are preserved preparation history.

Reviewed October 23, 2026 against the release working tree. This is a code review and implementation specification, not a calibrated trading recommendation. The screenshot alone cannot identify its exact rejected contract or prove that the full chain had no usable covered call.

## What the screen currently does

`routes.py` chooses call expirations nearest the middle of a 14–45 calendar-day window and put expirations nearest the middle of 25–60 days. If none lie inside a window, it uses the closest listed expiry to the midpoint. These are research defaults, not optimized holding periods.

`options_strategies.py:build_strategy_matrix` selects a long call nearest spot; a covered call nearest the stock target (ATM only when there is no target); a protective put nearest the stock stop (otherwise ATM); and a cash-secured put using the selected put chain. The nearest strike is chosen before price/contract validation. `_leg` then rejects unpriced, crossed, nonfinite, unsupported or invalid-expiry selections. It does not exhaustively search alternative strikes or expirations. Therefore a covered call can be absent while a differently selected long call prices successfully. Missing share declarations are a separate preference blocker; they do not cause this matrix pricing omission.

Delta, gamma and OI do not jointly rank these contracts. Breakeven is calculated after selection and does not optimize the choice. The stock target can have a longer horizon than the option. Settled prices are research references, and a midpoint is not an executable fill. The route can combine a current underlying reference with an older settled option quote; an executable ranking must use synchronized quote/spot observations and disclose its valuation time.

`commonOptionPlans.ts` already highlights a **conditional preference** after objective, holdings, cash, premium budget, assignment acceptance, usable spread and snapshot-age checks. Income tries covered call then CSP; protection tries protective put; upside ordinarily tries long call then CSP, reversing that order when valid IV rank is at least 70. The 20% spread ceiling, seven-calendar-day snapshot allowance and IV-rank threshold are unvalidated policies. No current regime/trend/company forecast is fused into this frontend preference. Archived quotes never authorize an entry: the banner remains WAIT for fresh quotes and broker checks.

The release adds visible text disclosing selection heuristics and the absence of exhaustive alternative search. It does not change strike selection or trading thresholds.

## TSM screenshot follow-up

The user identified the screenshot as **TSM**. A read-only, repeatable-read production reproduction at `2026-10-24T01:27:35Z` used its displayed stop **404.07** and target **584.39**. It read the **October 22** stored chain; this reproduces the missing card with current stored evidence, rather than proving the screenshot used that exact snapshot.

The selected call expiry was **November 20, 2026**; put expiry **December 4**. With underlying reference **418.51**, the nearest call strike to 584.39 was **580**, contract `TSM261120C00580000`. Its bid was **0**, ask **0.09**, and last-price field **0**: no usable two-sided quote or last-price fallback. Thus both the legacy covered-call leg and matrix covered call were absent. Root-based standard-contract classification passed, so this witness is a quote/selection failure, not a contract-identity refusal or missing share declaration.

There were **76 call rows**, of which **64** passed basic supported-contract/price checks. This does not establish they pass spread, freshness, liquidity, assignment or portfolio eligibility, nor that any is the best choice. The reproduction confirms why choosing the distant stock-target strike first can suppress this card while other strikes price. It supports the candidate diagnostics and comparable-alternative search proposed below. No production code, database rows or settings changed. The direct strategy API rejected the service credential with HTTP 401; no user authentication was fabricated. The reproduction used internal calculation helpers over an explicitly read-only, time-limited database transaction and rolled back.

[TSM reproduction evidence](evidence/2026-10-23-followup-deployment/tsm-selection.json).

## Proposed next implementation, in order

1. **Candidate diagnostics first.** Return selected contract identity, target anchor, expiry selection reason, quote timestamp/source and machine-readable rejection reasons for each strategy. Distinguish no chain, no candidate, invalid quote, unsupported terms and valid contract blocked by holdings. Render counts of evaluated and rejected candidates. Regression: invalid nearest strike with valid neighbour must not become a claim that the entire chain is unpriceable.
2. **Enumerate before ranking.** Search all eligible strikes across disclosed expiry windows, retain exact contract identity and deliverables, and apply quote age, two-sided spread, executable-size, volume/OI completeness, event-date and assignment checks. Missing Greeks or OI must stay unknown; OI alone is not evidence of a fill. Return a deterministic shortlist with exclusions rather than silently changing the target to manufacture a candidate.
3. **Separate strategy fit from contract fit.** Establish objective and capital/holding constraints first. Then compare delta exposure, gamma concentration, theta/day, vega, spread dollars/percent, expiry versus thesis duration, net debit/credit, breakeven, maximum loss, capped upside, collateral and stress loss. Show the trade-offs; do not maximize delta or minimize breakeven as an isolated objective. For owned shares show stock cost basis separately from current-reference economics.
4. **Timestamped market context.** Attach market/sector regime, trend over the same horizon, company outlook and event calendar, prediction horizon, model version and out-of-sample uncertainty. Reject future or stale context. Conflicting evidence should show mixed/no preference. A bullish forecast does not make selling upside appropriate for every holder. Earnings/ex-dividend data missing is an explicit unresolved input.
5. **Conditional highlight with explanations.** Show “Fits your objective” and a ranked alternative only when eligible, with strengths, risks, missing evidence and the exact selection method. Preserve a visible no-preference state. Market-policy examples to test: protection under elevated downside risk; uncapped bullish exposure when the move/volatility scenario supports its cost; covered-call income when the holder accepts capped upside; CSP only when willing and funded to own shares. These are hypotheses, not fixed profitable rules.
6. **Validate before an expected-return ranking.** Freeze candidate snapshots and forecasts; compare every eligible strategy including wait, underlying-only and simple baselines using executable bid/ask, fees, slippage, dividends, early assignment and expiry settlement. Use chronological held-out folds, horizon-aware embargo, regime/sector breakdowns and a prospective shadow period. Report net expectancy, drawdown, tail loss, capital utilization, turnover, sample size and confidence intervals as well as win rate. Optimize no parameters on the final test set. Keep automatic calibration promotion blocked until this evidence exists.

No universal best delta, gamma, strike, expiry or strategy is established by this review. The remaining optimizer and market-aware ranking are planned work; this release does not claim to implement them.

## Financial reference

OIC describes the covered call as stock plus a written call: income is received in exchange for capped upside while substantial stock downside remains. [OIC covered call](https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write). OIC explains that gamma changes delta and is more concentrated in near-term ATM options: [OIC gamma](https://prd-web.optionseducation.org/advancedconcepts/gamma). Those mechanics motivate explicit risk comparisons; they do not establish a profitable ranking.


## Follow-up implementation: explicit covered-call comparison

Implemented locally after the TSM clarification; not deployed. The existing anchor contract is preserved. The backend now inspects the already-fetched call rows for the selected expiration, explains the original selection failure, counts screening rejections, and returns up to three distinct alternatives. The route keeps a response available when alternatives are its only usable research content.

Screening requires supported contract terms, future expiry, finite positive non-crossed two-sided quotes, an exact spread no greater than the existing 20% research policy, and consistent covered-call payoff with positive maximum profit. Last-trade-only quotes do not qualify. Decimal comparison prevents binary floating-point error at exactly 20%. Missing/invalid Greeks stay unknown. The rows supplied to the matrix may already exclude unsupported terms; the UI discloses that its counts are after upstream exclusions. No additional chain reads are made and no other expiry is searched.

Order: distance to the original target (or spot), unrounded spread, measured OI, strike and contract identity. This deterministic proximity policy does not optimize returns. OI only breaks otherwise-equal ties; it neither establishes executability nor supplies a verified reporting date. Alternatives use the original covered-call payoff arithmetic. Duplicate contract identities are not listed twice.

The browser preserves the original card by default and exposes a contract-comparison selector. Choosing an alternative changes only the displayed covered-call comparison; the stock target, original combinations and backend recommendation are unchanged. Its different upside cap and the unchanged target are explicit. Assignment acceptance is cleared and must be given for the newly displayed contract. Existing share, objective, budget, expiry and snapshot-age checks still govern the conditional preference; entry remains WAIT. A fresh matrix discards the old alternative selection.

OI, delta, gamma, theta, vega and spread now appear on strategy cards when supplied. These are provider snapshot option-contract estimates, not combined strategy Greeks or probabilities of profit. The route adapter now preserves the Greek fields it previously dropped; no Greeks are estimated or backfilled from another contract.

This completes the first bounded improvement: explaining a failed target candidate and exposing explicit alternatives from one expiry. Multi-expiry optimization, measured market/context integration and expected-return ranking remain planned and require the validation described above. The previous TSM evidence remains local because public publication of those detailed quotes was rejected; this implementation performs no production mutation or external publication.


Validation follow-up: the first full backend run found one harness-only failure: dotted monkeypatch lookup traversed a stubbed root package lacking the expected attribute. The fixture now patches the imported module object directly, preserving the real handler/composition assertions. The subsequent full backend suite passed **5,799 tests, 26 skipped** (379.55 seconds). The UI also honors the original candidate's backend screening failure even when its displayed rounded spread would appear acceptable; explicitly chosen alternatives are assessed separately from that original rejection.


Final follow-up verification: **5,799 backend tests passed, 26 skipped**; **834 frontend tests passed across 71 files**; full frontend typecheck and whitespace checks passed. Both isolated guard-removal witnesses still detect regression after the harness repair. The tracker marks the broader candidate/context improvement in progress, with this covered-call comparison implementation local and tested. No interactive browser acceptance, production build, public push or deployment was performed in this follow-up. [Validation evidence](evidence/2026-10-23-covered-call-candidates/).
