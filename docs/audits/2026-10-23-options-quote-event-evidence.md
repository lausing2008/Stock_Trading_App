# Options quote provenance and reported-event timing

Date: October 23, 2026 (America/Los_Angeles). Frontend-only continuation of the [four-strategy expiration comparisons](2026-10-23-four-strategy-expirations.md). Public and deployed application release: `4e6b1f0cb860e586ad259d2e5fd9ce9d67ab415f`.

## Available evidence and actual gaps

The archive supplies a market session date, bid/ask values and an ingestion timestamp. Its schema and game-plan response do not establish a per-quote observation timestamp, quote size or an independently timed reference spot. The new panel keeps those execution-evidence fields explicitly unavailable. It does not promote an ingestion time to an observed quote time or introduce a live-quote assertion.

The stock overview already loads yfinance fundamentals containing `next_earnings_date`, `ex_dividend_date` and `fetched_at`. The frontend type now exposes the existing ex-dividend field. The stock page passes the same overview's dates and retrieval timestamp with the requested symbol into the option comparison. No new provider calls, polling, API routes, database writes or schema changes are introduced.

Calendar retrieval is treated as current only for a matching symbol and a timezone-qualified, readable, nonfuture timestamp at most 24 hours old. This is a **research freshness policy matching the existing fundamentals cache TTL**, not an optimized trading parameter or verification that an event date is current. Issuer confirmation, event time and full calendar coverage remain unverified even for a recent retrieval. The page states the source and the distinction between retrieval and event observation/confirmation.

## Window classification and optional filter

The evidence table uses the contract actually being compared for each of the four strategies, including explicitly selected alternatives. Reported date classifications distinguish missing, unreadable, past, within the planned hold, after the planned hold but by expiry, and after expiry. Date-only values must round-trip as actual calendar dates; February 30 cannot normalize into March. Past dates mean the next event is unknown, including an old ex-dividend record. Events today or on expiry are included; a date after expiry is not presented as proof of a complete event-free window.

The planned hold ends at the current New York calendar date plus declared holding days, inclusive. Event classification also checks the whole option life, because a planned exit does not guarantee the position is closed then. Invalid/undeclared holding periods still allow an event to be described as reported by expiry, while existing holding-period eligibility checks remain in force.

The optional checkbox reads **Require a current calendar record with reported next earnings after expiration**. It is off by default and local to the page, preserving ordinary goal-fit behavior. It does not change backend signal scores, trading gates, account settings or order execution. When enabled, every strategy needs current matched retrieval evidence and a reported earnings date strictly after its compared option expiry. Missing, malformed, timezone-unverified, future, stale and past records cannot pass. Earnings on expiry or between the planned exit and expiry also block this research filter. This tests a specific report condition; it does not assert absence of other events or issuer verification. Ex-dividend exposure is disclosed separately and does not automatically change rankings.

The existing 30-second UI clock updates retrieval age and window classifications without requests. Existing budget, holdings, assignment acceptance, quote, archive-age and holding/expiry checks continue to apply. Entry stays WAIT for fresh quotes and broker checks. Market-aware expected-return ranking remains unavailable.

## Related recovery fix

When every original preset contract is unpriced, the unavailable-plan branch previously discarded its valid strategy matrix, reference price and archive session. An explicit longer-expiry search then could not match its results to the original basis and was refused. The branch now retains that evidence when supplied and labels the **original preset contracts** unavailable. A regression exercises a successful explicit alternative selection after that response. A genuinely unavailable basis still cannot be invented; normal feed-failure states remain visible.

## Assignment context and sources

The panel asks for covered-call assignment review around ex-dividend dates and states that early assignment is possible for both short calls and short puts. Assignment probability is not calculated: fresh option time value, dividend amount and verified deliverables are not supplied by this plan. This limited educational wording follows the [Options Industry Council assignment FAQ](https://www.optionseducation.org/referencelibrary/faq/options-assignment). The source supports the assignment context, not the 24-hour UI policy or a probability estimate.

## Validation and rollout

**906 frontend tests passed across 73 files**, including 83 focused regressions. Full typecheck and whitespace check passed. Production build compiled all 76 pages; authorized frontend-only rollout completed successfully. One initial DOM check assumed strategy-card titles were headings; the test now checks the actual card grid so event-table labels cannot satisfy it. Typecheck also caught nullable expiry input, which is now handled explicitly before the successful full run. Backend source is unchanged; its most recent full validation remains 5,822 passed / 26 skipped from the preceding release, not a new backend test run.

Tests cover real/invalid dates and leap days, timestamp timezone and freshness boundaries, mismatched symbols, event-day/expiry-day boundaries, an event beyond planned exit but before expiry, optional filter behavior, all four calendar blockers, ageing without requests, provenance wording, and recovery from an unpriced original preset with a stable archive basis.

## Remaining work

- Per-quote observation timestamps and sizes, independently dated spot and price-basis checks.
- Issuer-confirmed earnings/ex-dividend dates, event time, dividend amount and coverage evidence.
- [Explicit delta/DTE controls are now implemented and deployed](2026-10-23-options-delta-dte-preferences.md). Measured out-of-sample cost/fill/assignment outcomes remain required before optimal settings or expected-return ranking.
- Dark-pool correction identity and feed-gap completeness remain separate work.

[Evidence and final verification](evidence/2026-10-23-options-quote-event-evidence/README.md).


### Existing-cache witness

A Redis GET-only production check (no provider refresh or writes) found TSM reporting next earnings `2027-01-14`, ex-dividend `2026-12-10`, retrieved `2026-10-24T01:32:15.737056Z`. The timestamp passed the retrieval policy at verification. The exact reviewed TypeScript classifier was exercised against that record and expiration examples from the preceding TSM archive witness: it produced both reported-after-expiry and reported-before-expiry cases. This is cached provider evidence and policy verification, not issuer confirmation or a fresh market search. A longer option life can therefore encounter a reported event beyond the planned exit even when a shorter expiration does not.


### Authorized production rollout

Application release `4e6b1f0c` was pushed to public `prod`, built fresh on EC2 using production settings, and deployed under the shared host lock with an exact-release image tag and retained rollback image. Frontend build `WFsiB5NjASsFNyuSSVBMs` matches the prepared image. All 15 containers are healthy; all 12 backend source digests match the unchanged checkout, and every one of the other 14 containers retained its start time. Market-data was not restarted. Five public pages returned HTTP 200; public TSM and Improvements assets contain the new evidence panel, calendar filter, recovery message and tracker entry.

Verification combines 906 frontend tests, full typecheck, production compilation, existing-cache GET-only evidence, the exact TypeScript classifier replay, served-build markers and container/source checks. No authenticated interactive browser acceptance or issuer calendar confirmation was performed. Per-quote observation timestamps/sizes and independent reference-spot timing remain unavailable. These limits are visible in the UI and remain requirements for a later market-aware return ranking.

The final deployment/evidence follow-up is documentation only and requires no further service restart.
