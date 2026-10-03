# Earnings coverage remediation review — 2026-10-03

## Scope and conclusion

Reviewed the local implementation through `5e72235a` and Claude's deployment account. Independently ran the report tests: **26 passed** (`python -m pytest -q --noconftest services/research-engine/tests/test_intelligence_reports.py`). This review did not independently inspect production, run PostgreSQL contention tests, or verify the browser. Deployment and production measurements below remain attributed to Claude.

The fixes improve correctness: fractional returns convert at the report adapter, reused reports compare against their predecessor, bar availability is no longer fabricated, and release-date boundaries use exchange-local time. Reporting a missing event instead of inventing one is appropriate. The source ingestion gap should be the next implementation priority, before richer narration.

## Remaining qualifications

### 1. Cadence detects suspected gaps, not confirmed missing releases

`_event_coverage_warning()` in `services/research-engine/src/intel_reports/generators.py` labels an event `CONFLICTING` and `OBSERVED_FACT` when its age exceeds the issuer's median reporting interval. Its reason asserts the next release is due or overdue and not on file.

That conclusion is stronger than the calculation. The median is not a reporting deadline; normal variation can exceed it. The helper reads stored event dates, not only source-confirmed releases, and missing/duplicate events can distort its gaps. Its 95-day fallback is explicitly an assumption. A future scheduled date is shown but does not affect the warning.

**Recommendation:** keep the warning but classify it as an interpretation: “Possible coverage gap: latest stored release is older than the historical median; verify the issuer's release calendar.” Separate `suspected_gap`, `confirmed_missing_event`, and `coverage_unknown`. Promote to confirmed only when a dated authoritative release or equivalent reconciled evidence identifies an absent canonical event. An absence of warning must not certify complete coverage. Test an ordinary interval longer than the median, sparse history, duplicate dates, semiannual reporters, and a source-confirmed missing release.

Claude's MU database trace and the generic cadence rule are different evidence: the former can identify a particular missing event when paired with its release document; the latter alone cannot.

### 2. Return scaling is fixed; window labels remain too short

The producer `_compute_post_earnings_returns()` in `services/event-intelligence/src/services/earnings.py` uses the last close before the report date as baseline and `after[1]` / `after[5]` as endpoints, where `after` includes the report-date bar. The report adapter still labels these `1 session` / `5 sessions`; the historical summary likewise says `1-session`.

For a trading-day release, these span **two / six close-to-close intervals**, respectively. A direct execution of the extracted production function with September 29 close 100, September 30 close 110, and October 1 close 121 returns `0.21` for `return_1d`: 21% across two intervals, not either one-session return of 10%.

**Recommendation:** preserve the legacy values, but display exact baseline/end dates and label the endpoint convention explicitly. Do not silently redefine historical outcomes. Add separately versioned release-aligned outcomes once announcement timing is known. Before-open, after-close, nontrading-date releases and missing bars need distinct acceptance cases. The new basis note helps, but does not fully repair the current window labels.

### 3. Unknown availability is honest, not proof of point-in-time eligibility

`record_bar()` now records `first_available_at=None` and an explicit limitation. Correct. A historical/pre-release evaluation must still refuse to treat that bar as proven available solely because its observation date precedes a cutoff. Persist ingestion/availability evidence prospectively; distinguish report generation cutoff from proven availability coverage.

### 4. The missing MU event is established in the reported trace; ingestion root cause needs branch evidence

The scheduler registers a daily `sync_all_earnings()`, which calls `_fetch_earnings_for_symbol()` for tracked symbols; that fetcher includes historical retrieval. Therefore an old last stored historical event does not by itself prove the history job never ran. Provider responses, swallowed branch errors, matching, attempted upserts and committed rows must be correlated before assigning the precise cause.

Also, `sync_todays_earnings()` selects existing recent pending events. An event absent from the table cannot enter that candidate set. Faster polling of that set alone cannot repair the missing-event case.

## Recommended next implementation slice

1. **Repair source coverage.** Capture per-symbol history/calendar attempt and success timestamps, provider row counts, mapping results, write results and error reasons. Use an overlapping historical reconciliation window and advance a successful watermark only after committed processing. Measure coverage independently of existing pending-event rows.
2. **Repair MU with evidence and bounded replay.** Preview the official release-to-event mapping, fiscal period, announcement timestamp, accounting basis and units. Use idempotent upserts, preserve previously frozen reports, and create corrected versions. Historical repair must not automatically send old alerts or trigger trades; apply existing delivery/cutover policy explicitly.
3. **Attach official documents to canonical events.** Store issuer identity, fiscal period/end, document type, publication/retrieval times, content hash, revision lineage and field-level citations. Guidance needs its own target period. Corrections and conflicting sources remain visible. A document join must not depend exclusively on an event row that may itself be missing.
4. **Complete browser verification.** Exercise all four report types, nested tables, unknown/conflicting states, reused-version diffs, narrow viewports and export. Unit tests do not establish usable rendering.
5. **Build the richer earnings layout and optional narration.** Use the existing evidence-grounded narration design. Facts/calculations stay deterministic; interpretations cite evidence and retain missingness. No inference that a double beat proves organic growth, cash flow above net income proves earnings quality, or a price decline proves expectations were priced in.

Acceptance should include missing-event discovery despite zero pending rows, repeated ingestion producing one event, provider correction without rewriting frozen baselines, mapping failure remaining retryable and visible, no historical email replay, and a complete release-document-to-report evidence chain.

## Status

Report fixes: locally reviewed with the targeted suite passing. Production deployment and zero drift: reported by Claude, not reverified here. Browser verification and source-ingestion repair: open. Cadence semantics and precise return-window labeling: follow-up corrections recommended. No application code, production data, flags, emails or trading state changed by this review.
