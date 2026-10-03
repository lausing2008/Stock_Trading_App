"""Ingesting an official issuer release, and associating it with an earnings event.

WHY ASSOCIATION IS AN EXPLICIT ACT. Discovery deliberately refuses to match a document to an
event by date proximity — a window is a guess dressed as identity, and the quantity it guesses
(the announcement lag) is the one thing that must not be assumed. So an association is RECORDED
by someone, with the evidence that justifies it, rather than inferred.

That matters immediately for MU: the provider reports the fiscal Q4 period ending 2026-08-31,
while the issuer's own release says 2026-09-03. Those are three days apart and the issuer is
authoritative. A proximity rule would have silently picked one; an explicit association resolves
it with the document as the evidence and records what changed.

THE DOCUMENT IS THE SOURCE OF FISCAL AND ANNOUNCEMENT IDENTITY. Associating one corrects the
event's period end and replaces a substituted date with the real announcement date — which is
what finally makes a post-earnings reaction window measurable.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime

from sqlalchemy import select

from common.logging import get_logger

log = get_logger("event-intelligence.issuer_documents")

#: How `report_date` was established once an issuer release supplies it.
ISSUER_RELEASE = "issuer_release"

#: How the figures got out of the document and into `facts`. Recorded because a transcription
#: and a parser fail in completely different ways, and a reader deciding how much to trust a
#: number needs to know which one produced it.
MANUAL_TRANSCRIPTION = "manual_transcription"
AUTOMATED_EXTRACTION = "automated_extraction"


def _db():
    """Imported at call time — see earnings_discovery._db for why."""
    from db import EarningsEvent, IssuerDocument, SessionLocal, Stock
    return EarningsEvent, IssuerDocument, SessionLocal, Stock


def content_hash(payload: dict | str) -> str:
    """Identity of the bytes, so a silent edit at the source is detectable."""
    import json
    raw = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:48]


def ingest_release(symbol: str, *, source_url: str, publisher: str, title: str,
                   fiscal_period_end: date, fiscal_label: str, fiscal_source: str,
                   published_at: datetime, facts: dict, extraction_method: str,
                   actor: str, document_type: str = "press_release",
                   commit: bool = False) -> dict:
    """Store one official release. PREVIEW unless `commit=True`.

    `facts` carries each figure with its own units, accounting basis and period — a bare number
    is not storable here, because a GAAP gross margin and a non-GAAP one are different values
    the issuer itself reported separately, and collapsing them loses the distinction.
    """
    missing = [k for k, v in facts.items()
               if not isinstance(v, dict) or "value" not in v or "basis" not in v]
    if missing:
        return {"error": "every fact needs a value and an accounting basis",
                "offending_keys": sorted(missing), "committed": False}
    if extraction_method not in (MANUAL_TRANSCRIPTION, AUTOMATED_EXTRACTION):
        return {"error": f"unknown extraction_method {extraction_method!r}", "committed": False}

    digest = content_hash(facts)
    plan = {
        "symbol": symbol.upper().strip(), "source_url": source_url,
        "fiscal_period_end": fiscal_period_end.isoformat(), "fiscal_label": fiscal_label,
        "published_at": published_at.isoformat(), "facts": len(facts),
        "content_hash": digest, "extraction_method": extraction_method, "actor": actor,
        "committed": False,
    }
    if not commit:
        return plan

    _EV, IssuerDocument, SessionLocal, Stock = _db()
    with SessionLocal() as s:
        stock = s.execute(select(Stock).where(Stock.symbol == plan["symbol"])).scalars().first()
        if stock is None:
            return plan | {"error": f"{plan['symbol']} is not in the universe"}
        # Idempotent on (issuer, url, content): the same bytes re-ingested is the same document.
        existing = s.execute(
            select(IssuerDocument).where(IssuerDocument.stock_id == stock.id,
                                         IssuerDocument.source_url == source_url,
                                         IssuerDocument.content_hash == digest)
            .limit(1)).scalars().first()
        if existing is not None:
            return plan | {"committed": False, "document_id": existing.id,
                           "note": "identical document already stored; nothing written"}
        doc = IssuerDocument(
            stock_id=stock.id, document_type=document_type, source_url=source_url,
            publisher=publisher, title=title,
            fiscal_period_end=fiscal_period_end, fiscal_label=fiscal_label,
            fiscal_source=fiscal_source,
            published_at=published_at, retrieved_at=datetime.utcnow(),
            content_hash=digest,
            facts=facts | {"_provenance": {"extraction_method": extraction_method,
                                           "actor": actor, "source_url": source_url}})
        s.add(doc); s.commit()
        log.info("issuer_document.ingested", symbol=plan["symbol"], document_id=doc.id,
                 actor=actor, extraction=extraction_method)
        return plan | {"committed": True, "document_id": doc.id}


def associate_with_event(document_id: int, event_id: int, *, actor: str, rationale: str,
                         commit: bool = False) -> dict:
    """Record that this release reports this event, and take identity from the document.

    PREVIEW unless `commit=True`, and the preview shows every value that would change — an
    association rewrites the event's fiscal period and announcement date, which is exactly the
    kind of correction that should be read before it is applied.

    NOTIFICATION SUPPRESSION IS NOT LIFTED. A release ingested days after the fact must not
    become eligible for delivery because its date is now accurate; the suppression records that
    this result arrived through a historical import, which remains true however good the date is.
    """
    EarningsEvent, IssuerDocument, SessionLocal, _ST = _db()
    with SessionLocal() as s:
        doc = s.get(IssuerDocument, document_id)
        ev = s.get(EarningsEvent, event_id)
        if doc is None or ev is None:
            return {"error": "document or event not found", "committed": False}
        if doc.stock_id != ev.stock_id:
            return {"error": "document and event belong to different issuers",
                    "committed": False}
        if doc.published_at is None:
            return {"error": "the document has no publication time, so it cannot establish an "
                             "announcement date", "committed": False}

        announcement = doc.published_at.date()
        changes = {
            "period_end": {"from": ev.period_end.isoformat() if ev.period_end else None,
                           "to": doc.fiscal_period_end.isoformat() if doc.fiscal_period_end else None},
            "report_date": {"from": ev.report_date.isoformat(), "to": announcement.isoformat()},
            "report_date_source": {"from": ev.report_date_source, "to": ISSUER_RELEASE},
            "document_event_link": {"from": doc.event_id, "to": event_id},
        }
        plan = {"document_id": document_id, "event_id": event_id, "actor": actor,
                "rationale": rationale, "changes": changes,
                "notification_suppression": (
                    "unchanged: a historical import stays ineligible for delivery however "
                    "accurate its date becomes"),
                "committed": False}
        if not commit:
            return plan

        doc.event_id = event_id
        if doc.fiscal_period_end:
            ev.period_end = doc.fiscal_period_end
        ev.report_date = announcement
        ev.report_date_source = ISSUER_RELEASE
        ev.fetched_at = datetime.utcnow()
        s.commit()
        log.info("issuer_document.associated", document_id=document_id, event_id=event_id,
                 actor=actor, announcement=announcement.isoformat())
        return plan | {"committed": True}
