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
from ..services.ingestion import _classify_session

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

_AVG_VOLUME_KEY = "stockai:avg_volume"
_AVG_VOLUME_TTL = 6 * 3600  # 6h — refreshed a few times/day; avg volume barely moves intraday


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
    session: str = "REGULAR"


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
        _avg_volume_cache: dict[str, int] = json.loads(_get_redis().get(_AVG_VOLUME_KEY) or "{}")
    except Exception:
        _avg_volume_cache = {}

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
            except Exception:
                volume = None
            # MD-F11: the 2-day download window above is too short for a meaningful average
            # (needs len(vols) >= 5, never true with period="2d") — read the real multi-week
            # average from the separately-cached, infrequently-refreshed avg-volume table instead
            # of widening this every-1-minute bulk fetch just to compute one slow-moving number.
            avg_volume = _avg_volume_cache.get(symbol)

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


def refresh_avg_volume_cache(stocks: list) -> int:
    """MD-F11: compute a real multi-week average volume per symbol and cache it in Redis.

    Runs far less often than the 1-minute live-price refresh (see _AVG_VOLUME_TTL) since
    average volume barely moves intraday — _fetch_live_bulk reads from this cache instead
    of trying to compute an average from its own short 2-day download window.
    Returns the number of symbols successfully cached.
    """
    if not stocks:
        return 0
    symbols = [s.symbol for s in stocks]
    try:
        raw = yf.download(
            symbols,
            period="1mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
        )
    except Exception as exc:
        log.warning("avg_volume.bulk_download_failed", error=str(exc))
        return 0

    if raw is None or raw.empty:
        return 0

    cache: dict[str, int] = {}
    for symbol in symbols:
        try:
            sym_data = raw[symbol] if len(symbols) > 1 else raw
            vols = sym_data["Volume"].dropna() if "Volume" in sym_data.columns else pd.Series(dtype=float)
            if len(vols) >= 5:
                cache[symbol] = int(float(vols.mean()))
        except Exception:
            continue

    if cache:
        try:
            _get_redis().setex(_AVG_VOLUME_KEY, _AVG_VOLUME_TTL, json.dumps(cache))
        except Exception:
            pass
    log.info("avg_volume.cache_refresh", count=len(cache))
    return len(cache)


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
            r = float(spx_close.iloc[i] / spx_close.iloc[i - 20] - 1) if abs(i - 20) <= len(spx_close) else 0.0
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


@router.get("/regime")
def regime(market: str = Query("US", description="US or HK")):
    """Current market regime — the single canonical classifier used to gate paper trading entries.

    T232-DL-REGIME5X: this is paper_trading_engine's own _fetch_market_regime()/
    _fetch_hk_market_regime() output, exposed over HTTP so other services (decision-engine,
    signal-engine) can call the SAME classifier instead of maintaining independent copies that
    drift apart. Unauthenticated — read-only, no sensitive data, same pattern as /fear_greed.

    Returns the cached value from the most recent paper trading cycle (fresh within one scan
    interval); performs a lazy fetch if the cache is empty (e.g. right after a container restart).
    """
    from ..services.paper_trading_engine import get_last_regime, get_last_hk_regime
    try:
        if market.upper() == "HK":
            return get_last_hk_regime()
        return get_last_regime()
    except Exception as exc:
        log.warning("regime.fetch_failed", market=market, error=str(exc))
        raise HTTPException(503, "Regime data unavailable")


@router.get("/regime-state")
def hmm_regime_state():
    """Current 4-state HMM regime classification (T211/T232-ML7/T233-ARCH-HMMREGIME).

    Uses a GaussianHMM trained on standardized (VIX_level, SPY_5d_return, IWM_vs_EMA200).
    States: bull | neutral | choppy | bear, labeled by a composite (return + VIX) rank.
    Model auto-refreshes when older than 7 days; falls back to the existing pickle if a
    refresh fails. Returns {"error": ...} if hmmlearn is not installed or data fetch fails.
    No auth required — public endpoint, advisory data only (same pattern as /fear_greed).

    T233-ARCH-HMMREGIME: moved here from ml-prediction 2026-07-04 — paper_trading_engine
    was the only consumer anywhere in the codebase and called this over HTTP on every
    regime computation; colocating eliminates that network hop entirely.
    """
    from ..services.hmm_regime import predict_current
    return predict_current()


@router.post("/regime-refit")
def hmm_regime_refit(_user=Depends(get_current_user)):
    """Force-refit the HMM regime model. Requires auth."""
    from ..services.hmm_regime import refit
    result = refit()
    if "error" in result:
        raise HTTPException(503, result["error"])
    return result


@router.get("/style-params")
def style_params():
    """Canonical per-style game-plan parameters (entry/breakout/stop/target percentages).

    T232-DL-STYLEPARAMS3X: this dict was previously triplicated (scheduler.py, inlined again
    in paper_trading_engine.py, and re-invented a third time in decision-engine's aggregator.py
    with WRONG values for GROWTH and two dead styles — SCALP/INCOME — that don't exist in the
    real trading engine). Only 4 real styles exist: SHORT, SWING, LONG, GROWTH.

    Reads paper_trading_engine's live in-memory _STYLE_PARAMS, which _load_tuned_params()
    overwrites with Optuna-tuned stop_pct/default_tp_pct values when available — so this
    endpoint reflects the ACTUAL values currently in effect, not a static snapshot.
    Unauthenticated — read-only, no sensitive data.
    """
    from ..services.paper_trading_engine import _STYLE_PARAMS
    return _STYLE_PARAMS


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
    ex_dividend_date: str | None = None   # YYYY-MM-DD, from yfinance exDividendDate (unix ts → date)
    # Valuation ratios (Phase 1 additions)
    peg_ratio: float | None = None        # PE / forward earnings growth (yfinance pegRatio)
    debt_to_equity: float | None = None   # total debt / total equity (yfinance debtToEquity)
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
    shares_short_prior_month: int | None = None  # prior month short interest (yfinance sharesShortPriorMonth)
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


def _parse_ex_div_date(raw) -> str | None:
    """Convert yfinance exDividendDate (unix timestamp int) to YYYY-MM-DD string."""
    if raw is None:
        return None
    try:
        from datetime import date as _d, datetime as _dt
        if isinstance(raw, (int, float)):
            return _dt.utcfromtimestamp(raw).date().isoformat()
        return str(raw)[:10]  # already a string
    except Exception:
        return None


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
        "peg_ratio", "debt_to_equity",
        "held_percent_institutions", "held_percent_insiders",
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
        ex_dividend_date=_parse_ex_div_date(_safe(info, "exDividendDate")),
        peg_ratio=_safe(info, "pegRatio"),
        debt_to_equity=_safe(info, "debtToEquity"),
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
        shares_short_prior_month=_safe(info, "sharesShortPriorMonth"),
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

    # AUD-MD-FUNDAMENTALS-EMPTY-OVERWRITE: a transient yfinance failure (rate-limit, timeout,
    # empty response) makes ticker.info == {} — every _safe(info, ...) call above then returns
    # None, producing a `data` that's entirely null fields but still gets treated as a normal
    # successful response: cached for 24h AND upserted into the DB, silently overwriting
    # yesterday's real values (confirmed happening in production 2026-07-16: AAPL/MU's
    # fundamentals row went from real values to 100% NULL after one bad nightly batch run,
    # blanking the stock detail page's Company Financials section and P/E/EV/Beta cards for
    # every symbol until the next successful refresh). marketCap/trailingPE/totalRevenue are
    # present on essentially every real yfinance response, even for thinly-covered stocks —
    # their combined absence is a reliable signal the fetch itself failed, not that this
    # particular stock genuinely has none of the three.
    # AUD-FUNDAMENTALS-ETF-FALSEPOSITIVE: ETFs (GLD, SPY, sector ETFs) legitimately have none
    # of market_cap/trailing_pe/total_revenue on a genuinely SUCCESSFUL yfinance fetch — they
    # report totalAssets/fundFamily instead, since those three fields are equity-specific
    # concepts. Without this carve-out, the guard above tripped on every real ETF fetch,
    # never caching or persisting fundamentals for any ETF and re-hitting yfinance on every
    # request with zero cache protection. quoteType=="ETF" (or the presence of totalAssets,
    # a field ONLY yfinance populates for a real successful fund-type response) distinguishes
    # a genuinely-sparse-but-successful ETF fetch from an actually-failed one.
    _is_fund_type = info.get("quoteType") in ("ETF", "MUTUALFUND") or info.get("totalAssets") is not None
    fetch_looks_empty = (
        not _is_fund_type
        and data.market_cap is None and data.trailing_pe is None and data.total_revenue is None
    )
    if fetch_looks_empty:
        log.warning("fundamentals.empty_fetch_skip_write", symbol=symbol)
        try:
            stale = _get_redis().get(cache_key)
            if stale:
                return json.loads(stale)
        except Exception:
            pass
        return data

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
                peg_ratio=data.peg_ratio,
                debt_to_equity=data.debt_to_equity,
                dividend_yield=data.dividend_yield,
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
                    peg_ratio=data.peg_ratio,
                    debt_to_equity=data.debt_to_equity,
                    dividend_yield=data.dividend_yield,
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


_QUARTERLY_TTL = 86_400  # 24 hours


@router.get("/{symbol}/quarterly")
def get_quarterly_financials(symbol: str):
    """Last 8 quarters of income statement data from yfinance, Redis-cached for 24 h."""
    cache_key = f"stockai:quarterly:{symbol.upper()}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    result: list[dict] = []
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.quarterly_income_stmt
        if df is not None and not df.empty:
            # Columns are dates (newest first), rows are line items
            import math as _math
            cols = list(df.columns)[:8]  # last 8 quarters, newest first

            def _val(df_, col_, row_name: str):
                try:
                    v = df_.loc[row_name, col_] if row_name in df_.index else None
                    if v is None:
                        return None
                    if isinstance(v, float) and _math.isnan(v):
                        return None
                    return int(v)
                except Exception:
                    return None

            for col in cols:
                date_str = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)[:10]
                result.append({
                    "date": date_str,
                    "revenue": _val(df, col, "Total Revenue"),
                    "gross_profit": _val(df, col, "Gross Profit"),
                    "net_income": _val(df, col, "Net Income"),
                    "ebitda": _val(df, col, "EBITDA"),
                })
    except Exception as exc:
        log.warning("quarterly_financials.fetch_failed", symbol=symbol, error=str(exc))

    try:
        _get_redis().setex(cache_key, _QUARTERLY_TTL, json.dumps(result))
    except Exception:
        pass

    return result


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


# ── 2026 macro event calendar (pre-announced schedules) ───────────────────────
# Sources: FOMC=federalreserve.gov; CPI/NFP/PCE=bls.gov/bea.gov
#
# T249-MARKETMOVER-P0: this hand-maintained list is fragile and has already had one wrong
# date (July 2026 CPI, off by a day — caught and fixed 2026-07-14). FOMC meeting dates stay
# hardcoded here since FRED has no release calendar for Fed meetings (they're announced by
# the Fed itself, not published as a data release). The CPI/PPI/NFP/GDP/PCE entries below are
# now a FALLBACK ONLY — events_calendar() prefers the real, live release-date rows synced by
# economic.py's sync_fred_release_dates() (event-intelligence, sourced from FRED's own
# fred/release/dates endpoint) via _macro_events_from_db() below, and only falls back to these
# hardcoded entries for a given (type, date-range) if the DB has no rows yet — e.g. right after
# this fix ships, before the first sync_fred_release_dates() run has populated the table, or if
# FRED_API_KEY is ever unset again. Once the DB sync is confirmed reliably populated going
# forward, these hardcoded entries can be deleted outright rather than kept as a fallback.
_MACRO_2026: list[dict] = [
    # FOMC decisions (second day of each meeting)
    {"type": "fomc", "date": "2026-01-29", "title": "FOMC Rate Decision", "description": "Federal Reserve interest rate decision — Jan meeting", "impact": "high"},
    {"type": "fomc", "date": "2026-03-18", "title": "FOMC Rate Decision", "description": "Federal Reserve interest rate decision — Mar meeting", "impact": "high"},
    {"type": "fomc", "date": "2026-05-07", "title": "FOMC Rate Decision", "description": "Federal Reserve interest rate decision — May meeting", "impact": "high"},
    {"type": "fomc", "date": "2026-06-18", "title": "FOMC Rate Decision", "description": "Federal Reserve interest rate decision — Jun meeting", "impact": "high"},
    {"type": "fomc", "date": "2026-07-30", "title": "FOMC Rate Decision", "description": "Federal Reserve interest rate decision — Jul meeting", "impact": "high"},
    {"type": "fomc", "date": "2026-09-17", "title": "FOMC Rate Decision", "description": "Federal Reserve interest rate decision — Sep meeting", "impact": "high"},
    {"type": "fomc", "date": "2026-10-29", "title": "FOMC Rate Decision", "description": "Federal Reserve interest rate decision — Oct meeting", "impact": "high"},
    {"type": "fomc", "date": "2026-12-10", "title": "FOMC Rate Decision", "description": "Federal Reserve interest rate decision — Dec meeting", "impact": "high"},
    # CPI releases (BLS, ~2nd week of month for prior month)
    {"type": "cpi", "date": "2026-01-15", "title": "CPI Release", "description": "Consumer Price Index — Dec 2025 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-02-12", "title": "CPI Release", "description": "Consumer Price Index — Jan 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-03-12", "title": "CPI Release", "description": "Consumer Price Index — Feb 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-04-10", "title": "CPI Release", "description": "Consumer Price Index — Mar 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-05-14", "title": "CPI Release", "description": "Consumer Price Index — Apr 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-06-11", "title": "CPI Release", "description": "Consumer Price Index — May 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-07-14", "title": "CPI Release", "description": "Consumer Price Index — Jun 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-08-13", "title": "CPI Release", "description": "Consumer Price Index — Jul 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-09-11", "title": "CPI Release", "description": "Consumer Price Index — Aug 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-10-14", "title": "CPI Release", "description": "Consumer Price Index — Sep 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-11-13", "title": "CPI Release", "description": "Consumer Price Index — Oct 2026 data (BLS)", "impact": "high"},
    {"type": "cpi", "date": "2026-12-11", "title": "CPI Release", "description": "Consumer Price Index — Nov 2026 data (BLS)", "impact": "high"},
    # NFP — Non-Farm Payrolls (BLS, first Friday of month)
    {"type": "nfp", "date": "2026-01-09", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Dec 2025 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-02-06", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Jan 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-03-06", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Feb 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-04-03", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Mar 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-05-08", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Apr 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-06-05", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — May 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-07-02", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Jun 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-08-07", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Jul 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-09-04", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Aug 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-10-02", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Sep 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-11-06", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Oct 2026 data (BLS)", "impact": "high"},
    {"type": "nfp", "date": "2026-12-04", "title": "Jobs Report (NFP)", "description": "Non-Farm Payrolls — Nov 2026 data (BLS)", "impact": "high"},
    # PCE — Personal Consumption Expenditures (BEA, ~last Friday of month)
    {"type": "pce", "date": "2026-01-30", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Nov 2025 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-02-27", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Dec 2025 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-03-27", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Jan 2026 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-04-30", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Feb 2026 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-05-29", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Mar 2026 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-06-26", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Apr 2026 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-07-31", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — May 2026 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-08-28", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Jun 2026 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-09-25", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Jul 2026 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-10-30", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Aug 2026 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-11-25", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Sep 2026 data (BEA)", "impact": "high"},
    {"type": "pce", "date": "2026-12-18", "title": "PCE Inflation", "description": "Personal Consumption Expenditures — Oct 2026 data (BEA)", "impact": "high"},
    # GDP advance estimates (BEA, ~4 weeks after quarter end)
    {"type": "gdp", "date": "2026-01-29", "title": "GDP Advance Estimate", "description": "Q4 2025 GDP advance (BEA)", "impact": "medium"},
    {"type": "gdp", "date": "2026-04-30", "title": "GDP Advance Estimate", "description": "Q1 2026 GDP advance (BEA)", "impact": "medium"},
    {"type": "gdp", "date": "2026-07-30", "title": "GDP Advance Estimate", "description": "Q2 2026 GDP advance (BEA)", "impact": "medium"},
    {"type": "gdp", "date": "2026-10-29", "title": "GDP Advance Estimate", "description": "Q3 2026 GDP advance (BEA)", "impact": "medium"},
]


# T249-MARKETMOVER-P0: (event_type in _MACRO_2026's hardcoded "type" field) -> the real
# {event_type}_release rows economic.py's sync_fred_release_dates() writes. Used to know which
# hardcoded "type" values now have a live DB equivalent to prefer.
_MACRO_TYPE_TO_RELEASE_EVENT_TYPE = {
    "cpi": "cpi_release",
    "nfp": "nfp_release",
    "pce": "pce_release",
    "gdp": "gdp_release",
}


def _macro_events_from_db(session: "Session", today, cutoff) -> tuple[list[dict], set[tuple[str, int, int]]]:
    """T249-MARKETMOVER-P0: read the real release-date calendar from economic_events'
    *_release rows (synced from FRED's own fred/release/dates endpoint) for the hardcoded
    macro types that now have a live equivalent. Returns (events, covered_type_months) — the
    caller uses covered_type_months to decide which _MACRO_2026 entries to skip as redundant/
    stale, falling back to the hardcoded list only for a (type, year, month) the DB has no row
    for yet.

    AUD250-MACRO-CALENDAR-FALLBACK-GRANULARITY: this was originally a per-type set[str] —
    if the DB had even ONE row for a type anywhere in [today, cutoff], every _MACRO_2026
    fallback entry for that type was skipped across the ENTIRE requested window, including
    date ranges the DB sync never actually reached. sync_fred_release_dates() only syncs 180
    days ahead by default; GET /stocks/events/calendar?days_ahead=365 is a valid request (up
    to 365 per the route's own Query bound) — a caller requesting >180 days ahead could see a
    real near-term DB row silently suppress fallback coverage for months 181-365 that the DB
    genuinely has no data for. Tracking per-(type, year, month) instead of per-type scopes the
    skip to only the specific months the DB actually returned a row for — a gap in coverage
    for a later month now correctly falls back to the hardcoded entry for that month instead
    of being silently dropped.

    Shape matches the fallback _MACRO_2026 path exactly (type/date/title/description/impact
    plus the same days_to_event/symbol/name/market/sector fields events_calendar() adds to
    every macro event below) so callers see one consistent event shape regardless of source.
    """
    from db import EconomicEvent as _EconomicEvent

    # AUD-PREMARKET-DATECUTOFF: event_date is a DateTime column (rows land at e.g.
    # 08:30 UTC on release day, not midnight). Comparing it against a bare `date` makes
    # Postgres coerce cutoff to midnight, silently excluding every same-day row with a
    # nonzero time-of-day — invisible for callers passing a multi-day-ahead cutoff
    # (events_calendar()'s default 90-day window), but fatal for a same-day cutoff==today
    # call (send_premarket_brief()), where it excluded literally every release. Widen the
    # upper bound to end-of-day so a bare `date` cutoff still includes the whole day.
    cutoff_end_of_day = datetime.combine(cutoff, datetime.max.time())
    release_event_types = list(_MACRO_TYPE_TO_RELEASE_EVENT_TYPE.values())
    rows = session.execute(
        select(_EconomicEvent).where(
            _EconomicEvent.event_type.in_(release_event_types),
            _EconomicEvent.event_date >= today,
            _EconomicEvent.event_date <= cutoff_end_of_day,
        )
    ).scalars().all()

    events: list[dict] = []
    covered_type_months: set[tuple[str, int, int]] = set()
    for row in rows:
        macro_type = next(
            (k for k, v in _MACRO_TYPE_TO_RELEASE_EVENT_TYPE.items() if v == row.event_type),
            row.event_type,
        )
        ev_date = row.event_date.date()
        covered_type_months.add((macro_type, ev_date.year, ev_date.month))
        events.append({
            "type": macro_type,
            "date": ev_date.isoformat(),
            "title": row.title,
            "description": f"{row.title} (FRED release calendar)",
            "impact": row.importance or "medium",
            "days_to_event": (ev_date - today).days,
            "symbol": None,
            "name": None,
            "market": None,
            "sector": None,
        })
    return events, covered_type_months


@router.get("/events/calendar")
def events_calendar(
    days_ahead: int = Query(90, ge=1, le=365),
    session: Session = Depends(get_session),
):
    """Return all upcoming events: earnings, ex-dividends, and macro events (FOMC, CPI, NFP, PCE, GDP)."""
    from datetime import date as _date
    today = _date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []

    # ── Macro events ─────────────────────────────────────────────────────────
    # T249-MARKETMOVER-P0: prefer the real, live release-date rows from the DB; only fall
    # back to the hardcoded _MACRO_2026 list for a (type, date) the DB doesn't have a row for
    # yet (e.g. before the first successful sync_fred_release_dates() run, or if
    # FRED_API_KEY is ever unset again). FOMC has no DB equivalent (FRED doesn't publish a
    # release calendar for Fed meetings) so it always comes from _MACRO_2026.
    #
    # AUD250-MACRO-CALENDAR-FALLBACK-GRANULARITY: the skip check below is scoped per
    # (type, year, month) rather than per-type — see _macro_events_from_db()'s own docstring
    # for why a per-type check silently dropped fallback coverage for months the DB sync
    # never actually reached (sync_fred_release_dates() only syncs 180 days ahead; this route
    # allows days_ahead up to 365).
    db_macro_events, _covered_type_months = _macro_events_from_db(session, today, cutoff)
    events.extend(db_macro_events)

    for ev in _MACRO_2026:
        try:
            ev_date = _date.fromisoformat(ev["date"])
        except Exception:
            continue
        if (ev["type"], ev_date.year, ev_date.month) in _covered_type_months:
            continue  # real DB row already covers this specific type+month
        if today <= ev_date <= cutoff:
            events.append({
                **ev,
                "days_to_event": (ev_date - today).days,
                "symbol": None,
                "name": None,
                "market": None,
                "sector": None,
            })

    # ── Stock events: earnings + ex-dividends ─────────────────────────────────
    r = _get_redis()
    stocks = session.execute(select(Stock).where(Stock.active.is_(True))).scalars().all()

    for stock in stocks:
        mkt = stock.market.value if hasattr(stock.market, "value") else str(stock.market)
        cache_key = f"stockai:fundamentals:v2:{stock.symbol}"
        try:
            cached = r.get(cache_key)
            if not cached:
                continue
            data = json.loads(cached)

            # Earnings
            ned = data.get("next_earnings_date")
            if ned:
                try:
                    ned_date = _date.fromisoformat(ned)
                    if today <= ned_date <= cutoff:
                        events.append({
                            "type": "earnings",
                            "date": ned,
                            "days_to_event": (ned_date - today).days,
                            "title": f"{stock.symbol} Earnings",
                            "description": stock.name,
                            "impact": "high",
                            "symbol": stock.symbol,
                            "name": stock.name,
                            "sector": stock.sector,
                            "market": mkt,
                            "eps_estimate": data.get("forward_eps"),
                            "trailing_eps": data.get("trailing_eps"),
                            "revenue_growth": data.get("revenue_growth"),
                            "earnings_growth": data.get("earnings_growth"),
                            "market_cap": data.get("market_cap"),
                        })
                except Exception:
                    pass

            # Ex-dividend
            ex_div = data.get("ex_dividend_date")
            if ex_div:
                try:
                    ex_date = _date.fromisoformat(str(ex_div)[:10])
                    if today <= ex_date <= cutoff:
                        events.append({
                            "type": "dividend",
                            "date": ex_div[:10],
                            "days_to_event": (ex_date - today).days,
                            "title": f"{stock.symbol} Ex-Dividend",
                            "description": stock.name,
                            "impact": "medium",
                            "symbol": stock.symbol,
                            "name": stock.name,
                            "sector": stock.sector,
                            "market": mkt,
                            "dividend_rate": data.get("dividend_rate"),
                            "dividend_yield": data.get("dividend_yield"),
                        })
                except Exception:
                    pass
        except Exception:
            continue

    events.sort(key=lambda x: (x["days_to_event"], x["type"]))
    return events


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


# ── Short Interest Dashboard ──────────────────────────────────────────────────

@router.get("/short-interest")
def short_interest(
    _user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Return stocks sorted by short percent of float (from fundamentals table)."""
    from sqlalchemy import text as _text
    rows = session.execute(_text("""
        SELECT st.symbol, st.name, st.market,
               f.short_percent_of_float, f.short_ratio, f.market_cap
        FROM stocks st
        JOIN (
            SELECT DISTINCT ON (stock_id) stock_id,
                   short_percent_of_float, short_ratio, market_cap
            FROM fundamentals
            WHERE short_percent_of_float IS NOT NULL
            ORDER BY stock_id, as_of DESC
        ) f ON f.stock_id = st.id
        WHERE st.active = TRUE
        ORDER BY f.short_percent_of_float DESC
        LIMIT 200
    """)).fetchall()
    return [
        {
            "symbol": r.symbol,
            "name": r.name,
            "market": r.market if isinstance(r.market, str) else r.market.value,
            "short_percent_of_float": float(r.short_percent_of_float) * 100 if r.short_percent_of_float is not None else None,
            "short_ratio": float(r.short_ratio) if r.short_ratio is not None else None,
            "market_cap": int(r.market_cap) if r.market_cap is not None else None,
        }
        for r in rows
    ]


# ── T220-G: Sector K-Score Rotation ──────────────────────────────────────────

@router.get("/stocks/sector-rotation")
def get_sector_rotation():
    """Return current sector K-Score momentum (computed Sunday, cached in Redis).

    Returns {sector_name: {momentum: +1/0/-1, recent_kscore, prior_kscore, delta}}
    where momentum=+1 means sector K-Score rose >3 pts vs 4 weeks ago (institutional
    tailwind), -1 means fell >3 pts (headwind), 0 means flat.
    """
    import json as _json
    r = _get_redis()
    raw = r.get("stockai:sector_rotation")
    if not raw:
        return {}
    try:
        return _json.loads(raw)
    except Exception:
        return {}


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
                "shares_short_prior_month": data.get("shares_short_prior_month"),
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


# ── Per-symbol Relative Strength ─────────────────────────────────────────────

_SECTOR_ETF_MAP: dict[str, str] = {
    "Technology": "XLK", "Health Care": "XLV", "Healthcare": "XLV",
    "Financials": "XLF", "Financial Services": "XLF",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP",
    "Energy": "XLE", "Utilities": "XLU", "Materials": "XLB",
    "Industrials": "XLI", "Real Estate": "XLRE",
    "Communication Services": "XLC", "Telecommunications": "XLC",
}
_RS_TTL    = 3600      # 1h — refreshed each signal generation cycle
_ETF_TTL   = 4 * 3600  # 4h — ETF data changes slowly


@router.get("/{symbol}/relative-strength")
def get_relative_strength(symbol: str, db: Session = Depends(get_session)):
    """Return RS score vs sector ETF for a symbol.

    Uses DB prices for the stock (20-day return) and yfinance for the sector ETF
    (cached 4h in Redis so only one yfinance call per ETF ticker per session).
    Full result cached 1h per symbol. Single source of truth for all signal consumers.
    """
    sym = symbol.upper()
    rs_key = f"stockai:rs:{sym}"
    try:
        cached = _get_redis().get(rs_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    stock = db.execute(select(Stock).where(Stock.symbol == sym)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {sym}")

    # Stock 20-day return from DB prices (no yfinance call needed)
    prices = db.execute(
        select(Price.close, Price.ts)
        .where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1)
        .order_by(Price.ts.desc())
        .limit(21)
    ).all()
    if len(prices) < 21:
        return {"symbol": sym, "rs_score": None, "rs_rank": None,
                "sector_etf_above_sma50": None, "stock_20d_return_pct": None, "etf_ticker": None}

    prices_sorted = sorted(prices, key=lambda r: r.ts)
    stock_ret = float(prices_sorted[-1].close / prices_sorted[0].close - 1)

    # Sector ETF — cached per ticker to avoid repeated yfinance calls across symbols
    market = str(stock.market).upper() if stock.market else "US"
    sector = stock.sector or ""
    etf_ticker = "^HSI" if market == "HK" else _SECTOR_ETF_MAP.get(sector, "SPY")

    etf_key = f"stockai:etf_rs:{etf_ticker}"
    etf_data: dict | None = None
    try:
        cached_etf = _get_redis().get(etf_key)
        if cached_etf:
            etf_data = json.loads(cached_etf)
    except Exception:
        pass

    if etf_data is None:
        try:
            import numpy as np
            hist = yf.Ticker(etf_ticker).history(period="3mo")
            if len(hist) >= 50:
                etf_ret_val   = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-21] - 1)
                etf_sma50_val = float(hist["Close"].rolling(50).mean().iloc[-1])
                etf_above     = bool(hist["Close"].iloc[-1] > etf_sma50_val)
                etf_data = {"ret": etf_ret_val, "above_sma50": etf_above}
                try:
                    _get_redis().setex(etf_key, _ETF_TTL, json.dumps(etf_data))
                except Exception:
                    pass
        except Exception:
            pass

    if etf_data is None or abs(1 + etf_data.get("ret", 0)) < 0.01:
        return {"symbol": sym, "rs_score": None, "rs_rank": None,
                "sector_etf_above_sma50": None, "stock_20d_return_pct": None, "etf_ticker": etf_ticker}

    import numpy as np
    etf_ret  = etf_data["ret"]
    # AUD232-065: ranking-engine's independent RS implementation (_rs_score in
    # ranking-engine/src/api/routes.py) received the T234-RANK-RS-UNBOUNDED fix that this,
    # the docstring-declared "single source of truth", never did — a tighter 1e-6 near-zero
    # denominator floor (the pre-check above uses a looser <0.01 threshold that doesn't catch
    # etf_ret exactly at -0.99) and an explicit rs_rank clip to [-20, 20] (previously only
    # rs_score was clipped; rs_rank itself was returned completely unbounded and could reach
    # 100+ during a real sector-ETF crash). Ported both fixes here so the two implementations
    # no longer diverge on this edge case.
    denom    = 1 + etf_ret if abs(etf_ret + 1) > 1e-6 else 1e-6
    rs_rank  = (1 + stock_ret) / denom
    rs_score = float(np.clip(50 + (rs_rank - 1.0) * 100, 0, 100))
    rs_rank  = float(np.clip(rs_rank, -20.0, 20.0))
    result   = {
        "symbol":                sym,
        "rs_score":              round(rs_score, 1),
        "rs_rank":               round(rs_rank, 4),
        "sector_etf_above_sma50": etf_data["above_sma50"],
        "stock_20d_return_pct":  round(stock_ret * 100, 2),
        "etf_ticker":            etf_ticker,
    }
    try:
        _get_redis().setex(rs_key, _RS_TTL, json.dumps(result))
    except Exception:
        pass
    return result


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
        raise HTTPException(status_code=502, detail=f"Failed to fetch dividends for {sym}")


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
        raise HTTPException(status_code=502, detail=f"Failed to fetch institutional data for {sym}")


@router.get("/conviction")
def conviction_status():
    """Return latest conviction gate check result per symbol:style from Redis."""
    import json as _json
    r = _get_redis()
    keys = list(r.scan_iter("conv_gate:*"))
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


@router.get("/hk-connect-flow/{symbol}")
def hk_connect_flow(
    symbol: str,
    days: int = Query(20, ge=1, le=90),
    session: Session = Depends(get_session),
):
    """T209: Return HKEX Stock Connect southbound flow summary for a HK stock.

    Intentionally public (no auth) — signal-engine calls this without a JWT.
    Returns {} when no flow data is available (e.g. non-HK symbol, not yet ingested).

    Keys:
      flow_5d_net_hkd  — rolling 5-day net buy sum in HKD millions (positive = net buying)
      flow_20d_net_hkd — rolling 20-day net buy sum in HKD millions
      flow_strength    — 5-day avg vs 20-day avg; >1.0 = southbound flow accelerating
    """
    from ..services.hk_connect import get_flow_summary
    return get_flow_summary(session, symbol.upper(), days=days)


@router.get("/{symbol}/rvol")
def get_rvol(symbol: str, session: Session = Depends(get_session)):
    """Time-of-day-adjusted relative volume: today's cumulative volume-so-far vs the average
    cumulative volume other recent trading days had reached by this SAME point in their own
    session, using real 5-minute intraday bars (Price, timeframe=M5).

    Returns {"symbol": str, "rvol": float | None, "today_volume": int, "avg_volume": float,
    "minutes_since_open": int | None}. RVOL > 2.0 = unusual activity for this point in the day.

    T241-AUDIT-RVOL-INTRADAY-BIAS (fixed 2026-07-10, found via a Fable 5 audit): this endpoint
    previously queried a table (`prices_5m`) that has never existed in this schema — it 500'd
    on every call and had zero real callers anywhere in the frontend (all RVOL display is
    computed client-side from stockai:live_prices/avg_volume, comparing full-day cumulative
    volume against a full-day average with no time-of-day adjustment — a real source of false
    "quiet"/"surging" reads early in the trading session, per the same audit). Rewritten
    against the real `prices` table (Price model, keyed by stock_id + timeframe, not a raw
    `symbol` column) with a genuinely time-of-day-aware comparison: "minutes since THIS
    market's own open" rather than raw UTC hour-of-day, so an HK stock queried from a
    US-timezone server context still compares against the correct point in HK's own session.
    """
    from zoneinfo import ZoneInfo

    from db import Market as _Market

    stock = session.execute(select(Stock).where(Stock.symbol == symbol.upper())).scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    market_tz = ZoneInfo("Asia/Hong_Kong") if stock.market == _Market.HK else ZoneInfo("America/New_York")
    now_local = datetime.now(market_tz)
    today_local = now_local.date()
    minutes_since_midnight = now_local.hour * 60 + now_local.minute

    bars = session.execute(
        select(Price.ts, Price.volume)
        .where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.M5)
        .order_by(Price.ts.asc())
    ).all()
    if not bars:
        return {"symbol": stock.symbol, "rvol": None, "today_volume": 0, "avg_volume": 0.0, "minutes_since_open": None}

    # Price.ts is stored naive-UTC (per the shared schema) — localize to this stock's own
    # market timezone before comparing calendar dates or minutes-of-day.
    by_local_date: dict[date, list[tuple[int, float]]] = {}
    for ts, vol in bars:
        local_ts = ts.replace(tzinfo=timezone.utc).astimezone(market_tz)
        minutes = local_ts.hour * 60 + local_ts.minute
        by_local_date.setdefault(local_ts.date(), []).append((minutes, float(vol or 0)))

    today_bars = by_local_date.get(today_local, [])
    today_vol = sum(v for m, v in today_bars if m <= minutes_since_midnight)
    if today_vol == 0:
        return {"symbol": stock.symbol, "rvol": None, "today_volume": 0, "avg_volume": 0.0, "minutes_since_open": minutes_since_midnight}

    # Same cumulative-by-this-time-of-day comparison across the last 20 PRIOR trading days
    # that have any bars at all (skips weekends/holidays automatically — no bars exist for
    # non-trading days) rather than the last 20 calendar days.
    prior_dates = sorted((d for d in by_local_date if d < today_local), reverse=True)[:20]
    daily_cumulative: list[float] = []
    for d in prior_dates:
        day_total = sum(v for m, v in by_local_date[d] if m <= minutes_since_midnight)
        if day_total > 0:
            daily_cumulative.append(day_total)

    avg_vol = sum(daily_cumulative) / len(daily_cumulative) if daily_cumulative else 0.0
    rvol = round(today_vol / avg_vol, 2) if avg_vol > 0 else None

    return {
        "symbol": stock.symbol,
        "rvol": rvol,
        "today_volume": int(today_vol),
        "avg_volume": round(avg_vol, 0),
        "minutes_since_open": minutes_since_midnight,
    }


@router.get("/signal-outcomes/summary")
def get_signal_outcomes_summary(days: int = 30, session: Session = Depends(get_session)):
    """T225-D: Win rate + avg return by (market, style, direction) for the last N days.

    Gives permanent operational visibility into signal quality without SQL access.
    Returns list of {market, horizon, signal_direction, n, win_pct, avg_return,
    avg_confidence, avg_ta_score, avg_ml_prob}.
    """
    from sqlalchemy import text as _text
    rows = session.execute(_text("""
        SELECT
            st.market,
            so.horizon,
            so.signal_direction,
            COUNT(*) AS n,
            ROUND(AVG(CASE WHEN so.is_correct THEN 1.0 ELSE 0 END) * 100, 1) AS win_pct,
            ROUND(AVG(so.pct_return)::numeric, 3) AS avg_return,
            ROUND(AVG(so.confidence)::numeric, 1) AS avg_confidence,
            ROUND(AVG(so.ta_score)::numeric, 3) AS avg_ta_score,
            ROUND(AVG(so.ml_prob)::numeric, 3) AS avg_ml_prob
        FROM signal_outcomes so
        JOIN stocks st ON so.stock_id = st.id
        WHERE so.ts_evaluated >= NOW() - CAST(:days || ' days' AS INTERVAL)
        GROUP BY st.market, so.horizon, so.signal_direction
        ORDER BY st.market, so.horizon, so.signal_direction
    """), {"days": days}).fetchall()
    return [dict(r._mapping) for r in rows]


@router.get("/{symbol}", response_model=StockOut)
def get_stock(symbol: str, session: Session = Depends(get_session)):
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Unknown symbol: {symbol}")
    return stock


class PriceTfOut(BaseModel):
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: str = "REGULAR"


@router.get("/{symbol}/prices_tf", response_model=list[PriceTfOut])
def get_prices_tf(
    symbol: str,
    tf: str = Query("1d", regex="^(15m|1h|4h|1d)$"),
):
    """Return OHLCV bars for the requested timeframe, computed on-demand via yfinance.

    Supported timeframes:
      15m  — last 5 days,  15-minute bars
      1h   — last 60 days, 1-hour bars
      4h   — last 120 days, 60-minute bars resampled to 4-hour
      1d   — handled by frontend using existing daily prices (returns empty list here)

    Results are cached in Redis for 10 minutes.
    """
    if tf == "1d":
        return []

    cache_key = f"stockai:prices_tf:{symbol.upper()}:{tf}"
    try:
        r = _get_redis()
        cached = r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    yf_params: dict = {}
    if tf == "15m":
        yf_params = {"period": "5d", "interval": "15m"}
    elif tf == "1h":
        yf_params = {"period": "60d", "interval": "1h"}
    elif tf == "4h":
        yf_params = {"period": "120d", "interval": "60m"}

    # T230-CHARTING-PREMARKET: US only, same as the DB-backed ingestion path — HK has no
    # pre/post-market session, so there's nothing extra for prepost=True to surface there.
    market = "HK" if symbol.upper().endswith(".HK") else "US"

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(**yf_params, auto_adjust=True, prepost=(market == "US"))
        if hist.empty:
            return []

        # Normalise MultiIndex columns (yfinance sometimes returns them)
        if isinstance(hist.columns, pd.MultiIndex):
            hist.columns = hist.columns.get_level_values(0)

        hist = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
        hist.index = pd.to_datetime(hist.index, utc=True)

        if tf == "4h":
            hist = (
                hist.resample("4h")
                .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
                .dropna()
            )

        rows = []
        for ts, row in hist.iterrows():
            rows.append({
                "ts": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]),
                "session": _classify_session(ts.tz_convert("UTC").replace(tzinfo=None), market),
            })

        try:
            r = _get_redis()
            r.setex(cache_key, 600, json.dumps(rows))
        except Exception:
            pass

        return rows
    except Exception as exc:
        log.warning("prices_tf.error", symbol=symbol, tf=tf, error=str(exc))
        raise HTTPException(500, f"Failed to fetch {tf} prices for {symbol}: {exc}")


@router.get("/{symbol}/prices", response_model=list[PriceOut])
def get_prices(
    symbol: str,
    timeframe: str = "1d",
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(1000, ge=1, le=10000),
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
        # Use tomorrow as upper bound so all of today's intraday bars are included.
        # date.today() converts to midnight 00:00:00 UTC in PostgreSQL, which excludes
        # any bar timestamped after midnight today (i.e. all intraday 5m/1m bars).
        end = date.today() + timedelta(days=1)
    if start and end and start > end:
        raise HTTPException(400, "start date must not be after end date")

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
            session=r.session,
        )
        for r in rows
    ]
