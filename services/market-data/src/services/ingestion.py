"""Ingestion service — incremental loads, validation, Parquet + Postgres sinks."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.config import get_settings
from common.logging import get_logger
from db import Price, SessionLocal, Stock, TimeFrame

from ..adapters import get_adapter, get_adapters

log = get_logger("ingestion")

_settings = get_settings()


class IngestionError(Exception):
    pass


def validate_ohlcv(df: pd.DataFrame, symbol: str, allow_zero_volume: bool = False) -> pd.DataFrame:
    """Reject bars with bad invariants (low>high, negative prices, etc).

    allow_zero_volume: yfinance's prepost=True intraday bars (T230-CHARTING-PREMARKET) commonly
    report volume=0 for real pre/post-market trades — a known yfinance quirk, not a sign of an
    invalid bar the way volume=0 would be on a regular-session bar. Without this, every single
    extended-hours bar was silently dropped, defeating the feature entirely (discovered via a
    real ingest showing 342/576 fetched bars dropped, all zero-volume, all outside 9:30-16:00 ET).
    Regular-session and daily bars keep the strict volume>0 check — real trading always has
    nonzero volume there.
    """
    if df.empty or not {"high", "low", "open", "close", "volume"}.issubset(df.columns):
        return df
    before = len(df)
    df = df.copy()
    df = df[(df["high"] >= df["low"]) & (df["high"] >= df["open"]) & (df["high"] >= df["close"])]
    df = df[(df["low"] <= df["open"]) & (df["low"] <= df["close"])]
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    if not allow_zero_volume:
        df = df[df["volume"] > 0]
    dropped = before - len(df)
    if dropped:
        log.warning("ohlcv.drop_invalid", symbol=symbol, dropped=dropped)
    return df


_PREMARKET_OPEN_ET = dtime(4, 0)
_MARKET_OPEN_ET = dtime(9, 30)
_MARKET_CLOSE_ET = dtime(16, 0)
_POSTMARKET_CLOSE_ET = dtime(20, 0)


# T230-CHARTING-PREMARKET: classify each intraday bar's timestamp as PRE/REGULAR/POST.
# US only — HK has no pre/post-market session concept (a pure cash-open market), and
# ingest_symbol always routes HK through yfinance with prepost=True harmlessly returning
# only regular-session bars for HK tickers (yfinance simply has no extended-hours data to add).
# ts here is UTC-naive (see adapters/base.py's _to_canonical intraday branch), so it's
# converted to US Eastern before comparing against the regular-session clock boundaries.
def _classify_session(ts: datetime, market: str) -> str:
    if market != "US":
        return "REGULAR"
    et = ts.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York"))
    t = et.time()
    if t < _PREMARKET_OPEN_ET or t >= _POSTMARKET_CLOSE_ET:
        return "REGULAR"  # outside yfinance's extended-hours window entirely — treat as regular
    if _MARKET_OPEN_ET <= t < _MARKET_CLOSE_ET:
        return "REGULAR"
    return "PRE" if t < _MARKET_OPEN_ET else "POST"


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
    force: bool = False,
) -> dict:
    """Idempotent incremental ingest — loads only bars newer than DB head.

    If force=True, deletes all existing price rows for the symbol+timeframe first,
    then re-fetches the full lookback_days window from scratch.
    """
    tf = TimeFrame(timeframe)

    with SessionLocal() as session:
        stock = session.execute(
            select(Stock).where(Stock.symbol == symbol)
        ).scalar_one_or_none()
        if stock is None:
            raise IngestionError(f"Unknown symbol: {symbol} (seed universe first)")

        if force:
            session.execute(
                delete(Price).where(Price.stock_id == stock.id, Price.timeframe == tf)
            )
            # F-2: do NOT commit here — keep DELETE and INSERT in one transaction so no
            # reader sees a gap window between the two operations.
            log.info("ingest.force_delete", symbol=symbol, tf=timeframe)

        head = None if force else _last_bar_ts(session, stock.id, tf)
        if head:
            if timeframe == "1d":
                # Look back 7 extra days so split-adjusted prices overwrite stale bars.
                start = head.date() - timedelta(days=7) + timedelta(days=1)
            else:
                # For intraday, re-fetch from the same calendar date as the last bar
                # so we pick up new bars that arrived after the last stored bar.
                # on_conflict_do_update handles duplicates safely.
                start = head.date()
        else:
            start = date.today() - timedelta(days=lookback_days)
        # yfinance only serves intraday bars within the last 60 days
        if timeframe in ("1m", "5m", "15m", "1h"):
            start = max(start, date.today() - timedelta(days=59))
        end = date.today() + timedelta(days=1)

        if start >= end:
            return {"symbol": symbol, "inserted": 0, "skipped": "up_to_date"}

        # Adapter selection strategy:
        #   - Explicit provider requested → use that provider
        #   - HK stocks → always yfinance (Polygon doesn't support HK)
        #   - Batch context (force or no existing bars) → yfinance (preserve Polygon quota for incremental)
        #   - US incremental → Polygon first (Polygon 429 fast-fails → yfinance fallback)
        if provider:
            adapters = [get_adapter(provider, market)]
        elif symbol.endswith(".HK") or market == "HK":
            adapters = [get_adapter("yfinance")]
        elif force or head is None:
            adapters = [get_adapter("yfinance")]
        else:
            adapters = get_adapters(market, timeframe)

        # T230-CHARTING-PREMARKET: only the US-intraday prepost=True path can legitimately
        # produce real zero-volume bars (yfinance's extended-hours quirk) — daily/weekly bars
        # and HK (no extended-hours session) keep the strict volume>0 invariant check.
        allow_zero_volume = market == "US" and timeframe not in ("1d", "1w")

        last_err: Exception | None = None
        df: pd.DataFrame | None = None
        for adapter in adapters:
            try:
                ohlcv = adapter.fetch_ohlcv(symbol, start, end, timeframe)
                candidate = validate_ohlcv(ohlcv.df, symbol, allow_zero_volume=allow_zero_volume)
                if not candidate.empty:
                    df = candidate
                    break
                log.warning("ingest.adapter_empty", adapter=adapter.name, symbol=symbol)
            except Exception as exc:
                log.warning("ingest.adapter_failed", adapter=adapter.name, symbol=symbol, error=str(exc))
                last_err = exc
        if df is None:
            if last_err:
                raise IngestionError(f"All adapters failed for {symbol}: {last_err}")
            return {"symbol": symbol, "inserted": 0, "skipped": "no_bars"}

        # Parquet write (partitioned by symbol)
        _write_parquet(df, symbol, timeframe)

        rows = [
            {
                "stock_id": stock.id,
                "ts": (_ts := r.ts.to_pydatetime() if hasattr(r.ts, "to_pydatetime") else r.ts),
                "timeframe": tf,
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": float(r.volume),
                "adj_close": float(r.adj_close) if pd.notna(r.adj_close) else None,
                "session": _classify_session(_ts, market) if timeframe not in ("1d", "1w") else "REGULAR",
            }
            for r in df.itertuples(index=False)
        ]

        stmt = pg_insert(Price).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["stock_id", "ts", "timeframe"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "adj_close": stmt.excluded.adj_close,
                "session": stmt.excluded.session,
            },
        )
        result = session.execute(stmt)
        session.commit()

        log.info("ingest.done", symbol=symbol, inserted=result.rowcount, tf=timeframe)
        return {"symbol": symbol, "inserted": result.rowcount, "tf": timeframe}


def _write_parquet(df: pd.DataFrame, symbol: str, timeframe: str) -> None:
    out = Path(_settings.parquet_dir) / f"timeframe={timeframe}" / f"symbol={symbol}"
    out.mkdir(parents=True, exist_ok=True)
    fname = out / f"{df['ts'].min().strftime('%Y%m%d')}_{df['ts'].max().strftime('%Y%m%d')}.parquet"
    df.to_parquet(fname, index=False)


def _bust_live_price_cache() -> None:
    try:
        import redis as redis_lib
        r = redis_lib.Redis.from_url(_settings.redis_url, decode_responses=True)
        r.delete("stockai:live_prices")
    except Exception:
        pass


def ingest_universe(symbols: list[str], timeframe: str = "1d", max_workers: int = 6, force: bool = False) -> list[dict]:
    """Fetch all symbols in parallel (I/O-bound — safe to thread)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch(sym: str) -> dict:
        try:
            return ingest_symbol(sym, timeframe=timeframe, force=force)
        except Exception as exc:
            log.error("ingest.symbol_failed", symbol=sym, error=str(exc))
            return {"symbol": sym, "error": str(exc)}

    results: list[dict] = [{}] * len(symbols)
    index = {sym: i for i, sym in enumerate(symbols)}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            results[index[sym]] = fut.result()
    _bust_live_price_cache()
    return results
