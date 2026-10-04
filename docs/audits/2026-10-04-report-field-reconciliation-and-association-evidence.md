# Report field reconciliation, association evidence, and the screenshot discrepancy closed

Date: October 4, 2026. Branch `prod`. Inputs: the association review (4 gaps), the screenshot
review (5 P1/P2 contradictions), and the unresolved question of why screenshots labelled
"Contract v3 · policy 3" showed none of the v3 presentation.

## 1. The screenshot discrepancy — resolved, and my earlier explanation was wrong

I previously attributed the missing sections to the user viewing v2 snapshots. That was wrong.
Matching each screenshot's generation timestamp to its stored payload settles it:

| Report | Subject | Generated | Contract/policy | Fields carrying `section`/`timeframe` |
|---|---|---|---|---|
| #8  | `market:US` | 2026-10-03 20:45:46 | 3 / 3 | all |
| #9  | `stock:MU` | 2026-10-03 20:46:26 | 3 / 3 | all |
| #10 | `earnings:MU:2026-12-23` | 2026-10-03 20:46:44 | 3 / 3 | all |
| #11 | `earnings:MU:2026-08-31` | 2026-10-03 20:46:59 | 3 / 3 | **25 of 25** |

The payloads were never missing the metadata. The cause was the **served frontend bundle**: the
running image was built 2026-10-03T22:02:05Z, `groupFields` was absent from the chunks it
served, and the grouped rendering shipped in commit `a7bbd26c`. The post-`a7bbd26c` deploys had
either raced (the 502 incident) or been refused by the new deployment lock. The frontend was
rebuilt from `d054222d` after pruning 41.65 GB of dangling images (EC2 was at 99%).

**The lesson that generalises:** my earlier verification — "the expected strings are in the
bundle" — was not evidence. Grepping a built artifact for a string it may carry in a build cache
cannot establish which code the browser executes. The decisive check was the *absence* of
`groupFields` from the served chunks plus the image build time against the commit date.

## 2. P1 content contradictions — one report was giving two answers

Confirmed directly in the stored payloads of #12 and #15, not inferred from screenshots:

| Field | Said | While, in the same report |
|---|---|---|
| `revenue_actual` | UNAVAILABLE | `official_figures.revenue` = $54.23B GAAP |
| `fiscal_period` | UNKNOWN, "no source-confirmed period is stored" | `source_confirmed_fiscal_period` = FY2026 Q4, ended 2026-09-03 |
| `accounting_basis` | UNKNOWN, "the platform does not store the basis" | every official figure carries its own GAAP/non-GAAP basis |
| `guidance_change` | UNAVAILABLE, "guidance is not assembled here" | `official_figures` carries Q1 revenue and EPS guidance |

The release had been *attached* but never *reconciled*. `documents.reconcile_into_metrics()` now
promotes source-backed figures into the primary metrics, and `confirmed_fiscal_period()` answers
`fiscal_period` once a document confirms it.

Three rules the reconciliation obeys, each closing a way this could go wrong:

- **Units are not assumed to match.** The stored estimate carries no units and no accounting
  basis. The surprise is therefore **not** recomputed against the issuer's figure — it keeps
  saying which actual produced it. Dividing an unknown-unit estimate into a sourced dollar
  figure is how a 100× error gets printed as a percentage.
- **A provider disagreement is preserved, not overwritten.** Both figures are shown, the
  issuer's marked authoritative. A silent overwrite destroys the evidence they ever disagreed.
- **Current guidance ≠ a raise.** `guidance_current` is OK (the company said it). `guidance_change`
  stays UNKNOWN: classifying raised/maintained/lowered needs the prior guidance for the *same*
  target period on the *same* basis, which is not stored. The previous quarter's guidance for a
  different quarter is not that comparison.

`guidance_q1_revenue` and `revenue` both contain "revenue" and only one is the reported actual —
the fact→metric map is explicit, never a substring match. A test asserts the completed quarter's
revenue does not appear under guidance; it caught exactly that when sabotage-checked.

## 3. The reaction window is named, not implied

+3.03% is the **2026-09-29 close to 2026-10-01 close** return — two close-to-close intervals,
including pre-announcement trading. The value already carried its window; the headline label did
not. Labels are now "Share price, close-to-close across the release", and the executive verdict
carries the window and the caveat rather than the bare number.

## 4. Durable association evidence — four gaps closed

- **Rationale now persisted.** `IssuerDocument.association` stores actor, rationale, timestamp,
  and the event's before/after values. Previously this existed only as a log line, so months
  later the event row asserted a corrected identity with no record of who asserted it or what it
  replaced.
- **The hash says what it covers.** `content_hash` held a digest of the *extracted facts* while
  being documented as byte identity — a claim the value could not support. Now `facts_hash`
  (our extraction) and `source_bytes_hash` (the retrieved document) are separate, both prefixed
  with what they cover, and a row without bytes says plainly that source-edit detection is
  **not available** for it.
- **Announcement dating is timezone-explicit.** `published_at.date()` on a naive-UTC stamp puts
  any US after-close release on the *next* calendar day — one session off, in the single field
  the whole reaction window is measured from. `_announcement_date()` converts to the issuer's
  exchange timezone and shows the conversion, including the UTC date it differs from.
- **Figure validation enforces units, period and a field-level citation.** A figure without them
  is refused at ingestion: $54.23B and 54230 are the same revenue in different units, a figure
  for another period is a different fact, and a citation is what makes *one* number checkable
  rather than handing a reader the whole release.

MU's stored document predates the citation requirement; the rule binds new ingestion. Its hash
is relabelled by `scripts/relabel_issuer_document_hashes.py` (one-off, previews by default —
deliberately not a startup migration, which is its own incident class).

## 5. Cross-subject correction pointer (#11 → #12)

`supersedes_id` can only link versions of one subject. Report #11 (`earnings:MU:2026-08-31`)
and #12 (`earnings:MU:2026-09-30`) have different subject keys, so #12 is version 1 of its own
subject and nothing connected the two — a reader of #11 had no way to learn it was wrong.

`IntelligenceReport.corrected_by_id` + `correction` record the pointer and the reason. The
superseded payload is **never edited**: a frozen report whose contents can change is not
evidence of what was known at the time. Set by `scripts/record_report_correction.py`, which
previews by default and refuses a self-correction, a different issuer, a same-subject pair
(that is a version) and a correcting report older than the one it corrects. The page shows it
in a distinct, louder banner than the version banner — a later version refines the same event;
this says the report in front of you is about the wrong event entirely.

## Tests

- `services/research-engine/tests/test_report_reconciliation.py` — 11 new tests.
- `services/event-intelligence/tests/test_issuer_documents.py` — 15 tests (6 new).
- Sabotage-verified: disabling conflict preservation and widening the guidance filter each fail
  their owning test. The guidance test initially passed under sabotage and was tightened.
- Full suites: event-intelligence 624 passed, research-engine 132 passed.

## Still open

- Narration stays unbuilt until fields are consistent in the browser, per the user's priority.
- N1 packets/validators remain offline work.
- Repair expansion to other issuers stays paused.
- Reports #5/#7 (`earnings:MU:2026-06-24`) present June as the latest results. They are
  candidates for the same correction pointer; not recorded without a decision.
