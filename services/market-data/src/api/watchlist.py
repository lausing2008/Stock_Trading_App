"""Watchlist endpoints — per-user add/remove/list watched symbols."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Stock, User, WatchlistItem, get_session
from .auth import get_current_user

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistItemOut(BaseModel):
    symbol: str
    name: str
    name_zh: str | None = None
    market: str
    exchange: str
    sector: str | None = None
    currency: str
    added_at: str

    class Config:
        from_attributes = True


@router.get("", response_model=list[WatchlistItemOut])
def list_watchlist(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(WatchlistItem, Stock)
        .join(Stock, WatchlistItem.stock_id == Stock.id)
        .where(WatchlistItem.user_id == current.id)
        .order_by(WatchlistItem.added_at.desc())
    ).all()
    return [
        WatchlistItemOut(
            symbol=stock.symbol, name=stock.name, name_zh=stock.name_zh,
            market=stock.market, exchange=stock.exchange, sector=stock.sector,
            currency=stock.currency, added_at=item.added_at.isoformat(),
        )
        for item, stock in rows
    ]


@router.post("/{symbol}", response_model=WatchlistItemOut)
def add_to_watchlist(
    symbol: str,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    existing = session.execute(
        select(WatchlistItem).where(
            WatchlistItem.stock_id == stock.id,
            WatchlistItem.user_id == current.id,
        )
    ).scalar_one_or_none()
    if existing:
        return WatchlistItemOut(
            symbol=stock.symbol, name=stock.name, market=stock.market,
            exchange=stock.exchange, sector=stock.sector, currency=stock.currency,
            added_at=existing.added_at.isoformat(),
        )
    item = WatchlistItem(stock_id=stock.id, user_id=current.id)
    session.add(item)
    session.commit()
    session.refresh(item)
    return WatchlistItemOut(
        symbol=stock.symbol, name=stock.name, market=stock.market,
        exchange=stock.exchange, sector=stock.sector, currency=stock.currency,
        added_at=item.added_at.isoformat(),
    )


@router.delete("/{symbol}")
def remove_from_watchlist(
    symbol: str,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    item = session.execute(
        select(WatchlistItem).where(
            WatchlistItem.stock_id == stock.id,
            WatchlistItem.user_id == current.id,
        )
    ).scalar_one_or_none()
    if item:
        session.delete(item)
        session.commit()
    return {"status": "removed", "symbol": symbol}
