# Covered-call candidate release — October 23, 2026

**Deployed application commit: `e08dd338`.** The user authorized the public `prod` push, including the pending detailed TSM evidence, and automatic production deployment. The push advanced the public branch from `d0d46cb8` through `4d509066` and `e08dd338`. No further publication approval was requested.

## Rollout

Production was clean at `d0d46cb8`, with 15 healthy containers, approximately 25 GB free and the scheduled backup completed. Exact-commit market-data/frontend images were built on EC2 under the shared host lock; the frontend used production settings. Previous images were retained and recorded in `/tmp/options-release-images-e08dd338.txt`. Only **market-data and frontend** were recreated, with health checks and retained-image rollback handling. Signal-engine and the remaining services were unchanged. There was no schema migration or persistent database mutation for this feature.

## Delivered behavior

The original target-strike selection remains visible. A candidate review explains its failure and screens the supplied call rows for the selected expiration. Up to three distinct alternatives are offered in deterministic proximity/spread/OI order. The user must explicitly choose an alternative; the comparison updates its payoff and clears assignment acceptance. The original stock target and combinations are unchanged. OI and available provider contract Greeks are displayed, with missing values marked unavailable. Existing holdings, budget and snapshot checks remain; entry stays WAIT for fresh quotes and broker checks. A backend-screen failure prevents highlighting the original candidate even if its displayed spread rounds down to 20%.

This does not implement multi-expiry or market-regime/company-forecast ranking, and it does not establish an optimal return. The broader tracker item remains in progress for that work. Its shipped “LOCAL IMPLEMENTATION” prefix comes from the tested predeployment commit; this release report supersedes that prefix. Update that small presentation detail with the next frontend release.

## Verification

Predeployment validation: **5,799 market-data tests passed, 26 skipped**; **834 frontend tests passed**, 71 files; full frontend typecheck and whitespace checks passed. The first full backend run exposed a test-harness import lookup issue; its repair preserved the real route assertions and the complete rerun passed. Isolated guard removals detect admitting wide spreads or same-day expirations.

Production verification at **October 23, 21:08 PDT** (`2026-10-24T04:08:54Z`):

- All **15 containers healthy**.
- All **12 backend src/shared hashes match** the application commit.
- Running frontend matches the prepared image: build ID `EinBdMqNIJxOWRz-mfkl3`.
- `/`, `/stock/TSM`, `/improvements`, `/signal-accuracy`, `/signal-tuning`: HTTP 200.
- Served TSM/tracker chunks contain the new selection controls, assignment warning and Greek labels.
- Deployed TSM calculation checks passed in a time-limited read-only transaction; rollback completed. No interactive authenticated browser acceptance was run.

## TSM result

Using the screenshot's **584.39** target and **404.07** stop, the deployed helper reads the **October 22** snapshot and selects the November 20 **580 call** as the original target candidate. Its reason remains `two_sided_quote_required`. Of 76 supplied call rows, 27 pass the research quote/payoff screen. The three returned alternatives are:

| Call strike | Expiry | Snapshot credit per standard contract | Displayed spread |
|---|---|---:|---:|
| 480 | November 20, 2026 | $108 | 13.0% |
| 470 | November 20, 2026 | $177 | 8.5% |
| 460 | November 20, 2026 | $280 | 7.1% |

These are archived research comparisons before fees. They cap upside below the original stock target, so no substitution or assignment acceptance occurs automatically. Quotes, size, true deliverables, event dates and portfolio constraints require current verification before entry. These values do not establish the best trade today.

[New production evidence](evidence/2026-10-23-covered-call-candidates-deployment/) is separate from [synthetic regression evidence](evidence/2026-10-23-covered-call-candidates/). [Selection design and remaining work](2026-10-23-strategy-selection-review.md).
