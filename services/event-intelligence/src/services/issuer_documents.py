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
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

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

#: Every stored figure must carry these. A number without them is not usable evidence: $54.23B
#: and 54230 are the same revenue in different units, a GAAP margin and a non-GAAP one are
#: different values the issuer reported separately, and a figure for the wrong period is simply
#: a different fact. `citation` locates the figure INSIDE the document, so a reader can check
#: this one number rather than being handed the whole release and told to look.
_REQUIRED_FACT_KEYS = ("value", "units", "basis", "period", "citation")

#: Where an issuer's publication time is anchored when turning it into an announcement DATE.
#: A release at 16:05 America/New_York is a UTC instant on the following day, so taking
#: `.date()` off the UTC stamp moved MU's announcement forward by a day and would have measured
#: the reaction window from the wrong session.
_MARKET_TZ = {"US": "America/New_York", "HK": "Asia/Hong_Kong"}


def _db():
    """Imported at call time — see earnings_discovery._db for why."""
    from db import EarningsEvent, IssuerDocument, SessionLocal, Stock
    return EarningsEvent, IssuerDocument, SessionLocal, Stock


def _digest(raw: bytes | str, *, kind: str) -> str:
    """A digest that says WHAT IT COVERS, because the two kinds support different claims.

    `sha256-bytes:` is taken over the retrieved document and does detect the issuer silently
    editing the release at the same URL. `sha256-facts:` is taken over our own extraction and
    detects only that WE changed a number — it is blind to any edit at the source. The previous
    single `sha256:` prefix was computed over the facts and documented as byte identity, so the
    stronger claim was being made by a weaker value.
    """
    import json
    if isinstance(raw, bytes):
        data = raw
    else:
        data = (raw if isinstance(raw, str)
                else json.dumps(raw, sort_keys=True, default=str)).encode()
    return f"sha256-{kind}:" + hashlib.sha256(data).hexdigest()[:48]


def facts_digest(facts: dict) -> str:
    """Identity of OUR EXTRACTION. Not evidence about the source document."""
    import json
    return _digest(json.dumps(facts, sort_keys=True, default=str), kind="facts")


def bytes_digest(raw: bytes) -> str:
    """Identity of the retrieved document itself."""
    return _digest(raw, kind="bytes")


def _announcement_date(published_at: datetime, market: str) -> tuple[date, dict]:
    """The exchange-local DATE of a publication instant, with the conversion shown.

    `published_at` is stored naive-UTC. `.date()` on it is the UTC calendar day, which for any
    US after-close release is the NEXT day — one session off, in the single field the whole
    reaction window is measured from.
    """
    tz_name = _MARKET_TZ.get(str(market).upper(), "America/New_York")
    aware = published_at.replace(tzinfo=timezone.utc) if published_at.tzinfo is None else published_at
    local = aware.astimezone(ZoneInfo(tz_name))
    return local.date(), {
        "published_at_utc": aware.astimezone(timezone.utc).isoformat(),
        "exchange_timezone": tz_name,
        "published_at_local": local.isoformat(),
        "utc_calendar_date": aware.astimezone(timezone.utc).date().isoformat(),
        "announcement_date": local.date().isoformat(),
        "note": ("the announcement date is the EXCHANGE-LOCAL date of the publication instant. "
                 "Where it differs from the UTC calendar date above, the UTC date is one "
                 "session off and must not be used to place a reaction window."),
    }


def ingest_release(symbol: str, *, source_url: str, publisher: str, title: str,
                   fiscal_period_end: date, fiscal_label: str, fiscal_source: str,
                   published_at: datetime, facts: dict, extraction_method: str,
                   actor: str, document_type: str = "press_release",
                   source_bytes: bytes | None = None,
                   commit: bool = False) -> dict:
    """Store one official release. PREVIEW unless `commit=True`.

    `facts` carries each figure with its own units, accounting basis and period — a bare number
    is not storable here, because a GAAP gross margin and a non-GAAP one are different values
    the issuer itself reported separately, and collapsing them loses the distinction.
    """
    offending = {}
    for k, v in facts.items():
        if not isinstance(v, dict):
            offending[k] = ["not an object"]
            continue
        absent = [r for r in _REQUIRED_FACT_KEYS if v.get(r) in (None, "")]
        if absent:
            offending[k] = absent
    if offending:
        return {"error": "every stored figure needs a value, units, an accounting basis, the "
                         "period it describes, and a citation locating it in the document",
                "offending_keys": offending, "committed": False}
    if extraction_method not in (MANUAL_TRANSCRIPTION, AUTOMATED_EXTRACTION):
        return {"error": f"unknown extraction_method {extraction_method!r}", "committed": False}

    f_digest = facts_digest(facts)
    b_digest = bytes_digest(source_bytes) if source_bytes is not None else None
    # The unique key is the strongest identity available. With the bytes in hand it is the
    # document itself; without them it can only be our extraction, and the prefix says so.
    digest = b_digest or f_digest
    plan = {
        "symbol": symbol.upper().strip(), "source_url": source_url,
        "fiscal_period_end": fiscal_period_end.isoformat(), "fiscal_label": fiscal_label,
        "published_at": published_at.isoformat(), "facts": len(facts),
        "content_hash": digest, "facts_hash": f_digest, "source_bytes_hash": b_digest,
        "extraction_method": extraction_method, "actor": actor,
        "source_edit_detection": (
            "available: the retrieved bytes are hashed, so an edit at this URL is detectable"
            if b_digest else
            "NOT AVAILABLE: no document bytes were supplied, so this row cannot detect the "
            "issuer silently editing the release. Only our own extraction is hashed."),
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
            content_hash=digest, facts_hash=f_digest, source_bytes_hash=b_digest,
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
    EarningsEvent, IssuerDocument, SessionLocal, Stock = _db()
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

        stock = s.get(Stock, doc.stock_id)
        market = getattr(getattr(stock, "market", None), "value", None) or \
            str(getattr(stock, "market", "US"))
        announcement, tz_trace = _announcement_date(doc.published_at, market)
        changes = {
            "period_end": {"from": ev.period_end.isoformat() if ev.period_end else None,
                           "to": doc.fiscal_period_end.isoformat() if doc.fiscal_period_end else None},
            "report_date": {"from": ev.report_date.isoformat(), "to": announcement.isoformat()},
            "report_date_source": {"from": ev.report_date_source, "to": ISSUER_RELEASE},
            "document_event_link": {"from": doc.event_id, "to": event_id},
        }
        plan = {"document_id": document_id, "event_id": event_id, "actor": actor,
                "rationale": rationale, "changes": changes,
                "announcement_dating": tz_trace,
                "notification_suppression": (
                    "unchanged: a historical import stays ineligible for delivery however "
                    "accurate its date becomes"),
                "committed": False}
        if not commit:
            return plan

        # THE EVIDENCE IS WRITTEN WITH THE CHANGE, not emitted beside it. A log line is not
        # queryable from the row that the association altered, so months later the event row
        # asserted a corrected identity with no stored record of who asserted it or why.
        doc.association = {
            "actor": actor,
            "rationale": rationale,
            "associated_at": datetime.utcnow().isoformat() + "Z",
            "event_id": event_id,
            "before": {k: v["from"] for k, v in changes.items()},
            "after": {k: v["to"] for k, v in changes.items()},
            "announcement_dating": tz_trace,
            "notification_suppression": "not lifted by this association",
        }
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
