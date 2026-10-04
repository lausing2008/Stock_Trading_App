# Discovery and MU repair semantic review — 2026-10-03

Reviewed source at `27f1ec1e`. Ran the discovery suite: **18 passed**. Independently executed the actual repair against disposable SQLite with a stubbed provider, and the actual discovery and return functions. No production access or mutation occurred. Deployment, zero drift and the production MU write remain Claude's reported observations.

Evidence: [discovery-repair-review.py](evidence/2026-10-03-discovery-repair-review.py). It prints defect witnesses rather than serving as a regression gate; it uses no real provider. Existing report-probe bootstrap supplies the disposable schema.

## DR-01 — P1: repair substitutes period end for announcement date without persistent provenance

`earnings_discovery.repair()` inserts `EarningsEvent(report_date=period_end, ...)`. The explanation of the substitution is a string on the returned repair plan. It is not persisted with the event. The current event schema has no date-basis or announcement-unknown field; clearing fiscal labels does not convey this distinction to consumers.

The real local repair inserted `report_date=2026-08-31`. Its record cannot tell a later reader that this is a provider period end rather than an announcement date. This contradicts the docstring's claim that recording the substitution prevents downstream confusion.

Concrete consequences:

- The report presents August 31 as the event date for a release reported in this conversation as September 30.
- Return backfill uses `report_date` directly. A fixture with August 28/31/September 1 prices 100/110/121 returns **21%** from the real producer before the September 30 announcement even occurs. Correct price arithmetic does not make that an earnings reaction.
- The “34 days old, therefore outside the email window” safety argument uses the substituted date. On October 3, a September 30 announcement is about three calendar days old. Notification suppression must be explicit for historical imports rather than depend on a false event age.
- Repeating discovery with nothing planned establishes stability under its own matching rule, not correctness of the repaired identity.

**Recommended containment:** do not extend this repair to other issuers or enable its scheduling until date semantics are enforced. Inspect the production row and all report/return artifacts derived from it before correction. Do not delete or silently rewrite frozen reports. This review does not establish that incorrect derived returns already exist in production.

**Solution:** store provider period identity separately from authoritative fiscal period and announcement date/time, with source and reconciliation status. If announcement timing is unknown, keep the observation staged/unresolved and make announcement-dependent consumers abstain. For MU, prepare a correction from an authoritative release, preserve aliases/audit lineage, invalidate any affected derived outcomes, and issue corrected report versions. Enforce historical-repair notification/trading suppression independently of event age or mutable surprise fields.

## DR-02 — P1: discovery still promotes temporal candidates to confirmed presence

`discover()` now finds any stored date between a provider period end and the next period end, excluding future dates for rows with actual EPS. This remains a proximity rule. It reads only dates, so it cannot establish fiscal identity, distinguish a stale pending event from a reported result, or identify multiple competing matches.

Reproduction: provider period August 31 with reported EPS, and a September 15 stored event with **no actual EPS**, returns `present=1`. It proves only that a dated event exists in the interval. It does not establish that the actual result has been ingested. A delayed earlier-period release in that interval would likewise be a candidate, not proof of the requested period's presence.

**Solution:** distinguish `candidate_found`, `association_confirmed`, `actuals_missing`, `unresolved` and `confirmed_absent` after explicit resolution. Load event IDs, provenance and period associations; do not decide presence from dates alone. A missing `eps_actual` is not proof of a scheduled future event either—it may be a released result with incomplete provider coverage. Add behavioural tests for late previous-period announcements, pending events in the interval, multiple matches, missing EPS with other reported figures, and concurrent/idempotent real database writes.

## Ledger improvement and remaining meaning

Unknown request bounds now remain NULL and the watermark comes from `history_newest_written`, addressing the previous processing-date substitution. However, the maximum written date is **latest observed progress**, not evidence of continuous historical coverage. Holes can exist before that maximum. Keep discovery overlap and reconciliation independent of it; avoid calling it a completeness watermark. The date is also not semantically consistent if some written `report_date` values are announcement dates and others are substituted period ends.

## Recommended next actions

1. Treat DR-01 as a correction to the current MU repair, not an optional enhancement. Prepare a read-only manifest of the event, provenance, reports and derived returns affected.
2. Add explicit date semantics and historical-import policy; correct MU only from verified source identity and timing, with a reviewable repair plan.
3. Replace date-only discovery verdicts with evidence-backed associations and unresolved states. Validate provider period labels against issuer documents; matching EPS alone does not validate period dates, units or accounting basis.
4. Keep browser acceptance open, but do not let it distract from incorrect event dates. Run it in parallel once the report fixtures have trustworthy identities.
5. Schedule discovery and add narration only after these boundaries hold.

No code, production event, email, flag or trading state was changed during this review. Only this document and a local reproduction script were added. CLAUDE.md was not modified.

## Follow-up on `e9feee30`

The discovery tests now pass **24 tests** locally. Source inspection confirms `period_end` and `report_date_source` are persisted, repaired stand-ins carry `substituted_period_end`, and return backfill excludes that marker. This addresses the specific previously reproduced return-anchor defect. Claude reports the production inventory found no derived returns or sent impact and one report snapshot; this review did not independently inspect production. That snapshot still contains the earlier semantics and should retain an explicit correction link rather than be silently rewritten.

Two qualifications remain:

1. **DR-02 is narrowed, not closed.** Discovery still takes the first event in the period-to-next-period interval, now requiring some actual result for `present`. A result for another fiscal period can still match. More importantly, the new placeholder-fill path chooses the first empty event in that interval and writes EPS and period identity into it, without an evidence-backed association. Do not extend automatic repair until ambiguous candidates abstain and an authoritative association is required before updating an existing event. Test multiple candidates, an earlier-period delayed result, and a placeholder belonging to another period. These are source-level findings in this follow-up, not newly executed production witnesses.
2. **Suppressed is not sent.** Both insert and placeholder-fill branches stamp `impact_sent_at` despite no notification. This can gate the existing impact-email path, but fabricates a delivery timestamp. Introduce an explicit historical-import suppression status/reason/actor/time and enforce it in relevant notification consumers, leaving actual delivery evidence untouched. Do not simply clear the existing timestamp before that guard exists, because doing so may re-enable replay. Its presence alone does not establish that every alert family respects suppression.

The `FIRST_FLASH` shape fix and unknown-announcement presentation are appropriate. Strings in a served bundle do not establish the rendering branch is reached; inspect the actual response fields, selected report version, DOM grouping, event-selector interaction and narrow viewport behavior. Browser verification remains open. Keep discovery/repair expansion and narration gated on the remaining identity semantics; browser verification can proceed independently.
