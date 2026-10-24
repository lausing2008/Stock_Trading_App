# Holding-compatible covered-call expiration search

Date: October 23, 2026 (America/Los_Angeles). Implementation continuation after the horizon/context release. Deployment and final validation are recorded below when completed.

## Behavior and limits

The original covered call is still anchored to the stock target (or spot) and its original expiration. The Options page now offers **Find expirations for holding period**. This explicit action requests a bounded archive search; editing the holding-period field makes no request.

The advanced-tier game-plan route accepts optional `holding_days` (integer 1–365). With it, the search reads the first four distinct expirations at least `holding_days + 2` calendar days from the current New York date. Two days is the existing research buffer, not an optimized trading setting. All extra chain reads pin the same archive session; a returned session mismatch is a failed read. Up to four extra archive/database reads occur per search, in addition to the normal two plan reads. No new options-provider HTTP calls are introduced.

The existing supported-contract, positive two-sided/noncrossed quote, exact 20% spread and payoff-consistency screens apply. Up to three candidates per expiration are retained; the original contract is excluded from alternatives. Ordering is earliest eligible expiry first, then distance to the unchanged anchor, exact spread, measured OI, strike and contract identity. The nearest strike in a different expiration remains eligible. This is a transparent comparison order, not optimization of delta, gamma, win rate or return.

The response preserves original singles, combinations, target and recommendation. Its versioned candidate review adds requested holding period, buffer, total eligible expirations, attempted/completed/failed expirations and screened/rejected counts. Zero eligible expirations, a successfully read empty chain and read failures remain distinguishable. Counts describe the archived data supplied to the screen, not verified full market coverage. A failed expiration does not cause a fifth read; search cost remains bounded, and partial coverage is visible.

The browser accepts supplementary candidates only when symbol, archive session, reference spot, method version and requested holding period match. Changed context or request failure leaves the original comparison available with a message. Editing the holding period, changing horizon, receiving a replacement matrix or unmounting invalidates pending results. A comparison requires explicit selection; selection and each new search clear assignment acceptance. The chosen leg continues through the same freshness, holding/expiry, holdings, budget and quote checks. Entry status remains WAIT for fresh quotes and broker checks.

## Remaining improvements

- Extend the bounded expiration search to long calls, protective puts and cash-secured puts; each needs its own objective and payoff screens.
- Add independently dated spot/chain price-basis checks and current quotes, quote sizes and event dates before any executable comparison.
- Add documented delta/DTE preference controls and measure their coverage before asserting optimal settings.
- Validate costs, fills, early assignment and portfolio risk in strategy replay before highlighting expected-return winners. Existing context evidence and goal-fit highlighting do not supply that validation.

## Validation and deployment

Frontend validation: 869 tests passed across 72 files; full typecheck and whitespace check passed. Targeted backend checks: 52 passed. Full backend validation and authorized deployment remain pending. Two initial frontend test runs used unsupported assertion helpers or an ambiguous status query; those test-harness errors were corrected before the successful full run. Typecheck also caught a nullable selection access, now explicitly guarded. Regression coverage exercises bounded reads, holding-buffer boundaries, nearest-strike inclusion in other expirations, source-session pinning/mismatch, partial failures, no coverage, explicit selection, context changes, changed spot/session and failed requests.
