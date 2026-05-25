"""Trade Journal endpoints — per-user CRUD for logged trades."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import TradeJournal, User, get_session
from .auth import get_current_user

router = APIRouter(prefix="/journal", tags=["journal"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class TradeIn(BaseModel):
    symbol: str
    action: str  # BUY | SELL_SHORT
    shares: float
    entry_price: float
    exit_price: float | None = None
    entry_date: date
    exit_date: date | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy: str | None = None
    signal_confidence: float | None = None
    notes: str | None = None


class TradeOut(BaseModel):
    id: int
    symbol: str
    action: str
    shares: float
    entry_price: float
    exit_price: float | None
    entry_date: str
    exit_date: str | None
    stop_loss: float | None
    take_profit: float | None
    strategy: str | None
    signal_confidence: float | None
    notes: str | None
    created_at: str

    class Config:
        from_attributes = True


def _out(t: TradeJournal) -> TradeOut:
    return TradeOut(
        id=t.id,
        symbol=t.symbol,
        action=t.action,
        shares=t.shares,
        entry_price=t.entry_price,
        exit_price=t.exit_price,
        entry_date=t.entry_date.isoformat(),
        exit_date=t.exit_date.isoformat() if t.exit_date else None,
        stop_loss=t.stop_loss,
        take_profit=t.take_profit,
        strategy=t.strategy,
        signal_confidence=t.signal_confidence,
        notes=t.notes,
        created_at=t.created_at.isoformat(),
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[TradeOut])
def list_trades(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(TradeJournal)
        .where(TradeJournal.user_id == current.id)
        .order_by(TradeJournal.entry_date.desc(), TradeJournal.created_at.desc())
    ).scalars().all()
    return [_out(t) for t in rows]


@router.post("", response_model=TradeOut)
def create_trade(
    body: TradeIn,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    trade = TradeJournal(
        user_id=current.id,
        symbol=body.symbol.upper(),
        action=body.action,
        shares=body.shares,
        entry_price=body.entry_price,
        exit_price=body.exit_price,
        entry_date=body.entry_date,
        exit_date=body.exit_date,
        stop_loss=body.stop_loss,
        take_profit=body.take_profit,
        strategy=body.strategy,
        signal_confidence=body.signal_confidence,
        notes=body.notes,
    )
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return _out(trade)


@router.put("/{trade_id}", response_model=TradeOut)
def update_trade(
    trade_id: int,
    body: TradeIn,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    trade = session.execute(
        select(TradeJournal).where(TradeJournal.id == trade_id, TradeJournal.user_id == current.id)
    ).scalar_one_or_none()
    if not trade:
        raise HTTPException(404, "Trade not found")
    trade.symbol = body.symbol.upper()
    trade.action = body.action
    trade.shares = body.shares
    trade.entry_price = body.entry_price
    trade.exit_price = body.exit_price
    trade.entry_date = body.entry_date
    trade.exit_date = body.exit_date
    trade.stop_loss = body.stop_loss
    trade.take_profit = body.take_profit
    trade.strategy = body.strategy
    trade.signal_confidence = body.signal_confidence
    trade.notes = body.notes
    session.commit()
    session.refresh(trade)
    return _out(trade)


@router.delete("/{trade_id}")
def delete_trade(
    trade_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    trade = session.execute(
        select(TradeJournal).where(TradeJournal.id == trade_id, TradeJournal.user_id == current.id)
    ).scalar_one_or_none()
    if not trade:
        raise HTTPException(404, "Trade not found")
    session.delete(trade)
    session.commit()
    return {"status": "deleted", "id": trade_id}
