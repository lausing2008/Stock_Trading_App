# Document join and coverage ledger follow-up — 2026-10-03

Reviewed local commit `badba021`. The targeted report suite passes: **31 tests**. Independently reproduced four gaps using disposable SQLite and the real query/classifier functions. No production access, deployment, browser verification, flag changes, or event repair performed.

Evidence script: [2026-10-03-document-join-followup.py](evidence/2026-10-03-document-join-followup.py). It prints defect observations; it is not an acceptance suite. The ledger reproduction exercises the classifier, not a live provider sync. SQLite establishes the selection and unique-constraint findings here, not production concurrency guarantees.

## Assessment

The cadence interpretation, explicit uncertainty inside the normal interval, legacy return preservation, and documented unimplemented ingestion are appropriate corrections. Building the document interface before ingestion is reasonable if treated as a partial foundation. However, the claims of period identity, confirmed missing events, revision support, and successful coverage watermarking exceed what the current implementation enforces.

## DJ-01 — P1: document matching is proximity, not fiscal identity

`documents_for_period()` in `services/research-engine/src/intel_reports/documents.py` uses a ±75-day range even when `period_end` is supplied. `post_earnings()` supplies `period_end=None`, so it matches around the announcement date. `official_release()` selects the newest publication and labels the match “source-confirmed fiscal period end” merely because that document has a period end.

Reproduction: an event on May 15, Q1 document ending March 31 published May 15, and Q2 document ending June 30 published July 20. The real query selects **Q2** for the May event. Both periods fit its window. The document can accurately identify its own period while being attached to the wrong report.

**Fix:** separate candidate discovery from confirmed linkage. Exact canonical issuer/period identity must establish the link; a proximity-only match remains ambiguous and cannot populate authoritative period figures. Filter document types appropriately rather than treating the latest transcript, presentation or other document as the results release. Test neighbouring quarters, delayed filings, annual and quarterly documents, and ambiguous candidates.

## DJ-02 — P1: missing-event confirmation lacks temporal and identity enforcement

`confirm_missing_event(..., now=...)` never uses `now`. It selects documents with any non-null period end without checking publication/retrieval cutoff, document type, or fiscal-source confirmation. It decides an event exists by finding any announcement within 75 days after period end. That is another proximity test, not proof that the table contains the same fiscal event.

Reproduction: a document published January 20, 2027 is counted as a confirmed missing event in an October 3, 2026 assessment. The report attachment query also has no cutoff parameter, so later document revisions can enter a historical report.

**Fix:** require a cutoff for every document query; distinguish publication from platform availability and keep unknown timestamps ineligible for point-in-time certification. Require an appropriate authoritative release and validated issuer/period identity. An unresolved event match must remain `unknown` or `suspected`, not confirmed. Test future publications, late retrievals, same-issuer unrelated documents and an existing event with unresolved fiscal identity.

Additional source-inspection concern: confirmation returns citations to all orphan documents, but the generator records evidence only for the selected primary document. Ensure every confirmed-gap citation is added to the evidence book; test saving a report whose missing-period document differs from the older report's attached document. This integration case was not executed in this review.

## DJ-03 — P1: ledger success does not establish successful historical coverage

`_coverage_outcome()` in `services/event-intelligence/src/services/earnings.py` returns `ok` for any positive aggregate write count after a nonempty history response. It ignores mapped counts and drops. The underlying fetch combines history and calendar writes, catches history failures internally, and increments historical writes before commit. `rows_mapped` is initialized but not populated. The classifier never produces `partial` or `write_failed` despite those documented states.

Reproduction: four historical rows returned, zero mapped, four mapping drops, and one aggregate write → **`ok`**. A calendar write can therefore hide unsuccessful historical processing. `_record_coverage_attempt()` assigns today's date as watermark for this result, with no requested window supplied. A ledger commit proves that the ledger row persisted, not that history coverage succeeded.

**Fix:** return separate history/calendar stage results from the fetcher. Record errors where caught, count only committed writes, and reconcile returned rows into mapped/rejected/unchanged categories with reasons. Persist attempted window and provider coverage limitations. Never advance a historical coverage watermark from calendar success or an unresolved partial history result. A zero-row provider response may be a successful request without establishing complete coverage through today. Test history failure plus calendar success, partial mapping, failed commit, empty response, and ledger-write failure.

## DJ-04 — P2: same-URL corrections cannot be stored as new revisions

`IssuerDocument` has a unique `(stock_id, source_url)` index. A corrected document published at the same URL cannot become a new row linked by `supersedes_id`.

Reproduction: inserting such a revision raises **`IntegrityError`**. This contradicts the stated immutable-revision design for a common source correction pattern.

**Fix:** separate stable source identity from immutable document versions. Use a content/version identity to deduplicate identical retrievals while permitting changed bytes at the same URL. Preserve original content and extraction-version lineage for frozen citations. Test repeated identical retrieval, changed content at the same URL, and concurrent ingestion on PostgreSQL.

## Next implementation order

1. Fix document identity/cutoff and ledger classification before enabling document ingestion or trusting watermarks.
2. Implement absent-event discovery independently of existing pending rows, with overlapping reconciliation and explicit provider coverage limits.
3. Prepare a bounded MU repair preview from authoritative evidence; keep historical repair separate from notification and trading triggers. Preserve frozen reports and emit corrected versions.
4. Verify the browser and then build the richer content/narration slice against these enforced contracts.

`issuer_documents` being empty in production is reported by Claude, not independently verified here. If accurate, the document defects are latent until population; that makes this the right time to fix them. The coverage ledger already being wired is not equivalent to its measurements being correct. No profitability or signal-accuracy conclusion follows from these changes.
