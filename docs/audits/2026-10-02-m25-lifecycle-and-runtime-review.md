# M25 lifecycle and runtime-counter review

Review of reported commits `2d243695` and `acb3a01a`, October 2, 2026.

## Conclusions

The missing M25 dispatcher is confirmed by source inspection: `submit_pending`, `needs_reconciliation` and `reconcile_submission` have no production call sites. Keep M25 disabled. Existing lifecycle primitives and their tests are not an operational submission/reconciliation service.

The stored PostgreSQL probe/evidence supports the narrower SF-01 **close-before-claim** acceptance, including a blocked UPDATE rechecking eligibility. I did not rerun PostgreSQL or interrogate production in this review. The R4 claim-first scenario calls `record_closure_disposition` explicitly and then closes the fixture; it is not an end-to-end broker cancellation test through all four close paths. Preserve that distinction in closure reporting.

## Two independently executed control gaps

AST-extracted the current `_naive_utc`, `intent_age_seconds`, `price_drift_pct`, and `dispatch_block_reason` functions and executed with `now=2026-10-02 15:00:00` and entry price 100:

| Input | Actual result | Required result |
|---|---|---|
| Entry time equals now; quoted price is `float('nan')` | `None` — no block | Explicit invalid-price block |
| Entry time is one day in the future; quoted price is 100 | `None` — no block | Explicit invalid-time block |

These are local control witnesses, not observed production orders. `abs(NaN) > limit` is false; a negative age does not exceed the maximum age. Require finite positive entry/quote prices, valid finite policy values and a nonnegative age subject to an explicitly bounded clock-skew policy. Quote parsing exceptions must not abort the rest of a batch.

Additional source-traced limits:

- `quote(trade)` returns only a number; it supplies no timestamp, source or session evidence. Numeric availability does not establish freshness.
- `now` is resolved once for the batch. Controls run before a potentially blocking claim. An intent/quote can age while the claim waits, so check freshness again immediately before provider submission using a current clock and define a safe unsent disposition if it fails.
- Recording `retains_reserved_exposure=True` is not proof every exposure calculator retains a closed-but-unreconciled intent. Exercise a subsequent entry against the real reservation/risk path in the claim-first test.
- Broker disconnection after claim needs explicit dispatch/cancel semantics; a pre-claim predicate alone cannot settle that boundary.

## Policy constants

`900 seconds` and `1%` are reasonable **candidate sandbox experiment settings**, not validated production limits and not values approved by this review. Neither can be called universally conservative: a one-percent move may consume most of a tight stop's risk budget, and a fifteen-minute-old breakout may already be invalid.

Use an immutable policy version per intent. Bound validity by the earliest of intent TTL, signal/plan expiry, session rules and event invalidation. Validate quote age separately. Recheck stop geometry, position size, buying power and exposure at the permitted execution price. Distinguish adverse drift from a favorable price that nevertheless breaks the thesis. A pre-submit check does not guarantee fill price; enforce an appropriate order-price constraint too.

First measure would-block rates and outcomes in shadow/sandbox, by strategy and market, without changing existing trading thresholds. This needs request/quote observations and counterfactual decisions; a previously running production dispatcher is not required to begin measurement.

## Runtime-counter interpretation

The reported aggregate 429 count does not identify the constrained endpoint or the provider's limiting dimension. Daily/minute header headroom does not rule out burst, endpoint, concurrency, entitlement or account-specific limits. Option chains' 89% volume share is a hypothesis for prioritizing investigation, not attribution of 89% of failures.

Add bounded-label counters for endpoint template, client/service, status class, logical requests versus attempts/retries, latency and sanitized rate-limit metadata. Instrument all three UW writers consistently. Do not include secrets or per-symbol/unbounded URL labels. Honor provider retry instructions and coordinate backoff; more blind retries can worsen throttling. Fix or retire the inconsistent rolling counter rather than letting dashboards interpret a missing key as zero failures.

Premarket measurement needs actual per-provider requests, cache hits, useful rows written and data freshness in the next **exchange trading-day** premarket. The 8,520→2,840 projection is not a measured reduction. Container logs alone are insufficient for a durable baseline.

A declining recovery-marker TTL establishes expiry behavior, not whether the original recovery attempt was legitimately consumed. Preserve the unresolved provenance question; no reset is implied by this review.

## Next implementation boundaries

1. Fix and behaviorally test invalid numeric/time inputs, timestamped quotes and post-wait validity.
2. Build a disabled dispatcher plus an operator reconciliation surface, explicit unsent-expired/cancelled states and observable backlog. Pending local intents must never appear as confirmed broker fills.
3. Test the real end-to-end sandbox route: durable intent → dispatch → broker response/fill → close/cancel/reconciliation → retained or released capacity. Include crashes and late responses.
4. Instrument UW/premarket requests and retain a baseline before optimization.
5. Prepare a separate activation decision with evidence, policy values and rollback semantics. Do not strand existing intents or reroute them by simply flipping the global flag back.

No production changes, flag activation, recovery actions, emails, orders or edits to `CLAUDE.md` occurred in this review. No full-suite or PostgreSQL rerun is claimed.
