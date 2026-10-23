# Follow-up working-tree review — October 23, 2026

Reviewed the uncommitted options follow-up to `d52518bf`: polling state, moneyness estimates, settlement states, equity caveats, relevant tests and saved archive-query evidence. No application code changed, no production queries rerun, no deployment approved. Unrelated working-tree changes were left alone.

## Remaining commit blocker: hidden tabs still execute error retries

`frontend/src/lib/usePolledSWR.ts` sets `refreshWhenHidden:false`, which pauses SWR's normal polling, but its custom `onErrorRetry` schedules an unconditional `setTimeout(() => revalidate(...), delay)`. An error received while visible schedules retries that continue after the user hides the tab.

Reproduction using the real hook and the existing fake-timer harness:

1. Mount visibly with a fetcher that rejects with HTTP 503.
2. Flush the initial response, then set document visibility to hidden.
3. Advance three minutes.
4. Expected one initial request; observed **six requests**.

The saved [witness](2026-10-23-hidden-retry-witness.test.tsx) includes the existing harness and nine passing tests plus the new failing case. Copy temporarily into `frontend/src/__tests__/` and run with the project's Vitest configuration. The review's temporary copy was removed afterward. This is a desired-behaviour regression test: it should pass after the correction.

Required correction: retry execution must respect visibility and online state, with controlled recovery when the tab becomes visible again. Do not merely suppress the hidden retry permanently: SWR skips normal polling while an error remains cached, and focus revalidation is disabled here. Preserve rate-limit deadlines, avoid duplicate retry timers and clean up timers on key change/unmount. Test HTTP 503 and 429 while hidden, hide-before-deadline, visibility recovery, and repeated failures. The current HTTP-200 provider-rate-limit polling tests should remain green.

## Validation in this review

- Backend `test_options_contract_terms.py` and `test_options_missing_inputs.py`: **61 passed**.
- Frontend existing polling tests, `income-settlement-caveats.test.tsx`, and `options-missing-inputs.test.tsx`: **24 passed**.
- Additional hidden-retry regression: **1 failed**, observed 6 requests instead of 1.
- `git diff --check`: passed.

The full-suite counts reported in section 9.4 were not independently rerun. The two new polling fixes described there work under their existing tests; this finding concerns the separate error-retry path.

## Evidence wording

The two reported archive populations total **52,526,842 rows**, rather than 51 million. The queries classify readable symbols by root matching; describe the result as **zero exclusions under the current R05 classifier**. It is not independent verification of every contract's legal deliverable or proof that root matching detects all adjustments. No new archive scan is needed solely to correct this wording.

Moneyness now clearly states an estimate and retains the small-split counterexample. Unresolved-settlement and approximate-valuation fields are connected to visible UI caveats. No additional blocker was identified in those inspected changes. Commit approval and deployment remain held pending the retry correction.

## Re-check after retry-scheduler correction — October 23, 2026

Inspected the replacement deadline-based `RetryScheduler` and hook wiring. Hidden/offline retries are held; visibility/online wake events preserve the deadline and consume a due retry once. Success and effect cleanup clear pending work, and reconnect revalidation is disabled to avoid a competing recovery path.

Independently reran `npm test -- src/__tests__/polling-behaviour.test.tsx`: **18 passed**. `git diff --check` also passed. Vitest emitted an `act(...)` environment warning in one recovery test; no assertion failed. The full 808-test frontend run and full typecheck remain attributed to the implementer's verification, not rerun in this re-check.

The hidden-retry commit blocker is cleared. Approve a scoped local follow-up commit including this review and the preserved witness, leaving unrelated changes unstaged. This is not deployment approval; review the exact staged diff before committing. The earlier failing result remains above as historical evidence.
