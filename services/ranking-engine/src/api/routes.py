"""Ranking API — per-symbol + market-wide leaderboard."""
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta

import httpx
import numpy as np
import pandas as pd
try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    yf = None  # type: ignore[assignment]
    _HAS_YF = False
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db import Price, Ranking, Stock, TimeFrame, get_session

from ..scoring import compute_kscore

import os
_MARKET_DATA_URL = os.environ.get("MARKET_DATA_URL", "http://market-data:8001")

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


def _etf_20d_return(ticker: str, session: "Session | None" = None) -> float | None:
    """Return 20-day price return for an ETF/index. Reads from DB when session provided."""
    if ticker in _ETF_CACHE:
        return _ETF_CACHE[ticker]
    # DB path — ETFs are seeded as inactive stocks with full price history
    if session is not None and not ticker.startswith("^"):
        from sqlalchemy import select as sa_select
        stock = session.execute(
            sa_select(Stock).where(Stock.symbol == ticker)
        ).scalars().first()
        if stock:
            df = _load_prices(session, stock.id, lookback=60)
            if not df.empty and len(df) >= 21:
                ret = float(df["close"].iloc[-1] / df["close"].iloc[-21] - 1)
                _ETF_CACHE[ticker] = ret
                return ret
    # Fallback: yfinance (for ^HSI index and any ETF not yet in DB)
    if not _HAS_YF:
        _ETF_CACHE[ticker] = None
        return None
    try:
        hist = yf.Ticker(ticker).history(period="2mo")
        if hist.empty or len(hist) < 21:
            _ETF_CACHE[ticker] = None
            return None
        ret = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-21] - 1)
        _ETF_CACHE[ticker] = ret
        return ret
    except Exception:
        _ETF_CACHE[ticker] = None
        return None


def _prewarm_etf_cache(session: "Session") -> None:
    """Pre-load all sector ETF returns from DB before a bulk refresh."""
    tickers = list(set(_SECTOR_ETF.values())) + [_US_FALLBACK]
    for t in tickers:
        _etf_20d_return(t, session=session)
    # ^HSI via yfinance (not in DB)
    _etf_20d_return(_HK_BENCHMARK)


def _rs_score(stock_ret: float, etf_ret: float | None) -> tuple[float, float]:
    """Return (rs_score 0-100, rs_rank) given stock and sector 20-day returns."""
    if etf_ret is None:
        return 50.0, 1.0
    denom = 1 + etf_ret if abs(etf_ret + 1) > 1e-6 else 1e-6
    rs_rank = (1 + stock_ret) / denom
    score = float(np.clip(50 + (rs_rank - 1.0) * 100, 0, 100))
    return round(score, 2), round(rs_rank, 4)

router = APIRouter(prefix="/rankings", tags=["rankings"])

# ── Sector-relative fundamental scoring ──────────────────────────────────────

def _fetch_fundamentals_bulk() -> dict[str, dict]:
    """Fetch all cached fundamentals from market-data in one HTTP call.

    Returns {symbol: {trailing_pe, price_to_book, ev_to_ebitda, ...}} for every
    symbol that has a warm Redis cache entry. Symbols with no cache are omitted
    — they will fall back to the price-based K-Score proxies.
    """
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(f"{_MARKET_DATA_URL}/stocks/fundamentals_bulk")
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {}


def _percentile_rank(value: float, peer_values: list[float]) -> float:
    """Return percentile rank (0-100) of value among peer_values (higher = better rank)."""
    if not peer_values:
        return 50.0
    return sum(1 for v in peer_values if v < value) / len(peer_values) * 100


def _sector_relative_scores(
    fundamentals: dict[str, dict],
    stock_sectors: dict[str, str],
) -> dict[str, dict[str, float]]:
    """Compute sector-percentile-ranked value and growth scores for all stocks.

    For valuation metrics (PE, PB, EV/EBITDA) a lower value = cheaper = higher
    score, so we invert the percentile rank. For growth/quality metrics (revenue
    growth, earnings growth, ROE) a higher value = better = higher score.

    Returns {symbol: {"value": 0-100, "growth": 0-100}}.
    Stocks with fewer than 3 sector peers with valid data fall back to None
    (price proxy will be used instead).
    """
    # Group symbols by sector
    by_sector: dict[str, list[str]] = defaultdict(list)
    for symbol, sector in stock_sectors.items():
        if symbol in fundamentals:
            by_sector[sector or "Unknown"].append(symbol)

    result: dict[str, dict[str, float]] = {}

    for sector, symbols in by_sector.items():
        funds = {s: fundamentals[s] for s in symbols}

        # ── Valuation metrics: lower = cheaper = higher score ──────────────
        def _pos(key: str, cap: float = 1e6) -> dict[str, float]:
            return {
                s: f[key] for s, f in funds.items()
                if f.get(key) is not None and 0 < f[key] < cap
            }

        pe_map   = _pos("trailing_pe", 500)
        pb_map   = _pos("price_to_book", 100)
        ev_map   = _pos("ev_to_ebitda", 200)

        # ── Growth / quality metrics: higher = better = higher score ───────
        def _any(key: str) -> dict[str, float]:
            return {s: f[key] for s, f in funds.items() if f.get(key) is not None}

        rev_g_map  = _any("revenue_growth")
        earn_g_map = _any("earnings_growth")
        roe_map    = _any("return_on_equity")

        for symbol in symbols:
            val_parts: list[float] = []
            grow_parts: list[float] = []

            # Value: invert percentile (lower ratio → higher score)
            if symbol in pe_map and len(pe_map) >= 3:
                peers = list(pe_map.values())
                rank  = _percentile_rank(pe_map[symbol], peers)
                val_parts.append(100 - rank)  # invert

            if symbol in pb_map and len(pb_map) >= 3:
                peers = list(pb_map.values())
                rank  = _percentile_rank(pb_map[symbol], peers)
                val_parts.append(100 - rank)

            if symbol in ev_map and len(ev_map) >= 3:
                peers = list(ev_map.values())
                rank  = _percentile_rank(ev_map[symbol], peers)
                val_parts.append(100 - rank)

            # Growth: direct percentile (higher growth → higher score)
            if symbol in earn_g_map and len(earn_g_map) >= 3:
                grow_parts.append(_percentile_rank(earn_g_map[symbol], list(earn_g_map.values())))

            if symbol in rev_g_map and len(rev_g_map) >= 3:
                grow_parts.append(_percentile_rank(rev_g_map[symbol], list(rev_g_map.values())))

            if symbol in roe_map and len(roe_map) >= 3:
                grow_parts.append(_percentile_rank(roe_map[symbol], list(roe_map.values())))

            entry: dict[str, float] = {}
            if val_parts:
                entry["value"]  = round(sum(val_parts)  / len(val_parts),  2)
            if grow_parts:
                entry["growth"] = round(sum(grow_parts) / len(grow_parts), 2)

            if entry:
                result[symbol] = entry

    return result


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


def _stock_rs(stock: "Stock", df: pd.DataFrame, session: "Session | None" = None) -> tuple[float | None, float | None]:
    """Compute (rs_score, rs_rank) for a stock given its sector and price history."""
    if len(df) < 21:
        return None, None
    stock_ret = float(df["close"].iloc[-1] / df["close"].iloc[-21] - 1)
    if stock.market and str(stock.market.value).upper() == "HK":
        etf_ticker = _HK_BENCHMARK
    else:
        etf_ticker = _SECTOR_ETF.get(stock.sector or "", _US_FALLBACK)
    etf_ret = _etf_20d_return(etf_ticker, session=session)
    score, rs_rank = _rs_score(stock_ret, etf_ret)
    return score, rs_rank


@router.get("/sector_rotation")
def sector_rotation(
    market: str | None = None,
    session: Session = Depends(get_session),
):
    """Return sectors ranked by average relative strength vs their ETF benchmark.

    Includes RS momentum (change vs 5-7 days ago) and top/bottom stocks per sector.
    """
    # ── Current rankings ──────────────────────────────────────────────────────
    latest_subq = (
        select(Ranking.stock_id, func.max(Ranking.as_of).label("max_as_of"))
        .group_by(Ranking.stock_id)
        .subquery()
    )
    stmt = (
        select(Stock, Ranking)
        .join(Ranking, Stock.id == Ranking.stock_id)
        .join(latest_subq,
              (Ranking.stock_id == latest_subq.c.stock_id)
              & (Ranking.as_of == latest_subq.c.max_as_of))
        .where(Stock.active.is_(True))
        .where(Ranking.rs_score.isnot(None))
    )
    if market:
        stmt = stmt.where(Stock.market == market.upper())
    rows = list(session.execute(stmt).all())
    if not rows:
        return {"as_of": str(date.today()), "sectors": []}

    as_of = str(max(row[1].as_of for row in rows))

    # ── RS from 5–7 days ago for momentum ────────────────────────────────────
    pivot = date.today() - timedelta(days=7)
    past_subq = (
        select(Ranking.stock_id, func.max(Ranking.as_of).label("past_as_of"))
        .where(Ranking.as_of >= pivot)
        .where(Ranking.as_of < date.today() - timedelta(days=3))
        .group_by(Ranking.stock_id)
        .subquery()
    )
    past_stmt = (
        select(Ranking.stock_id, Ranking.rs_score)
        .join(past_subq,
              (Ranking.stock_id == past_subq.c.stock_id)
              & (Ranking.as_of == past_subq.c.past_as_of))
        .where(Ranking.rs_score.isnot(None))
    )
    past_rs: dict[int, float] = {
        sid: rs for sid, rs in session.execute(past_stmt).all() if rs is not None
    }

    # ── Group by sector ───────────────────────────────────────────────────────
    by_sector: dict[str, list[dict]] = defaultdict(list)
    for stock, ranking in rows:
        sector = stock.sector or "Unknown"
        by_sector[sector].append({
            "symbol":     stock.symbol,
            "name":       stock.name,
            "rs_score":   ranking.rs_score,
            "kscore":     ranking.score,
            "past_rs":    past_rs.get(stock.id),
        })

    sectors = []
    for sector, stocks in by_sector.items():
        rs_vals   = [s["rs_score"] for s in stocks if s["rs_score"] is not None]
        past_vals = [s["past_rs"]  for s in stocks if s["past_rs"]  is not None]
        if not rs_vals:
            continue
        avg_rs   = round(sum(rs_vals)   / len(rs_vals),   1)
        avg_past = round(sum(past_vals) / len(past_vals), 1) if past_vals else None
        rs_change = round(avg_rs - avg_past, 1) if avg_past is not None else None

        leading = sum(1 for v in rs_vals if v >= 60)
        lagging = sum(1 for v in rs_vals if v < 40)

        top = sorted(stocks, key=lambda x: x["rs_score"] or 0, reverse=True)[:5]
        bot = sorted(stocks, key=lambda x: x["rs_score"] or 0)[:3]

        sectors.append({
            "sector":       sector,
            "etf":          _SECTOR_ETF.get(sector, _US_FALLBACK),
            "avg_rs":       avg_rs,
            "rs_change":    rs_change,
            "stock_count":  len(stocks),
            "leading":      leading,
            "lagging":      lagging,
            "leading_pct":  round(leading / len(rs_vals) * 100),
            "top_stocks":   top,
            "bottom_stocks": bot,
        })

    sectors.sort(key=lambda s: s["avg_rs"], reverse=True)
    return {"as_of": as_of, "sectors": sectors}


@router.get("/{symbol}")
def rank_symbol(symbol: str, session: Session = Depends(get_session)):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    df = _load_prices(session, stock.id)
    if df.empty:
        raise HTTPException(404, f"No price data for {symbol}")
    rs_score_val, rs_rank = _stock_rs(stock, df, session=session)
    # Sector-relative scores for single-symbol live endpoint
    fundamentals = _fetch_fundamentals_bulk()
    stock_sectors = {symbol: (stock.sector or "Unknown")}
    sc = _sector_relative_scores(fundamentals, stock_sectors).get(symbol, {})
    comp = compute_kscore(
        df,
        rs_score=rs_score_val,
        value_score=sc.get("value"),
        growth_score=sc.get("growth"),
    )
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

    fundamentals  = _fetch_fundamentals_bulk()
    stock_sectors = {s.symbol: (s.sector or "Unknown") for s in stocks}
    sector_scores = _sector_relative_scores(fundamentals, stock_sectors)

    results = []
    for s in stocks:
        df = _load_prices(session, s.id)
        if df.empty or len(df) < 60:
            continue
        rs_score_val, _ = _stock_rs(s, df, session=session)
        sc   = sector_scores.get(s.symbol, {})
        comp = compute_kscore(
            df,
            rs_score=rs_score_val,
            value_score=sc.get("value"),
            growth_score=sc.get("growth"),
        )
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

    today = date.today()
    with SessionLocal() as session:
        _prewarm_etf_cache(session)  # load all sector ETF returns from DB once

        # Fetch fundamentals + build sector map for all stocks in this batch
        all_stocks = {
            s.id: s for s in session.execute(
                select(StockModel).where(StockModel.id.in_(stock_ids))
            ).scalars()
        }
        fundamentals = _fetch_fundamentals_bulk()
        stock_sectors = {s.symbol: (s.sector or "Unknown") for s in all_stocks.values()}
        sector_scores = _sector_relative_scores(fundamentals, stock_sectors)

        rows = []
        for sid in stock_ids:
            stock = all_stocks.get(sid)
            if not stock:
                continue
            df = _load_prices(session, sid)
            if df.empty or len(df) < 60:
                continue
            rs_score_val, _ = _stock_rs(stock, df, session=session)
            sc = sector_scores.get(stock.symbol, {})
            c = compute_kscore(
                df,
                rs_score=rs_score_val,
                value_score=sc.get("value"),
                growth_score=sc.get("growth"),
            )
            rows.append(
                {
                    "stock_id": sid,
                    "as_of": today,
                    "score":     _clean(c.score),
                    "technical": _clean(c.technical),
                    "momentum":  _clean(c.momentum),
                    "value":     _clean(c.value),
                    "growth":    _clean(c.growth),
                    "volatility":_clean(c.volatility),
                    "fair_price":_clean(c.fair_price),
                    "rs_score":  _clean(c.relative_strength),
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
