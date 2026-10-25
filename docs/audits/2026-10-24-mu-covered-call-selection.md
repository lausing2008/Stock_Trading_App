# MU covered-call strike selection and broker-chain reconciliation

Date: October 24, 2026 (America/Los_Angeles). User reported MU around $877.55, a Fidelity view showing $855–$900, and app covered-call comparisons near a $1,561.91 stock target.

## Findings and correction to the earlier explanation

The distant strikes were not created by formatting the stock forecast. The stored October 22 chain contains both $855–$900 and $1,540–$1,580 November 20 calls, with matching encoded identifiers. A read-only, 15-second-limited production query returned 15 rows; no archive changes were made.

An independent, unauthenticated Cboe delayed-chain check found the exact identifiers `MU261120C01560000`, `MU261120C01580000` and `MU261120C00880000`. The November 20 standard-root call slice contained 276 entries spanning $5–$2,500, including all $5 increments from $855 through $900. Provider timestamp was `2026-10-25 00:31:23` with no timezone in that string; checked at `2026-10-25T04:31:22Z` (October 24 evening Pacific). Reference price was $879.90. This independently supports presence in a delayed chain at that check, not execution, Fidelity availability, future listing status or verified deliverables. The broker app's filter settings were not inspected. Earlier suggestions that the distant records might be invalid remain hypotheses, not demonstrated defects. A high strike alone is not invalid.

The confirmed app defect was selection quality: legacy covered-call selection, the strategy matrix and its expiry-search review used the stock take-profit target as an anchor. Ranking by distance to $1,561.91 naturally promoted far OTM calls despite a $877.55 reference. Maximum-profit arithmetic made these choices look attractive without any return-probability evidence. A forecast is not a listed strike or a validated option selection rule.

## Implemented changes

- Covered-call original selection and bounded alternative rankings now use **reference stock price**, across the legacy plan, matrix and expiry search. The original remains an explicit reference and alternatives still require selection. The code selects actual supplied archive rows; it never rounds a forecast into a new contract or imposes an invented $855–$900 listing range.
- The stock target remains separate. Combination strategies retain their existing target/stop legs. AI stock forecasts, signal scores and horizons are unchanged; the $1,561.91 forecast's reliability is not validated by this fix.
- A covered call can be researched without a stock take-profit. The optional legacy target field is nullable and the UI no longer calls `toFixed` on a missing target.
- Comparison UI labels the archive source session, unverified current/Fidelity listing status and independent stock forecast. It distinguishes a new reference-price plan from an older cached target-anchored response. The dropdown says “Original archived selection.” Learning tutorial and Improvements tracker are updated.
- At the shared route adapter, supplied strike, expiry and side are checked against the encoded contract identity before automatic payoff is allowed. A disagreement yields `contract_identity_mismatch`, no assumed multiplier/deliverable and no automatic payoff. Raw archive rows are preserved. Matching metadata remains a consistency check, not an exchange deliverable or current-listing record.

## Regression evidence

A MU-shaped witness uses spot $877.55, target $1,561.91, near strikes plus $1,560/$1,580. The default is $880; alternatives are $875/$885/$860. Changing the target to $900 leaves the covered-call default/review identical. Separate tests cover the legacy and matrix agreement with and without a target, encoded strike/expiry/side mismatch, matching identity, and rendering the new archive/forecast separation.

Four earlier candidate tests assumed target-distance ordering; their fixtures/assertions were changed to exercise the corresponding spot-reference behavior without weakening quote/spread/expiry screens. Quote failure, deterministic ordering, bounded expiry reads, manual alternative selection, assignment reset, holding/delta preferences and WAIT checks remain covered.

Frontend final suite: **949 passed across 78 files**, full typecheck clean. Focused strategy/identity/legacy checks: **105 passed**. The first full backend run had 11 failures: seven source-extraction harnesses needed the new checked-terms dependency; four historical target-anchored fixtures no longer produced a priced covered call at the default spot. The harness now supplies the real helper. Coverage fixtures use a valid near-spot quote; long calls can therefore legitimately remain for an insufficient-share account, while covered calls remain excluded. An additional isolated only-covered-call fallback test preserves the original insufficiency witness. All 68 affected tests pass; the final full backend rerun passed **5,868 tests / 26 skipped**.

In-memory restoration of target-based leg selection changes the MU witness from $880 back to $1,560. Removing the existing holdings guard changes the explicit insufficient-share witness from no preference to covered call. These checks do not mutate tracked source or production.

Final backend suite: **5,868 passed / 26 skipped**; frontend **949 passed**, full typecheck and whitespace clean. Authorized production rollout follows below. Initial test commands from repository root failed to locate the service modules/package; corrected commands use the service working directory and frontend package prefix. These were test invocation errors, not production failures.

## Remaining boundaries

This is not a five-minute live-chain integration. The app still uses settled archived chains. The one-off public Cboe probe is debug evidence, not an added licensed production feed, authentication to Fidelity or continuous listing verification. No archive records are deleted merely for being far from spot. The generic search-context mismatch behavior is unchanged; it remains a separate recovery/diagnostics improvement. Fresh broker quotes, size, permissions, assignment funding, deliverables and issuer dates still require verification.

[Evidence](evidence/2026-10-24-mu-covered-call-selection/README.md). [Complete session index](../2026-10-24/session-index.md).
