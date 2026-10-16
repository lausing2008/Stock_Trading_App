"""Per-user sourced product watches and actual alert funnel state."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from db import NewsImpactEvent, NewsImpactReceipt, NotificationOutbox, User, get_session
from .auth import get_current_user

router = APIRouter(prefix="/news-impact", tags=["news-impact"])


@router.get("")
def list_impacts(current: User = Depends(get_current_user),
                 session: Session = Depends(get_session), followed_only: bool = False):
    query = select(NewsImpactEvent)
    if followed_only:
        query = query.join(NewsImpactReceipt, NewsImpactReceipt.event_id == NewsImpactEvent.id).where(
            NewsImpactReceipt.user_id == current.id)
    events = session.execute(query.order_by(NewsImpactEvent.published_at.desc()).limit(30)).scalars().all()
    if not events:
        return []
    receipts = {r.event_id: r for r in session.execute(select(NewsImpactReceipt).where(
        NewsImpactReceipt.user_id == current.id,
        NewsImpactReceipt.event_id.in_([e.id for e in events]))).scalars()}
    outbox_ids = [r.outbox_event_id for r in receipts.values() if r.outbox_event_id]
    outbox = {r.event_id: r for r in session.execute(select(NotificationOutbox).where(
        NotificationOutbox.event_id.in_(outbox_ids))).scalars()} if outbox_ids else {}
    results = []
    for event in events:
        receipt = receipts.get(event.id)
        message = outbox.get(receipt.outbox_event_id) if receipt and receipt.outbox_event_id else None
        results.append({
            "event_key": event.event_key, "title": event.title,
            "published_at": event.published_at.isoformat(),
            "source_url": event.source_url, "observed_url": event.observed_url,
            "evidence_status": event.evidence_status, "impacts": event.impacts,
            "matched_symbols": receipt.matched_symbols if receipt else [],
            "state": receipt.state if receipt else "not_subscribed",
            "reason": receipt.reason if receipt else "No affected symbol in your watchlist when evaluated",
            "email_state": message.state if message else None,
            "email_reason": message.terminal_reason if message else None,
            "provider_accepted_at": message.accepted_at.isoformat() if message and message.accepted_at else None,
        })
    return results
