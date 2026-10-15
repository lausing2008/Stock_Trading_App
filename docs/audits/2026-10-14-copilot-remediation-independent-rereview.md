# Copilot remediation: independent re-review, October 14, 2026

Reviewed `1691875e`, `e757ccaa`, and the deployment documentation through `1c58150d` (reported deployed application commit: `13ab0dd3`). The initial working tree was clean. This review adds only an offline probe and this report. It does not independently attest production state: no production account, database, provider key or live deployment was accessed.

**Recommendation:** address the two remaining parser defects below before chart commands. Prioritize the separately identified Unusual Whales timezone/counter repair in its own change. Keep watchlist moves deferred. The old AI Screen removal is consistent with the accepted decision.

## C1 — P1: Numeric alert parsing can accept only a prefix of a number

Locations: `services/research-engine/src/copilot/intent.py:166–170`; `drafts.py`, price-alert validation.

The regex's greedy numeric capture can backtrack when its negative lookahead rejects a unit. Because the resulting capture does not require a complete numeric token, these counterproposals pass the real validator:

| User question | Parsed/accepted target |
| --- | --- |
| Alert me below 10% | Absolute price at or below **$1** |
| Alert me below 50-day average | Absolute price at or below **$5** |
| Alert me below 1,000 | Absolute price at or below **$1** |

The first example also records a percentage refusal, but validation only applies that refusal when there are no parsed targets. The accidentally parsed target bypasses it. The second example rejects a correct `{{trend.sma50}}` proposal because the typed-price parser already consumed the prefix and skipped source matching. This affects both refusal of incorrect proposals and acceptance of correct ordinary wording.

Fix: tokenize the complete amount and unit before constructing a target. Prevent partial numeric matches, explicitly support or refuse comma-grouped numbers, and identify named-average sources before treating their window number as an amount. Percentage constraints must not be bypassed by a partial target. Reject unsupported clauses explicitly instead of guessing. Regression tests should include both incorrect counterproposals and the correct average proposal above, with/without “its,” multi-digit percentages, decimals and separators.

The probe supplies scripted proposals; it establishes a validation defect, not the frequency with which a real model would produce the incorrect draft. Confirmation still requires the user's Save click.

## C2 — P2: Numeric negation and disjunction are silently changed into positive AND filters

Locations: `services/research-engine/src/copilot/intent.py`, `parse_screen`; `screen.py`, `validate`.

The new metric/comparator binding fixes the previous positive-comparison counterexamples, but does not preserve numeric polarity or boolean composition:

- “K-Score **not above** 70” accepts `min_score=70`, with no warning.
- “K-Score below 70 **or** growth above 80” accepts both `max_score=70` and `min_growth=80`. The underlying screen combines them with AND, narrowing the requested union to its intersection, with no unsupported condition reported.

Fix: the bounded grammar can simply reject numeric negation and OR clauses until they are implemented. If implemented, preserve an explicit boolean expression through validation and execution. Add negative-proposal tests for the two questions above. Do not claim exact interpretation from matching individual bounds alone.

The probe also records that “K-Score above 70%” is read as a score threshold of 70; decide whether that unit is supported or should be clarified rather than silently discarded.

## Earlier fixes inspected

- Durable per-draft receipts are written before executing; ambiguous downstream failures retain `executing`, and retries reuse the operation's key before reapplying expiry/state checks. Separate receipts remove the prior message-JSON update pattern.
- Price-alert preconditions are scoped to identical active alerts, allowing sibling drafts with different conditions/thresholds.
- Watchlist and signal previews carry effect versions, checked again in their write endpoints. Unique-constraint conflicts return the existing row when one exists.
- Cards now disclose observed-price comparisons and fixed average-derived thresholds.
- Draft saves check migration readiness, including required uniqueness and receipts.

These changes address the earlier examples and their regression suite passed locally. This review did not rerun the PostgreSQL concurrency/migration suites, so their reported results remain implementer evidence. Re-reading consent inside a transaction is also not equivalent to serializing it against concurrent preference changes; use appropriate database locking/version checks if that stronger guarantee is required.

## Unusual Whales failure: confirmed scope for a separate repair

`services/market-data/src/adapters/unusual_whales_adapter.py:223` compares a timezone-aware series with `pd.Timestamp(start/end)` made from date-only bounds. A local pandas reproduction with a `2026-10-14T14:00:00Z` observation and `date(2026,10,14)` raises the reported `TypeError: Invalid comparison between dtype=datetime64[ns, UTC] and Timestamp`.

The conversion/filtering/canonicalization section is outside the metered request's exception handler. Consequently this processing failure bypasses the existing failure classification, matching the reported observability gap. No vendor call was needed to reproduce the mismatch.

Recommended change scope:

1. Define daily-bar date semantics separately from intraday UTC timestamps, normalize observations and window bounds consistently, and preserve the canonical adapter contract.
2. For date-only intraday windows, include the whole intended end date; merely attaching UTC to the existing midnight end bound would still exclude later bars that day.
3. Meter post-response parse/filter/canonicalization failures once, with a useful failure category. Preserve fallback, existing HTTP/rate-limit accounting and cooldown behavior.
4. Test realistic timezone-bearing candles, daily date strings, date-bound inclusivity and malformed responses with mocked HTTP responses. Assert both returned bars and failure counters. Existing instrumentation tests that inspect source text cannot establish correct processing-failure accounting.

The deployment index says the adapter and caller were unchanged in the deploy. That is reported evidence; this local review does not independently establish when the production fault first began or confirm current fallback coverage.

## Validation

- Research-engine copilot regression suite: **43 passed**.
- Market-data alert/watchlist idempotency suite: **8 passed, 1 PostgreSQL case skipped**.
- Frontend focused copilot checks: **15 passed** across rendering, interaction and screening tests.
- Ranking HTTP authorization: the first unauthenticated refresh case failed with `TimeoutError` after its 20-second timeout, awaiting an AnyIO worker-thread dependency. A full-suite attempt was bounded at 45 seconds; a first-failure rerun captured this traceback. Do not count the implementer's reported 19 passes as independently reproduced here; the timeout does not establish an authorization bypass.
- [Offline parser probe](evidence/2026-10-14-copilot-rereview/probe.py) and [results](evidence/2026-10-14-copilot-rereview/results.json). Run from repository root. It clears `STOCKAI_INTEL_DB` within its process and uses the existing harness's disposable SQLite database and scripted model output. No paid calls.

Infrastructure reachability, source synchronization and signed-out 401 checks do not replace an authenticated copilot smoke test. The production answer/draft flow remains unverified by this reviewer. Keep a signed-in answer, evidence expansion, screening and one explicitly selected draft/save/replay in the next operator acceptance check.
