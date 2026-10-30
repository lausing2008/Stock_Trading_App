# Candidate qualification release

Runtime revision: `66e4c7eec8206dc725a15c846d9ec279734276e0`. This integrates the report change `55dae269` with the concurrently released production changes, preserving them. Only research-engine and frontend were rebuilt, using the existing deployment lock, rollback image tags and health verification.

[Design and implementation](../features/2026-10-29-valuation-candidate-qualification.md) document the new issuer review, qualification contract and remaining research limits.

- **Production report 55 / v4**, completed price session October 29, 2026: 60 companies, 38 outside the tracked set, one reviewed issuer (ADBE), zero qualified opportunities. An empty qualified list is the supported conclusion; this release does not pre-approve a stock.
- Adobe’s source-linked review includes counterevidence, thesis invalidation and monitoring questions. Its separate owner-cash sensitivity produces $173.99 / $247.84 / $318.67 under the disclosed assumptions; it does not replace the provider screen or constitute a calibrated fair value.
- All 1,001 frontend tests, 180 selected research tests and frontend typecheck passed. After merging concurrent production changes, 38 report/qualification/isolation checks passed.
- Live API acceptance verified authentication, generation, immutable reads and history. Desktop 1440px and mobile 390px acceptance verified reviewed filtering, source links, sensitivity and invalidation content, CSV export and the empty qualified list. No runtime errors or horizontal page overflow occurred.
- Final rollout manifest: all 15 containers healthy. At integrated revision `66e4c7ee`, all 12 backend source trees matched production. Subsequent unrelated production commits are outside this release record.

Evidence: [release summary](evidence/2026-10-29-valuation-qualification/release-summary.json), [API summary](evidence/2026-10-29-valuation-qualification/api-summary.json), [review and browser acceptance](evidence/2026-10-29-valuation-qualification/qualification-summary.json), [container manifest](evidence/2026-10-29-valuation-qualification/containers-after.json), [source parity](evidence/2026-10-29-valuation-qualification/drift.txt), [rollout summary](evidence/2026-10-29-valuation-qualification/deploy-summary.txt).

The full provider payload and screenshots are retained locally and excluded from git. Short-lived acceptance credentials stayed in memory.
