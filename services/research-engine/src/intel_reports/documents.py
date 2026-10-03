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

#: A release follows its period end by days to a few weeks. 75 days was wide enough to reach
#: the NEIGHBOURING quarter — it matched a Q2 release to a Q1 event while claiming fiscal-period
#: identity. A quarter is ~91 days, so any window approaching that is ambiguous by construction.
_RELEASE_LAG_DAYS = 45

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
                         limit: int = 10) -> tuple[list[IssuerDocument], str]:
    """Candidate results documents for one reporting period, and how confidently they matched.

    TWO DIFFERENT THINGS, DELIBERATELY SEPARATED. A CONFIRMED link needs the document's own
    source-confirmed `fiscal_period_end` to sit in the window a release can occupy relative to
    the event — at or before the report date, and no more than `_RELEASE_LAG_DAYS` before it.
    A proximity match on publication date alone is a CANDIDATE: it cannot populate authoritative
    period figures, because it cannot tell a late Q1 release from an early Q2 one.

    Returns `(documents, match_quality)` where quality is "confirmed_period", "proximity_only"
    or "none", so the caller never has to infer how much the match is worth.
    """
    base = select(IssuerDocument).where(
        IssuerDocument.stock_id == stock_id,
        IssuerDocument.document_type.in_(_RESULTS_DOCUMENT_TYPES))
    base = _visible(base, cutoff=cutoff)

    anchor = period_end or report_date
    if anchor is None:
        return [], "none"

    # Confirmed: the document names a period that this event could be reporting on.
    confirmed = list(session.execute(
        base.where(IssuerDocument.fiscal_period_end.is_not(None),
                   IssuerDocument.fiscal_period_end <= anchor,
                   IssuerDocument.fiscal_period_end >= anchor - timedelta(days=_RELEASE_LAG_DAYS))
        .order_by(IssuerDocument.fiscal_period_end.desc(),
                  IssuerDocument.published_at.desc().nullslast())
        .limit(limit)).scalars().all())
    if confirmed:
        return confirmed, "confirmed_period"

    # Candidate only: published near the report date, with no period of its own to check.
    lo = datetime.combine(anchor - timedelta(days=7), datetime.min.time())
    hi = datetime.combine(anchor + timedelta(days=7), datetime.max.time())
    nearby = list(session.execute(
        base.where(IssuerDocument.fiscal_period_end.is_(None),
                   IssuerDocument.published_at >= lo, IssuerDocument.published_at <= hi)
        .order_by(IssuerDocument.published_at.desc()).limit(limit)).scalars().all())
    return (nearby, "proximity_only") if nearby else ([], "none")


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
                     cutoff: datetime) -> dict[str, Field]:
    """The official release attached to a report, or a named reason there is none."""
    docs, quality = documents_for_period(session, stock_id, period_end=period_end,
                                         report_date=report_date, cutoff=cutoff)
    if not docs:
        return {
            "official_release": unavailable(
                "no official issuer document is stored for this period. Figures below come from "
                "the provider's earnings row, which is not the issuer's own release."),
            "source_confirmed_fiscal_period": unavailable(
                "no issuer document is stored, so the fiscal period cannot be source-confirmed"),
        }

    primary = docs[0]
    out: dict[str, Field] = {}
    if quality == "proximity_only":
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
        }
    out["official_release"] = observed(
        {"document_id": primary.id, "type": primary.document_type, "title": primary.title,
         "publisher": primary.publisher, "url": primary.source_url,
         "published_at": primary.published_at.isoformat() if primary.published_at else None,
         "retrieved_at": primary.retrieved_at.isoformat(),
         "content_hash": primary.content_hash,
         "other_documents": len(docs) - 1,
         "matched_on": (f"source-confirmed fiscal period end {primary.fiscal_period_end}, "
                        f"within {_RELEASE_LAG_DAYS} days before the event")},
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
    return out


def confirm_missing_event(session, stock_id: int, *, now: datetime,
                          book: EvidenceBook | None = None) -> Field | None:
    """A document for a period with NO event row is dated evidence the event is absent.

    THIS IS THE PROMOTION CADENCE CANNOT MAKE. Exceeding an issuer's median gap is an inference
    about reporting rhythm; an official release for a period the event table does not contain is
    a fact about coverage. Only this path may say `confirmed_missing_event`.
    """
    # CUTOFF-BOUNDED, and results documents only. Without the cutoff a release published after
    # the report's own cutoff certified a gap the report could not have known about.
    q = select(IssuerDocument).where(
        IssuerDocument.stock_id == stock_id,
        IssuerDocument.fiscal_period_end.is_not(None),
        IssuerDocument.document_type.in_(_RESULTS_DOCUMENT_TYPES))
    docs = list(session.execute(
        _visible(q, cutoff=now)
        .order_by(IssuerDocument.fiscal_period_end.desc()).limit(8)).scalars().all())
    orphans = []
    cited: list[str] = []
    for doc in docs:
        anchor = doc.fiscal_period_end
        match = session.execute(
            select(EarningsEvent).where(
                EarningsEvent.stock_id == stock_id,
                EarningsEvent.report_date >= anchor,
                EarningsEvent.report_date <= anchor + timedelta(days=_RELEASE_LAG_DAYS))
            .limit(1)).scalars().first()
        if match is None:
            # Recorded, not merely cited. The report refuses to save a citation that resolves
            # to nothing, and it caught this exact omission.
            if book is not None:
                cited.append(record_document(book, doc))
            orphans.append({
                "document_id": doc.id, "fiscal_period_end": anchor.isoformat(),
                "fiscal_label": doc.fiscal_label,
                "published_at": doc.published_at.isoformat() if doc.published_at else None,
                "url": doc.source_url})
    if not orphans:
        return None
    return Field(
        value={"coverage_state": "confirmed_missing_event", "orphan_documents": orphans},
        state=FieldState.CONFLICTING,
        reason=(f"{len(orphans)} official issuer document(s) describe a reporting period with no "
                f"corresponding event row. This is not an inference from reporting cadence — a "
                f"dated release names the period, and the event table does not contain it."),
        statement=StatementClass.OBSERVED_FACT,
        evidence_ids=cited)
