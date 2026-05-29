"""Ranking API — per-symbol + market-wide leaderboard."""
from dataclasses import asdict
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import Price, Ranking, Stock, TimeFrame, get_session

from ..scoring import compute_kscore

router = APIRouter(prefix="/rankings", tags=["rankings"])


def _clean(v):
    """Return None for NaN/Inf so the response stays JSON-safe."""
    import math
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


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
    d = {k: _clean(v) for k, v in asdict(comp).items()}
    return {"symbol": symbol, **d}


@router.get("")
def leaderboard(
    market: str | None = None,
    limit: int = Query(500, le=500),
    session: Session = Depends(get_session),
):
    """Return the pre-computed leaderboard from the Ranking table.

    Rankings are refreshed by the scheduler (5×/day on market days). Reading
    from the persisted table avoids recomputing scores for all stocks on every
    page load, which would otherwise be O(N_stocks × price_history) per request.
    Falls back to live computation only when no cached data exists (first run).
    """
    # Latest ranking date per stock
    latest_subq = (
        select(Ranking.stock_id, func.max(Ranking.as_of).label("max_as_of"))
        .group_by(Ranking.stock_id)
        .subquery()
    )
    stmt = (
        select(Stock, Ranking)
        .join(Ranking, Stock.id == Ranking.stock_id)
        .join(
            latest_subq,
            (Ranking.stock_id == latest_subq.c.stock_id)
            & (Ranking.as_of == latest_subq.c.max_as_of),
        )
        .where(Stock.active.is_(True))
    )
    if market:
        stmt = stmt.where(Stock.market == market.upper())

    rows = list(session.execute(stmt).all())

    if not rows:
        # No persisted rankings yet — compute live on first run
        return _leaderboard_live(market, limit, session)

    results = [
        {
            "symbol":    stock.symbol,
            "name":      stock.name,
            "name_zh":   stock.name_zh,
            "market":    stock.market.value,
            "sector":    stock.sector,
            "score":     _clean(ranking.score),
            "technical": _clean(ranking.technical),
            "momentum":  _clean(ranking.momentum),
            "value":     _clean(ranking.value),
            "growth":    _clean(ranking.growth),
            "volatility":_clean(ranking.volatility),
            "fair_price":_clean(ranking.fair_price),
        }
        for stock, ranking in rows
    ]
    results.sort(key=lambda r: r["score"] or 0, reverse=True)
    as_of = str(max((row[1].as_of for row in rows), default=date.today()))
    return {"as_of": as_of, "rankings": results[:limit]}


def _leaderboard_live(market: str | None, limit: int, session: Session) -> dict:
    """Fallback: compute rankings live when no persisted data exists."""
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
                "symbol":    s.symbol,
                "name":      s.name,
                "name_zh":   s.name_zh,
                "market":    s.market.value,
                "sector":    s.sector,
                "score":     _clean(comp.score),
                "technical": _clean(comp.technical),
                "momentum":  _clean(comp.momentum),
                "value":     _clean(comp.value),
                "growth":    _clean(comp.growth),
                "volatility":_clean(comp.volatility),
                "fair_price":_clean(comp.fair_price),
            }
        )
    results.sort(key=lambda r: r["score"] or 0, reverse=True)
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
