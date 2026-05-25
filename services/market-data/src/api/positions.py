"""Positions — per-user portfolio positions with embedded trade history."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from db import UserPosition, PositionTrade, UserCash, User, get_session
from .auth import get_current_user

router = APIRouter(prefix="/positions", tags=["positions"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class TradeOut(BaseModel):
    id: int
    type: str
    shares: float
    price: float
    date: str


class PositionOut(BaseModel):
    id: int
    symbol: str
    shares: float
    avg_cost: float
    currency: str
    added_at: str
    trades: list[TradeOut]


class AddPositionIn(BaseModel):
    symbol: str
    shares: float
    price: float
    currency: str = "USD"


class TradeIn(BaseModel):
    shares: float
    price: float


class CashIn(BaseModel):
    USD: float = 0.0
    HKD: float = 0.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _trade_out(t: PositionTrade) -> TradeOut:
    return TradeOut(id=t.id, type=t.type, shares=t.shares, price=t.price, date=t.date.isoformat())


def _pos_out(p: UserPosition) -> PositionOut:
    sorted_trades = sorted(p.trades, key=lambda t: t.date, reverse=True)
    return PositionOut(
        id=p.id,
        symbol=p.symbol,
        shares=p.shares,
        avg_cost=p.avg_cost,
        currency=p.currency,
        added_at=p.added_at.isoformat(),
        trades=[_trade_out(t) for t in sorted_trades],
    )


def _fetch_pos(position_id: int, user_id: int, session: Session) -> UserPosition:
    pos = session.execute(
        select(UserPosition)
        .where(UserPosition.id == position_id, UserPosition.user_id == user_id)
        .options(selectinload(UserPosition.trades))
    ).scalar_one_or_none()
    if not pos:
        raise HTTPException(404, "Position not found")
    return pos


# ── Cash routes (must be defined before /{position_id} to avoid int-parse) ───

@router.get("/cash")
def get_cash(current: User = Depends(get_current_user), session: Session = Depends(get_session)):
    rows = session.execute(
        select(UserCash).where(UserCash.user_id == current.id)
    ).scalars().all()
    result = {"USD": 0.0, "HKD": 0.0}
    for r in rows:
        if r.currency in result:
            result[r.currency] = r.amount
    return result


@router.put("/cash")
def update_cash(
    body: CashIn,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    for currency, amount in [("USD", body.USD), ("HKD", body.HKD)]:
        row = session.execute(
            select(UserCash).where(UserCash.user_id == current.id, UserCash.currency == currency)
        ).scalar_one_or_none()
        val = max(0.0, amount)
        if row:
            row.amount = val
        else:
            session.add(UserCash(user_id=current.id, currency=currency, amount=val))
    session.commit()
    return {"USD": body.USD, "HKD": body.HKD}


# ── Position CRUD ─────────────────────────────────────────────────────────────

@router.get("", response_model=list[PositionOut])
def list_positions(
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(UserPosition)
        .where(UserPosition.user_id == current.id)
        .options(selectinload(UserPosition.trades))
        .order_by(UserPosition.added_at.asc())
    ).scalars().all()
    return [_pos_out(p) for p in rows]


@router.post("", response_model=PositionOut)
def add_position(
    body: AddPositionIn,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    pos = UserPosition(
        user_id=current.id,
        symbol=body.symbol.upper(),
        shares=body.shares,
        avg_cost=body.price,
        currency=body.currency,
    )
    session.add(pos)
    session.flush()
    session.add(PositionTrade(
        user_id=current.id, position_id=pos.id, type="BUY",
        shares=body.shares, price=body.price,
    ))
    session.commit()
    pos = session.execute(
        select(UserPosition).where(UserPosition.id == pos.id).options(selectinload(UserPosition.trades))
    ).scalar_one()
    return _pos_out(pos)


@router.post("/{position_id}/buy", response_model=PositionOut)
def buy_more(
    position_id: int,
    body: TradeIn,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    pos = _fetch_pos(position_id, current.id, session)
    total = pos.shares + body.shares
    pos.avg_cost = (pos.shares * pos.avg_cost + body.shares * body.price) / total
    pos.shares = total
    session.add(PositionTrade(
        user_id=current.id, position_id=pos.id, type="BUY",
        shares=body.shares, price=body.price,
    ))
    session.commit()
    pos = session.execute(
        select(UserPosition).where(UserPosition.id == pos.id).options(selectinload(UserPosition.trades))
    ).scalar_one()
    return _pos_out(pos)


@router.post("/{position_id}/sell")
def sell_shares(
    position_id: int,
    body: TradeIn,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    pos = _fetch_pos(position_id, current.id, session)
    remaining = pos.shares - body.shares
    if remaining <= 0:
        session.delete(pos)
        session.commit()
        return Response(status_code=204)
    session.add(PositionTrade(
        user_id=current.id, position_id=pos.id, type="SELL",
        shares=body.shares, price=body.price,
    ))
    pos.shares = remaining
    session.commit()
    pos = session.execute(
        select(UserPosition).where(UserPosition.id == pos.id).options(selectinload(UserPosition.trades))
    ).scalar_one()
    return _pos_out(pos)


@router.delete("/{position_id}")
def remove_position(
    position_id: int,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    pos = session.execute(
        select(UserPosition).where(UserPosition.id == position_id, UserPosition.user_id == current.id)
    ).scalar_one_or_none()
    if not pos:
        raise HTTPException(404, "Position not found")
    session.delete(pos)
    session.commit()
    return {"status": "deleted", "id": position_id}
