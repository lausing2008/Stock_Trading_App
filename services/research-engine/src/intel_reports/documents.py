"""Joining official issuer documents to a report — including when the event row is missing.

THE JOIN IS ISSUER + FISCAL PERIOD, NOT EVENT ID. A document keyed only to an event can never
describe a period whose event row does not exist, which is precisely the case that needs
describing: MU's 30 September release is real and has no event row. Resolving by issuer and
source-confirmed period means the document can be found, cited, and used to say that the event
is missing.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select

from db import EarningsEvent, IssuerDocument
from intelligence.report_contract import (
    Evidence, EvidenceBook, Field, FieldState, StatementClass,
    observed, unavailable, unknown,
)

#: NO WINDOW. Identity is exact or it is not identity.
#:
#: This was 75 days, then 45, and both were wrong in the same way: a proximity window is a guess
#: dressed as a match, and tuning it only moves which guesses go unnoticed. Two executed cases
#: settled it — asking for the April 30 period returned the March 31 document as
#: `confirmed_period`, and a genuine 55-day announcement lag made a correctly-stored event look
#: like a missing one.
#:
#: So a CONFIRMED link now requires exact source-confirmed fiscal identity, or an explicit
#: evidence-backed association. Everything else stays a candidate or stays unknown — which is
#: less useful and the only thing that is true.

#: How near a publication has to be to even be WORTH LISTING as a candidate. This bounds a
#: listing, never a match — nothing confirmed is derived from it.
_CANDIDATE_DAYS = 30

#: Only these document types report results. A transcript or a slide deck accompanies a release;
#: treating "the latest document" as the results release is how a presentation becomes the
#: authoritative source for period figures.
_RESULTS_DOCUMENT_TYPES = ("press_release", "sec_exhibit")


def _visible(q, *, cutoff: datetime):
    """Only documents this report could actually have held at its cutoff.

    EVERY DOCUMENT QUERY TAKES A CUTOFF. Without one, a January 2027 release counted towards an
    October 2026 assessment — a document from the future certifying a gap in the past. Both the
    publication and the retrieval must precede the cutoff: published later means it did not
    exist, retrieved later means we did not have it, and either one disqualifies it. A document
    with no publication time is ineligible for point-in-time use rather than assumed available.
    """
    return q.where(IssuerDocument.retrieved_at <= cutoff,
                   IssuerDocument.published_at.is_not(None),
                   IssuerDocument.published_at <= cutoff)


def documents_for_period(session, stock_id: int, *, period_end: date | None,
                         report_date: date | None, cutoff: datetime,
                         event_id: int | None = None,
                         limit: int = 10) -> tuple[list[IssuerDocument], str]:
    """Candidate results documents for one reporting period, and how confidently they matched.

    TWO DIFFERENT THINGS, DELIBERATELY SEPARATED. A CONFIRMED link needs the document's own
    source-confirmed `fiscal_period_end` to sit in the window a release can occupy relative to
    the event — at or before the report date, and no more than `_RELEASE_LAG_DAYS` before it.
    A proximity match on publication date alone is a CANDIDATE: it cannot populate authoritative
    period figures, because it cannot tell a late Q1 release from an early Q2 one.

    Returns `(documents, match_quality)` where quality is "confirmed_period" (exact fiscal
    identity), "confirmed_link" (an explicitly recorded association), "candidate_only" or
    "none" — so the caller never has to infer how much a match is worth.
    """
    base = select(IssuerDocument).where(
        IssuerDocument.stock_id == stock_id,
        IssuerDocument.document_type.in_(_RESULTS_DOCUMENT_TYPES))
    base = _visible(base, cutoff=cutoff)

    # CONFIRMED requires the document's own period to EQUAL the one asked for. A document
    # describing 31 March does not describe the quarter ending 30 April, however close the two
    # dates are.
    if period_end is not None:
        exact = list(session.execute(
            base.where(IssuerDocument.fiscal_period_end == period_end)
            .order_by(IssuerDocument.published_at.desc().nullslast())
            .limit(limit)).scalars().all())
        if exact:
            return exact, "confirmed_period"

    # CONFIRMED also when an association was recorded explicitly — a mapping step asserted this
    # document belongs to this event, which is evidence rather than arithmetic on dates.
    if event_id is not None:
        linked = list(session.execute(
            base.where(IssuerDocument.event_id == event_id)
            .order_by(IssuerDocument.published_at.desc().nullslast())
            .limit(limit)).scalars().all())
        if linked:
            return linked, "confirmed_link"

    # Anything else is at best a CANDIDATE. Documents published near the report date are listed
    # so a reader can see what exists, and they never populate period figures.
    anchor = period_end or report_date
    if anchor is None:
        return [], "none"
    lo = datetime.combine(anchor - timedelta(days=_CANDIDATE_DAYS), datetime.min.time())
    hi = datetime.combine(anchor + timedelta(days=_CANDIDATE_DAYS), datetime.max.time())
    nearby = list(session.execute(
        base.where(IssuerDocument.published_at >= lo, IssuerDocument.published_at <= hi)
        .order_by(IssuerDocument.published_at.desc()).limit(limit)).scalars().all())
    return (nearby, "candidate_only") if nearby else ([], "none")


def record_document(book: EvidenceBook, doc: IssuerDocument) -> str:
    """An evidence record for the document itself, with its own times kept apart."""
    return book.add(Evidence(
        evidence_id=f"issuer_document:{doc.id}",
        source=doc.source_url,
        value={"type": doc.document_type, "title": doc.title, "publisher": doc.publisher,
               "fiscal_label": doc.fiscal_label, "fiscal_source": doc.fiscal_source,
               "content_hash": doc.content_hash},
        observed_period=doc.fiscal_period_end.isoformat() if doc.fiscal_period_end else None,
        published_at=doc.published_at,
        # The issuer published it; we held it from retrieval. For a document these ARE knowable,
        # unlike a price bar, so they are recorded rather than left unknown.
        first_available_at=doc.retrieved_at,
        retrieved_at=doc.retrieved_at,
        revision=f"supersedes:{doc.supersedes_id}" if doc.supersedes_id else None))


def official_release(session, book: EvidenceBook, stock_id: int, *,
                     period_end: date | None, report_date: date | None,
                     cutoff: datetime, event_id: int | None = None):
    """The official release attached to a report, or a named reason there is none.

    Returns (fields, primary_document_or_None). The document itself is returned, not just
    rendered into fields, because the caller must reconcile its figures into the report's
    primary metrics — a release that is displayed but not reconciled is how one report came
    to say both "$54.23B" and "revenue unavailable".
    """
    docs, quality = documents_for_period(session, stock_id, period_end=period_end,
                                         report_date=report_date, cutoff=cutoff,
                                         event_id=event_id)
    if not docs:
        return {
            "official_release": unavailable(
                "no official issuer document is stored for this period. Figures below come from "
                "the provider's earnings row, which is not the issuer's own release."),
            "source_confirmed_fiscal_period": unavailable(
                "no issuer document is stored, so the fiscal period cannot be source-confirmed"),
        }, None

    primary = docs[0]
    out: dict[str, Field] = {}
    if quality == "candidate_only":
        # A candidate, not a link. It is reported so a reader can see what exists, but it never
        # populates period figures or confirms a fiscal identity.
        return {
            "official_release": unknown(
                f"a document published near this report date exists ({primary.source_url}) but "
                f"carries no fiscal period of its own, so it cannot be shown to describe THIS "
                f"period rather than a neighbouring one. Treated as a candidate, not a source.",
                evidence_ids=[record_document(book, primary)]),
            "source_confirmed_fiscal_period": unknown(
                "the candidate document carries no fiscal period end"),
            "official_figures": unavailable(
                "no document is confirmed for this period, so no official figures are used"),
        }, None
    out["official_release"] = observed(
        {"document_id": primary.id, "type": primary.document_type, "title": primary.title,
         "publisher": primary.publisher, "url": primary.source_url,
         "published_at": primary.published_at.isoformat() if primary.published_at else None,
         "retrieved_at": primary.retrieved_at.isoformat(),
         "content_hash": primary.content_hash,
         "other_documents": len(docs) - 1,
         "matched_on": ("exact source-confirmed fiscal period end "
                        f"{primary.fiscal_period_end}" if quality == "confirmed_period"
                        else "an explicitly recorded association with this event")},
        evidence_ids=[record_document(book, primary)])

    if primary.fiscal_period_end:
        out["source_confirmed_fiscal_period"] = observed(
            {"period_end": primary.fiscal_period_end.isoformat(),
             "label": primary.fiscal_label,
             "confirmed_by": primary.fiscal_source or primary.source_url},
            evidence_ids=[f"issuer_document:{primary.id}"])
    else:
        out["source_confirmed_fiscal_period"] = unknown(
            "the stored document carries no fiscal period end, so the period remains unconfirmed")

    # Figures from the issuer's own release, each keeping its basis — this is what makes a
    # GAAP/non-GAAP distinction reportable instead of a caveat.
    if primary.facts:
        out["official_figures"] = observed(
            primary.facts, evidence_ids=[f"issuer_document:{primary.id}"])
    else:
        out["official_figures"] = unavailable(
            "the document is stored but no figures have been extracted from it")
    return out, primary


#: Which document fact answers which report metric. Named explicitly rather than matched by
#: string similarity: "revenue" and "guidance_q1_revenue" are both revenue and only one of them
#: is the reported actual.
_FACT_TO_METRIC = {
    "revenue": "revenue_actual",
    "eps_adjusted": "eps_actual",
}


def reconcile_into_metrics(fields: dict, doc_facts: dict, *, document_id: int) -> None:
    """Promote source-backed figures into the report's PRIMARY metrics, in place.

    THE DEFECT THIS CLOSES. The release was attached and the report still said
    `revenue_actual: UNAVAILABLE` directly beneath `official_figures` showing revenue of
    $54.23B. Two fields of one report answering the same question differently is worse than
    either answer alone, and a narrator reading that report would have to pick one.

    PROVENANCE TRAVELS WITH THE VALUE. A provider disagreement is preserved rather than
    overwritten: where both exist and differ, both are shown with the issuer's marked
    authoritative, because a silent overwrite destroys the evidence that they ever disagreed.

    UNITS ARE NOT ASSUMED TO MATCH. The stored estimate carries no units at all. The issuer
    reports revenue in dollars and states so; the provider's row may be in dollars, millions or
    billions. Dividing one by the other across an unestablished unit boundary is how a 100x
    error gets printed as a surprise percentage, so the surprise is NOT recomputed against the
    issuer's figure — it keeps saying which actual produced it, and when that is no longer the
    actual shown above, it says that too.
    """
    from intelligence.report_contract import (Field, FieldState, StatementClass, observed,
                                              unknown)

    for fact_key, metric in _FACT_TO_METRIC.items():
        fact = doc_facts.get(fact_key)
        if not isinstance(fact, dict) or fact.get("value") is None:
            continue
        prior = fields.get(metric)
        provider = prior.value if prior is not None and prior.state is FieldState.OK else None
        value = {"value": fact["value"], "units": fact.get("units"),
                 "basis": fact.get("basis"), "period": fact.get("period"),
                 "source": "the issuer's own release", "document_id": document_id}
        if provider is not None and provider != fact["value"]:
            value["provider_value"] = provider
            value["conflict"] = ("the provider's row and the issuer's own release carry "
                                 "different figures, possibly only different units. The "
                                 "issuer's is authoritative here; the provider's is kept so "
                                 "the disagreement stays visible rather than being overwritten")
        fields[metric] = observed(
            value, units=fact.get("units"),
            evidence_ids=[f"issuer_document:{document_id}"],
            label=getattr(prior, "label", None))

        # The surprise was computed from the provider's actual against an estimate of unknown
        # units and unknown basis. Say so, rather than letting it read as a surprise against
        # the sourced figure now shown beside it.
        name = metric.rsplit("_", 1)[0]
        surprise = fields.get(f"{name}_surprise_pct")
        if surprise is not None and surprise.state is FieldState.OK:
            fields[f"{name}_surprise_pct"] = Field(
                value={**(surprise.value if isinstance(surprise.value, dict) else
                          {"value": surprise.value}),
                       "computed_against": provider,
                       "note": ("computed from the PROVIDER's actual, not the issuer figure "
                                "shown above. The stored estimate carries neither units nor "
                                "an accounting basis, so it cannot be divided into the "
                                "issuer's figure without assuming both")},
                state=FieldState.OK, statement=StatementClass.DETERMINISTIC_CALCULATION,
                units=surprise.units, evidence_ids=list(surprise.evidence_ids),
                label=surprise.label)
        elif surprise is not None:
            fields[f"{name}_surprise_pct"] = unknown(
                "an issuer figure is available above, but the stored estimate carries no units "
                "and no accounting basis, so a surprise against it would assume both",
                label=surprise.label)

    # Guidance: what the company said is AVAILABLE; whether it was RAISED is a different claim
    # needing a comparable prior forecast for the same period on the same basis.
    guidance = {k: v for k, v in doc_facts.items() if k.startswith("guidance")}
    if guidance:
        fields["guidance_current"] = observed(
            guidance, evidence_ids=[f"issuer_document:{document_id}"],
            label="Guidance issued with these results")
        fields["guidance_change"] = Field(
            value={"current_guidance_available": True, "comparison": "not established"},
            state=FieldState.UNKNOWN,
            reason=("the company's CURRENT guidance is shown above, from its own release. "
                    "Whether it was RAISED, maintained, lowered or first-issued is a different "
                    "claim: it needs the PRIOR guidance for the SAME target period on the SAME "
                    "accounting basis, which is not stored. The previous quarter's guidance for "
                    "a different quarter is not that comparison."),
            statement=StatementClass.INTERPRETATION,
            label="Guidance change")

    # The accounting basis is no longer uniformly unknown: the issuer stated one per figure.
    stated = {k: v.get("basis") for k, v in doc_facts.items()
              if isinstance(v, dict) and v.get("basis")}
    if stated:
        fields["accounting_basis"] = Field(
            value={"stated_by_issuer_per_metric": stated,
                   "estimate_basis": "UNKNOWN",
                   "note": "the issuer states a basis for each figure above. The basis of the "
                           "stored ESTIMATE is still unknown, so a surprise computed against "
                           "it remains a factual difference and NOT a verified beat."},
            state=FieldState.OK, statement=StatementClass.OBSERVED_FACT,
            evidence_ids=[f"issuer_document:{document_id}"], label="Accounting basis")


def confirmed_fiscal_period(fields: dict, primary) -> "Field | None":
    """The source-confirmed identity, as the answer to `fiscal_period` itself.

    `A.fiscal_period()` correctly refuses the stored label, which is derived from the calendar
    month and mislabels every non-calendar fiscal year. But once an issuer document confirms the
    period, the report carried both the refusal AND the confirmation, in adjacent fields: "no
    confirmed period is stored" directly above "FY2026 Q4, confirmed by the issuer". The refusal
    is only correct while nothing confirms it.
    """
    from intelligence.report_contract import observed
    if not getattr(primary, "fiscal_period_end", None):
        return None
    return observed(
        {"label": primary.fiscal_label,
         "period_end": primary.fiscal_period_end.isoformat(),
         "confirmed_by": primary.fiscal_source or primary.source_url,
         "note": "from the issuer's own release. The platform's stored fiscal_quarter is "
                 "derived from the calendar month and is NOT used here."},
        evidence_ids=[f"issuer_document:{primary.id}"],
        label="Fiscal period")


def assess_event_association(session, stock_id: int, *, now: datetime,
                             book: EvidenceBook | None = None) -> Field | None:
    """Whether each stored release is associated with an event — or whether that is unresolved.

    WHAT THIS NO LONGER DOES, AND WHY. It used to look for an event within N days after a
    document's period end and call the absence of one a CONFIRMED missing event. A real
    announcement lag of 55 days then made a correctly-stored event look missing — the arithmetic
    was asserting a fact about coverage from nothing but a date difference.

    An association is EVIDENCE: a mapping step recorded `event_id` on the document. Its absence
    is not counter-evidence; it means nobody has mapped it yet. So the only honest states here
    are `associated` and `unresolved_association`, and NEITHER confirms a missing event.

    `confirmed_missing_event` is therefore not reachable from date arithmetic at all. It needs a
    resolution step that attempted the association and recorded that no event exists — which is
    the ingestion-discovery work, not this function. Until then a coverage gap stays a cadence
    SUSPICION, which is what it is.
    """
    q = select(IssuerDocument).where(
        IssuerDocument.stock_id == stock_id,
        IssuerDocument.document_type.in_(_RESULTS_DOCUMENT_TYPES))
    docs = list(session.execute(
        _visible(q, cutoff=now)
        .order_by(IssuerDocument.published_at.desc().nullslast()).limit(8)).scalars().all())
    if not docs:
        return None

    unresolved, associated, cited = [], [], []
    for doc in docs:
        entry = {"document_id": doc.id,
                 "fiscal_period_end": (doc.fiscal_period_end.isoformat()
                                       if doc.fiscal_period_end else None),
                 "published_at": doc.published_at.isoformat() if doc.published_at else None,
                 "url": doc.source_url}
        if doc.event_id is not None:
            associated.append(entry | {"event_id": doc.event_id})
            continue
        if book is not None:
            cited.append(record_document(book, doc))
        unresolved.append(entry)

    if not unresolved:
        return Field(
            value={"coverage_state": "documents_associated",
                   "associated": associated},
            state=FieldState.OK,
            statement=StatementClass.OBSERVED_FACT,
            label="Release documents")

    return Field(
        value={"coverage_state": "unresolved_association",
               "unassociated_documents": unresolved, "associated": associated},
        state=FieldState.UNKNOWN,
        reason=(f"{len(unresolved)} stored release document(s) have no recorded association "
                f"with an earnings event. That does NOT establish that the event is missing — "
                f"nobody has mapped them yet, and a real announcement lag can put a correctly "
                f"stored event well outside any date window. Resolving this needs an explicit "
                f"mapping step, not arithmetic on dates."),
        statement=StatementClass.INTERPRETATION,
        evidence_ids=cited,
        label="Release documents")
