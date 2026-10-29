# US valuation report — production release

Released October 29, 2026 at **https://lausing.com/reports?tab=valuation**.

Final application revision: `c1cf0ecfe83c9f603270335b49e1956d69b1c2dd`.
The initial `efda21c5` rollout rebuilt all 12 backends plus frontend because the shared report-type registry changed. The subsequent correction rebuilt research-engine and frontend only. All running backend Python sources match the final revision; the other backend sources did not change in the correction. Both rollouts used the existing host deployment lock, pinned revisions, retained rollback images and health checks.

## Actual provider behavior

Yahoo returned HTTP 429 and invalid-crumb/401 failures from the production host. Existing UW access successfully supplied company information, full financials, completed daily prices and dated analyst actions. The normal collection path uses these four existing endpoints when Yahoo fails; it does not request new subscriptions or enable previously disabled integrations.

UW Advanced+ company profiles and forward estimates returned 403 in the small entitlement probe. They are excluded from the normal path. Missing forward EPS, PEG and verified price-adjustment metadata remain explicit gaps. Trailing P/E appears in the main table; forward P/E remains available in company detail if a source supplies it. UW cash-flow models are explicitly identified as FCFF **proxy scenarios**, including the interest-classification, tax, basic-share and latest-filing limitations. These models alone cannot qualify a stock as an investment opportunity.

## Live report acceptance

Report **53, version 2**, uses the completed **October 28, 2026** price session:

| Measurement | Observed |
|---|---:|
| Sampled names | 60 |
| Outside the previously tracked stock universe | 38 |
| UW-backed rows | 59 |
| Inputs unavailable | 1 |
| Below base model value | 7 |
| Above base model value | 14 |
| Near base model value | 6 |
| Insufficient evidence / unsupported classification | 33 |
| Dated analyst risk flags | 18 |
| Fully qualified opportunities | 0 |

The analyst-risk count overlaps valuation categories. The 38 discovered names are outside the application's covered stock universe; no private watchlist membership is used to generate the public report. This is a bounded US-listing sample, not an exhaustive security master. The report offers research leads and records qualification blockers; it does not guarantee returns.

Verified through the production HTTPS API: unauthenticated reads return 401; authenticated generation completes; latest, history and saved-report reads agree; saved values remain frozen. Version 1 remains accessible as historical coverage and version 2 corrects the unavailable-provider attribution.

Actual Chromium acceptance on the deployed frontend passed at **1440px** and **390px**: document width equals viewport width, no page runtime errors, working category controls, company expansion and full CSV download. A first browser pass exposed missing screen-reader-only CSS and absolute descriptions escaping the scroll container. The correction visually hides and contains those descriptions, constrains report cards, keeps tables/tabs internally scrollable, and wraps the Reports header. The company detail uses one column on a phone.

## Validation and operations

- 999 frontend tests passed across 86 files; frontend typecheck and production build passed.
- 83 research tests, 5 market-data tests and 14 gateway route-coverage tests passed: **1,101 tests total**.
- All 13 application containers healthy; PostgreSQL and Redis also healthy.
- Final drift check: **12 backends checked, 0 drifted/errored**. Browser acceptance verifies the served frontend, rather than inferring it from backend hashes.
- Final deployment recorded 29GB free after cleanup and 3.1% I/O wait before cleanup. Rollback image tags were retained.

Evidence: [API summary](evidence/2026-10-29-valuation-report/api-summary.json), [browser summary](evidence/2026-10-29-valuation-report/browser-summary.json), [final container manifest](evidence/2026-10-29-valuation-report/containers-after.json), [final rollout log](evidence/2026-10-29-valuation-report/deploy-final.txt), and [source drift](evidence/2026-10-29-valuation-report/drift-final.txt). The full report payload and screenshots are retained locally in that evidence directory and excluded from git; the API summary records the captured payload hash. [Verification script](evidence/2026-10-29-valuation-report/verify_live.py) keeps test authentication in memory and never writes or prints credentials.

Generation is explicit and runs through FastAPI background tasks backed by Redis job state; worker restarts can interrupt jobs. A durable scheduler/queue, exhaustive coverage, issuer-verified dilution/material filings, sector-specific models, current forward-metric access and prospective return evaluation remain extensions described in the [implementation record](../features/2026-10-28-us-stock-valuation-report-implementation.md). They are not presented as delivered or validated capabilities.
