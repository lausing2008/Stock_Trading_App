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

#: How close a document's fiscal period end must be to an event's report date to be the same
#: quarter. Releases follow period end by days to weeks; a wider window would start matching the
#: neighbouring quarter, which is worse than matching nothing.
_PERIOD_MATCH_DAYS = 75


def documents_for_period(session, stock_id: int, *, period_end: date | None,
                         report_date: date | None, limit: int = 10) -> list[IssuerDocument]:
    """Documents belonging to one reporting period, newest publication first.

    Matches on the document's own `fiscal_period_end` when it has one — the source-confirmed
    identity — and otherwise falls back to publication proximity to the event's report date,
    which is weaker and is labelled as such by the caller.
    """
    q = select(IssuerDocument).where(IssuerDocument.stock_id == stock_id)
    anchor = period_end or report_date
    if anchor is not None:
        lo, hi = anchor - timedelta(days=_PERIOD_MATCH_DAYS), anchor + timedelta(days=_PERIOD_MATCH_DAYS)
        q = q.where(
            ((IssuerDocument.fiscal_period_end.is_not(None))
             & (IssuerDocument.fiscal_period_end >= lo)
             & (IssuerDocument.fiscal_period_end <= hi))
            | ((IssuerDocument.fiscal_period_end.is_(None))
               & (IssuerDocument.published_at.is_not(None))
               & (IssuerDocument.published_at >= datetime.combine(lo, datetime.min.time()))
               & (IssuerDocument.published_at <= datetime.combine(hi, datetime.max.time()))))
    return list(session.execute(
        q.order_by(IssuerDocument.published_at.desc().nullslast()).limit(limit)).scalars().all())


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
                     period_end: date | None, report_date: date | None) -> dict[str, Field]:
    """The official release attached to a report, or a named reason there is none."""
    docs = documents_for_period(session, stock_id, period_end=period_end,
                                report_date=report_date)
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
    out["official_release"] = observed(
        {"document_id": primary.id, "type": primary.document_type, "title": primary.title,
         "publisher": primary.publisher, "url": primary.source_url,
         "published_at": primary.published_at.isoformat() if primary.published_at else None,
         "retrieved_at": primary.retrieved_at.isoformat(),
         "content_hash": primary.content_hash,
         "other_documents": len(docs) - 1,
         "matched_on": ("source-confirmed fiscal period end"
                        if primary.fiscal_period_end else
                        "publication date proximity to the stored report date — WEAKER than a "
                        "source-confirmed period, and may match a neighbouring quarter")},
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


def confirm_missing_event(session, stock_id: int, *, now: datetime) -> Field | None:
    """A document for a period with NO event row is dated evidence the event is absent.

    THIS IS THE PROMOTION CADENCE CANNOT MAKE. Exceeding an issuer's median gap is an inference
    about reporting rhythm; an official release for a period the event table does not contain is
    a fact about coverage. Only this path may say `confirmed_missing_event`.
    """
    docs = list(session.execute(
        select(IssuerDocument).where(IssuerDocument.stock_id == stock_id,
                                     IssuerDocument.fiscal_period_end.is_not(None))
        .order_by(IssuerDocument.fiscal_period_end.desc()).limit(8)).scalars().all())
    orphans = []
    for doc in docs:
        anchor = doc.fiscal_period_end
        match = session.execute(
            select(EarningsEvent).where(
                EarningsEvent.stock_id == stock_id,
                EarningsEvent.report_date >= anchor,
                EarningsEvent.report_date <= anchor + timedelta(days=_PERIOD_MATCH_DAYS))
            .limit(1)).scalars().first()
        if match is None:
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
        evidence_ids=[f"issuer_document:{o['document_id']}" for o in orphans])
