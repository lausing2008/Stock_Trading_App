"""Authenticated preferences, explicit daily-close alerts, and a reviewed research pilot."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, delete, update
from sqlalchemy.exc import IntegrityError
from db import get_session
from db.models import (
    User,
    WorkspacePreference,
    DailyCloseAlert,
    ResearchRelationship,
    EventConnection,
    Stock,
)
from .auth import get_current_user, get_admin_user
from ..services import workspace_workflows as W

workspace_router = APIRouter(prefix="/workspace", tags=["workspace"])
close_router = APIRouter(prefix="/close-alerts", tags=["daily-close-alerts"])
connections_router = APIRouter(prefix="/connections", tags=["research-connections"])


class LayoutPut(BaseModel):
    revision: int = Field(ge=0)
    layout: dict


@workspace_router.get("/layout")
def get_layout(user=Depends(get_current_user), session=Depends(get_session)):
    row = session.get(WorkspacePreference, user.id)
    return {
        "revision": row.revision if row else 0,
        "layout": row.layout if row else None,
    }


@workspace_router.put("/layout")
def put_layout(
    body: LayoutPut, user=Depends(get_current_user), session=Depends(get_session)
):
    try:
        revision = W.save_layout(session, user.id, body.revision, body.layout)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if revision is None:
        session.rollback()
        raise HTTPException(
            409,
            "Another device changed your layout. Load it or explicitly replace it after reviewing.",
        )
    session.commit()
    return {"revision": revision, "layout": W.validate_layout(body.layout)}


class CloseAlertIn(BaseModel):
    symbol: str = Field(pattern=r"^[A-Z0-9.^\-]{1,20}$")
    condition: str = Field(pattern=r"^(above|below)$")
    threshold: float = Field(gt=0, allow_inf_nan=False)
    origin: str = Field(default="typed", pattern=r"^(typed|candidate|held)$")
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


def close_out(a):
    return {
        "id": a.id,
        "symbol": a.symbol,
        "condition": a.condition,
        "threshold": a.threshold,
        "origin": a.origin,
        "active": a.active,
        "observation": a.observation,
        "created_at": a.created_at.isoformat(),
        "delivery": "in-app only",
        "rule": "One-shot fixed threshold; post-close daily bar, not intraday. No stop is maintained or changed.",
    }


@close_router.get("")
def close_list(user=Depends(get_current_user), session=Depends(get_session)):
    return [
        close_out(a)
        for a in session.scalars(
            select(DailyCloseAlert)
            .where(DailyCloseAlert.user_id == user.id)
            .order_by(DailyCloseAlert.id.desc())
            .limit(100)
        )
    ]


@close_router.post("")
def close_create(
    body: CloseAlertIn, user=Depends(get_current_user), session=Depends(get_session)
):
    stock = session.scalar(
        select(Stock).where(Stock.symbol == body.symbol, Stock.active.is_(True))
    )
    if stock is None or getattr(stock.market, "value", stock.market) not in (
        "US",
        "HK",
    ):
        raise HTTPException(400, "A tracked US or HK stock is required")

    def replay():
        row = session.scalar(
            select(DailyCloseAlert).where(
                DailyCloseAlert.user_id == user.id,
                DailyCloseAlert.request_id == body.request_id,
            )
        )
        if row is not None and (
            row.symbol,
            row.condition,
            row.threshold,
            row.origin,
        ) != (body.symbol, body.condition, body.threshold, body.origin):
            raise HTTPException(
                409, "Request id was already used for a different close alert"
            )
        return row

    prior = replay()
    if prior:
        return close_out(prior)
    session.execute(select(User.id).where(User.id == user.id).with_for_update())
    # A duplicate may have committed while this request waited for the owner lock.
    prior = replay()
    if prior:
        return close_out(prior)
    from sqlalchemy import func

    count = session.scalar(
        select(func.count())
        .select_from(DailyCloseAlert)
        .where(DailyCloseAlert.user_id == user.id, DailyCloseAlert.active.is_(True))
    )
    if count >= 100:
        raise HTTPException(
            429, "Disable an existing close alert before creating more (limit 100)"
        )
    row = DailyCloseAlert(user_id=user.id, **body.model_dump(), created_at=W.utcnow())
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        prior = replay()
        if prior is None:
            raise
        return close_out(prior)
    session.refresh(row)
    return close_out(row)


@close_router.delete("/{alert_id}")
def close_delete(
    alert_id: int, user=Depends(get_current_user), session=Depends(get_session)
):
    # Keep the request identity and audit history; disable instead of deleting the key.
    row = session.get(DailyCloseAlert, alert_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Alert not found")
    row.active = False
    session.commit()
    return {"disabled": alert_id}


class RelationshipIn(BaseModel):
    source_symbol: str = Field(pattern=r"^[A-Z0-9.^\-]{1,20}$")
    target_symbol: str = Field(pattern=r"^[A-Z0-9.^\-]{1,20}$")
    relation: str = Field(pattern=r"^(supplier|customer|competitor|substitute)$")
    evidence_url: str = Field(max_length=2048)
    evidence_excerpt: str = Field(min_length=20, max_length=1500)
    valid_from: date
    valid_to: date
    keywords: list[str] = Field(min_length=1, max_length=8)
    mechanism: str = Field(min_length=20, max_length=1500)
    counterargument: str = Field(min_length=20, max_length=1500)
    falsifier: str = Field(min_length=20, max_length=1500)
    horizon_days: int = Field(ge=1, le=180)
    direction: str = Field(pattern=r"^(up|down|watch)$")

    @field_validator("evidence_url")
    @classmethod
    def url(cls, v):
        p = urlparse(v)
        if p.scheme != "https" or not p.hostname or p.username or p.password:
            raise ValueError("A public HTTPS source reference is required")
        return v

    @model_validator(mode="after")
    def dates(self):
        if self.valid_to < self.valid_from or self.source_symbol == self.target_symbol:
            raise ValueError("Use distinct issuers and a valid relationship interval")
        if any(not 3 <= len(x.strip()) <= 80 for x in self.keywords):
            raise ValueError("Keywords must be 3–80 characters")
        return self


def edge_out(row):
    return {
        "id": row.id,
        "source_symbol": row.source_symbol,
        "target_symbol": row.target_symbol,
        "approved": row.approved,
        "reviewed_by": row.reviewed_by,
        **row.payload,
    }


@connections_router.get("/catalog")
def starter_catalog(_user=Depends(get_admin_user)):
    from ..services.research_catalog import STARTER_RELATIONSHIPS

    return {
        "checked_at": "2026-10-17",
        "status": "unapproved source-backed examples",
        "relationships": STARTER_RELATIONSHIPS,
    }


@connections_router.post("/catalog/import")
def import_catalog(user=Depends(get_admin_user), session=Depends(get_session)):
    from ..services.research_catalog import STARTER_RELATIONSHIPS

    added, skipped = [], []
    for values in STARTER_RELATIONSHIPS:
        body = RelationshipIn(**values)
        symbols = (body.source_symbol, body.target_symbol)
        tracked = set(
            session.scalars(
                select(Stock.symbol).where(
                    Stock.symbol.in_(symbols), Stock.active.is_(True)
                )
            )
        )
        if not set(symbols) <= tracked:
            skipped.append(
                {
                    "target": body.target_symbol,
                    "reason": "Track both issuer symbols first",
                }
            )
            continue
        exists = session.scalars(
            select(ResearchRelationship).where(
                ResearchRelationship.source_symbol == body.source_symbol,
                ResearchRelationship.target_symbol == body.target_symbol,
            )
        ).all()
        if any(e.payload.get("evidence_url") == body.evidence_url for e in exists):
            skipped.append(
                {
                    "target": body.target_symbol,
                    "reason": "Source-backed relationship already imported",
                }
            )
            continue
        row = ResearchRelationship(
            source_symbol=body.source_symbol,
            target_symbol=body.target_symbol,
            payload=body.model_dump(
                mode="json", exclude={"source_symbol", "target_symbol"}
            ),
            approved=False,
        )
        session.add(row)
        session.flush()
        added.append(row.id)
    session.commit()
    return {
        "added": added,
        "skipped": skipped,
        "status": "All imported relationships still require review",
    }


@connections_router.get("/relationships")
def edges(_user=Depends(get_admin_user), session=Depends(get_session)):
    return [
        edge_out(r)
        for r in session.scalars(
            select(ResearchRelationship)
            .order_by(ResearchRelationship.id.desc())
            .limit(100)
        )
    ]


@connections_router.post("/relationships")
def edge_create(
    body: RelationshipIn, _user=Depends(get_admin_user), session=Depends(get_session)
):
    for symbol in (body.source_symbol, body.target_symbol):
        if (
            session.scalar(
                select(Stock.id).where(Stock.symbol == symbol, Stock.active.is_(True))
            )
            is None
        ):
            raise HTTPException(400, f"{symbol} is not a tracked stock")
    row = ResearchRelationship(
        source_symbol=body.source_symbol,
        target_symbol=body.target_symbol,
        payload=body.model_dump(
            mode="json", exclude={"source_symbol", "target_symbol"}
        ),
        approved=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return edge_out(row)


class ReviewIn(BaseModel):
    approve: bool


@connections_router.post("/relationships/{edge_id}/review")
def edge_review(
    edge_id: int,
    body: ReviewIn,
    user=Depends(get_admin_user),
    session=Depends(get_session),
):
    row = session.get(ResearchRelationship, edge_id)
    if row is None:
        raise HTTPException(404, "Relationship not found")
    row.approved = body.approve
    row.reviewed_by = user.id
    session.commit()
    return edge_out(row)


@connections_router.post("/propose")
def propose(_user=Depends(get_admin_user), session=Depends(get_session)):
    count = W.propose_connections(session)
    session.commit()
    return {
        "proposed": count,
        "delivery": "review queue only; no email, signals or orders",
    }


def card_out(c):
    return {
        "id": c.id,
        "symbol": c.symbol,
        "status": c.status,
        "payload": c.payload,
        "baseline": c.baseline,
        "published_at": c.published_at.isoformat() if c.published_at else None,
        "outcome": c.outcome,
    }


@connections_router.get("")
def cards(
    review_queue: bool = False,
    symbol: str | None = Query(None, max_length=20),
    user=Depends(get_current_user),
    session=Depends(get_session),
):
    from db import UserRole

    if review_queue and user.role != UserRole.ADMIN:
        raise HTTPException(403, "Admin review access required")
    query = select(EventConnection).where(
        EventConnection.status == "pending"
        if review_queue
        else EventConnection.status == "published"
    )
    if symbol:
        query = query.where(EventConnection.symbol == symbol)
    return [
        card_out(c)
        for c in session.scalars(query.order_by(EventConnection.id.desc()).limit(100))
    ]


@connections_router.get("/{card_id}")
def card_history(
    card_id: int, user=Depends(get_current_user), session=Depends(get_session)
):
    from db import UserRole

    row = session.get(EventConnection, card_id)
    if row is None or (
        row.status not in ("published", "superseded") and user.role != UserRole.ADMIN
    ):
        raise HTTPException(404, "Published research not found")
    return card_out(row)


@connections_router.post("/{card_id}/review")
def card_review(
    card_id: int,
    body: ReviewIn,
    user=Depends(get_admin_user),
    session=Depends(get_session),
):
    c = session.get(EventConnection, card_id)
    if c is None:
        raise HTTPException(404, "Connection not found")
    if c.status != "pending":
        raise HTTPException(409, "This immutable card has already been reviewed")
    edge = session.scalar(
        select(ResearchRelationship)
        .where(ResearchRelationship.id == c.relationship_id)
        .with_for_update()
    )
    same_event = (EventConnection.relationship_id == c.relationship_id) & (
        EventConnection.payload["event"]["url"].as_string() == c.payload["event"]["url"]
    )
    if body.approve and session.scalar(
        select(EventConnection.id).where(
            same_event,
            EventConnection.id > card_id,
            EventConnection.status == "published",
        )
    ):
        raise HTTPException(409, "A newer revision is already published")
    now = W.utcnow()
    if body.approve and (
        not edge.approved
        or not edge.payload["valid_from"]
        <= now.date().isoformat()
        <= edge.payload["valid_to"]
    ):
        raise HTTPException(409, "The relationship is unapproved or expired")
    values = {
        "status": "published" if body.approve else "rejected",
        "reviewed_by": user.id,
    }
    if body.approve:
        values.update(
            published_at=now, baseline=W.price_snapshot(session, c.symbol, now)
        )
    won = session.execute(
        update(EventConnection)
        .where(EventConnection.id == card_id, EventConnection.status == "pending")
        .values(**values)
    )
    if won.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "This card was reviewed concurrently")
    if body.approve:
        # Skip intermediate pending revisions without leaving an older publication live.
        session.execute(
            update(EventConnection)
            .where(
                same_event,
                EventConnection.id < card_id,
                EventConnection.status == "published",
            )
            .values(status="superseded")
        )
    session.commit()
    session.refresh(c)
    return card_out(c)
