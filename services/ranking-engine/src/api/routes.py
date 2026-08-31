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

from common.config import get_settings
from common.jwt_auth import get_current_username
from common.logging import get_logger
from db import Fundamental, Price, Ranking, Signal, SignalType, Stock, TimeFrame, TuneHistory, get_session

from ..scoring import compute_kscore

log = get_logger("ranking-engine")

_settings = get_settings()
# AUD-REDISAUDIT Phase 3: this file used to keep its own private os.environ-based URL
# constants (MARKET_DATA_URL/TA_URL env vars, each with its own hardcoded fallback port) —
# the only service in the repo bypassing shared/common/config.py's Settings for this. That
# private second source of truth already caused a real production bug once (T232-KS1: the
# TA_URL default was wrong — 8006 instead of 8002 — silently connection-refusing every
# bulk-patterns fetch). Routing through the same _settings.market_data_url/
# technical_analysis_url every other service already uses means a docker-compose port
# change can never miss this file again.
_MARKET_DATA_URL = _settings.market_data_url
_TA_URL = _settings.technical_analysis_url

_patterns_cache_ts: float = 0.0
_patterns_cache_data: dict = {}

# T232-DL8: stocks skipped for insufficient history (<60 daily bars) during the most recent
# _persist_rankings run. Previously this was invisible beyond an aggregate "skipped" counter
# in the batch log line — a stock with 55 bars silently had zero ranking row, no persisted
# reason, indistinguishable from "something is broken" without grepping container logs for
# the day it happened. Keyed by stock_id; overwritten wholesale on each refresh run (not
# accumulated across runs) so it always reflects only the most recent batch.
_skipped_insufficient_history: dict[int, dict] = {}


def _fetch_patterns_bulk() -> dict[str, list[str]]:
    """Fetch pre-computed patterns from TA service. Module-level 6h cache."""
    global _patterns_cache_ts, _patterns_cache_data
    if _time.time() - _patterns_cache_ts < 21600:
        return _patterns_cache_data
    try:
        with httpx.Client(timeout=90) as c:
            r = c.get(f"{_TA_URL}/ta/patterns/bulk", timeout=90)
            if r.status_code == 200:
                _patterns_cache_data = r.json().get("patterns") or {}
                _patterns_cache_ts = _time.time()
            else:
                log.warning("ranking.patterns_bulk_fetch_failed", status=r.status_code)
    except Exception as exc:
        log.warning("ranking.patterns_bulk_fetch_error", error=str(exc))
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
    # T247-RANKINGENGINE-HSI-SILENT: this fallback path is used exclusively for ^HSI (the HK
    # benchmark isn't DB-seeded) — every failure here previously returned None with zero log
    # line (unlike _fetch_patterns_bulk() a few lines above, which does log on failure), so a
    # yfinance rate-limit/import failure silently collapsed EVERY HK stock's relative-strength
    # score to a flat neutral 50.0 (via _rs_score()'s etf_ret=None branch) for the whole cache
    # window, indistinguishable in logs from HSI genuinely trading flat.
    # Fallback: yfinance (for ^HSI index and any ETF not yet in DB)
    if not _HAS_YF:
        log.warning("ranking.etf_return_fetch_failed", ticker=ticker, reason="yfinance_not_installed")
        with _ETF_CACHE_LOCK:
            _ETF_CACHE[ticker] = (None, _time.time())
        return None
    try:
        hist = yf.Ticker(ticker).history(period="2mo")
        if hist.empty or len(hist) < 21:
            log.warning("ranking.etf_return_fetch_failed", ticker=ticker,
                        reason="insufficient_history", bars=len(hist))
            with _ETF_CACHE_LOCK:
                _ETF_CACHE[ticker] = (None, _time.time())
            return None
        ret = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-21] - 1)
        with _ETF_CACHE_LOCK:
            _ETF_CACHE[ticker] = (ret, _time.time())
        return ret
    except Exception as exc:
        log.warning("ranking.etf_return_fetch_failed", ticker=ticker, error=str(exc))
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
    # T234-RANK-RS-UNBOUNDED: only `score` was clipped — rs_rank itself was returned raw and
    # could blow up arbitrarily as etf_ret approaches -100% (denom -> the 1e-6 floor above).
    # Bounded to [-20, 20], comfortably outside any realistic stock/sector return ratio, so a
    # genuine benchmark near-total-loss scenario degrades to a large-but-sane number instead
    # of an unbounded one reaching any consumer that reads rs_rank directly instead of score.
    rs_rank = float(np.clip(rs_rank, -20.0, 20.0))
    return round(score, 2), round(rs_rank, 4)

router = APIRouter(prefix="/rankings", tags=["rankings"])

# ── Sector-relative fundamental scoring ──────────────────────────────────────

def _fetch_fundamentals_bulk() -> dict[str, dict]:
    """Fetch all cached fundamentals from market-data in one HTTP call.

    Returns {symbol: {trailing_pe, price_to_book, ev_to_ebitda, ...}} for every
    symbol that has a warm Redis cache entry. Symbols with no cache are omitted
    — they will fall back to the price-based K-Score proxies.
    """
    # T247-RANKINGENGINE-FUNDAMENTALS-SILENT: this previously swallowed every failure with a
    # bare `except Exception: pass` (and silently fell through to `return {}` on a non-200
    # status without even reaching the except) — zero log line anywhere. A market-data outage
    # or timeout during a scheduled rankings refresh silently excluded every stock's value/
    # growth K-Score components for the whole outage window, indistinguishable in logs from
    # normal operation (genuinely-empty fundamentals cache).
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(f"{_MARKET_DATA_URL}/stocks/fundamentals_bulk")
            if r.status_code == 200:
                return r.json()
            log.warning("ranking.fundamentals_bulk_fetch_failed", status=r.status_code)
    except Exception as exc:
        log.warning("ranking.fundamentals_bulk_fetch_failed", error=str(exc))
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

        # T234-RANK-SECTOR-PEER-OFFBYONE: each *_map above includes the subject stock itself
        # (built from the full sector `symbols` list before any exclusion). The gate below used
        # to check `len(map) >= 3` and only exclude the subject stock on the NEXT line when
        # building `peers` — so a sector nominally satisfying "≥3" always supplied exactly one
        # fewer real comparison peer than the gate implied. Gate raised to >= 4 (subject stock +
        # 3 real peers) so the peer LIST — not the pre-exclusion map — actually has >= 3 entries.
        _MIN_PEER_GROUP = 4
        for symbol in symbols:
            val_parts: list[float] = []
            grow_parts: list[float] = []

            # Value: invert percentile (lower ratio → higher score)
            if symbol in pe_map and len(pe_map) >= _MIN_PEER_GROUP:
                peers = [v for s2, v in pe_map.items() if s2 != symbol]
                rank  = _percentile_rank(pe_map[symbol], peers)
                val_parts.append(100 - rank)  # invert

            if symbol in pb_map and len(pb_map) >= _MIN_PEER_GROUP:
                peers = [v for s2, v in pb_map.items() if s2 != symbol]
                rank  = _percentile_rank(pb_map[symbol], peers)
                val_parts.append(100 - rank)

            if symbol in ev_map and len(ev_map) >= _MIN_PEER_GROUP:
                peers = [v for s2, v in ev_map.items() if s2 != symbol]
                rank  = _percentile_rank(ev_map[symbol], peers)
                val_parts.append(100 - rank)

            # Growth: direct percentile (higher growth → higher score)
            if symbol in earn_g_map and len(earn_g_map) >= _MIN_PEER_GROUP:
                grow_parts.append(_percentile_rank(earn_g_map[symbol], [v for s2, v in earn_g_map.items() if s2 != symbol]))

            if symbol in rev_g_map and len(rev_g_map) >= _MIN_PEER_GROUP:
                grow_parts.append(_percentile_rank(rev_g_map[symbol], [v for s2, v in rev_g_map.items() if s2 != symbol]))

            if symbol in roe_map and len(roe_map) >= _MIN_PEER_GROUP:
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


def _compute_vol_ratio(vols_desc: list[float]) -> float | None:
    """avg5d / avg20d volume ratio. `vols_desc` must already be ordered newest-first
    (ts.desc()) — the literal most-recent 5/20 rows are used, INCLUDING zero-volume days.

    T247-RANKINGENGINE-VOLRATIO-STALEWINDOW: a prior version filtered out zero-volume rows
    before slicing [:5]/[:20], which shifts every later index — a single zero-volume day
    (halt/bad ingestion) anywhere in the window silently pulled in a bar older than the
    nominal "last 5"/"last 20" trading days, skewing vol_ratio away from what the label
    describes (especially for thinly-traded HK stocks where no-trade days are more common).
    market-data's own canonical vol_ratio computation (api/routes.py's
    vol.iloc[-5:]/vol.iloc[-20:]) takes the literal most-recent N rows including zeros —
    matches that convention instead of filtering first. Extracted to module level so it's
    independently unit-testable without the surrounding DB/session machinery.
    """
    if len(vols_desc) < 5:
        return None
    avg5 = sum(vols_desc[:5]) / 5
    avg20 = sum(vols_desc[:min(len(vols_desc), 20)]) / min(len(vols_desc), 20)
    return round(avg5 / avg20, 2) if avg20 > 0 else None


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

    # RK-D1-SCREENER-FULL-SCAN: previously scanned the ENTIRE Signal table (filtered only by
    # horizon == "SWING", no stock_id restriction) to build sig_map below, even though only
    # the stock_ids already present in `rows` (already filtered by market/sector/score/etc.
    # above) are ever looked up from it. Scoping to those stock_ids turns this into a bounded
    # lookup instead of a full-table scan — a no-op change in behavior (sig_map's contents are
    # identical either way, since anything outside `rows` was never read from it anyway).
    _screen_stock_ids = [stock.id for stock, _ranking in rows]

    # Latest SWING signal per stock — pin to SWING so multiple horizons written in the
    # same second don't produce arbitrary signal values in the screener display.
    if _screen_stock_ids:
        sig_subq = (
            select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
            .where(Signal.horizon == "SWING", Signal.stock_id.in_(_screen_stock_ids))
            .group_by(Signal.stock_id)
            .subquery()
        )
        sig_rows = session.execute(
            select(Signal.stock_id, Signal.signal, Signal.confidence, Signal.horizon)
            .join(sig_subq,
                  (Signal.stock_id == sig_subq.c.stock_id)
                  & (Signal.ts == sig_subq.c.max_ts))
            .where(Signal.horizon == "SWING", Signal.stock_id.in_(_screen_stock_ids))
        ).all()
    else:
        sig_rows = []
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


@router.get("/skipped")
def skipped_stocks():
    """T232-DL8: stocks excluded from the most recent ranking refresh for insufficient
    history (<60 daily bars), with the exact bar count seen — was previously only visible
    as an aggregate counter in container logs. Registered ABOVE /{symbol} below: FastAPI
    matches routes in registration order and a bare /{symbol} would otherwise swallow this
    path (the exact bug found and fixed in signal-engine's router the same day — see
    T232-OC5's fix notes).
    """
    return {
        "count": len(_skipped_insufficient_history),
        "min_bars_required": 60,
        "stocks": list(_skipped_insufficient_history.values()),
    }


@router.get("/kscore_weights_status")
def kscore_weights_status():
    """T288-KSCORE-WEIGHT-SWEEP status: the currently-EFFECTIVE weight set (Redis override if
    tune_kscore_weights has ever promoted one, else the hardcoded default) alongside the
    hardcoded default itself, so an admin can see at a glance whether a sweep has ever changed
    anything. Registered ABOVE /{symbol} below — the exact same reasoning /skipped's own
    comment documents (a bare GET /{symbol} catch-all registered first would otherwise
    silently swallow this literal path, the BUG233-ROUTERORDER bug class)."""
    from ..scoring.kscore import _WEIGHTS as _HARDCODED_WEIGHTS, _load_active_weights
    effective = _load_active_weights()
    return {
        "effective_weights": effective,
        "hardcoded_default_weights": _HARDCODED_WEIGHTS,
        "is_tuned": effective != _HARDCODED_WEIGHTS,
    }


@router.get("/kscore_curve_status")
def kscore_curve_status():
    """T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B status: mirrors kscore_weights_status()'s
    own shape/placement exactly — registered ABOVE /{symbol} below for the same
    BUG233-ROUTERORDER reasoning that entry's own comment documents."""
    from ..scoring.kscore import _CURVE_DEFAULTS, _load_active_curve_params
    effective = _load_active_curve_params()
    return {
        "effective_curve_params": effective,
        "hardcoded_default_curve_params": _CURVE_DEFAULTS,
        "is_tuned": effective != _CURVE_DEFAULTS,
    }


@router.get("/{symbol}")
def rank_symbol(symbol: str, session: Session = Depends(get_session)):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    df = _load_prices(session, stock.id)
    if df.empty:
        raise HTTPException(404, f"No price data for {symbol}")
    rs_score_val, rs_rank = _stock_rs(stock, df, session=session)
    # Sector-relative scores for single-symbol live endpoint.
    # T232-KS2: must rank against the full active universe, not a one-entry map — otherwise
    # every peer-count gate in _sector_relative_scores (len(pe_map) >= 3, etc.) always fails
    # and this endpoint silently falls back to price proxies, diverging from the leaderboard's
    # K-Score for the same stock on the same day.
    fundamentals = _fetch_fundamentals_bulk()
    # T247-RANKINGENGINE-CROSSMARKET: T232-KS2's comment above claims this ranks against "the
    # full active universe" to fix an under-populated peer-count gate — but "full active
    # universe" included every market, pooling e.g. HK Technology stocks against 27 US
    # Technology stocks whose PE/PB multiples trade on a structurally different basis. The
    # batch/leaderboard path (_persist_rankings) is naturally market-scoped because the
    # scheduler invokes /rankings/refresh once per market, so this single-symbol endpoint's
    # value/growth score diverged from the leaderboard's score for the same stock on the same
    # day. Scope the peer universe to the target stock's own market, matching that path.
    # BUG-DELISTED-GENERATION-BLIND: a delisted peer's stale fundamentals would pollute this
    # stock's sector value/growth peer-comparison basis — exclude it, matching the same fix
    # applied to /rankings/refresh above.
    universe = list(session.execute(
        select(Stock).where(Stock.active.is_(True), Stock.delisted.is_(False), Stock.market == stock.market)
    ).scalars())
    stock_sectors = {s.symbol: (s.sector or "Unknown") for s in universe}
    stock_sectors.setdefault(symbol, stock.sector or "Unknown")
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
    _vol_ratio_map: dict[int, float | None] = {
        _sid: _compute_vol_ratio(_vols) for _sid, _vols in _vols_by_stock.items()
    }

    results = [
        {
            "symbol":            stock.symbol,
            "name":              stock.name,
            "name_zh":           stock.name_zh,
            "market":            stock.market.value,
            "sector":            stock.sector,
            "index_membership":  stock.index_membership,
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
    # BUG-DELISTED-GENERATION-BLIND: same fix as /rankings/refresh — this is a real live-compute
    # path too, not just a display query.
    stmt = select(Stock).where(Stock.active.is_(True), Stock.delisted.is_(False))
    if market:
        stmt = stmt.where(Stock.market == market.upper())
    stocks = list(session.execute(stmt).scalars())

    fundamentals  = _fetch_fundamentals_bulk()
    stock_sectors = {s.symbol: (s.sector or "Unknown") for s in stocks}
    sector_scores = _sector_relative_scores(fundamentals, stock_sectors)

    results = []
    for s in stocks:
        # AUD232-043: _persist_rankings() got per-stock exception isolation after the
        # T232-RANKSTALE incident specifically so one bad symbol can't kill an entire batch.
        # This live-compute fallback (hit whenever the Ranking table has zero rows for the
        # requested market — e.g. right after a fresh DB bootstrap) had no equivalent
        # isolation: an uncaught exception from compute_kscore()/_stock_rs() on any single
        # stock would 500 the ENTIRE leaderboard request for every user, instead of just
        # skipping that one symbol and returning results for the rest.
        try:
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
        except Exception as _stock_exc:
            log.warning("ranking.leaderboard_live_stock_failed", symbol=s.symbol, error=str(_stock_exc))
    results.sort(key=lambda r: r["score"] or 0, reverse=True)
    return {"as_of": str(date.today()), "rankings": results[:limit]}


# ── T288-KSCORE-WEIGHT-SWEEP: walk-forward validated sweep of K-Score's factor weights ──
# _WEIGHTS (kscore.py) has been a hardcoded, never-empirically-validated guess since this
# service shipped. Ranking already stores all 6 individual factor scores per (stock_id,
# as_of) — meaning a sweep can recompute alternative composite scores directly from
# already-persisted historical data with NO signal regeneration needed, the exact same
# no-re-simulation advantage signal-engine's tune_strategy() already established for its own
# (buy_threshold x ml_weight_cap) grid. Mirrors that mechanism's chronological 70/30 split,
# validation-beats-current-live-baseline gate, and per-attempt TuneHistory recording exactly.

_KSCORE_SWEEP_MIN_ROWS = 200  # rankings rows needed, PER SLICE, before a candidate is trusted
_KSCORE_SWEEP_FORWARD_BARS = 20  # ~1 trading month — bar-index offset, not calendar days, so
# weekends/holidays never need special-casing (the same reasoning gate_harness.py's own T196
# fix documents for why a bar-index lookup is preferred over a calendar-day one).
_KSCORE_SWEEP_DELTA = 0.05  # how much a single factor's weight is perturbed per candidate
_KSCORE_SWEEP_TOP_DECILE = 0.10


def _record_kscore_tune_history(
    session: Session,
    run_id: str,
    old_value: dict,
    new_value: dict,
    train_window: tuple,
    validation_window: tuple,
    train_ev_pct: float | None,
    validation_ev_pct: float | None,
    baseline_validation_ev_pct: float | None,
    validation_n: int | None,
    promoted: bool,
    gate_failures: list[str],
    parameter_class: str = "kscore_weights",
    parameter_name: str = "factor_weights",
) -> None:
    """Local tune_history writer, matching ml-prediction's tuner.py and signal-engine's
    signals_shared.py — each service keeps its OWN copy rather than a cross-service import,
    per this repo's established per-service-duplication convention (docker cp deploys one
    service's code at a time; a shared import would create a cross-service coupling this
    app's deployment model doesn't otherwise have).

    parameter_class/parameter_name default to the ORIGINAL kscore-weights-sweep values so
    every one of tune_kscore_weights()'s 6 existing call sites needs zero changes — the new
    T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B curve sweep (tune_kscore_curve()) explicitly
    overrides both at each of its own 6 call sites so its TuneHistory rows are distinguishable
    in the audit trail from the weights sweep's rows, rather than silently sharing the same
    tag for two genuinely different sweep types."""
    session.add(TuneHistory(
        run_id=run_id, parameter_class=parameter_class, parameter_name=parameter_name,
        style="ALL", market="ALL", old_value=old_value, new_value=new_value,
        train_window_start=train_window[0], train_window_end=train_window[1],
        validation_window_start=validation_window[0], validation_window_end=validation_window[1],
        train_ev_pct=train_ev_pct, validation_ev_pct=validation_ev_pct,
        baseline_validation_ev_pct=baseline_validation_ev_pct, validation_n=validation_n,
        promoted=promoted, gate_failures=gate_failures, triggered_by="manual",
    ))
    session.commit()


def _kscore_active_weights_for_row(base_weights: dict, row) -> dict:
    """Replicates compute_kscore()'s exact active-weight redistribution (T234-RANK-KSCORE-
    PROXY-MIXING) against an ALREADY-STORED Ranking row's own None-ness of value/growth/
    rs_score — a candidate weight set must be excluded/renormalized the identical way
    compute_kscore() itself would have, or a recomputed historical score would silently
    diverge from what was actually live for that stock on that day."""
    active = dict(base_weights)
    if row.value is None:
        active.pop("value", None)
    if row.growth is None:
        active.pop("growth", None)
    if row.rs_score is None:
        active.pop("relative_strength", None)
    return active


def _kscore_recompute(base_weights: dict, row) -> float | None:
    """Recompute a Ranking row's composite score under an alternative weight set, using
    ONLY the 6 factor values that were already persisted for that row — never a fresh
    computation, matching this mechanism's own no-regeneration design."""
    active = _kscore_active_weights_for_row(base_weights, row)
    w_sum = sum(active.values())
    if w_sum <= 0:
        return None
    values = {
        "technical": row.technical, "momentum": row.momentum, "volatility": row.volatility,
        "value": row.value, "growth": row.growth, "relative_strength": row.rs_score,
    }
    return sum((w / w_sum) * values[f] for f, w in active.items())


def _kscore_candidate_weight_sets(base_weights: dict) -> list[dict]:
    """Generates one-factor-perturbed-at-a-time candidates rather than a full 6-dimensional
    grid (combinatorially intractable at any reasonable step size) — a coordinate-ascent-style
    sweep, matching the same 'search a tractable neighborhood, not the full space' judgment
    already made for tune_strategy's own 2-parameter (not 6-parameter) grid. For each factor,
    nudges it up or down by _KSCORE_SWEEP_DELTA and renormalizes every weight (including the
    perturbed one) so the full set still sums to 1.0 — a candidate is a complete, self-
    consistent weight set, never a single changed number with the rest left stale."""
    candidates = []
    for factor in base_weights:
        for sign in (1, -1):
            perturbed = dict(base_weights)
            perturbed[factor] = max(0.01, perturbed[factor] + sign * _KSCORE_SWEEP_DELTA)
            total = sum(perturbed.values())
            candidates.append({k: round(v / total, 4) for k, v in perturbed.items()})
    return candidates


def _kscore_cross_sectional_ev(
    rows_by_date: dict, forward_return_by_id: dict, composite_fn,
) -> dict | None:
    """The EV metric: per as_of date, rank all stocks that day by their (recomputed) composite
    score, take the top decile, average their forward returns — then average that daily figure
    across every date in the slice. A cross-sectional ranking metric, not a per-stock
    buy/no-buy threshold, matching what K-Score is actually used for (ranking stocks against
    each other on a given day), not a binary entry signal the way buy_threshold is.

    composite_fn(row) -> float | None recomputes ONE row's composite score under whatever is
    being swept — genuinely different logic per sweep (tune_kscore_weights passes a closure
    over _kscore_recompute(candidate_weights, row); tune_kscore_curve passes one that combines
    the row's already-cached raw #17/#18/#19 inputs with a candidate curve cfg) — pulled out
    as a parameter here (T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B) rather than duplicating
    this whole day-grouping/top-decile/mean-of-means EV machinery a second time, the exact
    'duplicate business logic that can silently drift' anti-pattern this codebase's own prior
    audits have repeatedly found and fixed elsewhere."""
    daily_evs: list[float] = []
    n_scored = 0
    for _as_of, day_rows in rows_by_date.items():
        scored = []
        for row in day_rows:
            fwd = forward_return_by_id.get(row.id)
            if fwd is None:
                continue
            composite = composite_fn(row)
            if composite is None:
                continue
            scored.append((composite, fwd))
        if len(scored) < 3:  # need at least a few stocks to have a meaningful "top decile" that day
            continue
        scored.sort(key=lambda pair: pair[0], reverse=True)
        n_top = max(1, int(len(scored) * _KSCORE_SWEEP_TOP_DECILE))
        top = scored[:n_top]
        daily_evs.append(sum(r for _c, r in top) / len(top))
        n_scored += len(top)
    if not daily_evs:
        return None
    import statistics as _stats
    return {
        "n_days": len(daily_evs), "n_scored": n_scored,
        "ev_pct": round(_stats.mean(daily_evs) * 100, 3),
    }


@router.post("/tune_kscore_weights")
def tune_kscore_weights(
    days: int = Query(365, description="Look-back window in calendar days"),
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Walk-forward validated sweep of K-Score's 6 factor weights (T288-KSCORE-WEIGHT-SWEEP).

    Re-uses ALREADY-PERSISTED Ranking rows (all 6 individual factor scores, per stock, per
    day) joined to REAL forward returns computed from Price.close — no signal regeneration.
    Chronological 70/30 train/validation split. For each of the 12 one-factor-perturbed
    candidates (see _kscore_candidate_weight_sets), measures the cross-sectional top-decile
    EV on train; the single best candidate only gets applied if it ALSO beats the CURRENT
    LIVE weights' own EV on the validation slice the search never saw — matching every other
    self-tuning mechanism's own beat-the-live-baseline discipline, never a fixed target.
    """
    from ..scoring.kscore import _KSCORE_WEIGHTS_REDIS_KEY, _load_active_weights

    current_weights = _load_active_weights()
    cutoff = date.today() - timedelta(days=days)

    all_rankings = list(session.execute(
        select(Ranking).where(Ranking.as_of >= cutoff).order_by(Ranking.as_of)
    ).scalars())

    stock_ids = sorted({r.stock_id for r in all_rankings})
    if not stock_ids or len(all_rankings) < _KSCORE_SWEEP_MIN_ROWS * 2:
        return {
            "applied": False,
            "reason": f"only {len(all_rankings)} ranking rows in the window (need {_KSCORE_SWEEP_MIN_ROWS * 2} for a valid train/validation split)",
        }

    # One bulk daily-close fetch per stock (not per ranking row) — a chronological
    # (date, close) list per stock_id lets forward returns be looked up by BAR-INDEX offset
    # (_KSCORE_SWEEP_FORWARD_BARS trading days ahead), not a calendar-day computation that
    # would need its own weekend/holiday handling.
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids), Price.timeframe == TimeFrame.D1)
        .order_by(Price.stock_id, Price.ts)
    ).all()
    closes_by_stock: dict[int, list[tuple]] = defaultdict(list)
    for sid, ts, close in price_rows:
        closes_by_stock[sid].append((ts.date(), close))

    # Forward return per ranking row: find this row's as_of date in its stock's own
    # chronological close list via bisect, then look _KSCORE_SWEEP_FORWARD_BARS entries
    # ahead in that SAME list — a pure bar-index offset, never a calendar-date arithmetic.
    import bisect
    forward_return_by_id: dict[int, float] = {}
    for r in all_rankings:
        closes = closes_by_stock.get(r.stock_id)
        if not closes:
            continue
        dates = [d for d, _c in closes]
        idx = bisect.bisect_left(dates, r.as_of)
        if idx >= len(closes) or closes[idx][0] != r.as_of:
            continue  # no exact price row for this ranking's own as_of — skip, never guess
        fwd_idx = idx + _KSCORE_SWEEP_FORWARD_BARS
        if fwd_idx >= len(closes):
            continue  # not enough trading days have elapsed yet for this row to be resolvable
        entry_close = closes[idx][1]
        exit_close = closes[fwd_idx][1]
        if entry_close and entry_close > 0:
            forward_return_by_id[r.id] = (exit_close - entry_close) / entry_close

    resolvable = [r for r in all_rankings if r.id in forward_return_by_id]
    if len(resolvable) < _KSCORE_SWEEP_MIN_ROWS * 2:
        return {
            "applied": False,
            "reason": f"only {len(resolvable)} ranking rows have a resolvable {_KSCORE_SWEEP_FORWARD_BARS}-bar forward return (need {_KSCORE_SWEEP_MIN_ROWS * 2})",
        }

    split = max(1, int(len(resolvable) * 0.7))
    train_rows = resolvable[:split]
    val_rows = resolvable[split:]
    if len(train_rows) < _KSCORE_SWEEP_MIN_ROWS or len(val_rows) < _KSCORE_SWEEP_MIN_ROWS:
        return {
            "applied": False,
            "reason": f"train/validation split too lopsided ({len(train_rows)}/{len(val_rows)}, need >= {_KSCORE_SWEEP_MIN_ROWS} each)",
        }

    train_window = (train_rows[0].as_of, train_rows[-1].as_of)
    val_window = (val_rows[0].as_of, val_rows[-1].as_of)

    train_by_date: dict = defaultdict(list)
    for r in train_rows:
        train_by_date[r.as_of].append(r)
    val_by_date: dict = defaultdict(list)
    for r in val_rows:
        val_by_date[r.as_of].append(r)

    run_id = __import__("uuid").uuid4().hex
    candidates = _kscore_candidate_weight_sets(current_weights)

    best_ev = -999.0
    best_weights: dict | None = None
    for cand in candidates:
        stats = _kscore_cross_sectional_ev(
            train_by_date, forward_return_by_id, lambda row, w=cand: _kscore_recompute(w, row),
        )
        if stats is not None and stats["ev_pct"] > best_ev:
            best_ev = stats["ev_pct"]
            best_weights = cand

    if best_weights is None:
        _record_kscore_tune_history(
            session, run_id, old_value=current_weights, new_value={},
            train_window=train_window, validation_window=val_window,
            train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
            validation_n=None, promoted=False, gate_failures=["no_candidate_met_train_criteria"],
        )
        return {"applied": False, "reason": "no candidate weight set met the train-slice criteria"}

    candidate_stats = _kscore_cross_sectional_ev(
        val_by_date, forward_return_by_id, lambda row: _kscore_recompute(best_weights, row),
    )
    baseline_stats = _kscore_cross_sectional_ev(
        val_by_date, forward_return_by_id, lambda row: _kscore_recompute(current_weights, row),
    )

    if candidate_stats is None:
        _record_kscore_tune_history(
            session, run_id, old_value=current_weights, new_value=best_weights,
            train_window=train_window, validation_window=val_window,
            train_ev_pct=best_ev, validation_ev_pct=None, baseline_validation_ev_pct=None,
            validation_n=None, promoted=False, gate_failures=["candidate_unmeasurable_on_validation"],
        )
        return {"applied": False, "reason": "candidate weights unmeasurable on the validation slice"}

    if baseline_stats is None:
        # T232-OC3 convention (already established in signal-engine's tune_strategy): no
        # honest baseline measurable on validation means we cannot claim a lift over it — skip
        # rather than assume baseline EV is 0, which would overstate the lift and apply too
        # eagerly.
        _record_kscore_tune_history(
            session, run_id, old_value=current_weights, new_value=best_weights,
            train_window=train_window, validation_window=val_window,
            train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
            baseline_validation_ev_pct=None, validation_n=candidate_stats["n_scored"],
            promoted=False, gate_failures=["baseline_unmeasurable_on_validation"],
        )
        return {"applied": False, "reason": "current live weights unmeasurable on the validation slice"}

    ev_lift = round(candidate_stats["ev_pct"] - baseline_stats["ev_pct"], 3)
    # Unconditional rejection of a non-positive lift — matching every other sweep in this
    # codebase's own "never promote a candidate that doesn't clear a genuinely positive,
    # validation-measured improvement" discipline (T232-OC3, tune_strategy, outcomes_
    # calibrate_apply). No shift-size escape hatch, no multiple-comparisons correction beyond
    # this floor — the candidate pool here (12 single-factor perturbations) is far smaller
    # than tune_strategy's own 403-cell grid, so the noise-inflation risk that motivated
    # gate_harness.py's stricter margin is smaller here, but a bare ev_lift > 0 floor is still
    # the correct minimum bar, not an assumed-safe shortcut.
    if ev_lift <= 0:
        _record_kscore_tune_history(
            session, run_id, old_value=current_weights, new_value=best_weights,
            train_window=train_window, validation_window=val_window,
            train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
            baseline_validation_ev_pct=baseline_stats["ev_pct"], validation_n=candidate_stats["n_scored"],
            promoted=False, gate_failures=[f"ev_lift_not_positive:{ev_lift}"],
        )
        return {
            "applied": False,
            "reason": f"validation-slice EV lift {ev_lift}pp is not positive",
            "candidate": best_weights, "current": current_weights,
        }

    redis_client = None
    try:
        from common.redis_client import get_redis
        import json
        redis_client = get_redis()
        redis_client.setex(_KSCORE_WEIGHTS_REDIS_KEY, 30 * 86400, json.dumps(best_weights))
    except Exception as _redis_exc:
        log.warning("ranking.kscore_weight_redis_write_failed", error=str(_redis_exc))
        _record_kscore_tune_history(
            session, run_id, old_value=current_weights, new_value=best_weights,
            train_window=train_window, validation_window=val_window,
            train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
            baseline_validation_ev_pct=baseline_stats["ev_pct"], validation_n=candidate_stats["n_scored"],
            promoted=False, gate_failures=["redis_write_failed"],
        )
        return {"applied": False, "reason": "validated but Redis write failed — not applied"}

    _record_kscore_tune_history(
        session, run_id, old_value=current_weights, new_value=best_weights,
        train_window=train_window, validation_window=val_window,
        train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
        baseline_validation_ev_pct=baseline_stats["ev_pct"], validation_n=candidate_stats["n_scored"],
        promoted=True, gate_failures=[],
    )
    return {
        "applied": True,
        "previous_weights": current_weights,
        "new_weights": best_weights,
        "train_ev_pct": best_ev,
        "validation_ev_pct": candidate_stats["ev_pct"],
        "validation_baseline_ev_pct": baseline_stats["ev_pct"],
        "ev_lift_pct": ev_lift,
        "validation_n_days": candidate_stats["n_days"],
    }


# ── T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group B: walk-forward sweep of K-Score's curve-shape
# constants (#17 RSI-to-score piecewise mapping, #18 ADX-boost normalization, #19 volatility
# scale factor) — kscore.py's _CURVE_DEFAULTS. Unlike tune_kscore_weights() above, these
# constants determine HOW technical/volatility get computed from raw Price history in the
# first place, not just how already-computed sub-scores get weighted together — so they
# cannot be recomputed from Ranking's own already-persisted technical/volatility columns
# (those columns already bake in whatever curve params were live when that row was written).
# This sweep bulk-fetches each stock's real historical daily Price bars ONCE, then reconstructs
# each Ranking row's point-in-time raw indicator inputs (RSI/ADX/SMA-booleans/realized-vol) via
# kscore.py's own _technical_raw_inputs()/_volatility_raw_input() — the same functions the live
# ranking-refresh path uses, never a second, independently-drifting reimplementation.
#
# Point-in-time correctness: a row's own as_of date bounds which Price bars are visible to it
# (ts.date() <= as_of) — mirrors gate_harness.py's own _historical_atr() discipline exactly, so
# a historical recompute can never leak a future bar into a past day's score.
#
# Compute-cost note (profiled directly before choosing this design): RSI/ADX are the dominant
# cost (~6ms/row, ~68s for a SINGLE full-window candidate, ~800s for a naive one-parameter-at-
# a-time sweep pool of 12). Splitting raw-indicator computation (once per row) from the cheap
# curve-remapping step (many times per row, one per candidate — ~0.1ms) brings a full sweep
# down to ~60s total, independent of how many candidates are swept.

_KSCORE_CURVE_SWEEP_DELTA = {
    # (relative step size as a fraction of the current value) — a percentage nudge rather than
    # a fixed absolute delta, since the 11 curve constants span wildly different real scales
    # (rsi_low=30 vs volatility_scale=1500).
    "rsi_low": 0.10, "rsi_mid": 0.10, "rsi_high": 0.10,
    "score_at_low": 0.10, "score_at_mid": 0.05, "score_at_high": 0.05,
    "rsi_overbought_decay_per_point": 0.20,
    "adx_center": 0.20, "adx_divisor": 0.20, "adx_boost_scale": 0.20,
    "volatility_scale": 0.20,
}


def _kscore_curve_candidate_sets(base_params: dict) -> list[dict]:
    """One-parameter-perturbed-at-a-time candidates, matching _kscore_candidate_weight_sets()'s
    own established 'search a tractable neighborhood, not the full 11-dimensional grid'
    judgment. Each candidate is a single-key override dict (e.g. {"rsi_low": 27.0}) — the
    OTHER 10 constants stay at whatever is currently active (resolved later via _curve_params'
    own live-override-then-candidate-layering), never independently perturbed in the same
    candidate."""
    candidates: list[dict] = []
    for key, pct in _KSCORE_CURVE_SWEEP_DELTA.items():
        base_val = base_params[key]
        step = abs(base_val) * pct
        if step == 0:
            continue  # a genuinely zero-valued base constant has no meaningful relative step
        for sign in (1, -1):
            candidates.append({key: round(base_val + sign * step, 4)})
    return candidates


_KSCORE_CURVE_RAW_CACHE_MAX_WINDOW = 300  # see the docstring below for why 300 is safe

def _kscore_curve_raw_cache(
    session: Session, rankings: list, price_by_stock: dict,
) -> dict:
    """Compute _technical_raw_inputs()/_volatility_raw_input() ONCE per Ranking row, keyed by
    row id — the expensive step every candidate in the sweep pool reuses via the cheap
    curve-remap functions instead of recomputing RSI/ADX/realized-vol from scratch per
    candidate. Point-in-time correct: only Price bars with ts.date() <= the row's own as_of are
    visible.

    BUG-KSCORECURVE-UNBOUNDEDWINDOW (2026-08-31): the original version passed the FULL
    "all history up to this row's own as_of date" slice into pd.DataFrame() + rolling/ewm
    computations for every single ranking row — a real, unbounded, O(n^2)-ish cost as `idx`
    (and therefore the per-row DataFrame size) grows across a sweep window. Confirmed live: a
    real 365-day/11,746-row sweep against production data (166 stocks, up to 767 bars of
    history each) did not complete _kscore_curve_raw_cache() alone within 250s, silently
    hanging POST /rankings/tune_kscore_curve well past every reasonable client timeout and
    repeatedly leaving orphaned idle-in-transaction Postgres connections behind (each aborted
    client retry abandoning the still-running server-side request, which never got the chance
    to close its own session cleanly).

    Only the trailing _KSCORE_CURVE_RAW_CACHE_MAX_WINDOW bars are actually needed:
    _technical_raw_inputs() computes at most a 200-bar SMA (its own longest rolling window),
    and _rsi()/_adx_value()'s EWM computations converge geometrically — verified directly
    against real production data (stock_id=1, 752 bars of real history) that a 300-bar window
    produces results differing from the full-history version by <4e-9 (RSI) / <1.2e-7 (ADX),
    many orders of magnitude below any threshold that could change which curve-shape candidate
    the sweep selects. 300 gives sma200 a 100-bar warmup margin beyond its own 200-bar
    requirement — comfortably enough for RSI/ADX's own EWM to have converged too.

    Also fixes a second, independent real inefficiency: `dates` was rebuilt from scratch (a
    full O(len(closes)) list comprehension) on EVERY ranking row, even though multiple rows
    for the same stock_id share the identical, unchanged `closes` list — now cached once per
    stock via `_dates_cache`, computed on first use rather than for every one of that stock's
    ~70-row average share of the sweep window.
    """
    from ..scoring.kscore import _technical_raw_inputs, _volatility_raw_input
    import bisect

    cache: dict[int, dict] = {}
    _dates_cache: dict[int, list] = {}
    for r in rankings:
        closes = price_by_stock.get(r.stock_id)
        if not closes:
            continue
        dates = _dates_cache.get(r.stock_id)
        if dates is None:
            dates = [d for d, _row in closes]
            _dates_cache[r.stock_id] = dates
        idx = bisect.bisect_right(dates, r.as_of)  # last bar with ts.date() <= as_of
        if idx == 0:
            continue  # no price history at or before this row's own date
        window_start = max(0, idx - _KSCORE_CURVE_RAW_CACHE_MAX_WINDOW)
        window = [row for _d, row in closes[window_start:idx]]
        if len(window) < 15:  # need at least enough bars for _adx_value()'s own 14-period floor
            continue
        df = pd.DataFrame(window)
        try:
            cache[r.id] = {
                "technical": _technical_raw_inputs(df),
                "volatility": _volatility_raw_input(df),
            }
        except Exception as _row_exc:
            log.warning("ranking.kscore_curve_raw_input_failed", ranking_id=r.id, error=str(_row_exc))
    return cache


def _kscore_curve_composite_fn(base_weights: dict, curve_cfg: dict, raw_cache: dict):
    """Builds the composite_fn closure _kscore_cross_sectional_ev() expects — recomputes a
    row's technical/volatility sub-scores from its CACHED raw inputs under the candidate
    curve_cfg, reuses the row's already-persisted momentum/value/growth/rs_score unchanged
    (only #17/#18/#19 affect technical/volatility — this sweep never touches the weights sweep's
    own domain), then applies the row's own None-aware active-weight redistribution exactly as
    compute_kscore() would."""
    from ..scoring.kscore import _technical_score_from_raw, _volatility_score_from_raw

    def _fn(row):
        raw = raw_cache.get(row.id)
        if raw is None:
            return None
        tech = _technical_score_from_raw(raw["technical"], curve_cfg)
        vol = _volatility_score_from_raw(raw["volatility"], curve_cfg)
        active = _kscore_active_weights_for_row(base_weights, row)
        w_sum = sum(active.values())
        if w_sum <= 0:
            return None
        values = {
            "technical": tech, "momentum": row.momentum, "volatility": vol,
            "value": row.value, "growth": row.growth, "relative_strength": row.rs_score,
        }
        return sum((w / w_sum) * values[f] for f, w in active.items())

    return _fn


@router.post("/tune_kscore_curve")
def tune_kscore_curve(
    days: int = Query(365, description="Look-back window in calendar days"),
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Walk-forward validated sweep of K-Score's curve-shape constants (T234-CONFIG-
    UNJUSTIFIED-THRESHOLDS Group B, items #17/#18/#19 — kscore.py's _CURVE_DEFAULTS).

    Unlike tune_kscore_weights() (which only re-weights already-persisted sub-scores), this
    recomputes technical/volatility from real historical Price bars under a candidate curve
    (see the module-level comment above this function for the full point-in-time and
    compute-cost design rationale). Chronological 70/30 train/validation split. For each of the
    ~20 one-constant-perturbed candidates, measures cross-sectional top-decile EV on train; the
    single best candidate only gets applied if it ALSO beats the CURRENT LIVE curve params' own
    EV on the validation slice the search never saw — matching tune_kscore_weights()'s own
    beat-the-live-baseline discipline exactly.
    """
    from ..scoring.kscore import (
        _KSCORE_CURVE_REDIS_KEY,
        _load_active_curve_params,
        _load_active_weights,
    )

    current_weights = _load_active_weights()
    current_curve = _load_active_curve_params()
    cutoff = date.today() - timedelta(days=days)

    all_rankings = list(session.execute(
        select(Ranking).where(Ranking.as_of >= cutoff).order_by(Ranking.as_of)
    ).scalars())

    stock_ids = sorted({r.stock_id for r in all_rankings})
    if not stock_ids or len(all_rankings) < _KSCORE_SWEEP_MIN_ROWS * 2:
        return {
            "applied": False,
            "reason": f"only {len(all_rankings)} ranking rows in the window (need {_KSCORE_SWEEP_MIN_ROWS * 2} for a valid train/validation split)",
        }

    # One bulk daily-OHLC fetch per stock (not per ranking row) — same convention as
    # tune_kscore_weights()'s own price fetch, but carrying high/low too (technical_raw_inputs
    # needs them for the ADX computation, unlike the plain-close forward-return lookup above).
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close, Price.high, Price.low)
        .where(Price.stock_id.in_(stock_ids), Price.timeframe == TimeFrame.D1)
        .order_by(Price.stock_id, Price.ts)
    ).all()
    price_by_stock: dict[int, list[tuple]] = defaultdict(list)
    for sid, ts, close, high, low in price_rows:
        price_by_stock[sid].append((ts.date(), {"close": close, "high": high, "low": low}))

    import bisect
    forward_return_by_id: dict[int, float] = {}
    for r in all_rankings:
        closes = price_by_stock.get(r.stock_id)
        if not closes:
            continue
        dates = [d for d, _row in closes]
        idx = bisect.bisect_left(dates, r.as_of)
        if idx >= len(closes) or dates[idx] != r.as_of:
            continue
        fwd_idx = idx + _KSCORE_SWEEP_FORWARD_BARS
        if fwd_idx >= len(closes):
            continue
        entry_close = closes[idx][1]["close"]
        exit_close = closes[fwd_idx][1]["close"]
        if entry_close and entry_close > 0:
            forward_return_by_id[r.id] = (exit_close - entry_close) / entry_close

    resolvable = [r for r in all_rankings if r.id in forward_return_by_id]
    if len(resolvable) < _KSCORE_SWEEP_MIN_ROWS * 2:
        return {
            "applied": False,
            "reason": f"only {len(resolvable)} ranking rows have a resolvable {_KSCORE_SWEEP_FORWARD_BARS}-bar forward return (need {_KSCORE_SWEEP_MIN_ROWS * 2})",
        }

    split = max(1, int(len(resolvable) * 0.7))
    train_rows = resolvable[:split]
    val_rows = resolvable[split:]
    if len(train_rows) < _KSCORE_SWEEP_MIN_ROWS or len(val_rows) < _KSCORE_SWEEP_MIN_ROWS:
        return {
            "applied": False,
            "reason": f"train/validation split too lopsided ({len(train_rows)}/{len(val_rows)}, need >= {_KSCORE_SWEEP_MIN_ROWS} each)",
        }

    train_window = (train_rows[0].as_of, train_rows[-1].as_of)
    val_window = (val_rows[0].as_of, val_rows[-1].as_of)

    train_by_date: dict = defaultdict(list)
    for r in train_rows:
        train_by_date[r.as_of].append(r)
    val_by_date: dict = defaultdict(list)
    for r in val_rows:
        val_by_date[r.as_of].append(r)

    # The expensive step, run ONCE for the whole resolvable set (not per candidate) — see the
    # module-level comment above this function for the compute-cost rationale.
    raw_cache = _kscore_curve_raw_cache(session, resolvable, price_by_stock)

    run_id = __import__("uuid").uuid4().hex
    candidates = _kscore_curve_candidate_sets(current_curve)

    best_ev = -999.0
    best_curve: dict | None = None
    for cand in candidates:
        fn = _kscore_curve_composite_fn(current_weights, cand, raw_cache)
        stats = _kscore_cross_sectional_ev(train_by_date, forward_return_by_id, fn)
        if stats is not None and stats["ev_pct"] > best_ev:
            best_ev = stats["ev_pct"]
            best_curve = cand

    if best_curve is None:
        _record_kscore_tune_history(
            session, run_id, old_value=current_curve, new_value={},
            train_window=train_window, validation_window=val_window,
            train_ev_pct=None, validation_ev_pct=None, baseline_validation_ev_pct=None,
            validation_n=None, promoted=False, gate_failures=["no_candidate_met_train_criteria"],
            parameter_class="kscore_curve", parameter_name="curve_shape",
        )
        return {"applied": False, "reason": "no candidate curve param met the train-slice criteria"}

    baseline_fn = _kscore_curve_composite_fn(current_weights, {}, raw_cache)
    candidate_fn = _kscore_curve_composite_fn(current_weights, best_curve, raw_cache)
    candidate_stats = _kscore_cross_sectional_ev(val_by_date, forward_return_by_id, candidate_fn)
    baseline_stats = _kscore_cross_sectional_ev(val_by_date, forward_return_by_id, baseline_fn)

    if candidate_stats is None:
        _record_kscore_tune_history(
            session, run_id, old_value=current_curve, new_value=best_curve,
            train_window=train_window, validation_window=val_window,
            train_ev_pct=best_ev, validation_ev_pct=None, baseline_validation_ev_pct=None,
            validation_n=None, promoted=False, gate_failures=["candidate_unmeasurable_on_validation"],
            parameter_class="kscore_curve", parameter_name="curve_shape",
        )
        return {"applied": False, "reason": "candidate curve params unmeasurable on the validation slice"}

    if baseline_stats is None:
        _record_kscore_tune_history(
            session, run_id, old_value=current_curve, new_value=best_curve,
            train_window=train_window, validation_window=val_window,
            train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
            baseline_validation_ev_pct=None, validation_n=candidate_stats["n_scored"],
            promoted=False, gate_failures=["baseline_unmeasurable_on_validation"],
            parameter_class="kscore_curve", parameter_name="curve_shape",
        )
        return {"applied": False, "reason": "current live curve params unmeasurable on the validation slice"}

    ev_lift = round(candidate_stats["ev_pct"] - baseline_stats["ev_pct"], 3)
    # Unconditional rejection of a non-positive lift — matching every other sweep in this
    # codebase's own established discipline (see tune_kscore_weights()'s own identical comment
    # for the full reasoning).
    if ev_lift <= 0:
        _record_kscore_tune_history(
            session, run_id, old_value=current_curve, new_value=best_curve,
            train_window=train_window, validation_window=val_window,
            train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
            baseline_validation_ev_pct=baseline_stats["ev_pct"], validation_n=candidate_stats["n_scored"],
            promoted=False, gate_failures=[f"ev_lift_not_positive:{ev_lift}"],
            parameter_class="kscore_curve", parameter_name="curve_shape",
        )
        return {
            "applied": False,
            "reason": f"validation-slice EV lift {ev_lift}pp is not positive",
            "candidate": best_curve, "current": current_curve,
        }

    new_curve = {**current_curve, **best_curve}
    redis_client = None
    try:
        from common.redis_client import get_redis
        import json
        redis_client = get_redis()
        redis_client.setex(_KSCORE_CURVE_REDIS_KEY, 30 * 86400, json.dumps(new_curve))
    except Exception as _redis_exc:
        log.warning("ranking.kscore_curve_redis_write_failed", error=str(_redis_exc))
        _record_kscore_tune_history(
            session, run_id, old_value=current_curve, new_value=best_curve,
            train_window=train_window, validation_window=val_window,
            train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
            baseline_validation_ev_pct=baseline_stats["ev_pct"], validation_n=candidate_stats["n_scored"],
            promoted=False, gate_failures=["redis_write_failed"],
            parameter_class="kscore_curve", parameter_name="curve_shape",
        )
        return {"applied": False, "reason": "validated but Redis write failed — not applied"}

    _record_kscore_tune_history(
        session, run_id, old_value=current_curve, new_value=best_curve,
        train_window=train_window, validation_window=val_window,
        train_ev_pct=best_ev, validation_ev_pct=candidate_stats["ev_pct"],
        baseline_validation_ev_pct=baseline_stats["ev_pct"], validation_n=candidate_stats["n_scored"],
        promoted=True, gate_failures=[],
        parameter_class="kscore_curve", parameter_name="curve_shape",
    )
    return {
        "applied": True,
        "previous_curve": current_curve,
        "new_curve": new_curve,
        "candidate_delta": best_curve,
        "train_ev_pct": best_ev,
        "validation_ev_pct": candidate_stats["ev_pct"],
        "validation_baseline_ev_pct": baseline_stats["ev_pct"],
        "ev_lift_pct": ev_lift,
        "validation_n_days": candidate_stats["n_days"],
    }


@router.post("/refresh")
def refresh(
    tasks: BackgroundTasks,
    market: str | None = None,
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Compute + persist rankings for the whole universe."""
    # BUG-DELISTED-GENERATION-BLIND: Stock.delisted (aud14-survivorship) never flips
    # Stock.active — a confirmed-delisted stock stays "active" forever, so this endpoint kept
    # recomputing fresh K-Scores for it on every refresh cycle, wasting real work on a stock
    # that can never be traded again. Confirmed sibling of BUG-PAPERPOS-DELISTED-FROZEN/
    # BUG-ALERTS-DELISTED-SILENT (2026-07-29, market-data) — same gap, the ranking-engine and
    # signal-engine generation side.
    stmt = select(Stock).where(Stock.active.is_(True), Stock.delisted.is_(False))
    if market:
        stmt = stmt.where(Stock.market == market.upper())
    stocks = list(session.execute(stmt).scalars())

    tasks.add_task(_persist_rankings, [s.id for s in stocks])
    return {"status": "scheduled", "count": len(stocks)}


def _persist_rankings(stock_ids: list[int]) -> None:
    # T232-RANKSTALE: this function runs inside a FastAPI BackgroundTasks callback, whose
    # exceptions are NOT surfaced anywhere by default — no response to fail, no automatic
    # log. It previously had zero logging, so a silent stall or an unhandled exception on
    # any single stock (killing the whole per-stock loop below) was completely invisible:
    # rankings.as_of sat 10+ days stale while POST /rankings/refresh kept returning 200
    # "scheduled" (that response is sent before this function even starts). Wrapped the
    # whole run in try/except and added explicit start/done/error logging so a stall or
    # crash is now visible in container logs instead of just an aging as_of column.
    from db import SessionLocal, Stock as StockModel

    today = date.today()
    log.info("ranking.persist_rankings_started", count=len(stock_ids))
    t0 = _time.time()
    try:
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
            skipped = 0
            errored = 0
            # T232-DL8: rebuilt wholesale each run, not accumulated — a stock that had
            # enough history last run and doesn't need re-flagging just isn't in this dict.
            skipped_this_run: dict[int, dict] = {}
            for sid in stock_ids:
                # T232-RANKSTALE: isolate each stock — one bad symbol (bad price data,
                # a compute_kscore edge case, etc.) must not silently abort the entire
                # batch the way it could before this try/except existed.
                try:
                    stock = all_stocks.get(sid)
                    if not stock:
                        continue
                    df = _load_prices(session, sid)
                    if df.empty or len(df) < 60:
                        skipped += 1
                        skipped_this_run[sid] = {
                            "symbol": stock.symbol,
                            "bars_available": int(len(df)),
                            "bars_required": 60,
                            "as_of": today.isoformat(),
                        }
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
                except Exception as _stock_exc:
                    errored += 1
                    log.warning("ranking.persist_rankings_stock_failed",
                                stock_id=sid, error=str(_stock_exc))
            global _skipped_insufficient_history
            _skipped_insufficient_history = skipped_this_run
            # T232-RANKSTALE: session.execute(stmt)/commit() were previously OUTSIDE this
            # `if rows:` block (same indentation as the `if`, not nested under it) — if
            # every stock in the batch was skipped or errored (e.g. a yfinance rate-limit
            # cascading into skip_low_volume-style skips upstream), `rows` was empty,
            # `stmt` was never assigned, and this raised NameError, killing the whole
            # background task with no caller to see the exception. Silent ranking
            # staleness with zero log output was the exact symptom this produced.
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
            log.info("ranking.persist_rankings_done",
                     requested=len(stock_ids), written=len(rows),
                     skipped_insufficient_history=skipped, errored=errored,
                     elapsed_s=round(_time.time() - t0, 1))
    except Exception as exc:
        log.error("ranking.persist_rankings_failed", error=str(exc), exc_info=True)
