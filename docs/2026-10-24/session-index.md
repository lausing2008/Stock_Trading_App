# Session record — October 24, 2026

Continuation of the [October 23 session](../2026-10-23/session-index.md). Dates use America/Los_Angeles; evidence timestamps are UTC.

Delta/DTE option research preferences are public and deployed in application release `dc2d4b7d9c40e043f4face36e42fb21ef9dbcf4f`. Controls are blank by default and shared across the four strategies. DTE filters run before the four-expiration cap; delta checks and original-contract exclusion run before the three-alternative cut. Active preferences gate page goal fit and require server acknowledgement. No automatically selected trade or optimal-return claim is introduced.

Validation: backend 5,849 passed / 26 skipped; frontend 928 passed across 74 files; full typecheck; 200 original matrices unchanged; production compiled 76 pages. All 15 containers healthy, all 12 backend source digests match, and the other 13 containers retained their start times after market-data/frontend deployment. Read-only TSM checks returned 12 alternatives per strategy for the example 60–180 DTE / 0.20–0.40 absolute-delta band, zero matches for exact-zero delta, and zero extra reads for an empty expiry band. These are verification cases, not recommended settings.

[Full report and evidence](../audits/2026-10-23-options-delta-dte-preferences.md). Quote observation times/sizes, dated price-basis evidence, issuer-confirmed events, validated strategy-return forecasts and dark-pool correction/gap semantics remain open. The final documentation sync needs no additional service restart.
