"""TA REST endpoints: indicators, patterns, trendlines, S/R."""
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Price, Stock, TimeFrame, get_session

from ..indicators import bollinger_bands, fibonacci_retracement, macd, rsi, sma, vwap
from ..indicators.trendlines import detect_support_resistance, detect_trendlines
from ..patterns import detect_patterns

router = APIRouter(prefix="/ta", tags=["technical-analysis"])


def _load_prices(session: Session, symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    since = date.today() - timedelta(days=days)
    rows = session.execute(
        select(Price)
        .where(Price.stock_id == stock.id, Price.timeframe == TimeFrame(timeframe), Price.ts >= since)
        .order_by(Price.ts)
    ).scalars().all()
    if not rows:
        raise HTTPException(404, f"No price data for {symbol} — run ingestion")
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


class IndicatorOut(BaseModel):
    ts: list[str]
    values: dict[str, list[float | None]]


@router.get("/{symbol}/indicators", response_model=IndicatorOut)
def get_indicators(
    symbol: str,
    timeframe: str = "1d",
    days: int = Query(400, le=2000),
    session: Session = Depends(get_session),
):
    df = _load_prices(session, symbol, timeframe, days)
    out = {
        "sma_20": sma(df["close"], 20),
        "sma_50": sma(df["close"], 50),
        "sma_200": sma(df["close"], 200),
        "ema_12": df["close"].ewm(span=12, adjust=False).mean(),
        "ema_26": df["close"].ewm(span=26, adjust=False).mean(),
        "rsi_14": rsi(df["close"], 14),
        "vwap": vwap(df["high"], df["low"], df["close"], df["volume"]),
    }
    macd_df = macd(df["close"])
    out.update({c: macd_df[c] for c in macd_df.columns})
    bb = bollinger_bands(df["close"])
    out.update({c: bb[c] for c in bb.columns})

    values = {
        k: [None if pd.isna(x) else float(x) for x in v.tolist()]
        for k, v in out.items()
    }
    return IndicatorOut(ts=[t.isoformat() for t in df["ts"]], values=values)


@router.get("/{symbol}/patterns")
def get_patterns(
    symbol: str,
    timeframe: str = "1d",
    days: int = 400,
    session: Session = Depends(get_session),
):
    df = _load_prices(session, symbol, timeframe, days)
    return {"symbol": symbol, "patterns": detect_patterns(df)}


@router.get("/{symbol}/levels")
def get_levels(
    symbol: str,
    timeframe: str = "1d",
    days: int = 400,
    session: Session = Depends(get_session),
):
    df = _load_prices(session, symbol, timeframe, days)
    levels = detect_support_resistance(df)
    lines = detect_trendlines(df)
    fib = fibonacci_retracement(float(df["high"].max()), float(df["low"].min()))
    return {
        "symbol": symbol,
        "support_resistance": [vars(L) for L in levels],
        "trendlines": [vars(T) for T in lines],
        "fibonacci": fib,
    }
