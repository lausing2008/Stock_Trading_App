# Original and returned option candidate comparison

Date: October 24, 2026, America/Los_Angeles. Frontend-only continuation after the [delta/DTE preference release](2026-10-23-options-delta-dte-preferences.md). Public and deployed application release: `003e05ab7bf1e98008ecb9004f82f551e88db348`.

## Behavior and scope

Under each selected strategy, **Compare candidate costs, risk and liquidity** opens a table of the original plan and already-returned alternatives. The original reference remains pinned first; an unpriced original retains its known strike, expiry and contract identity while premium/payoff fields stay unavailable. It is not assigned a zero price or silently replaced.

The table shows contract identity, strike/right, expiry and current New York calendar DTE, archived debit/credit per standard contract, maximum loss/profit, expiry breakeven, stated capital, displayed rounded spread, measured OI and provider contract delta/gamma. Finite zero spread and OI remain measured zero; missing, invalid and nonfinite sort measurements stay unknown. Null maximum profit is shown as unbounded only for long calls/protective puts, whose modeled stock-plus-option upside is unbounded; null profit on the two credit strategies remains unavailable. Loss null remains unavailable.

Capital is strategy-specific: covered calls include the stock at reference spot; protective puts show option debit and require shares separately; cash-secured puts use their stated full cash collateral. The source payoff arithmetic is reused without a new calculation or annualized-return score. Stock cost basis is not inferred. Figures are before fees, slippage and taxes and use settled archive references, not executable fills. Greeks are contract estimates, not combined strategy exposures or profit probabilities.

## Ordering and explicit selection

Default order is the server shortlist order. Optional view-only sorts are narrowest displayed spread, highest measured OI, lowest positive stated capital and earliest expiry. Unknown sort measurements are last, then ties use deterministic expiry/strike/contract identity with original index as the final tie-break. Spread sorting uses the rounded `spread_pct` exposed by the API; it cannot resolve the unrounded differences used by the backend screen. No metric is combined into a weighted score or called the best return.

Sorting is **only among the returned shortlist**, already limited by the backend's source session, preference screens, four-expiry cap and three-alternative cut per expiry. It does not search additional contracts or reprioritize the underlying chain. Thus a row first in a spread sort is not the market's most liquid contract or the highest expected-return choice.

Each row retains its original response index. Sorting never changes selection or assignment acceptance. Pressing Compare passes that source index through the same selection function as the existing dropdown, updates only the single-strategy card/graph and clears assignment acceptance. Selecting the original restores the original plan, including an unpriced reference. Malformed multi-leg/empty alternatives cannot be selected through either control. Existing preference, calendar, budget, holdings, quote, snapshot-age and expiry checks are preserved; entry status remains WAIT for fresh quotes and broker checks. Original stock levels and combination legs remain unchanged.

The comparison opens on request and adds no API/provider requests, polling, database writes, backend changes or dependencies. Current DTE uses the parent's existing clock and actual expiry date, not cached `days_to_expiry`. Changing strategies resets the view sort; shared comparison choices remain explicit and visible through the existing summary.

## Validation and deployment

Focused validation: 28 tests passed and full typecheck clean. **936 frontend tests passed across 76 files**, with full typecheck and whitespace clean. Production frontend compiled all 76 pages; authorized frontend-only rollout completed successfully. One pre-existing assertion assumed breakeven text appeared only once; it is now scoped to the actual strategy-card grid so the comparison table cannot satisfy the check accidentally. Backend code is unchanged; the preceding applicable backend validation remains 5,849 passed / 26 skipped, not a new backend suite run.

Tests cover source-order preservation and immutable arrays, unknown/zero measurements, deterministic metric ties, real current DTE and invalid calendar dates, unpriced original identity without invented dollars, appropriate null/unbounded semantics, capital scope, and contract-index preservation through sorting and actual Matrix selection. The integrated regression proves explicit selection clears assignment acceptance, later sorting preserves the same chosen contract/acceptance, and no fetch occurs.

## Remaining work

- Independent quote/spot observation times, quote size, price-basis evidence and issuer-confirmed event coverage.
- Cost/fill/assignment/portfolio replay and validated forecasts before a best-return ranking or optimal settings.
- Ranking the full candidate universe under a new declared coverage/cost policy; this view does not expand the existing bounded shortlist.
- Per-strategy/horizon preference proposals, dark-pool correction/gap semantics and the other open items in the [complete session index](../2026-10-24/session-index.md).

[Evidence and final verification](evidence/2026-10-24-options-candidate-comparison/README.md).


## Authorized production rollout and real-data replay

Release `003e05ab` was pushed to public `prod`, built fresh on EC2 using production settings, and deployed under the shared host lock with an exact-release image tag and retained rollback image. The disk guard reclaimed 1.601 GB from dangling images only; tagged rollback images remained retained. Only frontend was recreated. All 15 containers are healthy, all 12 backend source/shared digests match the unchanged checkout, and all 14 non-frontend containers retained their start times. Market-data was not restarted.

Running frontend build `mXeFkEZRi6TqqsD_g43Um` matches the prepared image. Five public pages returned HTTP 200; served TSM and Improvements assets contain the comparison table, view-sort control, rounded-spread label and tracker entry. No dark-pool ledger capture/persist errors were found in the verification log window.

A deployed-helper TSM witness used a source-session-pinned October 22 archive in a read-only, repeatable-read transaction with a 10-second per-statement timeout. Four additional archive reads supplied 12 returned alternatives for each of the four strategies. The original covered call remained unpriced while its strike/expiry/identity were preserved; the other three original singles were priced. These are settled research samples, not new live quotes or suggested trades.

The exact reviewed TypeScript sorter and React table were replayed locally on that saved data without network access. All four static tables preserved the actual identities and values; 20 strategy/sort-mode combinations preserved response-index mappings and left the original arrays unchanged. This complements DOM tests for explicit selection and assignment acceptance. It is not authenticated interactive browser acceptance or a production advanced-tier authorization test.

The full-session index reached by `.claude/CLAUDE.md` and the Improvements tracker now include this release. Older RZY/audit/operational entries remain preserved. The final report/evidence follow-up is documentation only and needs no service restart.
