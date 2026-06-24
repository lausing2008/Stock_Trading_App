"""Ranking API — per-symbol + market-wide leaderboard."""
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
import threading
import time as _time

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

from common.jwt_auth import get_current_username
from db import Fundamental, Price, Ranking, Signal, SignalType, Stock, TimeFrame, get_session

from ..scoring import compute_kscore

import os
_MARKET_DATA_URL = os.environ.get("MARKET_DATA_URL", "http://market-data:8001")
_TA_URL = os.environ.get("TA_URL", "http://technical-analysis:8006")

_patterns_cache_ts: float = 0.0
_patterns_cache_data: dict = {}


def _fetch_patterns_bulk() -> dict[str, list[str]]:
    """Fetch pre-computed patterns from TA service. Module-level 6h cache."""
    global _patterns_cache_ts, _patterns_cache_data
    if _time.time() - _patterns_cache_ts < 21600:
        return _patterns_cache_data
    try:
        with httpx.Client(timeout=90) as c:
            r = c.get(f"{_TA_URL}/ta/patterns/bulk", timeout=90)
            if r.status_code == 200:
                _patterns_cache_data = r.json().get("patterns", {})
                _patterns_cache_ts = _time.time()
    except Exception:
        pass
    return _patterns_cache_data

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


_ETF_CACHE_TTL = 3600  # 1 hour
_ETF_CACHE: dict[str, tuple[float | None, float]] = {}  # ticker → (return, timestamp)
_ETF_CACHE_LOCK = threading.Lock()


def _etf_20d_return(ticker: str, session: "Session | None" = None) -> float | None:
    """Return 20-day price return for an ETF/index. Reads from DB when session provided."""
    with _ETF_CACHE_LOCK:
        cached = _ETF_CACHE.get(ticker)
        if cached is not None and _time.time() - cached[1] < _ETF_CACHE_TTL:
            return cached[0]
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
                with _ETF_CACHE_LOCK:
                    _ETF_CACHE[ticker] = (ret, _time.time())
                return ret
    # Fallback: yfinance (for ^HSI index and any ETF not yet in DB)
    if not _HAS_YF:
        with _ETF_CACHE_LOCK:
            _ETF_CACHE[ticker] = (None, _time.time())
        return None
    try:
        hist = yf.Ticker(ticker).history(period="2mo")
        if hist.empty or len(hist) < 21:
            with _ETF_CACHE_LOCK:
                _ETF_CACHE[ticker] = (None, _time.time())
            return None
        ret = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-21] - 1)
        with _ETF_CACHE_LOCK:
            _ETF_CACHE[ticker] = (ret, _time.time())
        return ret
    except Exception:
        with _ETF_CACHE_LOCK:
            _ETF_CACHE[ticker] = (None, _time.time())
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
                peers = [v for s2, v in pe_map.items() if s2 != symbol]
                rank  = _percentile_rank(pe_map[symbol], peers)
                val_parts.append(100 - rank)  # invert

            if symbol in pb_map and len(pb_map) >= 3:
                peers = [v for s2, v in pb_map.items() if s2 != symbol]
                rank  = _percentile_rank(pb_map[symbol], peers)
                val_parts.append(100 - rank)

            if symbol in ev_map and len(ev_map) >= 3:
                peers = [v for s2, v in ev_map.items() if s2 != symbol]
                rank  = _percentile_rank(ev_map[symbol], peers)
                val_parts.append(100 - rank)

            # Growth: direct percentile (higher growth → higher score)
            if symbol in earn_g_map and len(earn_g_map) >= 3:
                grow_parts.append(_percentile_rank(earn_g_map[symbol], [v for s2, v in earn_g_map.items() if s2 != symbol]))

            if symbol in rev_g_map and len(rev_g_map) >= 3:
                grow_parts.append(_percentile_rank(rev_g_map[symbol], [v for s2, v in rev_g_map.items() if s2 != symbol]))

            if symbol in roe_map and len(roe_map) >= 3:
                grow_parts.append(_percentile_rank(roe_map[symbol], [v for s2, v in roe_map.items() if s2 != symbol]))

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
    _cutoff = date.today() - timedelta(days=60)
    latest_subq = (
        select(Ranking.stock_id, func.max(Ranking.as_of).label("max_as_of"))
        .where(Ranking.as_of >= _cutoff)
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


@router.get("/screen")
def screen(
    market: str | None = Query(None),
    sector: str | None = Query(None, max_length=100),
    signal: str | None = Query(None, description="BUY | HOLD | WAIT | SELL"),
    min_confidence: float | None = Query(None, ge=0, le=100),
    min_score: float | None = Query(None, ge=0, le=100),
    max_score: float | None = Query(None, ge=0, le=100),
    min_momentum: float | None = Query(None, ge=0, le=100),
    min_technical: float | None = Query(None, ge=0, le=100),
    min_rs: float | None = Query(None, ge=0, le=100),
    min_growth: float | None = Query(None, ge=0, le=100),
    sort_by: str = Query("score", description="score | momentum | technical | rs_score | confidence"),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    """SCR-1: Multi-factor screener — filter stocks by K-Score, signal, and sub-scores.

    All filter params are optional. Results sorted by `sort_by` descending.
    Returns matching stocks with ranking sub-scores, latest signal, and confidence.
    """
    # Latest ranking per stock — bounded to recent history for performance (PERF-5)
    _screen_cutoff = date.today() - timedelta(days=60)
    latest_subq = (
        select(Ranking.stock_id, func.max(Ranking.as_of).label("max_as_of"))
        .where(Ranking.as_of >= _screen_cutoff)
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
    )

    if market:
        stmt = stmt.where(Stock.market == market.upper())
    if sector:
        stmt = stmt.where(Stock.sector.ilike(f"%{sector}%"))
    if min_score is not None:
        stmt = stmt.where(Ranking.score >= min_score)
    if max_score is not None:
        stmt = stmt.where(Ranking.score <= max_score)
    if min_momentum is not None:
        stmt = stmt.where(Ranking.momentum >= min_momentum)
    if min_technical is not None:
        stmt = stmt.where(Ranking.technical >= min_technical)
    if min_rs is not None:
        stmt = stmt.where(Ranking.rs_score >= min_rs)
    if min_growth is not None:
        stmt = stmt.where(Ranking.growth >= min_growth)

    rows = session.execute(stmt).all()

    # Latest SWING signal per stock — pin to SWING so multiple horizons written in the
    # same second don't produce arbitrary signal values in the screener display.
    sig_subq = (
        select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
        .where(Signal.horizon == "SWING")
        .group_by(Signal.stock_id)
        .subquery()
    )
    sig_rows = session.execute(
        select(Signal.stock_id, Signal.signal, Signal.confidence, Signal.horizon)
        .join(sig_subq,
              (Signal.stock_id == sig_subq.c.stock_id)
              & (Signal.ts == sig_subq.c.max_ts))
        .where(Signal.horizon == "SWING")
    ).all()
    sig_map: dict[int, dict] = {
        r.stock_id: {"signal": r.signal.value, "confidence": float(r.confidence), "horizon": r.horizon.value}
        for r in sig_rows
    }

    results = []
    for stock, ranking in rows:
        sig = sig_map.get(stock.id, {})
        sig_value = sig.get("signal")
        confidence = sig.get("confidence", 0.0)

        if signal and sig_value != signal.upper():
            continue
        if min_confidence is not None and confidence < min_confidence:
            continue

        def _f(v):
            if v is None:
                return None
            try:
                f = float(v)
                return None if (f != f or f == float("inf") or f == float("-inf")) else round(f, 1)
            except (TypeError, ValueError):
                return None

        results.append({
            "symbol": stock.symbol,
            "name": stock.name,
            "sector": stock.sector,
            "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
            "score": _f(ranking.score),
            "technical": _f(ranking.technical),
            "momentum": _f(ranking.momentum),
            "value": _f(ranking.value),
            "growth": _f(ranking.growth),
            "rs_score": _f(ranking.rs_score),
            "signal": sig_value,
            "confidence": _f(confidence) if confidence is not None else None,
            "horizon": sig.get("horizon"),
        })

    sort_fields = {
        "score": lambda x: x["score"] or 0,
        "momentum": lambda x: x["momentum"] or 0,
        "technical": lambda x: x["technical"] or 0,
        "rs_score": lambda x: x["rs_score"] or 0,
        "confidence": lambda x: x["confidence"] or 0,
    }
    key_fn = sort_fields.get(sort_by, sort_fields["score"])
    results.sort(key=key_fn, reverse=True)
    return {"total": len(results), "items": results[:limit]}


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
    # PERF-5: Bound GROUP BY to recent history so it doesn't scan the entire rankings table.
    _rank_cutoff = date.today() - timedelta(days=60)
    latest_subq = (
        select(Ranking.stock_id, func.max(Ranking.as_of).label("max_as_of"))
        .where(Ranking.as_of >= _rank_cutoff)
        .group_by(Ranking.stock_id)
        .subquery()
    )
    # Latest fundamentals date per stock
    latest_fund_subq = (
        select(Fundamental.stock_id, func.max(Fundamental.as_of).label("max_date"))
        .group_by(Fundamental.stock_id)
        .subquery()
    )
    stmt = (
        select(Stock, Ranking, Fundamental)
        .join(Ranking, Stock.id == Ranking.stock_id)
        .join(
            latest_subq,
            (Ranking.stock_id == latest_subq.c.stock_id)
            & (Ranking.as_of == latest_subq.c.max_as_of),
        )
        .outerjoin(
            latest_fund_subq,
            Stock.id == latest_fund_subq.c.stock_id,
        )
        .outerjoin(
            Fundamental,
            (Fundamental.stock_id == latest_fund_subq.c.stock_id)
            & (Fundamental.as_of == latest_fund_subq.c.max_date),
        )
        .where(Stock.active.is_(True))
    )
    if market:
        stmt = stmt.where(Stock.market == market.upper())

    rows = list(session.execute(stmt).all())

    if not rows:
        # No persisted rankings yet — compute live on first run
        return _leaderboard_live(market, limit, session)

    def _cf(v: float | None) -> float | None:
        """Clean a raw fundamental float."""
        if v is None:
            return None
        try:
            return None if (v != v or v == float("inf") or v == float("-inf")) else round(v, 4)
        except (TypeError, ValueError):
            return None

    # Compute vol_ratio (avg5d / avg20d) for all stocks in one Price query
    _stock_ids = [row[0].id for row in rows]
    _vol_cutoff = date.today() - timedelta(days=35)
    _vol_rows = session.execute(
        select(Price.stock_id, Price.volume)
        .where(
            Price.stock_id.in_(_stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= str(_vol_cutoff),
        )
        .order_by(Price.stock_id, Price.ts.desc())
    ).all()
    from collections import defaultdict as _dd
    _vols_by_stock: dict[int, list[float]] = _dd(list)
    for _vr in _vol_rows:
        _vols_by_stock[_vr.stock_id].append(float(_vr.volume or 0))
    _vol_ratio_map: dict[int, float | None] = {}
    for _sid, _vols in _vols_by_stock.items():
        _valid = [v for v in _vols if v > 0]
        if len(_valid) < 5:
            _vol_ratio_map[_sid] = None
            continue
        _avg5  = sum(_valid[:5]) / 5
        _avg20 = sum(_valid[:min(len(_valid), 20)]) / min(len(_valid), 20)
        _vol_ratio_map[_sid] = round(_avg5 / _avg20, 2) if _avg20 > 0 else None

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
            "vol_ratio":         _vol_ratio_map.get(stock.id),
            # Raw fundamental fields for screener filtering
            "trailing_pe":       _cf(fund.trailing_pe) if fund else None,
            "forward_pe":        _cf(fund.forward_pe) if fund else None,
            "peg_ratio":         _cf(fund.peg_ratio) if fund else None,
            "revenue_growth":    _cf(fund.revenue_growth) if fund else None,
            "earnings_growth":   _cf(fund.earnings_growth) if fund else None,
            "debt_to_equity":    _cf(fund.debt_to_equity) if fund else None,
            "price_to_book":     _cf(fund.price_to_book) if fund else None,
            "market_cap":        int(_cf(fund.market_cap)) if fund and _cf(fund.market_cap) is not None else None,
        }
        for stock, ranking, fund in rows
    ]
    # Merge institutional ownership from Redis (not in DB Fundamental table)
    bulk_fund = _fetch_fundamentals_bulk()
    patterns  = _fetch_patterns_bulk()
    for r in results:
        fd = bulk_fund.get(r["symbol"]) or {}
        r["held_percent_institutions"] = fd.get("held_percent_institutions")
        r["held_percent_insiders"]     = fd.get("held_percent_insiders")
        r["patterns"] = patterns.get(r["symbol"], [])

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
    _: str = Depends(get_current_username),
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
