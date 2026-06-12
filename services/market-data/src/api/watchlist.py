"""Watchlist endpoints — per-user, multi-list support."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db import SignalAlert, Stock, User, Watchlist, WatchlistItem, get_session
from .auth import get_current_user

# ── Schemas ───────────────────────────────────────────────────────────────────

class WatchlistItemOut(BaseModel):
    symbol: str
    name: str
    name_zh: str | None = None
    market: str
    exchange: str
    sector: str | None = None
    currency: str
    added_at: str
    note: str | None = None

    class Config:
        from_attributes = True


class UpdateNoteRequest(BaseModel):
    note: str | None = None


class WatchlistOut(BaseModel):
    id: int
    name: str
    item_count: int
    trading_style: str | None = None
    created_at: str


class CreateWatchlistRequest(BaseModel):
    name: str
    trading_style: str | None = None


class RenameWatchlistRequest(BaseModel):
    name: str
    trading_style: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_default(session: Session, user: User) -> Watchlist:
    wl = session.execute(
        select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.created_at).limit(1)
    ).scalar_one_or_none()
    if not wl:
        wl = Watchlist(user_id=user.id, name="My Watchlist")
        session.add(wl)
        session.commit()
        session.refresh(wl)
    return wl


def _resolve(session: Session, list_id: int | None, user: User) -> Watchlist:
    if list_id is None:
        return _get_or_create_default(session, user)
    wl = session.execute(
        select(Watchlist).where(Watchlist.id == list_id, Watchlist.user_id == user.id)
    ).scalar_one_or_none()
    if not wl:
        raise HTTPException(404, "Watchlist not found")
    return wl


def _item_out(item: WatchlistItem, stock: Stock) -> WatchlistItemOut:
    return WatchlistItemOut(
        symbol=stock.symbol, name=stock.name, name_zh=stock.name_zh,
        market=stock.market, exchange=stock.exchange, sector=stock.sector,
        currency=stock.currency, added_at=item.added_at.isoformat(),
        note=item.note,
    )


# ── /watchlists — CRUD ────────────────────────────────────────────────────────

lists_router = APIRouter(prefix="/watchlists", tags=["watchlist"])


@lists_router.get("", response_model=list[WatchlistOut])
def list_watchlists(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(Watchlist, func.count(WatchlistItem.id).label("cnt"))
        .outerjoin(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
        .where(Watchlist.user_id == current.id)
        .group_by(Watchlist.id)
        .order_by(Watchlist.created_at)
    ).all()
    if not rows:
        # Auto-create default on first access
        wl = _get_or_create_default(session, current)
        return [WatchlistOut(id=wl.id, name=wl.name, item_count=0, trading_style=wl.trading_style, created_at=wl.created_at.isoformat())]
    return [WatchlistOut(id=wl.id, name=wl.name, item_count=cnt or 0, trading_style=wl.trading_style, created_at=wl.created_at.isoformat())
            for wl, cnt in rows]


@lists_router.post("", response_model=WatchlistOut)
def create_watchlist(
    body: CreateWatchlistRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    if session.execute(
        select(Watchlist).where(Watchlist.user_id == current.id, Watchlist.name == name)
    ).scalar_one_or_none():
        raise HTTPException(409, f"'{name}' already exists")
    wl = Watchlist(user_id=current.id, name=name, trading_style=body.trading_style)
    session.add(wl)
    session.commit()
    session.refresh(wl)
    return WatchlistOut(id=wl.id, name=wl.name, item_count=0, trading_style=wl.trading_style, created_at=wl.created_at.isoformat())


@lists_router.put("/{list_id}", response_model=WatchlistOut)
def rename_watchlist(
    list_id: int,
    body: RenameWatchlistRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    wl = _resolve(session, list_id, current)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    wl.name = name
    if body.trading_style is not None:
        wl.trading_style = body.trading_style if body.trading_style != '' else None
    session.commit()
    cnt = session.execute(select(func.count()).where(WatchlistItem.watchlist_id == wl.id)).scalar() or 0
    return WatchlistOut(id=wl.id, name=wl.name, item_count=cnt, trading_style=wl.trading_style, created_at=wl.created_at.isoformat())


@lists_router.delete("/{list_id}")
def delete_watchlist(
    list_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    wl = _resolve(session, list_id, current)
    total = session.execute(select(func.count()).where(Watchlist.user_id == current.id)).scalar() or 0
    if total <= 1:
        raise HTTPException(400, "Cannot delete the last watchlist")
    session.delete(wl)
    session.commit()
    return {"status": "deleted", "id": list_id}


# ── /watchlist — items ────────────────────────────────────────────────────────

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistItemOut])
def list_watchlist(
    list_id: int | None = Query(None),
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if list_id is not None:
        wl = _resolve(session, list_id, current)
        rows = session.execute(
            select(WatchlistItem, Stock)
            .join(Stock, WatchlistItem.stock_id == Stock.id)
            .where(WatchlistItem.watchlist_id == wl.id)
            .order_by(WatchlistItem.added_at.desc())
        ).all()
    else:
        # No list_id: return unique stocks across ALL user's watchlists (used by dashboard)
        rows = session.execute(
            select(WatchlistItem, Stock)
            .join(Stock, WatchlistItem.stock_id == Stock.id)
            .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
            .where(Watchlist.user_id == current.id)
            .order_by(WatchlistItem.added_at.desc())
        ).all()
        seen: set[str] = set()
        deduped = []
        for item, stock in rows:
            if stock.symbol not in seen:
                seen.add(stock.symbol)
                deduped.append((item, stock))
        rows = deduped
    return [_item_out(item, stock) for item, stock in rows]


@router.post("/{symbol}", response_model=WatchlistItemOut)
def add_to_watchlist(
    symbol: str,
    list_id: int | None = Query(None),
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    wl = _resolve(session, list_id, current)
    existing = session.execute(
        select(WatchlistItem).where(
            WatchlistItem.stock_id == stock.id,
            WatchlistItem.watchlist_id == wl.id,
        )
    ).scalar_one_or_none()
    if existing:
        return _item_out(existing, stock)
    item = WatchlistItem(stock_id=stock.id, watchlist_id=wl.id)
    session.add(item)

    # Auto-subscribe to signal alerts for this watchlist's horizon
    horizon = wl.trading_style or "SWING"
    existing_alert = session.execute(
        select(SignalAlert).where(
            SignalAlert.user_id == current.id,
            SignalAlert.symbol == stock.symbol,
            SignalAlert.horizon == horizon,
        )
    ).scalar_one_or_none()
    if not existing_alert:
        session.add(SignalAlert(
            user_id=current.id, symbol=stock.symbol,
            email=current.email, horizon=horizon,
        ))

    session.commit()
    session.refresh(item)
    return _item_out(item, stock)


@router.delete("/{symbol}")
def remove_from_watchlist(
    symbol: str,
    list_id: int | None = Query(None),
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    wl = _resolve(session, list_id, current)
    item = session.execute(
        select(WatchlistItem).where(
            WatchlistItem.stock_id == stock.id,
            WatchlistItem.watchlist_id == wl.id,
        )
    ).scalar_one_or_none()
    if item:
        session.delete(item)
        session.commit()
    return {"status": "removed", "symbol": symbol}


@router.patch("/{symbol}/note")
def update_note(
    symbol: str,
    body: UpdateNoteRequest,
    list_id: int | None = Query(None),
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """UI-2: Persist per-item note to the DB (was localStorage-only)."""
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    wl = _resolve(session, list_id, current)
    item = session.execute(
        select(WatchlistItem).where(
            WatchlistItem.stock_id == stock.id,
            WatchlistItem.watchlist_id == wl.id,
        )
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(404, "Stock not in watchlist")
    item.note = body.note
    session.commit()
    return {"status": "ok", "symbol": symbol, "note": item.note}
