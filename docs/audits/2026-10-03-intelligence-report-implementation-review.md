# Intelligence reports implementation review — October 3, 2026

Baseline: `59d0f28e`, following `2ae0381a` and the container-import fix. This reviews the reported deployment; it does not independently verify the production site, drift, delivery or browser rendering.

**Verdict:** sensible placement and useful report scaffolding, but the live feature does not yet enforce several central report guarantees. Fix baseline identity/time, evidence lineage, horizon semantics and persistence before adding more narrative or treating its outlooks as decision-quality analysis.

Evidence: [local behavioral probe](evidence/2026-10-03-intelligence-review.py) and [results](evidence/2026-10-03-intelligence-review.json). The probe uses the existing fixture/bootstrap in an explicitly temporary SQLite database, real generators/store and an AST-extracted API `generate` function. No provider, production database, email or order is contacted. It leaves the historical acceptance probe unchanged. Residual assertions are defect witnesses, not release acceptance requirements.

Existing targeted suite: `python -m pytest -q --noconftest services/research-engine/tests/test_intelligence_reports.py` — **13 passed**, using the local SQLite fallback. This is not a full-suite or PostgreSQL verification claim.

## Findings

### IR-01 — API admits a post-release report as a frozen pre-release baseline (P1)

`services/research-engine/src/api/intelligence_routes.py::generate` calls `frozen_pre_report(before=probe.meta.cutoff_at)`. That cutoff is the current post-report generation time, **not the release time**. Requiring both baseline timestamps to precede this later instant cannot prevent hindsight.

`pre_earnings()` selects events using `report_date >= now.date()` without a release-status/time guard. Thus an event that released earlier the same day can still produce a report labeled pre-earnings, even when actuals are already present.

**Executed:** modeled release at 18:00, pre-report generated at 19:00, post-report at 20:00, same date. The store correctly rejects the baseline when given 18:00; the actual API function selects it using 20:00.

**Fix:** bind to a canonical event's evidenced release instant and reject baselines generated or cut off at/after it. If release timing cannot be established, do not certify a baseline as pre-release. A conservative earlier-date baseline may be considered only under an explicit documented policy. Block new pre-report generation once the release is known; preserve reconstructions as separately labeled research. Test early releases, same-day generation, timezones, reschedules and the real API path.

### IR-02 — The evidence contract is declared but not populated or enforced (P1)

All four generators return an empty evidence list. Some fields carry IDs such as `price:...` and `signal:...`, but those IDs have no matching records in the saved report evidence. Many derived fields have no IDs at all. A source table ID without a preserved revision/value is also insufficient when that row changes.

`daily_bars()` and `latest_signal()` select latest data without a report cutoff or first-availability filter. The contract's timestamp fields do not implement that restriction. **Executed:** a stock report with a September 25 cutoff consumes the October 2 closing price and returns zero evidence records. The API currently uses current time rather than exposing an arbitrary historical cutoff, so this demonstrates the generator/replay boundary defect, not an externally accessible historical-report endpoint.

Further, stale `price_as_of` does not degrade `trend_structure`, horizon fields or breadth derived from those same bars. Calendar-day age is described as “sessions old.” Counting stored bars does not establish exchange sessions when bars are missing or duplicated, and the acceptance fixtures themselves generate weekend daily bars.

**Fix:** populate evidence records and validate every referenced ID; preserve the actual observations and revisions used. Enforce completed-session, observation-time and availability-time rules at input selection. Propagate required-input freshness to derived conclusions. Distinguish stale, missing and historical data from present evidence. Use session-aware tests with missing sessions, partial bars and revisions. Do not claim look-ahead prevention from a schema alone.

### IR-03 — Three horizons are the same rule, and bearish invalidation is reversed (P2)

`generators.py::_outlook_by_horizon` repeats the same latest-close-versus-SMA20 description across all three horizons; only labels/session ranges change. The `signal` argument is unused. Identical outcomes can be legitimate, but no horizon-specific evaluation occurs here.

For a below-SMA20 fixture, the report says “unconstructive while it remains below” while giving “close holds above” as its key condition and “a close below” as its invalidation. The supposed invalidation is already the condition supporting the bearish description.

**Fix:** reverse confirmation/invalidation appropriately for bearish states. Until independently defined horizon rules/data exist, render a single observed daily structure and mark unsupported horizon outlooks unavailable. Do not populate three confident-looking fields by copying one heuristic. Add divergence fixtures where weekly/daily/intraday or short/long evidence differs.

### IR-04 — Frozen and current consensus produce contradictory report verdicts (P2)

The frozen baseline is used in `_verdict`, but the main EPS/revenue surprise fields are built by `earnings_actuals(event)` from the mutable current estimate.

**Executed:** frozen EPS 1.50; later stored estimate 1.95; actual 1.72. The same post-report shows approximately **−11.79%** surprise and `result_vs_frozen='above'`. This discrepancy is independent of IR-01: even a genuinely pre-release baseline would encounter the two-denominator problem.

The code also computes surprise while explicitly acknowledging that accounting basis is unknown, and calls results `RECONCILED_RESULTS` if either EPS or revenue actual merely exists. `_verdict` labels a beat-plus-positive-return “confirmed” without comparing an actual recorded thesis; it can award confirmation to a report that made no directional claim.

**Fix:** use the same frozen, comparable-period/basis expectations in the headline surprise table. Display a later estimate separately, clearly dated. Unverified GAAP/adjusted comparability should not become a validated beat/miss merely because a caveat exists elsewhere. Reserve “reconciled” for completed reconciliation. Evaluate explicit frozen predictions/scenario conditions; otherwise report factual comparisons and `thesis not evaluable`.

### IR-05 — Transactional version derivation does not ensure concurrent uniqueness (P2, source-inspected)

`store.save()` checks for an existing fingerprint, reads latest version, increments and inserts. There is no per-subject lock or unique constraint on version/fingerprint. The indexes in `IntelligenceReport.__table_args__` are nonunique. Two concurrent requests can read the same prior state and both proceed. Sharing a transaction does not prevent this interleaving.

The eleven acceptance scenarios check sequential idempotence/versioning, not concurrent generation. Running those scenarios on PostgreSQL does not turn them into concurrency tests.

**Fix:** use a concurrency-safe identity/version allocation with database constraints and explicit conflict handling. Include report type, ownership scope and canonical event/subject in the appropriate identities; account for NULL public ownership. Fingerprints must include meaning-bearing state/reasons, evidence revisions, policy/contract versions and baseline identity. Currently generators hash only values of OK fields, so changes to unavailable reasons or unincorporated versions need not produce a new report. Do not silently re-serve a superseded historical row as the latest report when inputs recur.

**Acceptance:** barrier-controlled same-input and different-input concurrent PostgreSQL generations; one identity for identical inputs, unique ordered versions for revisions, correct supersedes links and no cross-user reuse. This review did not run those races.

## Corrections to the completion narrative

- The decision to omit unvalidated probabilities is correct. The quoted pooled “flat 36.5–43.9%” justification was already withdrawn as a pooling artifact in the earlier checkpoint review. Use the absence of demonstrated calibration for these particular horizons; do not restore the superseded statistical claim.
- A real market report proves that one generation/persistence path works in production. It does not verify all four types, rendered browser behavior, historical availability or forecast usefulness.
- Breadth `78/144 = 54.2%` describes symbols **above SMA20 among covered symbols**, if those are `above_sma20` and `covered`. Coverage is `covered/universe`, a different ratio. Preserve both denominators.
- `decision_engine_assessment` actually reads the `Signal` table, not an authoritative decision/risk evaluation. Rename it to the existing signal assessment and retain its actual source/horizon. Do not imply portfolio eligibility.
- “No macro calendar is ingested” is too broad: the existing event-intelligence service has economic-calendar functionality. Report-adapter gaps should say “not joined/available to this report with the required evidence,” not assert platform-wide absence without an inventory.
- Leaving an untrusted inferred fiscal label unknown is appropriate. It should remain a source-specific limitation, with a route to source-confirmed issuer fiscal identity, rather than a permanent unknown for every issuer.

## Next slice, in order

1. Fix IR-01 and IR-02; add route-level post-release exclusion and complete evidence/cutoff tests.
2. Fix bearish conditions and contradictory earnings comparisons; label report stage/thesis evaluation honestly.
3. Make persistence concurrency-safe and verify rendered reports, state changes, evidence references and before/after comparison in the browser.
4. Then join source-confirmed fiscal/release identity, frozen consensus metadata, company guidance and news. Reuse existing macro/market sources with source-specific limitations.
5. Add richer horizon rules and prospective evaluation. LLM narration, scheduling and email can follow; none fixes missing evidence or wrong event boundaries.

The navigation choice under Reports is reasonable. Deterministic generation, named partial states and the lack of trading side effects are good foundations. Treat this release as a **partial reporting foundation**, not yet a validated market/stock/earnings intelligence product.

This review changes only its documentation/evidence files. No application code, deployment, flags, email, trading state or `CLAUDE.md` was changed.
