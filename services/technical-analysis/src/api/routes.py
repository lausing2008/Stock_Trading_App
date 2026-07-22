"""TA REST endpoints: indicators, patterns, trendlines, S/R."""
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Price, Stock, TimeFrame, get_session

from ..indicators import bollinger_bands, cog, ema, fibonacci_retracement, macd, rsi, sma, supertrend
from ..indicators.trendlines import assess_breakout_quality, detect_accumulation_distribution, detect_fair_value_gaps, detect_sr_context, detect_support_resistance, detect_trendlines
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
        # AUD232-074: was raw .ewm(span=N, adjust=False).mean() with no min_periods —
        # this service's own core.py already defines and exports a canonical ema() with the
        # correct warmup-NaN convention (min_periods=window, matching sma/bollinger_bands/atr),
        # this endpoint just never called it.
        "ema_12": ema(df["close"], 12),
        "ema_20": ema(df["close"], 20),
        "ema_26": ema(df["close"], 26),
        "ema_50": ema(df["close"], 50),
        "rsi_14": rsi(df["close"], 14),
    }
    macd_df = macd(df["close"])
    out.update({c: macd_df[c] for c in macd_df.columns})
    bb = bollinger_bands(df["close"])
    out.update({c: bb[c] for c in bb.columns})

    cog_df = cog(df["close"])
    out.update({c: cog_df[c] for c in cog_df.columns})

    st = supertrend(df)
    out["supertrend"] = st["supertrend"]
    out["supertrend_trend"] = st["trend"]

    values = {
        k: [None if pd.isna(x) else float(x) for x in v.tolist()]
        for k, v in out.items()
    }
    return IndicatorOut(ts=[t.isoformat() for t in df["ts"]], values=values)


# ---------------------------------------------------------------------------
# Bulk pattern scan — module-level cache (TTL = 6 hours)
# NOTE: must be registered BEFORE /{symbol}/patterns or FastAPI will
# interpret "patterns" as a symbol value and return 404 for this route.
# ---------------------------------------------------------------------------
_patterns_bulk_cache: dict = {}  # {market_key: (timestamp, {symbol: [pattern_names]})}


@router.get("/patterns/bulk")
def get_patterns_bulk(
    market: str | None = None,
    session: Session = Depends(get_session),
):

    import time as _time

    market_key = market if market is not None else "__all__"
    now = _time.time()

    cached = _patterns_bulk_cache.get(market_key)
    if cached is not None:
        ts, data = cached
        if now - ts < 21600:
            return {"patterns": data, "count": len(data)}

    # Build stock query
    stmt = select(Stock).where(Stock.active == True)  # noqa: E712
    if market is not None:
        stmt = stmt.where(Stock.market == market)
    stocks = session.execute(stmt).scalars().all()

    since = date.today() - timedelta(days=400)
    result: dict = {}

    for stock in stocks:
        try:
            rows = session.execute(
                select(Price)
                .where(
                    Price.stock_id == stock.id,
                    Price.timeframe == TimeFrame("1d"),
                    Price.ts >= since,
                )
                .order_by(Price.ts)
            ).scalars().all()

            if len(rows) < 30:
                continue

            df = pd.DataFrame(
                {
                    "ts": [r.ts for r in rows],
                    "open": [r.open for r in rows],
                    "high": [r.high for r in rows],
                    "low": [r.low for r in rows],
                    "close": [r.close for r in rows],
                    "volume": [r.volume for r in rows],
                }
            )

            hits = detect_patterns(df)
            if hits:
                result[stock.symbol] = [p["name"] for p in hits]
        except Exception:
            continue

    _patterns_bulk_cache[market_key] = (now, result)
    return {"patterns": result, "count": len(result)}


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
    fvgs = detect_fair_value_gaps(df)
    swing = df.tail(90)
    swing_high = swing["high"].max()
    swing_low = swing["low"].min()
    import math as _math
    fib = fibonacci_retracement(float(swing_high), float(swing_low)) if not _math.isnan(swing_high) else {}
    # AUD-DUPLOGIC: sr_context (breakout/at_resistance/at_support/neutral classification) is
    # the canonical version signal-engine's own _sr_context() now delegates to over HTTP,
    # instead of independently reimplementing pivot detection with a different window/order —
    # see detect_sr_context()'s own docstring. Reuses the SAME `levels` already computed above
    # rather than detecting them a second time.
    sr_context = detect_sr_context(df, levels=levels)

    # T258-ACCUM-DIST-BREAKOUT-QUALITY: reuses the levels already computed above by
    # detect_sr_context() — no second level-detection pass. Uses sr_cleared_resistance/
    # sr_cleared_support (the level actually broken through), NOT sr_nearest_resistance/
    # sr_nearest_support (which are always still ahead of price, never yet reached).
    # assess_breakout_quality() itself returns None when nothing has broken in that direction.
    breakout_quality = None
    if sr_context.get("sr_cleared_resistance") is not None:
        breakout_quality = assess_breakout_quality(df, sr_context["sr_cleared_resistance"], direction="up")
    if breakout_quality is None and sr_context.get("sr_cleared_support") is not None:
        breakout_quality = assess_breakout_quality(df, sr_context["sr_cleared_support"], direction="down")

    return {
        "symbol": symbol,
        "support_resistance": [vars(L) for L in levels],
        "trendlines": [vars(T) for T in lines],
        "fair_value_gaps": [vars(G) for G in fvgs],
        "fibonacci": fib,
        "sr_context": sr_context,
        "accumulation_distribution": detect_accumulation_distribution(df),
        "breakout_quality": breakout_quality,
    }
