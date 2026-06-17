"""/stocks, /stocks/{symbol}/prices — read API for market data."""
from datetime import date, datetime, timedelta, timezone
import json

import pandas as pd

_MARKET_UTC_OFFSET_H = {"HK": 8, "CN": 8}

def _local_date(ts: datetime, market: str) -> str:
    """Return YYYY-MM-DD in the stock's local market timezone.

    Daily bars for non-US markets are stored as UTC-naive UTC times that
    represent midnight of the LOCAL trading date (e.g. 2026-05-05 16:00 UTC
    for a HK bar dated 2026-05-06 HKT). Add the UTC offset to recover the
    correct local date.
    """
    offset = _MARKET_UTC_OFFSET_H.get(market, 0)
    if offset and ts.hour >= (24 - offset):
        return (ts + timedelta(hours=offset)).strftime("%Y-%m-%d")
    return ts.strftime("%Y-%m-%d")


def _format_ts(ts: datetime, market: str, timeframe: str) -> str:
    """Return the timestamp string for the API response.

    Intraday bars (5m, 15m, etc.) are stored in UTC and returned as a full
    ISO-8601 datetime so the frontend can render time labels on the chart.
    Daily bars return YYYY-MM-DD as before.
    """
    if timeframe in ("1m", "5m", "15m", "1h"):
        return ts.strftime("%Y-%m-%dT%H:%M:%S")
    return _local_date(ts, market)
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
import redis as redis_lib
import yfinance as yf

from common.config import get_settings
from common.logging import get_logger
from db import Fundamental, Price, Stock, TimeFrame, get_session
from .auth import get_current_user

log = get_logger("routes")
router = APIRouter(prefix="/stocks", tags=["stocks"])

_settings = get_settings()
_redis: redis_lib.Redis | None = None

def _get_redis() -> redis_lib.Redis:
    global _redis
    if _redis is None:
        _redis = redis_lib.Redis.from_url(_settings.redis_url, decode_responses=True)
    return _redis

_LIVE_KEY = "stockai:live_prices"
_LIVE_TTL = 90  # seconds — refreshed every 1 min by scheduler; 90s gives a 30s buffer


class StockOut(BaseModel):
    id: int
    symbol: str
    name: str
    name_zh: str | None = None
    market: str
    exchange: str
    sector: str | None = None
    currency: str

    class Config:
        from_attributes = True


class PriceOut(BaseModel):
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_close: float | None = None


@router.get("", response_model=list[StockOut])
def list_stocks(
    market: str | None = None,
    limit: int = Query(200, le=5000),
    session: Session = Depends(get_session),
):
    stmt = select(Stock).where(Stock.active.is_(True))
    if market:
        stmt = stmt.where(Stock.market == market.upper())
    return list(session.execute(stmt.limit(limit)).scalars())


class LatestPriceOut(BaseModel):
    symbol: str
    price: float
    prev_close: float | None
    change_pct: float | None
    currency: str
    volume: int | None = None
    avg_volume: int | None = None


def _fetch_live_one(symbol: str, currency: str) -> dict | None:
    """Fetch live quote for one symbol via yfinance fast_info (real-time, for small sets)."""
    try:
        ticker = yf.Ticker(symbol)
        price = None
        prev_close = None
        try:
            fi = ticker.fast_info
            price = fi.last_price
            prev_close = getattr(fi, "previous_close", None)
        except Exception as fallback_exc:
            log.info("yfinance.fast_info.fallback", symbol=symbol, error=str(fallback_exc))
            hist = ticker.history(period="2d", interval="1d", auto_adjust=True)
            if hist is not None and not hist.empty and "Close" in hist.columns:
                if isinstance(hist.index, pd.MultiIndex):
                    hist.index = hist.index.droplevel(0)
                price = hist["Close"].iloc[-1]
                prev_close = hist["Close"].iloc[-2] if len(hist) > 1 else None

        if price is None:
            log.info("live_price.not_found", symbol=symbol, error="no_price_data")
            return None

        volume = None
        avg_volume = None
        try:
            volume = int(getattr(fi, "last_volume", None) or 0) or None
            avg_volume = int(getattr(fi, "three_month_average_volume", None) or 0) or None
        except Exception:
            pass

        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else None
        return {
            "symbol": symbol,
            "price": round(float(price), 4),
            "prev_close": round(float(prev_close), 4) if prev_close else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "currency": currency,
            "volume": volume,
            "avg_volume": avg_volume,
        }
    except Exception as exc:
        log.warning("live_price.unavailable", symbol=symbol, error=str(exc))
        return None


def _fetch_live_bulk(stocks: list) -> list[dict]:
    """Fetch prices for all symbols in one yf.download() call — avoids per-symbol rate limits.

    yf.download() uses Yahoo's batch chart endpoint which is far more lenient than
    calling fast_info/Ticker 68 times in parallel. Falls back to _fetch_live_one
    for any symbols missing from the download result.
    """
    if not stocks:
        return []

    currency_map = {s.symbol: s.currency for s in stocks}
    symbols = [s.symbol for s in stocks]

    try:
        raw = yf.download(
            symbols,
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as exc:
        log.warning("live_prices.bulk_download_failed", error=str(exc))
        return []

    if raw is None or raw.empty:
        return []

    results: list[dict] = []
    fetched: set[str] = set()

    for symbol in symbols:
        try:
            # Multi-ticker: columns are (symbol, price_type)
            if len(symbols) > 1:
                if symbol not in raw.columns.get_level_values(0):
                    continue
                closes = raw[symbol]["Close"].dropna()
            else:
                # Single ticker: flat columns
                closes = raw["Close"].dropna()

            if closes.empty:
                continue

            price = float(closes.iloc[-1])
            prev_close = float(closes.iloc[-2]) if len(closes) > 1 else None
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close else None

            try:
                sym_data = raw[symbol] if len(symbols) > 1 else raw
                vols = sym_data["Volume"].dropna() if "Volume" in sym_data.columns else pd.Series(dtype=float)
                volume = int(float(vols.iloc[-1])) if not vols.empty else None
                avg_volume = int(float(vols.mean())) if len(vols) >= 5 else None
            except Exception:
                volume = None
                avg_volume = None

            results.append({
                "symbol": symbol,
                "price": round(price, 4),
                "prev_close": round(prev_close, 4) if prev_close else None,
                "change_pct": round(change_pct, 2) if change_pct is not None else None,
                "currency": currency_map.get(symbol, "USD"),
                "volume": volume,
                "avg_volume": avg_volume,
            })
            fetched.add(symbol)
        except Exception:
            pass

    # Fill in any symbols the bulk download missed using individual fetches
    missed = [s for s in stocks if s.symbol not in fetched]
    if missed:
        log.info("live_prices.bulk_fallback", count=len(missed))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {pool.submit(_fetch_live_one, s.symbol, s.currency): s.symbol for s in missed}
            for fut in as_completed(futs):
                r = fut.result()
                if r:
                    results.append(r)

    return results


def _latest_prices_from_db(session: Session) -> list[LatestPriceOut]:
    """Fallback: read most recent stored close from Postgres."""
    ranked = (
        select(
            Price.stock_id, Price.close, Price.ts, Price.volume,
            func.row_number()
            .over(partition_by=Price.stock_id, order_by=Price.ts.desc())
            .label("rn"),
        )
        .where(Price.timeframe == TimeFrame.D1)
        .subquery()
    )
    r1 = ranked.alias("r1")
    r2 = ranked.alias("r2")
    stmt = (
        select(Stock.symbol, Stock.currency, r1.c.close.label("price"), r2.c.close.label("prev_close"), r1.c.volume.label("volume"))
        .join(r1, Stock.id == r1.c.stock_id)
        .outerjoin(r2, (Stock.id == r2.c.stock_id) & (r2.c.rn == 2))
        .where(Stock.active.is_(True))
        .where(r1.c.rn == 1)
    )
    result = []
    for symbol, currency, price, prev_close, volume in session.execute(stmt).all():
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else None
        result.append(LatestPriceOut(
            symbol=symbol, price=price, prev_close=prev_close,
            change_pct=round(change_pct, 2) if change_pct is not None else None,
            currency=currency,
            volume=int(volume) if volume is not None else None,
        ))
    return result


_INDICES = [
    ("S&P 500",   "^GSPC", "US"),
    ("NASDAQ",    "^IXIC", "US"),
    ("Dow Jones", "^DJI",  "US"),
    ("VIX",       "^VIX",  "US"),
    ("Hang Seng", "^HSI",  "HK"),
]
_MARKET_OVERVIEW_KEY = "stockai:market_overview"


def _fetch_index(name: str, ticker: str, market: str) -> dict:
    try:
        fi = yf.Ticker(ticker).fast_info
        price = fi.last_price
        prev  = getattr(fi, "previous_close", None)
        chg   = ((price - prev) / prev * 100) if prev and price else None
        return {
            "name": name, "ticker": ticker, "market": market,
            "price": round(float(price), 2) if price else None,
            "change_pct": round(chg, 2) if chg is not None else None,
        }
    except Exception:
        return {"name": name, "ticker": ticker, "market": market, "price": None, "change_pct": None}


@router.get("/market_overview")
def market_overview():
    """Live quotes for major US and HK indices. Redis-cached 60 s."""
    try:
        cached = _get_redis().get(_MARKET_OVERVIEW_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_index, n, t, m): (n, t, m) for n, t, m in _INDICES}
        for fut in as_completed(futures):
            results.append(fut.result())
    # preserve defined order
    order = {t: i for i, (_, t, _) in enumerate(_INDICES)}
    results.sort(key=lambda r: order.get(r["ticker"], 99))

    try:
        _get_redis().setex(_MARKET_OVERVIEW_KEY, 60, json.dumps(results))
    except Exception:
        pass
    return results


_FEAR_GREED_KEY = "stockai:fear_greed"
_FEAR_GREED_TTL = 60 * 60  # 1 hour


def _compute_fear_greed() -> dict:
    """Compute a market Fear & Greed score (0–100) from VIX + S&P momentum.

    Components (equal weight):
    1. VIX: low VIX → greed (inverted scale)
    2. S&P 500 vs 125-day MA: above → greed
    3. S&P 500 momentum (20-day return)
    4. Put/Call proxy: VIX vs 20-day VIX avg (spike → fear)
    """
    import pandas as pd

    def _rating(s: float) -> str:
        if s < 25: return "Extreme Fear"
        if s < 45: return "Fear"
        if s < 55: return "Neutral"
        if s < 75: return "Greed"
        return "Extreme Greed"

    spx = yf.download("^GSPC", period="14mo", interval="1d", progress=False, auto_adjust=True)
    vix = yf.download("^VIX",  period="14mo", interval="1d", progress=False, auto_adjust=True)

    if spx.empty or vix.empty:
        raise ValueError("no data")

    spx_close = spx["Close"].squeeze()
    vix_close = vix["Close"].squeeze()

    # 1. VIX component: VIX 10→100, 10=max greed 40=max fear
    vix_now = float(vix_close.iloc[-1])
    vix_score = float(100 - min(max((vix_now - 10) / 30 * 100, 0), 100))

    # 2. S&P vs 125-day MA
    ma125 = spx_close.rolling(125).mean().iloc[-1]
    spx_now = float(spx_close.iloc[-1])
    ma_score = 75.0 if spx_now > float(ma125) else 25.0

    # 3. 20-day momentum
    r20 = float(spx_close.iloc[-1] / spx_close.iloc[-21] - 1) if len(spx_close) >= 21 else 0.0
    mom_score = float(min(max(50 + r20 * 300, 0), 100))

    # 4. VIX spike vs 20-day avg (spike = fear)
    vix_ma20 = float(vix_close.rolling(20).mean().iloc[-1])
    spike_ratio = vix_now / vix_ma20 if vix_ma20 else 1.0
    spike_score = float(min(max(100 - (spike_ratio - 1) * 200, 0), 100))

    score = round((vix_score + ma_score + mom_score + spike_score) / 4, 1)

    # History: same calc on shifted dates
    def _score_at(offset: int) -> float | None:
        try:
            i = -1 - offset
            v = float(vix_close.iloc[i])
            s = float(spx_close.iloc[i])
            ma = float(spx_close.rolling(125).mean().iloc[i])
            r = float(spx_close.iloc[i] / spx_close.iloc[i - 20] - 1) if abs(i - 20) < len(spx_close) else 0.0
            vm = float(vix_close.rolling(20).mean().iloc[i])
            vs = 100 - min(max((v - 10) / 30 * 100, 0), 100)
            ms = 75.0 if s > ma else 25.0
            mo = min(max(50 + r * 300, 0), 100)
            sp = min(max(100 - (v / vm - 1) * 200 if vm else 100, 0), 100)
            return round((vs + ms + mo + sp) / 4, 1)
        except Exception:
            return None

    # Market regime: S&P 500 vs 200-day MA
    ma200 = spx_close.rolling(200).mean().iloc[-1]
    sp500_vs_ma200_pct = round((spx_now / float(ma200) - 1) * 100, 2) if not pd.isna(ma200) else None
    sp500_regime = "bull" if (sp500_vs_ma200_pct is not None and sp500_vs_ma200_pct > 0) else "bear"

    return {
        "score": score,
        "rating": _rating(score),
        "previous_close": _score_at(1),
        "previous_1_week": _score_at(5),
        "previous_1_month": _score_at(21),
        "previous_1_year": _score_at(252),
        "sp500_regime": sp500_regime,
        "sp500_vs_ma200_pct": sp500_vs_ma200_pct,
        "components": {
            "vix": round(vix_score, 1),
            "sp500_vs_ma": round(ma_score, 1),
            "momentum": round(mom_score, 1),
            "vix_spike": round(spike_score, 1),
        },
    }


@router.get("/fear_greed")
def fear_greed():
    """Computed Fear & Greed Index (0–100) from VIX + S&P momentum. Redis-cached 1 h."""
    try:
        cached = _get_redis().get(_FEAR_GREED_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    try:
        result = _compute_fear_greed()
    except Exception as exc:
        log.warning("fear_greed.compute_failed", error=str(exc))
        raise HTTPException(503, "Fear & Greed data unavailable")

    try:
        _get_redis().setex(_FEAR_GREED_KEY, _FEAR_GREED_TTL, json.dumps(result))
    except Exception:
        pass
    return result


_MARKET_BREADTH_KEY = "stockai:market_breadth"
_MARKET_BREADTH_TTL = 60 * 60 * 4  # 4 hours


@router.get("/market_breadth")
def market_breadth(session: Session = Depends(get_session)):
    """% of active US stocks trading above their 200-day SMA (from latest ranking fair_price).
    Redis-cached 4 h. Used as a regime signal: > 60% = healthy bull, < 40% = broad weakness."""
    from db import Ranking, Market as _Market
    from datetime import date as _date

    try:
        cached = _get_redis().get(_MARKET_BREADTH_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    today = _date.today()
    cutoff = today - timedelta(days=10)

    # Latest ranking per active US stock that has a fair_price (SMA-200)
    latest_subq = (
        select(Ranking.stock_id, func.max(Ranking.as_of).label("max_date"))
        .where(Ranking.as_of >= cutoff)
        .group_by(Ranking.stock_id)
        .subquery()
    )
    rows = session.execute(
        select(Stock.symbol, Ranking.fair_price)
        .join(latest_subq, Stock.id == latest_subq.c.stock_id)
        .join(Ranking, (Ranking.stock_id == latest_subq.c.stock_id) & (Ranking.as_of == latest_subq.c.max_date))
        .where(Stock.active.is_(True), Stock.market == _Market.US, Ranking.fair_price.is_not(None))
    ).all()

    if not rows:
        raise HTTPException(503, "Market breadth data not yet available — run a ranking refresh first.")

    # Compare latest live price to SMA-200; fall back to cached live prices
    live: dict[str, float] = {}
    try:
        cached_prices = _get_redis().get(_LIVE_KEY)
        if cached_prices:
            for item in json.loads(cached_prices):
                if item.get("price") is not None:
                    live[item["symbol"]] = float(item["price"])
    except Exception:
        pass

    above = below = no_price = 0
    for row in rows:
        price = live.get(row.symbol)
        if price is None:
            no_price += 1
            continue
        if price > row.fair_price:
            above += 1
        else:
            below += 1

    total = above + below
    breadth_pct = round(above / total * 100, 1) if total > 0 else None

    if breadth_pct is not None and breadth_pct >= 60:
        breadth_label = "Healthy"
        breadth_color = "#4ade80"
    elif breadth_pct is not None and breadth_pct >= 40:
        breadth_label = "Mixed"
        breadth_color = "#fbbf24"
    else:
        breadth_label = "Weak"
        breadth_color = "#f87171"

    result = {
        "breadth_pct": breadth_pct,
        "above_200ma": above,
        "below_200ma": below,
        "total": total,
        "label": breadth_label,
        "color": breadth_color,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _get_redis().setex(_MARKET_BREADTH_KEY, _MARKET_BREADTH_TTL, json.dumps(result))
    except Exception:
        pass
    return result


@router.get("/data_freshness")
def data_freshness(session: Session = Depends(get_session)):
    """Returns the most recent price bar timestamp (D1 or 5m) to indicate data staleness."""
    now = datetime.now(timezone.utc)
    best_ts = None
    best_tf = None
    for tf in (TimeFrame.M5, TimeFrame.D1):
        ts = session.execute(
            select(func.max(Price.ts)).where(Price.timeframe == tf)
        ).scalar()
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best_tf = tf.value
    if best_ts is None:
        return {"last_bar_ts": None, "hours_ago": None, "status": "no_data"}
    hours_ago = (now - best_ts).total_seconds() / 3600
    status = "fresh" if hours_ago < 8 else "stale" if hours_ago < 30 else "very_stale"
    return {"last_bar_ts": best_ts.isoformat(), "hours_ago": round(hours_ago, 1), "status": status, "timeframe": best_tf}


@router.get("/latest_prices", response_model=list[LatestPriceOut])
def latest_prices(
    symbols: str | None = Query(None, description="Comma-separated symbols to filter"),
    session: Session = Depends(get_session),
):
    """Live prices from yfinance fast_info, Redis-cached for 60 s; DB fallback.
    Pass ?symbols=AAPL,TSM to get a small subset fetched directly (bypasses bulk cache)."""
    symbol_set = {s.strip().upper() for s in symbols.split(",")} if symbols else None

    # Small filtered request — fetch only those symbols directly with per-symbol Redis keys
    # so they never depend on the bulk cache that may be stale after a restart.
    if symbol_set:
        results: list[dict] = []
        stocks_q = session.execute(
            select(Stock.symbol, Stock.currency)
            .where(Stock.active.is_(True), Stock.symbol.in_(symbol_set))
        ).all()
        with ThreadPoolExecutor(max_workers=min(len(stocks_q), 6)) as pool:
            futures = {pool.submit(_fetch_live_one, s.symbol, s.currency): s.symbol for s in stocks_q}
            for fut in as_completed(futures):
                r = fut.result()
                if r:
                    results.append(r)
        if results:
            return results
        # fallback to DB last-close prices
        db_rows = _latest_prices_from_db(session)
        return [r for r in db_rows if r.symbol in symbol_set]

    # 1. Try bulk Redis cache (no filter — full list)
    try:
        cached = _get_redis().get(_LIVE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    # 2. Get all active symbols from DB
    stocks = list(session.execute(
        select(Stock.symbol, Stock.currency).where(Stock.active.is_(True))
    ).all())
    if not stocks:
        return []

    # 3. Single bulk download — one HTTP call instead of 68 parallel fast_info requests
    bulk_results = _fetch_live_bulk(stocks)

    if not bulk_results:
        log.warning("live_prices.all_failed", count=len(stocks))
        return _latest_prices_from_db(session)

    # 4. Cache in Redis
    try:
        _get_redis().setex(_LIVE_KEY, _LIVE_TTL, json.dumps(bulk_results))
    except Exception:
        pass

    log.info("live_prices.ok", count=len(bulk_results), source="yfinance_bulk")
    return bulk_results


def refresh_live_price_cache() -> int:
    """Fetch live prices for all active stocks and write to Redis.

    Designed to be called by the scheduler every minute during market hours.
    Returns the number of symbols successfully refreshed, or 0 on failure.
    Intentionally lightweight — no DB writes, no ranking/signal computation.
    """
    from db import SessionLocal
    try:
        with SessionLocal() as session:
            stocks = list(session.execute(
                select(Stock.symbol, Stock.currency).where(Stock.active.is_(True))
            ).all())
        if not stocks:
            return 0
        results = _fetch_live_bulk(stocks)
        if results:
            _get_redis().setex(_LIVE_KEY, _LIVE_TTL, json.dumps(results))
            log.info("live_prices.cache_refresh", count=len(results))
            return len(results)
        return 0
    except Exception as exc:
        log.warning("live_prices.cache_refresh_failed", error=str(exc))
        return 0


class FundamentalsOut(BaseModel):
    # Valuation
    market_cap: int | None = None
    enterprise_value: int | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_book: float | None = None
    ev_to_ebitda: float | None = None
    ev_to_revenue: float | None = None
    # Income statement (TTM)
    total_revenue: int | None = None
    gross_profit: int | None = None
    net_income: int | None = None
    ebitda: int | None = None
    # Margins
    profit_margin: float | None = None
    operating_margin: float | None = None
    gross_margin: float | None = None
    # Cash flow & balance sheet
    free_cashflow: int | None = None
    operating_cashflow: int | None = None
    total_cash: int | None = None
    total_debt: int | None = None
    # Per share
    trailing_eps: float | None = None
    forward_eps: float | None = None
    book_value: float | None = None
    dividend_yield: float | None = None
    dividend_rate: float | None = None
    # Returns & risk
    return_on_equity: float | None = None
    return_on_assets: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    beta: float | None = None
    # 52-week range
    week_52_high: float | None = None
    week_52_low: float | None = None
    average_volume: int | None = None
    shares_outstanding: int | None = None
    # Analyst consensus
    target_price: float | None = None       # mean target
    target_high: float | None = None
    target_low: float | None = None
    target_median: float | None = None
    recommendation: str | None = None       # key: strongbuy / buy / hold / sell
    recommendation_mean: float | None = None  # 1.0 (strong buy) → 5.0 (sell)
    number_of_analysts: int | None = None
    # Analyst rating breakdown (current period)
    analyst_strong_buy: int | None = None
    analyst_buy: int | None = None
    analyst_hold: int | None = None
    analyst_underperform: int | None = None
    analyst_sell: int | None = None
    # Earnings calendar
    next_earnings_date: str | None = None   # YYYY-MM-DD
    days_to_earnings: int | None = None
    # Insider activity (6-month summary)
    insider_buy_shares_6m: int | None = None
    insider_sell_shares_6m: int | None = None
    insider_buy_transactions_6m: int | None = None
    insider_net_pct: float | None = None    # % net shares purchased
    # Individual analyst actions (last 90 days)
    analyst_actions: list[dict] = []
    # Short interest (Finviz-style)
    short_percent_of_float: float | None = None
    short_ratio: float | None = None
    shares_short: int | None = None
    # Ownership breakdown
    held_percent_institutions: float | None = None
    held_percent_insiders: float | None = None
    # Earnings surprise history (last 8 quarters)
    eps_beat_rate: float | None = None          # 0.0–1.0 fraction of quarters where actual > estimate
    eps_avg_surprise_pct: float | None = None   # mean surprise % across available quarters
    eps_surprise_trend: str | None = None       # "improving" | "declining" | "stable"
    eps_history: list[dict] = []                # [{quarter, actual, estimate, surprise_pct}]
    # Data freshness
    fetched_at: str | None = None               # ISO datetime when yfinance data was last fetched


_FUND_TTL = 60 * 60 * 24  # 24 hours — fundamentals change quarterly


def _safe(info: dict, key: str):
    v = info.get(key)
    if v in (None, "N/A", "None", "", "Infinity", float("inf"), float("-inf")):
        return None
    try:
        return v
    except Exception:
        return None


@router.get("/fundamentals_bulk")
def fundamentals_bulk(session: Session = Depends(get_session)):
    """Return fundamental valuation + growth data for all active stocks from Redis cache.

    Only symbols with a warm cache entry are included — symbols not yet fetched
    (or with expired 24 h TTL) are silently omitted. Used by the ranking engine
    for sector-relative scoring without triggering per-symbol yfinance calls.
    """
    active_symbols = [
        row[0] for row in session.execute(select(Stock.symbol).where(Stock.active.is_(True)))
    ]
    redis_client = _get_redis()
    result: dict[str, dict] = {}
    _FIELDS = (
        "trailing_pe", "forward_pe", "price_to_book",
        "ev_to_ebitda", "ev_to_revenue",
        "profit_margin", "operating_margin",
        "return_on_equity", "return_on_assets",
        "revenue_growth", "earnings_growth",
    )
    for symbol in active_symbols:
        try:
            cached = redis_client.get(f"stockai:fundamentals:v2:{symbol.upper()}")
            if cached:
                data = json.loads(cached)
                result[symbol] = {k: data.get(k) for k in _FIELDS}
        except Exception:
            pass
    return result


@router.get("/{symbol}/fundamentals", response_model=FundamentalsOut)
def get_fundamentals(symbol: str, refresh: bool = False, db: Session = Depends(get_session)):
    """Live company fundamentals from yfinance, Redis-cached for 24 h.
    Pass ?refresh=true to bypass the cache and force a fresh fetch."""
    cache_key = f"stockai:fundamentals:v2:{symbol.upper()}"
    if not refresh:
        try:
            cached = _get_redis().get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    info: dict = {}
    ticker = None
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
    except Exception as exc:
        log.warning("fundamentals.fetch_failed", symbol=symbol, error=str(exc))

    # Analyst rating breakdown from recommendations_summary (current period)
    a_strong_buy = a_buy = a_hold = a_underperform = a_sell = None
    try:
        if ticker is not None:
            recs = ticker.recommendations_summary
            if recs is not None and not recs.empty:
                cur = recs[recs["period"] == "0m"]
                if not cur.empty:
                    row = cur.iloc[0]
                    a_strong_buy   = int(row.get("strongBuy",   0))
                    a_buy          = int(row.get("buy",         0))
                    a_hold         = int(row.get("hold",        0))
                    a_underperform = int(row.get("underperform",0))
                    a_sell         = int(row.get("sell",        0))
    except Exception:
        pass

    # Earnings calendar
    next_earnings_date: str | None = None
    days_to_earnings: int | None = None
    try:
        if ticker is not None:
            cal = ticker.calendar
            if isinstance(cal, dict):
                ed_list = cal.get("Earnings Date") or []
                if ed_list:
                    from datetime import date as _date
                    today = _date.today()
                    future = [d for d in ed_list if (d if isinstance(d, _date) else d.date()) >= today]
                    if future:
                        next_ed = future[0] if isinstance(future[0], _date) else future[0].date()
                        next_earnings_date = next_ed.strftime("%Y-%m-%d")
                        days_to_earnings = (next_ed - today).days
    except Exception:
        pass

    # Analyst upgrades/downgrades — individual firm actions (last 90 days)
    analyst_actions: list[dict] = []
    try:
        if ticker is not None:
            ud = ticker.upgrades_downgrades
            if ud is not None and not ud.empty:
                from datetime import date as _adate, timedelta as _td
                cutoff = _adate.today() - _td(days=90)
                if hasattr(ud.index, 'date'):
                    ud = ud[ud.index.date >= cutoff]
                ud = ud.sort_index(ascending=False).head(20)
                for idx, row in ud.iterrows():
                    action = str(row.get("Action", "")).strip()
                    if not action:
                        continue
                    analyst_actions.append({
                        "date":       (idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]),
                        "firm":       str(row.get("Firm", "")).strip(),
                        "from_grade": str(row.get("FromGrade", "")).strip(),
                        "to_grade":   str(row.get("ToGrade",   "")).strip(),
                        "action":     action,
                    })
    except Exception:
        pass

    # Insider activity (6-month summary)
    # DataFrame layout: columns = ['Insider Purchases Last 6m', 'Shares', 'Trans']
    # Row 0 = Purchases, Row 1 = Sales, Row 4 = % Net Shares Purchased (Sold)
    insider_buy_shares_6m: int | None = None
    insider_sell_shares_6m: int | None = None
    insider_buy_transactions_6m: int | None = None
    insider_net_pct: float | None = None
    try:
        if ticker is not None:
            ip = ticker.insider_purchases
            if ip is not None and not ip.empty:
                def _col(df, *names):
                    for n in names:
                        if n in df.columns:
                            return n
                    return df.columns[1] if len(df.columns) > 1 else None

                shares_col = _col(ip, "Shares")
                trans_col  = _col(ip, "Trans", "Transactions")

                def _safe_val(row_idx, col):
                    try:
                        v = ip.iloc[row_idx][col]
                        return None if str(v) in ("nan", "None", "<NA>", "") else v
                    except Exception:
                        return None

                def _to_int(v):
                    try: return int(float(v)) if v is not None else None
                    except: return None
                def _to_float(v):
                    try: return float(v) if v is not None else None
                    except: return None

                if shares_col:
                    insider_buy_shares_6m        = _to_int(_safe_val(0, shares_col))
                    insider_sell_shares_6m        = _to_int(_safe_val(1, shares_col))
                    insider_net_pct               = _to_float(_safe_val(4, shares_col))
                if trans_col:
                    insider_buy_transactions_6m   = _to_int(_safe_val(0, trans_col))
    except Exception:
        pass

    data = FundamentalsOut(
        market_cap=_safe(info, "marketCap"),
        enterprise_value=_safe(info, "enterpriseValue"),
        trailing_pe=_safe(info, "trailingPE"),
        forward_pe=_safe(info, "forwardPE"),
        price_to_book=_safe(info, "priceToBook"),
        ev_to_ebitda=_safe(info, "enterpriseToEbitda"),
        ev_to_revenue=_safe(info, "enterpriseToRevenue"),
        total_revenue=_safe(info, "totalRevenue"),
        gross_profit=_safe(info, "grossProfits"),
        net_income=_safe(info, "netIncomeToCommon"),
        ebitda=_safe(info, "ebitda"),
        profit_margin=_safe(info, "profitMargins"),
        operating_margin=_safe(info, "operatingMargins"),
        gross_margin=_safe(info, "grossMargins"),
        free_cashflow=_safe(info, "freeCashflow"),
        operating_cashflow=_safe(info, "operatingCashflow"),
        total_cash=_safe(info, "totalCash"),
        total_debt=_safe(info, "totalDebt"),
        trailing_eps=_safe(info, "trailingEps"),
        forward_eps=_safe(info, "forwardEps"),
        book_value=_safe(info, "bookValue"),
        dividend_yield=_safe(info, "dividendYield"),
        dividend_rate=_safe(info, "dividendRate"),
        return_on_equity=_safe(info, "returnOnEquity"),
        return_on_assets=_safe(info, "returnOnAssets"),
        revenue_growth=_safe(info, "revenueGrowth"),
        earnings_growth=_safe(info, "earningsGrowth"),
        beta=_safe(info, "beta"),
        week_52_high=_safe(info, "fiftyTwoWeekHigh"),
        week_52_low=_safe(info, "fiftyTwoWeekLow"),
        average_volume=_safe(info, "averageVolume"),
        shares_outstanding=_safe(info, "sharesOutstanding"),
        target_price=_safe(info, "targetMeanPrice"),
        target_high=_safe(info, "targetHighPrice"),
        target_low=_safe(info, "targetLowPrice"),
        target_median=_safe(info, "targetMedianPrice"),
        recommendation=_safe(info, "recommendationKey"),
        recommendation_mean=_safe(info, "recommendationMean"),
        number_of_analysts=_safe(info, "numberOfAnalystOpinions"),
        analyst_strong_buy=a_strong_buy,
        analyst_buy=a_buy,
        analyst_hold=a_hold,
        analyst_underperform=a_underperform,
        analyst_sell=a_sell,
        next_earnings_date=next_earnings_date,
        days_to_earnings=days_to_earnings,
        insider_buy_shares_6m=insider_buy_shares_6m,
        insider_sell_shares_6m=insider_sell_shares_6m,
        insider_buy_transactions_6m=insider_buy_transactions_6m,
        insider_net_pct=insider_net_pct,
        analyst_actions=analyst_actions,
        short_percent_of_float=_safe(info, "shortPercentOfFloat"),
        short_ratio=_safe(info, "shortRatio"),
        shares_short=_safe(info, "sharesShort"),
        held_percent_institutions=_safe(info, "heldPercentInstitutions"),
        held_percent_insiders=_safe(info, "heldPercentInsiders"),
    )

    # Fetch earnings surprise history (last 8 quarters)
    try:
        eh = ticker.earnings_history
        if eh is not None and not eh.empty:
            eh = eh.tail(8)
            beats = int((eh["epsActual"] > eh["epsEstimate"]).sum())
            total = len(eh)
            data.eps_beat_rate = round(beats / total, 3) if total else None
            surprise_vals = eh["surprisePercent"].dropna().tolist()
            data.eps_avg_surprise_pct = round(float(sum(surprise_vals) / len(surprise_vals)) * 100, 2) if surprise_vals else None
            # Trend: compare avg surprise of first half vs second half
            if len(surprise_vals) >= 4:
                half = len(surprise_vals) // 2
                early = sum(surprise_vals[:half]) / half
                recent = sum(surprise_vals[half:]) / (len(surprise_vals) - half)
                data.eps_surprise_trend = "improving" if recent > early + 0.005 else "declining" if recent < early - 0.005 else "stable"
            data.eps_history = [
                {
                    "quarter": str(idx.date()) if hasattr(idx, "date") else str(idx),
                    "actual": round(float(row["epsActual"]), 4) if row["epsActual"] is not None else None,
                    "estimate": round(float(row["epsEstimate"]), 4) if row["epsEstimate"] is not None else None,
                    "surprise_pct": round(float(row["surprisePercent"]) * 100, 2) if row["surprisePercent"] is not None else None,
                }
                for idx, row in eh.iterrows()
            ]
    except Exception:
        pass

    from datetime import datetime as _dt
    data.fetched_at = _dt.utcnow().isoformat() + "Z"

    try:
        _get_redis().setex(cache_key, _FUND_TTL, data.model_dump_json())
    except Exception:
        pass

    # Persist key fields to DB for ML feature use — upsert on (stock_id, today)
    try:
        from datetime import date as _date
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stock_row = db.execute(
            select(Stock).where(Stock.symbol == symbol.upper())
        ).scalar_one_or_none()
        if stock_row:
            mkt_cap = info.get("marketCap")
            fcf = data.free_cashflow
            stmt = pg_insert(Fundamental).values(
                stock_id=stock_row.id,
                as_of=_date.today(),
                trailing_pe=data.trailing_pe,
                forward_pe=data.forward_pe,
                price_to_book=data.price_to_book,
                gross_margin=data.gross_margin,
                profit_margin=data.profit_margin,
                return_on_equity=data.return_on_equity,
                return_on_assets=data.return_on_assets,
                revenue_growth=data.revenue_growth,
                earnings_growth=data.earnings_growth,
                free_cashflow=fcf,
                market_cap=int(mkt_cap) if mkt_cap else None,
                short_percent_of_float=data.short_percent_of_float,
                short_ratio=data.short_ratio,
                recommendation_mean=data.recommendation_mean,
                number_of_analysts=data.number_of_analysts,
            ).on_conflict_do_update(
                constraint="uq_fundamentals_stock_date",
                set_=dict(
                    trailing_pe=data.trailing_pe,
                    forward_pe=data.forward_pe,
                    price_to_book=data.price_to_book,
                    gross_margin=data.gross_margin,
                    profit_margin=data.profit_margin,
                    return_on_equity=data.return_on_equity,
                    return_on_assets=data.return_on_assets,
                    revenue_growth=data.revenue_growth,
                    earnings_growth=data.earnings_growth,
                    free_cashflow=fcf,
                    market_cap=int(mkt_cap) if mkt_cap else None,
                    short_percent_of_float=data.short_percent_of_float,
                    short_ratio=data.short_ratio,
                    recommendation_mean=data.recommendation_mean,
                    number_of_analysts=data.number_of_analysts,
                    fetched_at=func.now(),
                ),
            )
            db.execute(stmt)
            db.commit()
    except Exception as exc:
        log.warning("fundamentals.db_persist_failed", symbol=symbol, error=str(exc))
        db.rollback()

    log.info("fundamentals.ok", symbol=symbol)
    return data


class QuickScanRequest(BaseModel):
    symbols: list[str]
    price_min: float | None = None
    price_max: float | None = None


class QuickScanOut(BaseModel):
    symbol: str
    price: float
    change_pct: float | None
    change_5d: float | None
    rsi: float | None
    sma20: float | None
    sma50: float | None
    above_sma20: bool | None
    above_sma50: bool | None
    vol_ratio: float | None
    range_pos_20d: float | None


def _scan_one(sym: str, price_min: float | None, price_max: float | None) -> dict | None:
    """Fetch 90d OHLCV for one symbol and compute basic swing indicators."""
    try:
        hist = yf.Ticker(sym).history(period="90d", interval="1d", auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 15:
            return None
        # Handle MultiIndex returned by some yfinance versions
        if isinstance(hist.index, pd.MultiIndex):
            hist.index = hist.index.droplevel(0)

        close = hist["Close"].dropna()
        vol   = hist["Volume"].dropna()
        if len(close) < 15:
            return None

        current = float(close.iloc[-1])
        if price_min is not None and current < price_min:
            return None
        if price_max is not None and current > price_max:
            return None

        prev        = float(close.iloc[-2]) if len(close) >= 2 else current
        change_pct  = round((current - prev) / prev * 100, 2) if prev else None
        change_5d   = round((current - float(close.iloc[-6])) / float(close.iloc[-6]) * 100, 2) if len(close) >= 6 else None

        sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None

        delta  = close.diff()
        gain   = delta.clip(lower=0).rolling(14).mean()
        loss   = (-delta.clip(upper=0)).rolling(14).mean()
        rs     = float(gain.iloc[-1]) / (float(loss.iloc[-1]) + 1e-9)
        rsi    = round(100 - 100 / (1 + rs), 1)

        avg20     = float(vol.iloc[-20:].mean()) if len(vol) >= 20 else None
        avg5      = float(vol.iloc[-5:].mean())  if len(vol) >= 5  else None
        vol_ratio = round(avg5 / avg20, 2) if (avg20 and avg20 > 0 and avg5 is not None) else None

        high20    = float(close.iloc[-20:].max()) if len(close) >= 20 else None
        low20     = float(close.iloc[-20:].min()) if len(close) >= 20 else None
        range_pos = round((current - low20) / (high20 - low20), 2) if (high20 and low20 and high20 > low20) else None

        return {
            "symbol": sym, "price": round(current, 4),
            "change_pct": change_pct, "change_5d": change_5d,
            "rsi": rsi,
            "sma20": round(sma20, 4) if sma20 else None,
            "sma50": round(sma50, 4) if sma50 else None,
            "above_sma20": bool(current > sma20) if sma20 else None,
            "above_sma50": bool(current > sma50) if sma50 else None,
            "vol_ratio": vol_ratio, "range_pos_20d": range_pos,
        }
    except Exception as exc:
        log.debug("quick_scan.symbol_failed", symbol=sym, error=str(exc))
        return None


@router.post("/quick_scan", response_model=list[QuickScanOut])
def quick_scan(req: QuickScanRequest, _user=Depends(get_current_user)):
    symbols = list({s.upper().strip() for s in req.symbols[:80] if s.strip()})
    if not symbols:
        return []
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_scan_one, sym, req.price_min, req.price_max): sym for sym in symbols}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)
    return results


# ── Sector Performance ────────────────────────────────────────────────────────

@router.get("/sector_performance")
def sector_performance(session: Session = Depends(get_session)):
    """Group all tracked stocks by sector with aggregate day-change performance."""
    stocks = session.execute(select(Stock).where(Stock.active.is_(True))).scalars().all()
    stock_map = {s.symbol: s for s in stocks}

    # Pull live prices from Redis
    prices: dict[str, dict] = {}
    try:
        cached = _get_redis().get(_LIVE_KEY)
        if cached:
            for item in json.loads(cached):
                prices[item["symbol"]] = item
    except Exception:
        pass
    # DB fallback for any missing symbols
    if not prices:
        for row in _latest_prices_from_db(session):
            prices[row["symbol"] if isinstance(row, dict) else row.symbol] = (
                row if isinstance(row, dict) else row.__dict__
            )

    from collections import defaultdict
    sectors: dict[str, list] = defaultdict(list)
    no_sector: list = []
    for sym, stock in stock_map.items():
        p = prices.get(sym)
        entry = {
            "symbol": sym,
            "name": stock.name,
            "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
            "price": p.get("price") if p else None,
            "change_pct": p.get("change_pct") if p else None,
        }
        if stock.sector:
            sectors[stock.sector].append(entry)
        else:
            no_sector.append(entry)

    result = []
    for sector_name, items in sectors.items():
        changes = [x["change_pct"] for x in items if x["change_pct"] is not None]
        avg_change = round(sum(changes) / len(changes), 3) if changes else None
        result.append({
            "sector": sector_name,
            "avg_change_pct": avg_change,
            "stock_count": len(items),
            "stocks": sorted(items, key=lambda x: (x["change_pct"] or 0), reverse=True),
        })
    if no_sector:
        changes = [x["change_pct"] for x in no_sector if x["change_pct"] is not None]
        result.append({
            "sector": "Other",
            "avg_change_pct": round(sum(changes) / len(changes), 3) if changes else None,
            "stock_count": len(no_sector),
            "stocks": no_sector,
        })
    result.sort(key=lambda x: x["avg_change_pct"] or -999, reverse=True)
    return result


# ── Sector Rotation Heatmap (RES-4) ──────────────────────────────────────────

_SECTOR_ETFS = {
    "XLK": "Technology",
    "XLV": "Healthcare",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLI": "Industrials",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
    "XLB": "Materials",
}
_SECTOR_ROTATION_TTL = 3_600  # 1-hour cache


@router.get("/sector_rotation")
def sector_rotation():
    """RES-4: Returns 1w / 1m / 3m returns for US sector ETFs vs SPY.

    Classification vs SPY 1m return:
      leading      — sector >= SPY + 3%
      in-line      — within 3% of SPY
      lagging      — sector < SPY - 1%
      distributing — sector < SPY - 5%
    """
    r = _get_redis()
    cache_key = "sector_rotation"
    try:
        cached = r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    tickers = list(_SECTOR_ETFS.keys()) + ["SPY"]
    try:
        raw = yf.download(tickers, period="4mo", interval="1d", progress=False, auto_adjust=True)
        # Multi-ticker download returns MultiIndex columns (field, ticker) — select Close level
        try:
            closes = raw["Close"]
        except KeyError:
            closes = raw  # single-ticker fallback (shouldn't happen here)
    except Exception as exc:
        log.warning("sector_rotation.yf_failed", error=str(exc))
        return {"error": "Unable to fetch sector data", "sectors": []}

    results = []
    spy_closes = closes["SPY"].dropna() if "SPY" in closes.columns else None

    def _ret(series, days: int) -> float | None:
        clean = series.dropna()
        if len(clean) < days + 1:
            return None
        return round((float(clean.iloc[-1]) / float(clean.iloc[-days - 1]) - 1) * 100, 2)

    spy_1m = _ret(spy_closes, 21) if spy_closes is not None else None

    for etf, sector_name in _SECTOR_ETFS.items():
        if etf not in closes.columns:
            continue
        s = closes[etf]
        ret_1w = _ret(s, 5)
        ret_1m = _ret(s, 21)
        ret_3m = _ret(s, 63)

        vs_spy = (ret_1m - spy_1m) if ret_1m is not None and spy_1m is not None else None
        if vs_spy is None:
            status = "unknown"
        elif vs_spy >= 3:
            status = "leading"
        elif vs_spy >= -1:
            status = "in-line"
        elif vs_spy >= -5:
            status = "lagging"
        else:
            status = "distributing"

        results.append({
            "etf": etf,
            "sector": sector_name,
            "ret_1w": ret_1w,
            "ret_1m": ret_1m,
            "ret_3m": ret_3m,
            "vs_spy_1m": round(vs_spy, 2) if vs_spy is not None else None,
            "status": status,
        })

    results.sort(key=lambda x: x["ret_1m"] or -999, reverse=True)
    payload = {"spy_1m": spy_1m, "sectors": results, "ts": datetime.now(timezone.utc).isoformat()}

    try:
        r.setex(cache_key, _SECTOR_ROTATION_TTL, json.dumps(payload))
    except Exception:
        pass

    return payload


# ── Earnings Calendar ─────────────────────────────────────────────────────────

@router.get("/earnings_calendar")
def earnings_calendar(days_ahead: int = Query(45, ge=1, le=180), session: Session = Depends(get_session)):
    """Return stocks with earnings in the next N days (from cached fundamentals)."""
    from datetime import date as _date
    stocks = session.execute(select(Stock).where(Stock.active.is_(True))).scalars().all()
    r = _get_redis()
    today = _date.today()
    cutoff = today + timedelta(days=days_ahead)
    results = []
    for stock in stocks:
        cache_key = f"stockai:fundamentals:v2:{stock.symbol}"
        try:
            cached = r.get(cache_key)
            if not cached:
                continue
            data = json.loads(cached)
            ned = data.get("next_earnings_date")
            if not ned:
                continue
            ned_date = _date.fromisoformat(ned)
            if today <= ned_date <= cutoff:
                dte = (ned_date - today).days
                results.append({
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "sector": stock.sector,
                    "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
                    "next_earnings_date": ned,
                    "days_to_earnings": dte,
                    "eps_estimate": data.get("forward_eps"),
                    "trailing_eps": data.get("trailing_eps"),
                    "revenue_growth": data.get("revenue_growth"),
                    "earnings_growth": data.get("earnings_growth"),
                    "market_cap": data.get("market_cap"),
                })
        except Exception:
            continue
    results.sort(key=lambda x: x["days_to_earnings"])
    return results


# ── Analyst Ratings Feed ──────────────────────────────────────────────────────

@router.get("/analyst_ratings")
def analyst_ratings(days: int = Query(30, ge=1, le=180), session: Session = Depends(get_session)):
    """Return recent analyst upgrades/downgrades aggregated from cached fundamentals."""
    from datetime import date as _adate
    stocks = session.execute(select(Stock).where(Stock.active.is_(True))).scalars().all()
    stock_map = {s.symbol: s for s in stocks}
    r = _get_redis()
    cutoff = (_adate.today() - timedelta(days=days)).isoformat()
    results = []
    for symbol, stock in stock_map.items():
        cache_key = f"stockai:fundamentals:v2:{symbol}"
        try:
            cached = r.get(cache_key)
            if not cached:
                continue
            data = json.loads(cached)
            for action in data.get("analyst_actions", []):
                if action.get("date", "") >= cutoff and action.get("action"):
                    results.append({
                        "symbol": symbol,
                        "name": stock.name,
                        "sector": stock.sector,
                        "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
                        "date": action["date"],
                        "firm": action.get("firm", ""),
                        "from_grade": action.get("from_grade", ""),
                        "to_grade": action.get("to_grade", ""),
                        "action": action.get("action", ""),
                        "target_price": data.get("target_price"),
                        "recommendation": data.get("recommendation"),
                    })
        except Exception:
            continue
    results.sort(key=lambda x: x["date"], reverse=True)
    return results


# ── Short Squeeze Scanner ─────────────────────────────────────────────────────

@router.get("/short_squeeze")
def short_squeeze(
    min_short_float: float = Query(10.0, description="Minimum short % of float"),
    session: Session = Depends(get_session),
):
    """Return high-short-interest stocks with positive momentum (squeeze candidates)."""
    from db import Ranking
    from datetime import date as _sdate
    stocks = session.execute(select(Stock).where(Stock.active.is_(True))).scalars().all()
    stock_map = {s.symbol: s for s in stocks}
    r = _get_redis()

    # Latest rankings for momentum scores
    today = _sdate.today()
    rank_rows = session.execute(
        select(Ranking)
        .where(Ranking.as_of >= today - timedelta(days=7))
        .order_by(Ranking.stock_id, Ranking.as_of.asc())
    ).scalars().all()
    rank_map = {rk.stock_id: rk for rk in rank_rows}  # last write per stock_id = most recent
    stock_id_map = {s.symbol: s.id for s in stocks}

    # Live prices
    prices: dict[str, dict] = {}
    try:
        cached_prices = _get_redis().get(_LIVE_KEY)
        if cached_prices:
            for item in json.loads(cached_prices):
                prices[item["symbol"]] = item
    except Exception:
        pass

    results = []
    for symbol, stock in stock_map.items():
        cache_key = f"stockai:fundamentals:v2:{symbol}"
        try:
            cached = r.get(cache_key)
            if not cached:
                continue
            data = json.loads(cached)
            spf = data.get("short_percent_of_float")
            if spf is None or spf * 100 < min_short_float:
                continue
            sid = stock_id_map.get(symbol)
            rank = rank_map.get(sid)
            p = prices.get(symbol)
            results.append({
                "symbol": symbol,
                "name": stock.name,
                "sector": stock.sector,
                "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
                "short_percent_of_float": round(spf * 100, 2),
                "short_ratio": data.get("short_ratio"),
                "shares_short": data.get("shares_short"),
                "price": p.get("price") if p else None,
                "change_pct": p.get("change_pct") if p else None,
                "momentum_score": rank.momentum if rank else None,
                "k_score": rank.score if rank else None,
                "volume": p.get("volume") if p else None,
            })
        except Exception:
            continue
    results.sort(key=lambda x: x["short_percent_of_float"], reverse=True)
    return results


# ── Relative Performance (multi-symbol normalized price series) ───────────────

@router.get("/relative_performance")
def relative_performance(
    symbols: str = Query(..., description="Comma-separated symbols (max 8)"),
    days: int = Query(90, ge=7, le=730),
    session: Session = Depends(get_session),
):
    """Return base-100 normalized daily close series for multiple symbols."""
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()][:8]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days + 5)  # +5 for alignment buffer
    result: dict[str, list] = {}

    for symbol in sym_list:
        stock = session.execute(
            select(Stock).where(Stock.symbol == symbol)
        ).scalar_one_or_none()
        if not stock:
            continue
        rows = session.execute(
            select(Price)
            .where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1, Price.ts >= cutoff)
            .order_by(Price.ts.asc())
        ).scalars().all()
        if len(rows) < 2:
            continue
        base = rows[0].close
        if not base:
            continue
        result[symbol] = [
            {
                "date": _local_date(r.ts, stock.market.value if hasattr(stock.market, "value") else str(stock.market)),
                "value": round((r.close / base) * 100, 3),
                "close": r.close,
            }
            for r in rows
        ]
    return result


# ── Options Flow ─────────────────────────────────────────────────────────────

_OPTIONS_TTL = 900  # 15-min cache — options volume refreshes intraday

@router.get("/{symbol}/options-flow")
def get_options_flow(symbol: str):
    """Unusual options activity for a symbol, derived from yfinance options chain.

    Fetches the two nearest expiration dates, aggregates call and put volume,
    flags contracts with volume > 30% of open interest (high activity), and
    computes a call/put ratio and a simple sentiment label.

    Returns null fields for HK stocks and others without listed options.
    """
    sym = symbol.upper()
    cache_key = f"options_flow:{sym}"
    try:
        rdb = _get_redis()
        cached = rdb.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    try:
        import yfinance as yf
        import pandas as pd

        t = yf.Ticker(sym)
        expiries = t.options
        if not expiries:
            result = {"symbol": sym, "available": False, "reason": "no_options_listed"}
            return result

        total_call_vol = 0
        total_put_vol = 0
        unusual: list[dict] = []

        for exp in expiries[:4]:  # nearest four expiries
            try:
                chain = t.option_chain(exp)
            except Exception:
                continue

            calls = chain.calls.fillna(0)
            puts  = chain.puts.fillna(0)

            c_vol = int(calls["volume"].sum())
            p_vol = int(puts["volume"].sum())
            total_call_vol += c_vol
            total_put_vol  += p_vol

            # Flag contracts where today's volume exceeds 30% of open interest
            for df, side in [(calls, "call"), (puts, "put")]:
                mask = (df["openInterest"] > 50) & (df["volume"] > df["openInterest"] * 0.30)
                for _, row in df[mask].sort_values("volume", ascending=False).head(3).iterrows():
                    vol = int(row["volume"])
                    last_price = float(row.get("lastPrice", 0))
                    premium = vol * last_price * 100
                    unusual.append({
                        "expiry":    exp,
                        "side":      side,
                        "strike":    float(row["strike"]),
                        "volume":    vol,
                        "oi":        int(row["openInterest"]),
                        "vol_oi":    round(float(row["volume"]) / max(float(row["openInterest"]), 1), 2),
                        "iv":        round(float(row["impliedVolatility"]) * 100, 1),
                        "itm":       bool(row["inTheMoney"]),
                        "premium":   round(premium, 2),
                        "is_whale":  premium > 500_000,
                    })

        if total_call_vol == 0 and total_put_vol == 0:
            result = {"symbol": sym, "available": False, "reason": "no_volume"}
            return result

        # Cap ratio at 10 to prevent unbounded values when put volume is near-zero.
        # Also require at least 100 put contracts before declaring strongly_bullish —
        # zero or tiny put volume usually means illiquid options, not extreme bullishness.
        cp_ratio = round(min(total_call_vol / max(total_put_vol, 1), 10.0), 2)
        sufficient_put_vol = total_put_vol >= 100

        if cp_ratio >= 2.0 and sufficient_put_vol:
            sentiment = "strongly_bullish"
        elif cp_ratio >= 1.3 and sufficient_put_vol:
            sentiment = "bullish"
        elif cp_ratio <= 0.5 and sufficient_put_vol:
            sentiment = "bearish"
        elif cp_ratio <= 0.8 and sufficient_put_vol:
            sentiment = "slightly_bearish"
        else:
            sentiment = "neutral"

        # Sort unusual by premium desc, keep top 10
        unusual.sort(key=lambda x: x["premium"], reverse=True)

        result = {
            "symbol":            sym,
            "available":         True,
            "call_volume":       total_call_vol,
            "put_volume":        total_put_vol,
            "cp_ratio":          cp_ratio,
            "sentiment":         sentiment,
            "unusual_count":     len(unusual),
            "unusual":           unusual[:10],
            "expiries_used":     list(expiries[:4]),
            "whale_count":       sum(1 for c in unusual if c.get("is_whale")),
            "top_whale_premium": max((c["premium"] for c in unusual), default=0),
        }

        try:
            rdb.setex(cache_key, _OPTIONS_TTL, json.dumps(result))
        except Exception:
            pass

        return result

    except Exception as exc:
        log.warning("options_flow.error", symbol=sym, error=str(exc))
        return {"symbol": sym, "available": False, "reason": "fetch_error"}


# ── Per-symbol Dividends ──────────────────────────────────────────────────────

@router.get("/{symbol}/dividends")
def get_dividends(symbol: str):
    """Return dividend history for a symbol from yfinance (3-day Redis cache)."""
    sym = symbol.upper()
    cache_key = f"stockai:dividends:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    try:
        ticker = yf.Ticker(sym)
        divs = ticker.dividends
        if divs is None or divs.empty:
            data = {"symbol": sym, "dividends": [], "annual_div_rate": None, "dividend_yield": None}
        else:
            records = []
            for dt, amt in divs.tail(40).items():
                records.append({"date": dt.strftime("%Y-%m-%d"), "amount": round(float(amt), 4)})
            records.reverse()
            # Estimate annualized rate from last 12 months
            from datetime import date as _ddate, timedelta as _dtd
            cutoff_div = _ddate.today() - _dtd(days=365)
            recent = [r for r in records if r["date"] >= cutoff_div.isoformat()]
            annual_rate = round(sum(r["amount"] for r in recent), 4) if recent else None
            info = ticker.info or {}
            data = {
                "symbol": sym,
                "dividends": records,
                "annual_div_rate": annual_rate,
                "dividend_yield": _safe(info, "dividendYield"),
                "ex_dividend_date": _safe(info, "exDividendDate"),
                "payout_ratio": _safe(info, "payoutRatio"),
            }
        _get_redis().setex(cache_key, 60 * 60 * 72, json.dumps(data))
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch dividends for {sym}: {e}")


# ── Institutional Holdings ────────────────────────────────────────────────────

@router.get("/{symbol}/institutional")
def get_institutional(symbol: str):
    """Return institutional and major holder breakdown (3-day Redis cache)."""
    sym = symbol.upper()
    cache_key = f"stockai:institutional:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    try:
        ticker = yf.Ticker(sym)
        info = ticker.info or {}

        major: dict = {}
        try:
            mh = ticker.major_holders
            if mh is not None and not mh.empty:
                for _, row in mh.iterrows():
                    label = str(row.iloc[1]).strip() if len(row) > 1 else str(row.index)
                    val = row.iloc[0]
                    try:
                        val = float(str(val).replace("%", "").strip()) / 100
                    except Exception:
                        pass
                    major[label] = val
        except Exception:
            pass

        inst_list = []
        try:
            ih = ticker.institutional_holders
            if ih is not None and not ih.empty:
                for _, row in ih.head(20).iterrows():
                    pct = row.get("% Out") or row.get("pctHeld")
                    val = row.get("Value")
                    shrs = row.get("Shares")
                    dr = row.get("Date Reported")
                    inst_list.append({
                        "holder": str(row.get("Holder", "")).strip(),
                        "shares": int(float(shrs)) if shrs and str(shrs) not in ("nan", "None") else None,
                        "date_reported": str(dr)[:10] if dr and str(dr) not in ("nan", "None", "NaT") else None,
                        "pct_out": round(float(pct), 4) if pct and str(pct) not in ("nan", "None") else None,
                        "value": int(float(val)) if val and str(val) not in ("nan", "None") else None,
                    })
        except Exception:
            pass

        data = {
            "symbol": sym,
            "held_pct_institutions": _safe(info, "heldPercentInstitutions"),
            "held_pct_insiders": _safe(info, "heldPercentInsiders"),
            "float_shares": _safe(info, "floatShares"),
            "shares_outstanding": _safe(info, "sharesOutstanding"),
            "major_holders": major,
            "institutional_holders": inst_list,
        }
        _get_redis().setex(cache_key, 60 * 60 * 72, json.dumps(data))
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch institutional data for {sym}: {e}")


@router.get("/conviction")
def conviction_status():
    """Return latest conviction gate check result per symbol:style from Redis."""
    import json as _json
    r = _get_redis()
    keys = r.keys("conv_gate:*")
    result: dict = {}
    for key in keys:
        parts = key.split(":", 2)
        if len(parts) == 3:
            _, sym, style = parts
            raw = r.get(key)
            if raw:
                result[f"{sym}:{style}"] = _json.loads(raw)
    return result


@router.get("/{symbol}/atr")
def stock_atr(
    symbol: str,
    period: int = Query(14, ge=5, le=50),
    session: Session = Depends(get_session),
):
    """Wilder ATR(period) for position sizing. Returns ATR, current close, and 2×ATR stop-loss."""
    import numpy as np

    stock = session.execute(select(Stock).where(Stock.symbol == symbol.upper())).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")

    rows = session.execute(
        select(Price.high, Price.low, Price.close)
        .where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1)
        .order_by(Price.ts.desc())
        .limit(period * 4)
    ).all()
    if len(rows) < period + 2:
        raise HTTPException(422, "Insufficient price history for ATR")

    rows = list(reversed(rows))
    h = [float(r.high)  for r in rows]
    l = [float(r.low)   for r in rows]
    c = [float(r.close) for r in rows]

    # True Range
    tr = [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])) for i in range(1, len(c))]

    # Wilder smoothing: seed with SMA of first `period` TRs, then EMA
    if len(tr) < period:
        raise HTTPException(422, "Insufficient price history for ATR")
    atr = sum(tr[:period]) / period
    for t in tr[period:]:
        atr = (atr * (period - 1) + t) / period

    close_now = c[-1]
    return {
        "symbol": symbol.upper(),
        "atr": round(atr, 4),
        "close": round(close_now, 4),
        "stop_loss_2atr": round(close_now - 2 * atr, 4),
        "period": period,
    }


@router.get("/{symbol}", response_model=StockOut)
def get_stock(symbol: str, session: Session = Depends(get_session)):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    return stock


@router.get("/{symbol}/prices", response_model=list[PriceOut])
def get_prices(
    symbol: str,
    timeframe: str = "1d",
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(1000, le=10000),
    session: Session = Depends(get_session),
):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    try:
        tf = TimeFrame(timeframe)
    except ValueError:
        raise HTTPException(400, f"Invalid timeframe '{timeframe}'. Valid values: {[v.value for v in TimeFrame]}")
    if not end:
        end = date.today()

    stmt = (
        select(Price)
        .where(
            Price.stock_id == stock.id,
            Price.timeframe == tf,
            *(Price.ts >= start,) if start else (),
            Price.ts <= end,
        )
        .order_by(Price.ts.desc())
        .limit(limit)
    )
    rows = list(reversed(list(session.execute(stmt).scalars())))
    return [
        PriceOut(
            ts=_format_ts(r.ts, stock.market, timeframe),
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.volume,
            adj_close=r.adj_close,
        )
        for r in rows
    ]
