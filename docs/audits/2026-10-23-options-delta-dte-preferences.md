# Explicit option delta and expiration preferences

Date: October 23, 2026 (America/Los_Angeles). Continuation after [quote/event evidence](2026-10-23-options-quote-event-evidence.md). Final validation and deployment are recorded below when complete.

## Behavior and policy

The contract-selection panel now offers a shared minimum/maximum days-to-expiry band and minimum/maximum absolute contract delta. All four fields start blank and are local to the page. No horizon or strategy gets an unvalidated optimized default. Clear preferences restores the blank behavior.

DTE inputs are optional whole calendar days from 1 through 1095. This bounds the user-control input, not the market's supported contract terms; blank bounds add no DTE restriction. Effective minimum life is still at least declared holding days plus two calendar days. DTE is recomputed from the current New York date rather than trusting cached `days_to_expiry`. Delta bounds accept 0 through 1 inclusively; zero is a real bound and measured zero is not treated as missing. Delta is the provider's contract estimate: calls should be nonnegative and puts nonpositive before applying buy/sell position signs. Absolute delta does not measure a covered position's combined exposure or establish profit probability. This sign convention follows [OIC delta guidance](https://prd-web.optionseducation.org/advancedconcepts/delta); the source does not establish optimal ranges or validate these trades.

When a delta range is active, missing/nonfinite/out-of-range/non-numeric delta values and inconsistent call/put signs are refused with separate reasons. Blank delta bounds preserve the previous unknown-Greek behavior. No zero is substituted for an unknown delta.

## Screening order and bounded scope

The existing advanced-tier game-plan route accepts `min_dte`, `max_dte`, `min_abs_delta`, `max_abs_delta` alongside `holding_days`. Invalid or inverted ranges return 422 before archive reads. Preferences without a holding-period search are rejected. Existing source-session, supported-contract, quote, exact spread and payoff screens remain in force.

DTE filters run **before selecting the earliest four eligible expirations**. Delta filters run on the full supplied expiry rows **before the three-alternative cut**. The original contract is also excluded before that cut, fixing the earlier case where it consumed a slot and left only two alternatives. Counts still describe screened archive rows, which may include the otherwise-valid original; returned alternatives exclude it. The original singles, combinations, stock target and stop are unchanged.

Each attempted expiration is still read at most once and shared across all four strategies; failed reads are cached. Maximum extra archive/database reads remains four, beyond the ordinary two initial plan reads. No provider HTTP calls are introduced. A failure does not cause a fifth expiration read, and a zero-result bounded search does not prove absence elsewhere.

The response echoes all four normalized preferences, the number of expirations eligible for the holding period before the DTE filter, the number after it, read coverage and row rejection counts. The browser requires acknowledgement of active preferences in each returned review, in addition to the existing symbol/session/spot/holding/search-date checks. Blank settings stay compatible with older responses.

## Comparison and eligibility

Preferences are shared across all four strategies to preserve a single four-expiry search budget. Per-strategy bands are future work requiring an explicit combined coverage/cost policy. Original or explicitly chosen contracts outside the active preferences cannot receive the page's goal-fit highlight. Invalid input also blocks goal fit and disables search. This is a page research control; backend AI scores, order execution, account settings and original plan construction do not change.

Editing preferences invalidates pending/displayed supplementary results, clears explicit comparisons and assignment acceptance, and makes no request. A user must press the existing search button again. Signed provider delta appears in the alternative labels. No replacement is selected automatically. Calendar, holdings, cash, budget, source-age, quote and holding-period checks still apply; entry remains WAIT for fresh quotes and broker checks.

## Validation and deployment

Targeted backend: 118 passed. Full frontend: **928 passed across 74 files**, full typecheck and whitespace clean. Offline equivalence witness: 200 complete original matrices remain identical. Full backend suite and production rollout are pending. Tests cover boundaries, zero, missing/wrong-sign delta, inverted ranges, before-cut matching, before-cap expiry filtering, original exclusion, shared real-handler read counts, acknowledgement, goal-fit gating, clear behavior and stale in-flight responses.

## Remaining work

- Independently dated quote/spot observations, quote size, price-basis checks and issuer-confirmed event coverage.
- Per-strategy/horizon preference proposals and measured archive coverage, costs, fills and assignment outcomes before selecting optimal settings.
- Validated option-return forecasts before a market-aware expected-return winner.
- Dark-pool correction identity and feed-gap completeness remain independent open work.

[Evidence and final verification](evidence/2026-10-23-options-delta-dte-preferences/README.md).
