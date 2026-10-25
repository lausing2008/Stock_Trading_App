# Option loss basis, expiry scenarios and chart-premium consistency

Date: October 25, 2026, America/Los_Angeles. Next improvement after the current-provider refresh release. Addresses the user's confusion about large covered-call / cash-secured-put losses and whether a strike above stock price prevents a loss.

## Changes

Cards and comparison rows now identify **stock + call**, **put / assignment**, **stock + hedge**, **option debit**, or combined loss. The two short-income cards explain the stock-zero worst case beside their figures. Covered-call downside includes 100 shares at reference spot; put downside includes the obligation to pay strike × 100 for shares that can be worth less. Premium is a cushion, not protection against every decline.

Each short-income card has **Why the loss is large — expiry scenarios**, with a local hypothetical expiry-price input and preset outcomes at zero, 80% of reference, reference, strike and above strike. Total P/L is the second column so the key result remains visible on narrow cards; horizontal scrolling reveals the decomposition. The table separates premium received from stock/assignment effects. Exact-strike text states uncertainty rather than guaranteeing assignment. Scenario edits do not change contract choice, assignment acceptance, goal fit, budgets or source data. An alternative choice reprices the same hypothetical expiry value. A new plan/capture clears the local input; the separate gain/loss calculator's saved fills and what-if fields retain their prior behavior.

The same-expiry model is per standard 100-share contract, before fees/taxes/dividends, using reference spot rather than the user's purchase cost. It does not model early-assignment timing, account holdings or expected return. Unsupported terms, non-finite references or inconsistent reported payoff bounds refuse a new illustration. Mechanics are supported by [OIC covered calls](https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write), [cash-secured puts](https://www.optionseducation.org/strategies/all-strategies/cash-secured-put) and [assignment](https://www.optionseducation.org/referencelibrary/faq/options-assignment). No assignment probability or optimal trading rule is inferred.

## Chart correction

The existing chart summed individual leg costs while cards used the strategy's aggregate premium. A half-cent midpoint can leave a different contract amount: per-share reference 52.48 / aggregate 5,248.00 versus raw leg cost 5,247.50. For the recorded MU-style $877.55 reference / $880 call, that made the old stock-zero chart loss 82,507.50 while the card reported 82,507.00. It now uses the displayed aggregate debit/credit once, then adds stock and option intrinsic effects. Signed negative credit-collar amounts use their magnitude as a received credit; debit cases remain payments.

This shared expiry helper now drives charts and the new scenarios. It changes the frontend illustration, not backend prices, API rounding, strategy ranking, signal scores, premium budgets or the separate gain/loss calculator's pricing convention. Chart captions explain the existing 75%–125% stock-price window, which can exclude the stock-zero worst case.

## Numerical and interaction evidence

The user's earlier illustration: 100 shares at $418.19, $480 call, $108 credit. Expiry at $400 gives stock/assignment effect −$1,819 plus $108 = **−$1,711**; at $480 or above, **+$6,289**; at zero, **−$41,711**. A $405 put / $1,457 credit at $350 gives **−$4,043**, and at zero **−$39,043**. Exact breakeven is normalized to positive zero at cent precision, avoiding a floating-point “−$0.00”. Bounded short-income payoff is combined before multiplication so extreme hypothetical prices do not create infinity cancellation; overflowing reference cash amounts are refused. Breakeven checks, half-cent mismatch, signed credit collars, debit calls, invalid fields, bound disagreement and non-mutation are tested.

DOM tests exercise custom zero/negative/at-strike values, alternative repricing, explicit assignment wording, unsupported records, and separation from contract choice / assignment acceptance. Native Chrome renders all four actual strategy cards, two open scenario tables and four open charts at **1449 / 1024 / 390 px**, with document widths matching the viewports. Each 580 px table scrolls inside its card; keyboard scrolling is verified. Final visual inspection also found a minus sign wrapping separately from its dollar amount; a contained follow-up keeps numeric cells on one line with tabular figures. The original screenshots remain unchanged; the final variant is saved separately. Cards have min-width zero and start alignment so an expanded card does not stretch sibling card backgrounds.

These fixtures use historical discussion examples, not live quotes or authenticated production screenshots. The first native assertion looked for mixed-case visible text despite uppercase CSS; it was corrected to check source text. Initial test fixture types lacked required DTE/breakeven fields; full typecheck caught these and they were supplied. Temporary renderer invocation was corrected to use the frontend directory.

Final validation: **980 frontend tests across 82 files**, full typecheck and whitespace clean. Backend source is unchanged; its preceding applicable result is **5,912 passed / 26 skipped**, not a new run. Learning tutorial, tracker `OPTIONS-RISK-BASIS-AND-SCENARIOS` and the complete CLAUDE-linked session index are updated. Authorized frontend-only rollout and final verification follow below.

[Evidence and reproduction](evidence/2026-10-25-options-risk-scenarios/README.md). [Complete session index](../2026-10-24/session-index.md).
