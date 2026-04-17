"""Ingestion service — incremental loads, validation, Parquet + Postgres sinks."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.config import get_settings
from common.logging import get_logger
from db import Price, SessionLocal, Stock, TimeFrame

from ..adapters import get_adapter

log = get_logger("ingestion")

_settings = get_settings()


class IngestionError(Exception):
    pass


def validate_ohlcv(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Reject bars with bad invariants (low>high, negative prices, etc)."""
    before = len(df)
    df = df.copy()
    df = df[(df["high"] >= df["low"]) & (df["high"] >= df["open"]) & (df["high"] >= df["close"])]
    df = df[(df["low"] <= df["open"]) & (df["low"] <= df["close"])]
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df = df[df["volume"] >= 0]
    dropped = before - len(df)
    if dropped:
        log.warning("ohlcv.drop_invalid", symbol=symbol, dropped=dropped)
    return df


def _last_bar_ts(session, stock_id: int, timeframe: TimeFrame) -> datetime | None:
    stmt = (
        select(Price.ts)
        .where(Price.stock_id == stock_id, Price.timeframe == timeframe)
        .order_by(Price.ts.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def ingest_symbol(
    symbol: str,
    market: str = "US",
    timeframe: str = "1d",
    lookback_days: int = 365 * 3,
    provider: str | None = None,
) -> dict:
    """Idempotent incremental ingest — loads only bars newer than DB head."""
    adapter = get_adapter(provider, market)
    tf = TimeFrame(timeframe)

    with SessionLocal() as session:
        stock = session.execute(
            select(Stock).where(Stock.symbol == symbol)
        ).scalar_one_or_none()
        if stock is None:
            raise IngestionError(f"Unknown symbol: {symbol} (seed universe first)")

        head = _last_bar_ts(session, stock.id, tf)
        start = (head.date() + timedelta(days=1)) if head else (date.today() - timedelta(days=lookback_days))
        end = date.today() + timedelta(days=1)

        if start >= end:
            return {"symbol": symbol, "inserted": 0, "skipped": "up_to_date"}

        ohlcv = adapter.fetch_ohlcv(symbol, start, end, timeframe)
        df = validate_ohlcv(ohlcv.df, symbol)
        if df.empty:
            return {"symbol": symbol, "inserted": 0, "skipped": "no_bars"}

        # Parquet write (partitioned by symbol)
        _write_parquet(df, symbol, timeframe)

        rows = [
            {
                "stock_id": stock.id,
                "ts": r.ts.to_pydatetime() if hasattr(r.ts, "to_pydatetime") else r.ts,
                "timeframe": tf,
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": float(r.volume),
                "adj_close": float(r.adj_close) if pd.notna(r.adj_close) else None,
            }
            for r in df.itertuples(index=False)
        ]

        stmt = pg_insert(Price).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["stock_id", "ts", "timeframe"])
        result = session.execute(stmt)
        session.commit()

        log.info("ingest.done", symbol=symbol, inserted=result.rowcount, tf=timeframe)
        return {"symbol": symbol, "inserted": result.rowcount, "tf": timeframe}


def _write_parquet(df: pd.DataFrame, symbol: str, timeframe: str) -> None:
    out = Path(_settings.parquet_dir) / f"timeframe={timeframe}" / f"symbol={symbol}"
    out.mkdir(parents=True, exist_ok=True)
    fname = out / f"{df['ts'].min().strftime('%Y%m%d')}_{df['ts'].max().strftime('%Y%m%d')}.parquet"
    df.to_parquet(fname, index=False)


def ingest_universe(symbols: list[str], timeframe: str = "1d") -> list[dict]:
    results = []
    for sym in symbols:
        try:
            results.append(ingest_symbol(sym, timeframe=timeframe))
        except Exception as exc:  # per-symbol fault isolation
            log.error("ingest.symbol_failed", symbol=sym, error=str(exc))
            results.append({"symbol": sym, "error": str(exc)})
    return results
