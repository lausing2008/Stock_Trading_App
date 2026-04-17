"""/stocks, /stocks/{symbol}/prices — read API for market data."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Price, Stock, TimeFrame, get_session

router = APIRouter(prefix="/stocks", tags=["stocks"])


class StockOut(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
    exchange: str
    sector: str | None = None
    currency: str

    class Config:
        from_attributes = True


class PriceOut(BaseModel):
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_close: float | None = None


@router.get("", response_model=list[StockOut])
def list_stocks(
    market: str | None = None,
    limit: int = Query(200, le=5000),
    session: Session = Depends(get_session),
):
    stmt = select(Stock).where(Stock.active.is_(True))
    if market:
        stmt = stmt.where(Stock.market == market.upper())
    return list(session.execute(stmt.limit(limit)).scalars())


@router.get("/{symbol}", response_model=StockOut)
def get_stock(symbol: str, session: Session = Depends(get_session)):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    return stock


@router.get("/{symbol}/prices", response_model=list[PriceOut])
def get_prices(
    symbol: str,
    timeframe: str = "1d",
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(1000, le=10000),
    session: Session = Depends(get_session),
):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    if not start:
        start = date.today() - timedelta(days=365)
    if not end:
        end = date.today()

    stmt = (
        select(Price)
        .where(
            Price.stock_id == stock.id,
            Price.timeframe == TimeFrame(timeframe),
            Price.ts >= start,
            Price.ts <= end,
        )
        .order_by(Price.ts)
        .limit(limit)
    )
    rows = list(session.execute(stmt).scalars())
    return [
        PriceOut(
            ts=r.ts.isoformat(),
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.volume,
            adj_close=r.adj_close,
        )
        for r in rows
    ]
