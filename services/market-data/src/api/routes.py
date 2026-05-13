"""/stocks, /stocks/{symbol}/prices — read API for market data."""
from datetime import date, datetime, timedelta
import json

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
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
import redis as redis_lib
import yfinance as yf

from common.config import get_settings
from common.logging import get_logger
from db import Price, Stock, TimeFrame, get_session

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
_LIVE_TTL = 60  # seconds


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


def _fetch_live_one(symbol: str, currency: str) -> dict | None:
    """Fetch live quote for one symbol via yfinance fast_info."""
    try:
        fi = yf.Ticker(symbol).fast_info
        price = fi.last_price
        prev_close = getattr(fi, "previous_close", None)
        if price is None:
            return None
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else None
        return {
            "symbol": symbol,
            "price": round(float(price), 4),
            "prev_close": round(float(prev_close), 4) if prev_close else None,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "currency": currency,
        }
    except Exception as exc:
        log.warning("live_price.failed", symbol=symbol, error=str(exc))
        return None


def _latest_prices_from_db(session: Session) -> list[LatestPriceOut]:
    """Fallback: read most recent stored close from Postgres."""
    ranked = (
        select(
            Price.stock_id, Price.close, Price.ts,
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
        select(Stock.symbol, Stock.currency, r1.c.close.label("price"), r2.c.close.label("prev_close"))
        .join(r1, Stock.id == r1.c.stock_id)
        .outerjoin(r2, (Stock.id == r2.c.stock_id) & (r2.c.rn == 2))
        .where(Stock.active.is_(True))
        .where(r1.c.rn == 1)
    )
    result = []
    for symbol, currency, price, prev_close in session.execute(stmt).all():
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else None
        result.append(LatestPriceOut(
            symbol=symbol, price=price, prev_close=prev_close,
            change_pct=round(change_pct, 2) if change_pct is not None else None,
            currency=currency,
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

    spx = yf.download("^GSPC", period="9mo", interval="1d", progress=False, auto_adjust=True)
    vix = yf.download("^VIX",  period="9mo", interval="1d", progress=False, auto_adjust=True)

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
    r20 = float(spx_close.iloc[-1] / spx_close.iloc[-21] - 1) if len(spx_close) > 21 else 0.0
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

    return {
        "score": score,
        "rating": _rating(score),
        "previous_close": _score_at(1),
        "previous_1_week": _score_at(5),
        "previous_1_month": _score_at(21),
        "previous_1_year": _score_at(252),
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


@router.get("/latest_prices", response_model=list[LatestPriceOut])
def latest_prices(session: Session = Depends(get_session)):
    """Live prices from yfinance fast_info, Redis-cached for 60 s; DB fallback."""
    # 1. Try Redis cache
    try:
        cached = _get_redis().get(_LIVE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    # 2. Get active symbols from DB
    stocks = list(session.execute(
        select(Stock.symbol, Stock.currency).where(Stock.active.is_(True))
    ).all())
    if not stocks:
        return []

    # 3. Fetch live quotes in parallel (6 workers, I/O bound)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_fetch_live_one, s.symbol, s.currency): s.symbol for s in stocks}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    if not results:
        log.warning("live_prices.all_failed", count=len(stocks))
        return _latest_prices_from_db(session)

    # 4. Cache in Redis
    try:
        _get_redis().setex(_LIVE_KEY, _LIVE_TTL, json.dumps(results))
    except Exception:
        pass

    log.info("live_prices.ok", count=len(results), source="yfinance")
    return results


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


_FUND_TTL = 60 * 60 * 24  # 24 hours — fundamentals change quarterly


def _safe(info: dict, key: str):
    v = info.get(key)
    if v in (None, "N/A", "None", "", "Infinity", float("inf"), float("-inf")):
        return None
    try:
        return v
    except Exception:
        return None


@router.get("/{symbol}/fundamentals", response_model=FundamentalsOut)
def get_fundamentals(symbol: str, refresh: bool = False):
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
    )

    try:
        _get_redis().setex(cache_key, _FUND_TTL, data.model_dump_json())
    except Exception:
        pass

    log.info("fundamentals.ok", symbol=symbol)
    return data


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
    if not start:
        start = date.today() - timedelta(days=365)
    if not end:
        end = date.today()

    stmt = (
        select(Price)
        .where(
            Price.stock_id == stock.id,
            Price.timeframe == TimeFrame(timeframe),
            Price.ts >= start,
            Price.ts <= end,
        )
        .order_by(Price.ts)
        .limit(limit)
    )
    rows = list(session.execute(stmt).scalars())
    return [
        PriceOut(
            ts=_local_date(r.ts, stock.market),
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.volume,
            adj_close=r.adj_close,
        )
        for r in rows
    ]
