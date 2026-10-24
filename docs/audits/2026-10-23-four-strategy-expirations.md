# Four-strategy holding-compatible expiration comparisons

Date: October 23, 2026 (America/Los_Angeles). Continuation of the [covered-call expiration search](2026-10-23-options-multi-expiry.md). Final validation and deployment are recorded below after completion.

## Changes

The explicit **Find expirations for holding period** action now searches long calls, covered calls, protective puts and cash-secured puts together. A strategy selector controls which candidate list is displayed. Selecting a candidate changes only that single-strategy comparison; a visible summary lists every active choice across strategy switches. Original stock stop/target and all combination legs remain unchanged. Any selection or new search clears assignment acceptance. Existing holdings, cash, premium budget, quote, snapshot-age and holding-period checks still apply before a goal-fit highlight; entry remains WAIT for fresh quotes and broker checks.

All four searches use the first four distinct archived expirations with at least declared holding days plus two calendar days remaining. Each expiration is read at most once per request, shared across calls/puts and all four strategies. Failed reads are also cached, so they cannot multiply requests. The maximum remains **four extra archive/database reads**, beyond the ordinary two plan reads. No new provider HTTP requests are added. A side that cannot be parsed can fail independently while the other side remains usable; whole-expiry failures appear in every affected strategy's coverage. All reads pin one source session and mismatched returned sessions are refused.

Within each expiration, the existing quote/terms screen applies: supported standard-contract classifier, finite positive noncrossed bid/ask, exact spread at most 20%, valid future expiration and valid strike. This is the existing research policy, not an optimal liquidity threshold. Ranking remains expiration first, then distance to the original anchor, spread, measured OI, strike and identity; up to three candidates per expiration, excluding the original contract. A different expiration can use the same strike.

| Strategy | Anchor | Leg | Additional payoff screen | Capital shown |
| --- | --- | --- | --- | --- |
| Long call | Reference spot | Buy call | Premium cannot exceed reference spot | Premium debit |
| Covered call | Stock target, otherwise spot | Sell call | Premium cannot exceed spot; maximum profit must be positive | Stock at reference spot |
| Protective put | Stock stop, otherwise spot | Buy put | Positive effective floor and nonnegative stock-plus-put maximum loss | Premium debit; shares required separately |
| Cash-secured put | Stock stop, otherwise spot | Sell put | Premium below strike, leaving positive stock-loss exposure | Full strike × 100 cash reserve |

These checks reject inconsistent archive/reference combinations; they do not prove executable arbitrage or price-basis agreement. Greeks remain provider contract estimates. None of the screens jointly optimizes delta, gamma, breakeven, win rate or returns.

The three new candidate builders share the exact payoff functions used by original cards. A deterministic offline witness compares 200 complete original matrices against release `1b3aee91`: singles, combinations, recommendation and metadata must be identical. New search results use `single_leg_candidates_v1` with strategy identity; the covered-call response retains its compatible version. Each review reports source expiry/anchor, requested holding period, search date, eligible/attempted/completed/failed expirations and row rejection counts. Original-candidate failures block goal fit for the affected strategy; explicitly choosing a screened alternative clears only that strategy's original-candidate blocker.

Pending responses and selections are invalidated when matrix, symbol/horizon, holding period, source session, reference spot, stock levels or the New York calendar date changes. The browser rejects a result with mismatched symbol, archive session, reference spot, holding period or search date. Switching strategies reuses the completed response without another request. Old responses without the new strategy reviews keep the original cards available.

## Validation

Targeted backend: 105 passed. Focused frontend: 44 passed; full typecheck passed. Offline original-matrix equivalence: 200 cases passed. Full frontend: **874 passed across 72 files**, full typecheck clean. Full backend, production build and authorized rollout are pending. An existing older-response DOM test initially caught that the new selector appeared without candidate data or research context; the compatibility guard was restored, then focused and full frontend checks passed.

Regression cases cover numeric payoff/capital and buy/sell/right for each new strategy, unsupported terms and quote failures on every strategy, invalid payoff bounds, same-session enforcement, failure caching/shared read count, all four reviews through the real handler/composition, explicit selection, assignment reset, no request on strategy switches, visible active choices and midnight invalidation.

## Remaining work

- Independently dated spot/chain price-basis checks, current two-sided quote sizes and event dates.
- Documented user controls for delta/DTE preferences, followed by coverage and out-of-sample cost/fill evaluation.
- Validated option-strategy return forecasts and portfolio/assignment risk before claiming a best market-aware strategy.
- Dark-pool provider correction semantics and feed-gap completeness remain separate open work; this change does not resolve them.

[Evidence and reproducible witnesses](evidence/2026-10-23-four-strategy-expirations/README.md).
