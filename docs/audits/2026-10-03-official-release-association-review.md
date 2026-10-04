# Official release association review — 2026-10-03

Scope: local source at `d054222d`, principally `services/event-intelligence/src/services/issuer_documents.py`; its **8 tests pass**. Production report #12, row correction, suppression, drift and deployment locking are Claude's reported evidence, not independently verified in this review. The findings below are source inspection, not assertions that MU's current figures are wrong.

## Progress accepted

An explicit issuer/provider discrepancy, official document attachment, separate accounting bases and retained replay suppression are substantial improvements over substituting an unmarked period end. A sourced announcement date makes a date-based return window possible. The quoted September 29–October 1 window must still be called a two-interval close-to-close return spanning the release; it includes pre-announcement trading and is not an isolated announcement reaction or a one-session return.

## Remaining implementation gaps

1. **Association rationale is not durably recorded.** `associate_with_event()` includes actor, rationale and before/after changes in its returned plan, but its commit updates only the document link and event fields. Its log records actor and announcement date, not rationale or the change set. A separately archived manual preview might document this particular action, but the function does not guarantee that history. Persist an append-only association/correction record in the same transaction, including source document/version, actor, rationale and old/new values. Require nonempty actor/rationale and protect against changes between preview and commit.

2. **The content hash is an extracted-facts hash, not a release-byte hash.** Ingestion calls `content_hash(facts)`. Changes to publication time, period identity, source text or commentary outside `facts` can therefore be treated as an identical document. New versions also do not set `supersedes_id`. Separate immutable source bytes/hash from extracted facts/hash and extraction version; preserve metadata corrections with explicit version lineage. At minimum label the existing hash honestly and do not claim source-byte identity.

3. **Publication date is assumed to be announcement date.** Association uses `doc.published_at.date()` without exchange-timezone conversion or distinction between initial results, later correction and SEC exhibit publication. This can shift the trading date across midnight or move an event when a later document is associated. Require an explicit sourced initial announcement instant/date and date basis; convert the instant to the issuer's market calendar. Later documents attach to the event without automatically changing its announcement anchor. Test HK/US midnight boundaries and later revisions. Nothing here establishes that MU's specific September 30 anchor is wrong.

4. **Figure validation is narrower than documented.** `ingest_release()` requires keys `value` and `basis`, but not nonempty basis, units, period, or field-level citation. A fact with an empty basis and no units passes. Document-level evidence identifies the release, but is not a locator for an individual table cell or statement. Add typed figure validation with source locator and comparative period before expanding ingestion or grounding narration in these facts.

## Report corrections and browser acceptance

The acknowledged report #11 orphan requires a cross-subject correction relation; changing an event date must not hide the correction from someone opening the old report. Resolve both directions and retain original payloads. Explain whether the correction changes event identity, computations or only presentation. Include #11 → #12 in browser acceptance, alongside temporal grouping, event selection and suppression of unknown-date reactions.

The latest explanation of the earlier screenshots is not yet reconciled: their visible metadata included `Contract v3 · policy 3`. Identifying some v2 records does not establish that they are the exact snapshots shown. Match report ID, generation timestamp, payload contract and field metadata to each capture. Treat the mismatch as unresolved until those agree, not as proof of a stale browser or user misreading.

## Next bounded slice

Fix durable association and document-version provenance alongside the cross-subject correction pointer; browser verification can proceed in parallel. Expose current guidance as a sourced range for its own target period without claiming it was raised. A prior quarter's release is useful context, but “raised” requires the previous comparable guidance for the **same target period and basis**, not just whichever release came before. Keep mutable consensus clearly distinct from a pre-release frozen estimate.

N1 packet construction and offline validators can proceed as engineering work; user-facing narration remains gated on the relevant facts and structured prerequisites. No application code, production records, flags, notifications or CLAUDE.md were changed during this review.
