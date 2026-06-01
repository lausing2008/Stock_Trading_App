"""Ranking API — per-symbol + market-wide leaderboard."""
from dataclasses import asdict
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import Price, Ranking, Stock, TimeFrame, get_session

from ..scoring import compute_kscore

# ── Sector → ETF mapping ──────────────────────────────────────────────────────
_SECTOR_ETF: dict[str, str] = {
    "Technology": "XLK",
    "Health Care": "XLV",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Financial Services": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Industrials": "XLI",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    "Telecommunications": "XLC",
}
_HK_BENCHMARK = "^HSI"   # Hang Seng Index for HK stocks
_US_FALLBACK  = "SPY"


_ETF_CACHE: dict[str, float | None] = {}


def _etf_20d_return(ticker: str) -> float | None:
    """Return 20-day price return for an ETF/index. Results cached for the process lifetime."""
    if ticker in _ETF_CACHE:
        return _ETF_CACHE[ticker]
    import time
    for attempt in range(3):
        try:
            hist = yf.Ticker(ticker).history(period="2mo")
            if hist.empty or len(hist) < 21:
                _ETF_CACHE[ticker] = None
                return None
            ret = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-21] - 1)
            _ETF_CACHE[ticker] = ret
            return ret
        except Exception:
            if attempt < 2:
                time.sleep(2 ** attempt)  # 1s, 2s backoff
    _ETF_CACHE[ticker] = None
    return None


def _prewarm_etf_cache() -> None:
    """Fetch all sector ETF returns once before a bulk refresh, with rate-limit spacing."""
    import time
    tickers = list(set(_SECTOR_ETF.values())) + [_HK_BENCHMARK, _US_FALLBACK]
    for t in tickers:
        if t not in _ETF_CACHE:
            _etf_20d_return(t)
            time.sleep(0.5)  # stay well under yfinance rate limits


def _rs_score(stock_ret: float, etf_ret: float | None) -> tuple[float, float]:
    """Return (rs_score 0-100, rs_rank) given stock and sector 20-day returns."""
    if etf_ret is None:
        return 50.0, 1.0
    denom = 1 + etf_ret if abs(etf_ret + 1) > 1e-6 else 1e-6
    rs_rank = (1 + stock_ret) / denom
    score = float(np.clip(50 + (rs_rank - 1.0) * 100, 0, 100))
    return round(score, 2), round(rs_rank, 4)

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


def _stock_rs(stock: "Stock", df: pd.DataFrame) -> tuple[float | None, float | None]:
    """Compute (rs_score, rs_rank) for a stock given its sector and price history."""
    if len(df) < 21:
        return None, None
    stock_ret = float(df["close"].iloc[-1] / df["close"].iloc[-21] - 1)
    if stock.market and str(stock.market.value).upper() == "HK":
        etf_ticker = _HK_BENCHMARK
    else:
        etf_ticker = _SECTOR_ETF.get(stock.sector or "", _US_FALLBACK)
    etf_ret = _etf_20d_return(etf_ticker)
    score, rs_rank = _rs_score(stock_ret, etf_ret)
    return score, rs_rank


@router.get("/{symbol}")
def rank_symbol(symbol: str, session: Session = Depends(get_session)):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    df = _load_prices(session, stock.id)
    if df.empty:
        raise HTTPException(404, f"No price data for {symbol}")
    rs_score_val, rs_rank = _stock_rs(stock, df)
    comp = compute_kscore(df, rs_score=rs_score_val)
    d = {k: _clean(v) for k, v in asdict(comp).items()}
    return {"symbol": symbol, "rs_rank": _clean(rs_rank), **d}


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
            "symbol":            stock.symbol,
            "name":              stock.name,
            "name_zh":           stock.name_zh,
            "market":            stock.market.value,
            "sector":            stock.sector,
            "score":             _clean(ranking.score),
            "technical":         _clean(ranking.technical),
            "momentum":          _clean(ranking.momentum),
            "value":             _clean(ranking.value),
            "growth":            _clean(ranking.growth),
            "volatility":        _clean(ranking.volatility),
            "fair_price":        _clean(ranking.fair_price),
            "relative_strength": _clean(ranking.rs_score),
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
        rs_score_val, _ = _stock_rs(s, df)
        comp = compute_kscore(df, rs_score=rs_score_val)
        results.append(
            {
                "symbol":            s.symbol,
                "name":              s.name,
                "name_zh":           s.name_zh,
                "market":            s.market.value,
                "sector":            s.sector,
                "score":             _clean(comp.score),
                "technical":         _clean(comp.technical),
                "momentum":          _clean(comp.momentum),
                "value":             _clean(comp.value),
                "growth":            _clean(comp.growth),
                "volatility":        _clean(comp.volatility),
                "fair_price":        _clean(comp.fair_price),
                "relative_strength": _clean(comp.relative_strength),
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
    from db import SessionLocal, Stock as StockModel

    _prewarm_etf_cache()  # fetch all sector ETFs once before the loop
    today = date.today()
    with SessionLocal() as session:
        rows = []
        for sid in stock_ids:
            stock = session.get(StockModel, sid)
            if not stock:
                continue
            df = _load_prices(session, sid)
            if df.empty or len(df) < 60:
                continue
            rs_score_val, _ = _stock_rs(stock, df)
            c = compute_kscore(df, rs_score=rs_score_val)
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
                    "rs_score": c.relative_strength,
                }
            )
        if rows:
            stmt = pg_insert(Ranking).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "as_of"],
                set_={col: stmt.excluded[col] for col in (
                    "score", "technical", "momentum", "value", "growth", "volatility", "fair_price", "rs_score"
                )},
            )
            session.execute(stmt)
            session.commit()
