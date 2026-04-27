"""Ranking API — per-symbol + market-wide leaderboard."""
from dataclasses import asdict
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import Price, Ranking, Stock, TimeFrame, get_session

from ..scoring import compute_kscore

router = APIRouter(prefix="/rankings", tags=["rankings"])


def _load_prices(session: Session, stock_id: int, lookback: int = 300) -> pd.DataFrame:
    since = date.today() - timedelta(days=lookback * 2)
    rows = session.execute(
        select(Price)
        .where(Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1, Price.ts >= since)
        .order_by(Price.ts)
    ).scalars().all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        {
            "ts": [r.ts for r in rows],
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        }
    )


@router.get("/{symbol}")
def rank_symbol(symbol: str, session: Session = Depends(get_session)):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    df = _load_prices(session, stock.id)
    if df.empty:
        raise HTTPException(404, f"No price data for {symbol}")
    comp = compute_kscore(df)
    return {"symbol": symbol, **asdict(comp)}


@router.get("")
def leaderboard(
    market: str | None = None,
    limit: int = Query(50, le=500),
    session: Session = Depends(get_session),
):
    stmt = select(Stock).where(Stock.active.is_(True))
    if market:
        stmt = stmt.where(Stock.market == market.upper())
    stocks = list(session.execute(stmt).scalars())

    results = []
    for s in stocks:
        df = _load_prices(session, s.id)
        if df.empty or len(df) < 60:
            continue
        comp = compute_kscore(df)
        results.append(
            {
                "symbol": s.symbol,
                "name": s.name,
                "name_zh": s.name_zh,
                "market": s.market.value,
                "sector": s.sector,
                "score": comp.score,
                "technical": comp.technical,
                "momentum": comp.momentum,
                "value": comp.value,
                "growth": comp.growth,
                "volatility": comp.volatility,
                "fair_price": comp.fair_price,
            }
        )
    results.sort(key=lambda r: r["score"], reverse=True)
    return {"as_of": str(date.today()), "rankings": results[:limit]}


@router.post("/refresh")
def refresh(
    tasks: BackgroundTasks,
    market: str | None = None,
    session: Session = Depends(get_session),
):
    """Compute + persist rankings for the whole universe."""
    stmt = select(Stock).where(Stock.active.is_(True))
    if market:
        stmt = stmt.where(Stock.market == market.upper())
    stocks = list(session.execute(stmt).scalars())

    tasks.add_task(_persist_rankings, [s.id for s in stocks])
    return {"status": "scheduled", "count": len(stocks)}


def _persist_rankings(stock_ids: list[int]) -> None:
    from db import SessionLocal

    today = date.today()
    with SessionLocal() as session:
        rows = []
        for sid in stock_ids:
            df = _load_prices(session, sid)
            if df.empty or len(df) < 60:
                continue
            c = compute_kscore(df)
            rows.append(
                {
                    "stock_id": sid,
                    "as_of": today,
                    "score": c.score,
                    "technical": c.technical,
                    "momentum": c.momentum,
                    "value": c.value,
                    "growth": c.growth,
                    "volatility": c.volatility,
                    "fair_price": c.fair_price,
                }
            )
        if rows:
            stmt = pg_insert(Ranking).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "as_of"],
                set_={c: stmt.excluded[c] for c in ("score", "technical", "momentum", "value", "growth", "volatility", "fair_price")},
            )
            session.execute(stmt)
            session.commit()
