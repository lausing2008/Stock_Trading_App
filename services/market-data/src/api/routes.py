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
from db import AnalystPriceTarget, EarningsAlertSubscription, Fundamental, Price, SqueezeWatch, SrWatch, Stock, StockGoal, TimeFrame, get_session
from .auth import get_current_user, get_advanced_user
from ..services.ingestion import _classify_session

log = get_logger("routes")
router = APIRouter(prefix="/stocks", tags=["stocks"])

_settings = get_settings()

def _get_redis() -> redis_lib.Redis:
    from common.redis_client import get_redis as _get_pool_redis
    return _get_pool_redis()

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
    # T260-DELISTED-BADGE: surfaces aud14-survivorship's real delisting detection — informational
    # only (see the design note in CLAUDE.md's aud14-survivorship entry). from_attributes=True
    # pulls this straight off the Stock ORM row, no handler-function changes needed.
    delisted: bool = False

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


_LIVE_BULK_FALLBACK_MAX = 20  # BUG-YFCALLVOL2: see _fetch_live_bulk()'s own docstring


def _fetch_live_bulk(stocks: list) -> list[dict]:
    """Fetch prices for all symbols in one yf.download() call — avoids per-symbol rate limits.

    yf.download() uses Yahoo's batch chart endpoint which is far more lenient than
    calling fast_info/Ticker 68 times in parallel. Falls back to _fetch_live_one
    for any symbols missing from the download result — but ONLY when the miss count is small
    (a handful of thinly-traded/delisted stragglers Yahoo's batch endpoint occasionally omits
    even under normal conditions).

    BUG-YFCALLVOL2 (2026-08-17): during a real Yahoo-side rate-limit event, the bulk call
    itself gets throttled and can miss MOST or ALL of the universe (confirmed live: repeated
    live_prices.bulk_fallback count=165 events, i.e. every single tracked symbol, recurring
    every ~1-2 minutes) — the SAME condition that caused the bulk miss also guarantees the
    per-symbol fallback loop below gets rate-limited too, except now it's ~150+ individual
    HTTP requests (up to 2 each: fast_info + a history() fallback inside _fetch_live_one)
    fired every single minute with zero backoff, actively amplifying the same throttle this
    function exists to avoid — the exact BUG-YFCALLVOL amplification pattern already fixed
    once in paper_trading_engine.py's _fetch_live_prices(), recurring here in a second,
    never-touched call site. Capping the fallback at a small threshold preserves the real,
    intended behavior (fill in the rare straggler) while refusing to pile more individual
    requests onto an endpoint that has already shown signs of being globally throttled this
    cycle — the cache just serves fewer symbols that minute instead, correctly recovering on
    its own once Yahoo's throttle window passes, rather than being kept alive by this app's
    own retry storm.
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

    # Fill in any symbols the bulk download missed using individual fetches — BUT only when
    # the miss count is small (see BUG-YFCALLVOL2 above). A large miss count means the bulk
    # call itself is being rate-limited, and firing 100+ individual fallback requests would
    # only make that worse; skip the fallback entirely and let the cache carry fewer symbols
    # this cycle rather than amplify an active throttle.
    missed = [s for s in stocks if s.symbol not in fetched]
    if missed and len(missed) > _LIVE_BULK_FALLBACK_MAX:
        log.warning("live_prices.bulk_fallback_skipped_too_many_misses",
                    count=len(missed), threshold=_LIVE_BULK_FALLBACK_MAX,
                    note="likely a Yahoo-side rate-limit event — skipping individual fallback to avoid amplifying it")
    elif missed:
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


@router.get("/entry-gate-params")
def entry_gate_params(style: str = "SWING", market: str = "US"):
    """T234-CONFIG-DECIDE-DEFAULT-MISMATCH: the real entry-gate thresholds
    (min_confidence/min_kscore/min_entry_score/min_ta_score/min_rr_ratio) a real portfolio of
    this style/market would use with no explicit portfolio.config overrides — same merge order
    _scan_for_entries() applies. A distinct dict from /style-params above (that one is
    game-plan geometry: entry/breakout/stop/target percentages; this one is the actual
    gates that decide whether a candidate is even allowed to enter).

    Built because decision-engine's standalone GET /decide/{symbol}/explain path (used by
    decide.tsx) never runs _scan_for_entries' own config merge at all — it's not a real
    portfolio scan — so it previously had no way to know the real value and silently used its
    own disconnected hardcoded literal (62.0) instead of the real per-style/market value
    (SWING=50/HK=65, LONG=40, etc.). Unauthenticated — read-only, no sensitive data.
    """
    from ..services.paper_trading_engine import resolve_entry_gate_params
    return resolve_entry_gate_params(style, market)


@router.get("/entry-weights")
def entry_weights():
    """T232-DL-DUALSCORER-DEBT item #23: the calibrated logistic-regression entry weights
    _should_enter() uses in place of the plain additive score>=min_entry_score comparison once
    a portfolio has >=100 closed trades (PT-3). decision-engine's own /decide/{symbol} verdict
    has no access to this — it lives in this service's own local file cache
    (/data/models/entry_weights.json, written by calibrate_entry_weights()) — so a
    decision-engine call for a portfolio that HAS crossed the calibration threshold would use
    the plain additive threshold decision-engine always has, silently diverging from what the
    real _should_enter() fallback would decide for the identical candidate. Exposes the same
    dict _should_enter() itself reads via _load_entry_weights(), so decision-engine can apply
    the identical calibrated-probability check when calibration data exists.

    Returns {} (no "intercept"/"n_trades" keys) when no calibration file exists yet — matching
    _load_entry_weights()'s own no-file sentinel exactly, so a caller's own
    `weights.get("n_trades", 0) >= 100` gate degrades correctly with zero special-casing.
    Unauthenticated — read-only, no sensitive data (this is a fitted-weights blob, not user data).
    """
    from ..services.paper_trading_engine import _load_entry_weights
    return _load_entry_weights()


_MARKET_BREADTH_KEY = "stockai:market_breadth"
_MARKET_BREADTH_TTL = 60 * 60 * 4  # 4 hours


@router.get("/market_breadth")
def market_breadth(market: str = Query("US", pattern="^(US|HK)$"), session: Session = Depends(get_session)):
    """% of active stocks (US or HK) trading above their 200-day SMA (from latest ranking
    fair_price). Redis-cached 4 h per market — the cache key is market-scoped (T255-REPORTS-TAB)
    so a US and an HK reading never overwrite each other. Used as a regime signal: > 60% =
    healthy bull, < 40% = broad weakness."""
    from db import Ranking, Market as _Market
    from datetime import date as _date

    _market_key = f"{_MARKET_BREADTH_KEY}:{market.upper()}"
    try:
        cached = _get_redis().get(_market_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    today = _date.today()
    cutoff = today - timedelta(days=10)

    # Latest ranking per active stock (in the requested market) that has a fair_price (SMA-200)
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
        .where(Stock.active.is_(True), Stock.market == _Market(market.upper()), Ranking.fair_price.is_not(None))
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
        _get_redis().setex(_market_key, _MARKET_BREADTH_TTL, json.dumps(result))
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
    # AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: the settlement date short_percent_of_float/
    # short_ratio/shares_short above are AS OF — exchange short interest settles ~2x/month with
    # a 1-2 week reporting lag, so this can legitimately be up to ~6 weeks old. YYYY-MM-DD string.
    short_interest_date: str | None = None
    # Ownership breakdown
    held_percent_institutions: float | None = None
    held_percent_insiders: float | None = None
    # Earnings surprise history (last 8 quarters)
    eps_beat_rate: float | None = None          # 0.0–1.0 fraction of quarters where actual > estimate
    eps_avg_surprise_pct: float | None = None   # mean surprise % across available quarters
    eps_surprise_trend: str | None = None       # "improving" | "declining" | "stable"
    eps_history: list[dict] = []                # [{quarter, actual, estimate, surprise_pct}]
    # AUD-EARNINGSCONSENSUS: forward-looking market consensus for the NEXT quarter/year (not
    # historical, unlike eps_history above) — yfinance's earnings_estimate/revenue_estimate/
    # eps_trend/eps_revisions, one row per period ("0q" = current/next quarter, "+1q" = quarter
    # after, "0y"/"+1y" = current/next fiscal year). None (not []) when yfinance has no
    # consensus data for this symbol at all (e.g. thin coverage) — never a fabricated row.
    earnings_consensus: dict | None = None
    # Past-quarter revenue history (actual only — yfinance's quarterly_financials has no
    # matching "what was estimated at the time" figure the way earnings_history does for EPS,
    # so this is real-values-only, not an actual-vs-estimate comparison like eps_history).
    revenue_history: list[dict] = []            # [{quarter, revenue}], oldest first
    # AUD-EARNINGSFORECAST: this stock's own projected growth vs. the broader index's — a real,
    # comparable "is this a bellwether outpacing/lagging the market" read (yfinance's
    # growth_estimates DataFrame, same period keys as earnings_consensus above). None when
    # yfinance has no comparison data for this symbol, never a fabricated pair.
    growth_vs_index: dict | None = None
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


def _refresh_days_to_earnings(payload: dict) -> dict:
    """BUG-FUNDAMENTALS-STALEDTE: days_to_earnings is a DERIVED, day-relative value
    ((next_earnings_date - today).days) computed once at fetch time and then cached
    alongside the rest of fundamentals for 24h (_FUND_TTL) — a TTL chosen because the
    underlying fundamentals data itself only changes quarterly, which is true for every
    OTHER field on this payload but not this one. A payload cached the day before a report
    (days_to_earnings=0, correct then) is still within its 24h TTL a day later, after the
    report has already happened, and would otherwise keep reading as "reports today" —
    exactly the stale "HPE reports Today" email a user received a day after HPE's real
    earnings date. next_earnings_date (an absolute date string) does NOT go stale this way,
    so days_to_earnings is always recomputed fresh from it here, at every point a fundamentals
    payload (cache hit OR fresh fetch) is about to be returned/consumed — never trusted as
    persisted. A next_earnings_date now in the past means the calendar itself is stale (the
    company already reported and yfinance hasn't rolled it to the NEXT quarter's date yet) —
    degrades to None/None rather than emitting a negative days_to_earnings, since "reports
    N days ago" is not a real signal this app's callers are built to handle.
    """
    ned = payload.get("next_earnings_date")
    if not ned:
        return payload
    try:
        from datetime import date as _date, datetime as _datetime
        next_ed = _datetime.strptime(ned, "%Y-%m-%d").date()
        today = _date.today()
        if next_ed >= today:
            payload["days_to_earnings"] = (next_ed - today).days
        else:
            payload["next_earnings_date"] = None
            payload["days_to_earnings"] = None
    except Exception:
        pass
    return payload


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
                return _refresh_days_to_earnings(json.loads(cached))
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
                    # wsz-analyst-accuracy-weighting: yfinance's own upgrades_downgrades frame
                    # already carries a per-firm currentPriceTarget/priorPriceTarget — this
                    # app's own analyst_actions capture previously discarded both, keeping
                    # only the qualitative Firm/ToGrade/FromGrade/Action columns. A yfinance
                    # value of exactly 0.00 means "no price target on this action" (confirmed
                    # live — e.g. a plain reiteration action), not a real $0 target — treated
                    # as None here rather than a literal zero that would corrupt any accuracy
                    # scoring downstream.
                    _cpt = row.get("currentPriceTarget")
                    _ppt = row.get("priorPriceTarget")
                    current_price_target = float(_cpt) if _cpt not in (None, 0, 0.0) else None
                    prior_price_target = float(_ppt) if _ppt not in (None, 0, 0.0) else None
                    analyst_actions.append({
                        "date":       (idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]),
                        "firm":       str(row.get("Firm", "")).strip(),
                        "from_grade": str(row.get("FromGrade", "")).strip(),
                        "to_grade":   str(row.get("ToGrade",   "")).strip(),
                        "action":     action,
                        "current_price_target": current_price_target,
                        "prior_price_target": prior_price_target,
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
        # AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: dateShortInterest is the settlement date
        # Yahoo's own quoteSummary schema carries alongside shortPercentOfFloat/shortRatio —
        # same Unix-timestamp shape as exDividendDate above, so the same conversion helper
        # applies directly.
        short_interest_date=_parse_ex_div_date(_safe(info, "dateShortInterest")),
        shares_short=_safe(info, "sharesShort"),
        shares_short_prior_month=_safe(info, "sharesShortPriorMonth"),
        held_percent_institutions=_safe(info, "heldPercentInstitutions"),
        held_percent_insiders=_safe(info, "heldPercentInsiders"),
    )

    # AUD-EARNINGSCONSENSUS: forward-looking market estimates for the next report — a
    # DIFFERENT question from eps_history below (which is backward-looking, "did the company
    # beat past estimates"). yfinance's earnings_estimate/revenue_estimate/eps_trend/
    # eps_revisions each return a small DataFrame indexed by period ("0q", "+1q", "0y", "+1y",
    # sometimes "LTG"). Only "0q"/"+1q"/"0y"/"+1y" are kept — "LTG" (long-term growth) has no
    # matching row in earnings_estimate/revenue_estimate at all, so a period this app can't
    # actually price a concrete estimate for is dropped rather than emitted half-populated.
    try:
        def _consensus_num(v):
            # yfinance's DataFrames can carry real NaN (confirmed live: growth_estimates'
            # own LTG row) — json.dumps(float('nan')) is non-standard and rejected by a strict
            # JSON.parse the same way the already-documented Infinity bug was (see this repo's
            # own AUD288-SQUEEZE-NO-VOLUME-CONFIRM-adjacent history for the exact prior
            # incident) — must degrade to None, never pass a real NaN through.
            if v is None:
                return None
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return None
            return None if fv != fv else fv  # fv != fv is the standard NaN self-inequality check

        _periods = ("0q", "+1q", "0y", "+1y")
        _ee = ticker.earnings_estimate
        _re = ticker.revenue_estimate
        _et = ticker.eps_trend
        _er = ticker.eps_revisions
        consensus: dict[str, dict] = {}
        for period in _periods:
            row: dict = {}
            if _ee is not None and not _ee.empty and period in _ee.index:
                r = _ee.loc[period]
                row["eps_avg"] = _consensus_num(r.get("avg"))
                row["eps_low"] = _consensus_num(r.get("low"))
                row["eps_high"] = _consensus_num(r.get("high"))
                row["eps_year_ago"] = _consensus_num(r.get("yearAgoEps"))
                row["number_of_analysts"] = _consensus_num(r.get("numberOfAnalysts"))
                row["eps_growth"] = _consensus_num(r.get("growth"))
            if _re is not None and not _re.empty and period in _re.index:
                r = _re.loc[period]
                row["revenue_avg"] = _consensus_num(r.get("avg"))
                row["revenue_low"] = _consensus_num(r.get("low"))
                row["revenue_high"] = _consensus_num(r.get("high"))
                row["revenue_growth"] = _consensus_num(r.get("growth"))
            if _et is not None and not _et.empty and period in _et.index:
                r = _et.loc[period]
                row["eps_trend_current"] = _consensus_num(r.get("current"))
                row["eps_trend_7d_ago"] = _consensus_num(r.get("7daysAgo"))
                row["eps_trend_30d_ago"] = _consensus_num(r.get("30daysAgo"))
                row["eps_trend_90d_ago"] = _consensus_num(r.get("90daysAgo"))
            if _er is not None and not _er.empty and period in _er.index:
                r = _er.loc[period]
                row["revisions_up_7d"] = _consensus_num(r.get("upLast7days"))
                row["revisions_up_30d"] = _consensus_num(r.get("upLast30days"))
                row["revisions_down_30d"] = _consensus_num(r.get("downLast30days"))
                row["revisions_down_7d"] = _consensus_num(r.get("downLast7Days"))
            if row:
                consensus[period] = row
        if consensus:
            data.earnings_consensus = consensus
    except Exception as exc:
        log.warning("fundamentals.earnings_consensus_fetch_failed", symbol=symbol, error=str(exc))

    try:
        def _growth_num(v):
            # Independent copy of earnings_consensus's own _consensus_num() NaN guard above —
            # deliberately NOT shared, since that one is defined inside a SIBLING try block and
            # would be undefined here if that earlier block raised before reaching its own def.
            if v is None:
                return None
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return None
            return None if fv != fv else fv

        gr = ticker.growth_estimates
        if gr is not None and not gr.empty:
            growth: dict[str, dict] = {}
            for period, r in gr.iterrows():
                row: dict = {}
                st = _growth_num(r.get("stockTrend"))
                ix = _growth_num(r.get("indexTrend"))
                if st is not None:
                    row["stock_growth"] = st
                if ix is not None:
                    row["index_growth"] = ix
                if row:
                    growth[str(period)] = row
            if growth:
                data.growth_vs_index = growth
    except Exception as exc:
        log.warning("fundamentals.growth_vs_index_fetch_failed", symbol=symbol, error=str(exc))

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

    # Past-quarter revenue history — a separate DataFrame from earnings_history above
    # (quarterly_financials is a full financial statement; only the "Total Revenue" row is
    # kept, not the whole thing). No pandas NaN can reach this: yfinance only reports a
    # "Total Revenue" ROW for a quarter that was genuinely reported (an unreported/future
    # quarter simply has no column at all), so real numeric values are the only thing here.
    try:
        qf = ticker.quarterly_financials
        if qf is not None and not qf.empty and "Total Revenue" in qf.index:
            rev_row = qf.loc["Total Revenue"].dropna().sort_index()
            data.revenue_history = [
                {"quarter": str(idx.date()) if hasattr(idx, "date") else str(idx), "revenue": float(v)}
                for idx, v in rev_row.items()
            ]
    except Exception as exc:
        log.warning("fundamentals.revenue_history_fetch_failed", symbol=symbol, error=str(exc))

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
                return _refresh_days_to_earnings(json.loads(stale))
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
            _si_date = _date.fromisoformat(data.short_interest_date) if data.short_interest_date else None
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
                short_interest_date=_si_date,
                recommendation_mean=data.recommendation_mean,
                number_of_analysts=data.number_of_analysts,
                target_price=data.target_price,
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
                    short_interest_date=_si_date,
                    recommendation_mean=data.recommendation_mean,
                    number_of_analysts=data.number_of_analysts,
                    target_price=data.target_price,
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

    # wsz-analyst-accuracy-weighting: persist each per-firm price-target action independently
    # from the Fundamental upsert above (a genuinely separate table/concern) — one row per
    # (stock_id, firm, grade_date), idempotent via ON CONFLICT DO NOTHING so re-fetching the
    # same 90-day window on a later day never duplicates an already-captured historical action.
    try:
        from sqlalchemy.dialects.postgresql import insert as pg_insert_apt
        stock_row2 = db.execute(
            select(Stock).where(Stock.symbol == symbol.upper())
        ).scalar_one_or_none()
        if stock_row2 and analyst_actions:
            for act in analyst_actions:
                if act.get("current_price_target") is None:
                    continue
                stmt2 = pg_insert_apt(AnalystPriceTarget).values(
                    stock_id=stock_row2.id,
                    symbol=symbol.upper(),
                    firm=act["firm"],
                    grade_date=_date.fromisoformat(act["date"]),
                    action=act.get("action"),
                    to_grade=act.get("to_grade"),
                    current_price_target=act["current_price_target"],
                    prior_price_target=act.get("prior_price_target"),
                ).on_conflict_do_nothing(
                    constraint="uq_analyst_price_target_stock_firm_date",
                )
                db.execute(stmt2)
            db.commit()
    except Exception as exc:
        log.warning("analyst_price_target.db_persist_failed", symbol=symbol, error=str(exc))
        db.rollback()

    log.info("fundamentals.ok", symbol=symbol)
    return data


# wsz-analyst-accuracy-weighting: minimum SCORED historical targets a firm needs before its
# accuracy is trusted enough to weight it above/below equal weighting — a firm with only 1-2
# resolved targets could show 100%/0% accuracy from pure noise, which would then swing the
# whole consensus disproportionately. Firms below this floor get equal weight (1.0), same as
# the "no data yet" case — this repo's established convention (kscore's own weight-blending,
# T234-ML-FUND-BROADCAST-LEAKAGE's PIT joins) is to degrade to a neutral default rather than
# act on a statistically unreliable value.
_ANALYST_ACCURACY_MIN_SAMPLES = 5
_ANALYST_CONSENSUS_LOOKBACK_DAYS = 90  # matches analyst_actions' own existing recency window


def _compute_weighted_analyst_consensus(session: Session, symbol: str) -> dict:
    """wsz-analyst-accuracy-weighting: an accuracy-weighted analyst price-target consensus,
    alongside the existing simple mean, for the given symbol.

    Firm accuracy is a FIRM-level property (computed across every symbol that firm has ever
    covered, not just this one) — a firm's own track record predicting AAPL is relevant
    evidence for how much to trust their MSFT target too, since the underlying skill being
    measured ("how good is this firm's price-target process") isn't symbol-specific. Weight
    = accuracy_pct when a firm has >= _ANALYST_ACCURACY_MIN_SAMPLES scored historical targets
    (any symbol); otherwise 1.0 (equal weight) — see the module-level constant's own comment
    for why an unreliable few-sample accuracy is never allowed to swing the consensus.

    Returns simple_mean=None / weighted_mean=None (not 0.0) when no firm has a recent target
    for this symbol at all — an absent consensus is a genuinely different state than "$0",
    and must never be silently conflated with it downstream.
    """
    stock_row = session.execute(select(Stock).where(Stock.symbol == symbol.upper())).scalar_one_or_none()
    if stock_row is None:
        return {"simple_mean": None, "weighted_mean": None, "n_firms": 0, "firms": []}

    cutoff = date.today() - timedelta(days=_ANALYST_CONSENSUS_LOOKBACK_DAYS)
    recent_targets = session.execute(
        select(AnalystPriceTarget)
        .where(
            AnalystPriceTarget.stock_id == stock_row.id,
            AnalystPriceTarget.grade_date >= cutoff,
            AnalystPriceTarget.current_price_target.is_not(None),
        )
        .order_by(AnalystPriceTarget.grade_date.desc())
    ).scalars().all()
    if not recent_targets:
        return {"simple_mean": None, "weighted_mean": None, "n_firms": 0, "firms": []}

    # Most recent target PER FIRM only — a firm that's re-issued multiple targets in the
    # window should contribute once with its latest view, not be double-counted.
    latest_per_firm: dict[str, AnalystPriceTarget] = {}
    for t in recent_targets:
        if t.firm not in latest_per_firm:
            latest_per_firm[t.firm] = t

    firm_names = list(latest_per_firm.keys())
    accuracy_rows = session.execute(
        select(
            AnalystPriceTarget.firm,
            func.count().label("n"),
            func.count().filter(AnalystPriceTarget.target_achieved.is_(True)).label("n_achieved"),
        )
        .where(
            AnalystPriceTarget.firm.in_(firm_names),
            AnalystPriceTarget.outcome_evaluated_at.is_not(None),
        )
        .group_by(AnalystPriceTarget.firm)
    ).all()
    accuracy_by_firm = {
        r.firm: (float(r.n_achieved) / float(r.n) if r.n > 0 else None, int(r.n))
        for r in accuracy_rows
    }

    targets = [float(t.current_price_target) for t in latest_per_firm.values()]
    simple_mean = round(sum(targets) / len(targets), 2)

    weighted_sum = 0.0
    weight_total = 0.0
    firms_out = []
    for firm, t in latest_per_firm.items():
        acc, n_scored = accuracy_by_firm.get(firm, (None, 0))
        if acc is not None and n_scored >= _ANALYST_ACCURACY_MIN_SAMPLES:
            weight = acc
        else:
            weight = 1.0  # equal weight — insufficient/no track record, never let noise swing the consensus
        weighted_sum += float(t.current_price_target) * weight
        weight_total += weight
        firms_out.append({
            "firm": firm,
            "current_price_target": float(t.current_price_target),
            "grade_date": t.grade_date.isoformat(),
            "accuracy_pct": round(acc * 100, 1) if acc is not None else None,
            "n_scored_targets": n_scored,
            "weight_used": round(weight, 4),
        })

    weighted_mean = round(weighted_sum / weight_total, 2) if weight_total > 0 else None
    firms_out.sort(key=lambda f: f["current_price_target"], reverse=True)
    return {
        "simple_mean": simple_mean,
        "weighted_mean": weighted_mean,
        "n_firms": len(firms_out),
        "firms": firms_out,
    }


@router.get("/{symbol}/analyst-consensus")
def analyst_consensus(symbol: str, session: Session = Depends(get_session)):
    """wsz-analyst-accuracy-weighting: accuracy-weighted analyst price-target consensus.

    Alongside the existing raw simple mean (yfinance's own targetMeanPrice, already surfaced
    via GET /stocks/{symbol}/fundamentals), this weights each contributing firm's current
    target by that firm's OWN historical accuracy (once _evaluate_analyst_target_outcomes()
    has scored enough of their past targets) — an 80%-accuracy firm's target counts more than
    a 30%-accuracy firm's, rather than every firm counting equally regardless of track record.

    weighted_mean is None (not a fallback to simple_mean) whenever no recent target exists —
    a caller must handle the absent case explicitly rather than silently substituting a
    different number for it.
    """
    return _compute_weighted_analyst_consensus(session, symbol)


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


# AUD265-SQUEEZE-CACHE-MISS-SILENT-SKIP: `if not cached: continue` treats a stockai:
# fundamentals:v2:{symbol} cache miss identically to "this symbol just doesn't qualify" — a
# real distinction gets erased, since a symbol whose 24h TTL lapsed without a page-view
# repopulating it before this endpoint/job next runs silently drops out with no signal
# anywhere. Every site sharing this cache-key pattern (earnings_calendar, stocks_events,
# analyst_ratings here, plus check_short_squeeze_alerts in scheduler.py) now reports its own
# miss count via this one shared helper instead of a bare `continue` — cheap, and gives
# operators something to actually check ("is this endpoint silently missing data right now")
# instead of only being able to infer it after the fact from a user complaint.
def _log_fundamentals_cache_misses(endpoint: str, miss_count: int, total: int) -> None:
    if miss_count > 0:
        log.info(
            "fundamentals_cache.misses",
            endpoint=endpoint, misses=miss_count, total=total,
            note="symbols silently excluded — cache miss is not the same as does-not-qualify",
        )


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
    _misses = 0
    for stock in stocks:
        cache_key = f"stockai:fundamentals:v2:{stock.symbol}"
        try:
            cached = r.get(cache_key)
            if not cached:
                _misses += 1
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
    _log_fundamentals_cache_misses("earnings_calendar", _misses, len(stocks))
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
#
# AUD264-MACRO-CALENDAR-TYPE-MAP-COVERS-4-OF-10: this used to map only 4 of the 10 real
# *_release event_types economic.py's own _FRED_RELEASES actually syncs (cpi/nfp/pce/gdp) —
# the other 6 (ppi/retail_sales/consumer_conf/housing_starts/jobless_claims/fed_funds) were
# never SELECTed by _macro_events_from_db() at all, so their already-synced, real DB rows
# were completely invisible to this endpoint — not merely duplicated by the hardcoded
# _MACRO_2026 fallback (which has no entries for these 6 types either), just missing
# outright. Extended to all 10, matching economic.py's _FRED_RELEASES exactly.
_MACRO_TYPE_TO_RELEASE_EVENT_TYPE = {
    "cpi": "cpi_release",
    "nfp": "nfp_release",
    "pce": "pce_release",
    "gdp": "gdp_release",
    "ppi": "ppi_release",
    "retail_sales": "retail_sales_release",
    "consumer_conf": "consumer_conf_release",
    "housing_starts": "housing_starts_release",
    "jobless_claims": "jobless_claims_release",
    "fed_funds": "fed_funds_release",
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
    from ..services import unusual_whales as _uw

    r = _get_redis()
    stocks = session.execute(select(Stock).where(Stock.active.is_(True))).scalars().all()

    _stock_events_misses = 0
    for stock in stocks:
        mkt = stock.market.value if hasattr(stock.market, "value") else str(stock.market)
        cache_key = f"stockai:fundamentals:v2:{stock.symbol}"
        try:
            cached = r.get(cache_key)
            if not cached:
                _stock_events_misses += 1
                continue
            data = json.loads(cached)

            # Earnings
            ned = data.get("next_earnings_date")
            if ned:
                try:
                    ned_date = _date.fromisoformat(ned)
                    if today <= ned_date <= cutoff:
                        # AUD-EARNINGSCAL-MARKETESTIMATES: "what does the market estimate before
                        # earnings" — eps_beat_rate/eps_avg_surprise_pct are already on the SAME
                        # cached fundamentals blob this loop already reads (zero new cost).
                        # analyst_price_target_* needs a real DB query
                        # (_compute_weighted_analyst_consensus), so it's only computed for
                        # symbols that actually have a near-term earnings event in this window —
                        # never for the full active-stock universe this loop otherwise iterates.
                        _consensus = _compute_weighted_analyst_consensus(session, stock.symbol)
                        # AUD-EARNINGSMOVE: real, options-market-implied expected move for the
                        # NEXT report, backed by up to 8 quarters of "was the market's fear
                        # justified" history for THIS symbol — a genuinely different, forward-
                        # looking complement to eps_beat_rate/eps_avg_surprise_pct above (those
                        # are about EPS accuracy, this is about PRICE reaction magnitude). Scoped
                        # to only near-term-earnings symbols, same as _consensus above — never
                        # the full active-stock universe this loop otherwise iterates. Redis-
                        # cached 6h inside get_historical_earnings_moves() itself, so repeated
                        # requests for the same symbol within that window cost nothing extra.
                        _earnings_moves = _uw.get_historical_earnings_moves(stock.symbol, limit=8)
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
                            "eps_beat_rate": data.get("eps_beat_rate"),
                            "eps_avg_surprise_pct": data.get("eps_avg_surprise_pct"),
                            "analyst_price_target_mean": _consensus.get("simple_mean"),
                            "analyst_price_target_weighted": _consensus.get("weighted_mean"),
                            "analyst_n_firms": _consensus.get("n_firms"),
                            # AUD-EARNINGSMOVE: next-report forecast (from this symbol's most
                            # recent historical row, since UW doesn't publish a forward-only
                            # forecast endpoint separately) + up to 8 quarters of real
                            # pre-report-forecast-vs-actual-outcome track record.
                            "earnings_expected_move_perc": (
                                _earnings_moves[0].expected_move_perc if _earnings_moves else None
                            ),
                            "earnings_move_history": [
                                {
                                    "report_date": r.report_date,
                                    "expected_move_perc": r.expected_move_perc,
                                    "post_earnings_move_1d": r.post_earnings_move_1d,
                                } for r in _earnings_moves
                            ],
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

    _log_fundamentals_cache_misses("events_calendar_stock_events", _stock_events_misses, len(stocks))
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
    _misses = 0
    for symbol, stock in stock_map.items():
        cache_key = f"stockai:fundamentals:v2:{symbol}"
        try:
            cached = r.get(cache_key)
            if not cached:
                _misses += 1
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
    _log_fundamentals_cache_misses("analyst_ratings", _misses, len(stock_map))
    results.sort(key=lambda x: x["date"], reverse=True)
    return results


# ── Short Interest Dashboard ──────────────────────────────────────────────────

@router.get("/short-interest")
def short_interest(
    _user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Return stocks sorted by short percent of float (from fundamentals table).

    AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: short_interest_date (the exchange's own
    settlement date for this reading — can legitimately be up to ~6 weeks old, see
    short_interest_date's own column comment in models.py) is now surfaced alongside the
    percentage so the UI can show real data age instead of implying every row is equally
    fresh. is_stale flags readings older than 30 days (a real, if imperfect, threshold —
    settlement lag alone can be ~2 weeks, so 30 days catches genuinely aged data without
    flagging every normal reading) — deliberately NOT filtered out entirely, since a stale
    reading is still the best data available and hiding it outright would be a worse UX than
    honestly labeling it.
    """
    from datetime import date as _date, timedelta as _timedelta
    from sqlalchemy import text as _text
    rows = session.execute(_text("""
        SELECT st.symbol, st.name, st.market,
               f.short_percent_of_float, f.short_ratio, f.market_cap, f.short_interest_date
        FROM stocks st
        JOIN (
            SELECT DISTINCT ON (stock_id) stock_id,
                   short_percent_of_float, short_ratio, market_cap, short_interest_date
            FROM fundamentals
            WHERE short_percent_of_float IS NOT NULL
            ORDER BY stock_id, as_of DESC
        ) f ON f.stock_id = st.id
        WHERE st.active = TRUE
        ORDER BY f.short_percent_of_float DESC
        LIMIT 200
    """)).fetchall()
    _stale_cutoff = _date.today() - _timedelta(days=30)
    return [
        {
            "symbol": r.symbol,
            "name": r.name,
            "market": r.market if isinstance(r.market, str) else r.market.value,
            "short_percent_of_float": float(r.short_percent_of_float) * 100 if r.short_percent_of_float is not None else None,
            "short_ratio": float(r.short_ratio) if r.short_ratio is not None else None,
            "market_cap": int(r.market_cap) if r.market_cap is not None else None,
            "short_interest_date": r.short_interest_date.isoformat() if r.short_interest_date is not None else None,
            "is_stale": (r.short_interest_date is None) or (r.short_interest_date < _stale_cutoff),
        }
        for r in rows
    ]


# ── T220-G: Sector K-Score Rotation ──────────────────────────────────────────

@router.get("/sector-rotation")
def get_sector_rotation():
    """Return current sector K-Score momentum (computed Sunday, cached in Redis).

    Returns {sector_name: {momentum: +1/0/-1, recent_kscore, prior_kscore, delta,
    rank, prior_rank, trajectory}} where momentum=+1 means sector K-Score rose >3 pts vs 4
    weeks ago (institutional tailwind), -1 means fell >3 pts (headwind), 0 means flat.

    T258-SECTOR-ROTATION-TRAJECTORY: `trajectory` is one of "Emerging Leader"/"Established
    Leader"/"Fading Leader"/"Emerging Laggard"/"Established Laggard"/"Fading Laggard" — how
    this sector's RANK among sectors has moved vs. the snapshot ~4 weeks ago (see
    services/sector_trajectory.py). `null` when there's no comparable snapshot from 4 weeks
    ago yet (first run, or the sector wasn't rankable then).
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
    from datetime import date as _sdate, timedelta as _stimedelta
    # AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: compared as ISO-format strings (not date
    # objects) since short_interest_date arrives from the JSON-serialized Redis cache below,
    # not a DB row — ISO-format string comparison is lexicographically equivalent to date
    # comparison for same-length YYYY-MM-DD strings.
    _stale_cutoff_str = (_sdate.today() - _stimedelta(days=30)).isoformat()
    # AUD265-SQUEEZE-SCREENER-NO-DELISTED-FILTER: an 11th instance of BUG-DELISTED-GENERATION-
    # BLIND — Stock.active.is_(True) alone does NOT exclude a confirmed delisting (a delisted
    # stock stays active=True forever), and this screener sorts by short_percent_of_float
    # descending, so a delisted heavily-shorted name stays pinned at the TOP indefinitely.
    stocks = session.execute(
        select(Stock).where(Stock.active.is_(True), Stock.delisted.is_(False))
    ).scalars().all()
    stock_map = {s.symbol: s for s in stocks}
    r = _get_redis()

    # AUD265-SQUEEZE-MOMENTUM-NULL-ON-STALE-RANKINGS: this used to filter
    # `Ranking.as_of >= today - timedelta(days=7)` before taking the latest-per-stock row —
    # a stock whose newest ranking predates that window was excluded from rank_rows entirely,
    # not merely marked stale, silently nulling momentum_score/k_score for every stock caught
    # by a lapsed ranking refresh (this repo's own history documents rankings going stale 7+
    # days at a time). Widened to 90 days (comfortably covers any realistic staleness
    # incident while still bounding the query against the full, unbounded-growth `rankings`
    # history table — Ranking has no unique(stock_id, as_of) constraint, so removing the
    # window filter entirely would pull every row ever written on every request) and now
    # surfaces how old the newest available ranking actually is via ranking_as_of/
    # ranking_is_stale instead of silently nulling the row, matching short_interest()'s own
    # established staleness-surfacing convention a few lines above.
    today = _sdate.today()
    _ranking_stale_cutoff = today - _stimedelta(days=7)
    rank_rows = session.execute(
        select(Ranking)
        .where(Ranking.as_of >= today - timedelta(days=90))
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

    # MPE-01/MPE-07: check Unusual Whales availability ONCE for the whole screener (not per
    # row) — is_available() itself is cheap (a Redis read), but per-row it would still be
    # min_short_float(15%+)-many redundant identical reads for no benefit.
    from ..services import unusual_whales as _uw
    _uw_on = _uw.is_available()

    results = []
    _misses = 0
    for symbol, stock in stock_map.items():
        cache_key = f"stockai:fundamentals:v2:{symbol}"
        try:
            cached = r.get(cache_key)
            if not cached:
                _misses += 1
                continue
            data = json.loads(cached)
            spf = data.get("short_percent_of_float")
            if spf is None or spf * 100 < min_short_float:
                continue
            sid = stock_id_map.get(symbol)
            rank = rank_map.get(sid)
            p = prices.get(symbol)
            _change_pct = p.get("change_pct") if p else None
            _momentum = rank.momentum if rank else None
            _dtc = data.get("short_ratio")

            # MPE-07: real Unusual Whales short-interest enrichment, when a subscription is
            # active — a per-symbol call only for rows that ALREADY cleared the short-float
            # floor above (never the whole universe), bounding real request cost to exactly
            # this screener's own already-filtered candidate set.
            _uw_fee_rate = None
            _uw_shares_avail = None
            _uw_short_interest = None
            if _uw_on:
                _uw_si = _uw.get_short_interest(symbol)
                if _uw_si is not None:
                    _uw_fee_rate = _uw_si.fee_rate
                    _uw_shares_avail = _uw_si.short_shares_available
                    _uw_short_interest = _uw_si.short_interest

            results.append({
                "symbol": symbol,
                "name": stock.name,
                "sector": stock.sector,
                "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
                "short_percent_of_float": round(spf * 100, 2),
                "short_ratio": _dtc,
                "shares_short": data.get("shares_short"),
                "shares_short_prior_month": data.get("shares_short_prior_month"),
                "price": p.get("price") if p else None,
                "change_pct": _change_pct,
                "momentum_score": _momentum,
                "k_score": rank.score if rank else None,
                # AUD265-SQUEEZE-MOMENTUM-NULL-ON-STALE-RANKINGS: rank is now the latest
                # ranking within 90 days regardless of whether it clears the (much tighter)
                # 7-day freshness bar a normal weekly refresh cadence implies — surface the
                # real age so a lapsed refresh is visible instead of masquerading as "no
                # ranking data for this stock" the way a silent null previously did.
                "ranking_as_of": rank.as_of.isoformat() if rank else None,
                "ranking_is_stale": (rank is None) or (rank.as_of < _ranking_stale_cutoff),
                "volume": p.get("volume") if p else None,
                # AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: see short_interest()'s own docstring
                # above for the same reasoning — surfaced here too, not filtered out, since a
                # stale reading is still the best data available.
                "short_interest_date": data.get("short_interest_date"),
                "is_stale": (
                    data.get("short_interest_date") is None
                    or data.get("short_interest_date") < _stale_cutoff_str
                ),
                # MPE-01/MPE-07: composite 0-100 score replacing the frontend's own binary
                # "Prime Candidate" heuristic — see compute_short_squeeze_score()'s own
                # docstring for the full weighting rationale.
                "squeeze_score": compute_short_squeeze_score(
                    short_percent_of_float=round(spf * 100, 2),
                    days_to_cover=_dtc,
                    momentum_score=_momentum,
                    change_pct=_change_pct,
                    short_shares_available=_uw_shares_avail,
                    fee_rate=_uw_fee_rate,
                    short_interest=_uw_short_interest,
                ),
                "uw_short_shares_available": _uw_shares_avail,
                "uw_fee_rate": _uw_fee_rate,
                "uw_short_interest": _uw_short_interest,
            })
        except Exception:
            continue
    _log_fundamentals_cache_misses("short_squeeze", _misses, len(stock_map))
    results.sort(key=lambda x: (x["squeeze_score"]["score"] if x["squeeze_score"] else -1), reverse=True)
    return results


@router.get("/bearish_puts_watch")
def bearish_puts_watch():
    """Puts-dominant options-expiry candidates, 3-5 days out, cross-checked against each
    stock's own real signals — see check_gamma_unwind_alerts()'s _bearish_puts_watch_
    candidates() in scheduler.py for the full computation. Read-only passthrough of the
    Redis cache that job already writes (6h TTL, refreshed a few times a day, same cadence as
    the underlying gamma-unwind scan) — no live computation here.

    high_conviction=True means at least 2 of 3 independent, already-tracked signals for that
    stock (SWING AI Signal SELL/HOLD, RSI<50, trading below its own 50-day average) agree with
    the puts-heavy options read — real corroborating evidence, not a prediction from options
    data alone. A candidate with high_conviction=False still has genuine puts-heavy pressure,
    it just isn't independently corroborated yet.
    """
    r = _get_redis()
    raw = r.get("stockai:bearish_puts_watch")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


# ── Squeeze Watchlist (T260-BEARISH-PUTS-WATCHLIST) ───────────────────────────
# Track a short-squeeze or bearish-puts-watch candidate over time and get a one-shot email
# the moment its short-side pressure fades — see SqueezeWatch in shared/db/models.py for the
# full design rationale and check_squeeze_watch_reverts() in scheduler.py for the revert logic.

class SqueezeWatchCreate(BaseModel):
    symbol: str
    watch_type: str  # "short_squeeze" | "bearish_puts"
    price_at_add: float | None = None
    metric_at_add: float | None = None  # short_percent_of_float OR puts concentration_pct
    note: str | None = None


class SqueezeWatchOut(BaseModel):
    id: int
    symbol: str
    watch_type: str
    added_at: str
    price_at_add: float | None = None
    metric_at_add: float | None = None
    reverted: bool
    reverted_at: str | None = None
    revert_reason: str | None = None
    note: str | None = None


def _squeeze_watch_out(w: SqueezeWatch) -> SqueezeWatchOut:
    return SqueezeWatchOut(
        id=w.id, symbol=w.symbol, watch_type=w.watch_type,
        added_at=w.added_at.isoformat(), price_at_add=w.price_at_add,
        metric_at_add=w.metric_at_add, reverted=w.reverted,
        reverted_at=w.reverted_at.isoformat() if w.reverted_at else None,
        revert_reason=w.revert_reason, note=w.note,
    )


@router.get("/squeeze-watch", response_model=list[SqueezeWatchOut])
def list_squeeze_watches(
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    rows = session.execute(
        select(SqueezeWatch)
        .where(SqueezeWatch.user_id == _user.id)
        .order_by(SqueezeWatch.added_at.desc())
    ).scalars().all()
    return [_squeeze_watch_out(w) for w in rows]


@router.post("/squeeze-watch", response_model=SqueezeWatchOut)
def add_squeeze_watch(
    req: SqueezeWatchCreate,
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    if req.watch_type not in ("short_squeeze", "bearish_puts"):
        raise HTTPException(status_code=400, detail="watch_type must be 'short_squeeze' or 'bearish_puts'")
    existing = session.execute(
        select(SqueezeWatch).where(
            SqueezeWatch.user_id == _user.id,
            SqueezeWatch.symbol == req.symbol,
            SqueezeWatch.watch_type == req.watch_type,
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Re-adding an already-reverted watch re-arms it with fresh add-time values, rather
        # than silently 409-ing — the user explicitly wants to track it again.
        existing.price_at_add = req.price_at_add
        existing.metric_at_add = req.metric_at_add
        existing.note = req.note
        existing.reverted = False
        existing.reverted_at = None
        existing.revert_reason = None
        session.commit()
        session.refresh(existing)
        return _squeeze_watch_out(existing)
    w = SqueezeWatch(
        user_id=_user.id, symbol=req.symbol, watch_type=req.watch_type,
        price_at_add=req.price_at_add, metric_at_add=req.metric_at_add, note=req.note,
    )
    session.add(w)
    session.commit()
    session.refresh(w)
    return _squeeze_watch_out(w)


@router.delete("/squeeze-watch/{watch_id}")
def remove_squeeze_watch(
    watch_id: int,
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    w = session.execute(
        select(SqueezeWatch).where(SqueezeWatch.id == watch_id, SqueezeWatch.user_id == _user.id)
    ).scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=404, detail="Watch not found")
    session.delete(w)
    session.commit()
    return {"status": "removed"}


# ── Support/Resistance Proximity Watch (SR-WATCH-PROXIMITY-ALERT) ─────────────
# Track a stock and get a one-shot email the moment price gets close (within an
# ATR-scaled band) to its nearest support or resistance level — "watch and decide whether
# to buy/sell yourself," never an automated trade signal. See SrWatch in shared/db/models.py
# for the full design rationale and check_sr_watch_reverts() in scheduler.py for the
# proximity-detection logic.

class SrWatchCreate(BaseModel):
    symbol: str
    atr_multiplier: float = 1.0
    note: str | None = None


class SrWatchOut(BaseModel):
    id: int
    symbol: str
    added_at: str
    atr_multiplier: float
    currently_near: bool
    last_alert_at: str | None = None
    last_alert_level_kind: str | None = None
    last_alert_level_price: float | None = None
    note: str | None = None


def _sr_watch_out(w: SrWatch) -> SrWatchOut:
    return SrWatchOut(
        id=w.id, symbol=w.symbol, added_at=w.added_at.isoformat(),
        atr_multiplier=w.atr_multiplier, currently_near=w.currently_near,
        last_alert_at=w.last_alert_at.isoformat() if w.last_alert_at else None,
        last_alert_level_kind=w.last_alert_level_kind,
        last_alert_level_price=w.last_alert_level_price, note=w.note,
    )


@router.get("/sr-watch", response_model=list[SrWatchOut])
def list_sr_watches(
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    rows = session.execute(
        select(SrWatch)
        .where(SrWatch.user_id == _user.id)
        .order_by(SrWatch.added_at.desc())
    ).scalars().all()
    return [_sr_watch_out(w) for w in rows]


@router.post("/sr-watch", response_model=SrWatchOut)
def add_sr_watch(
    req: SrWatchCreate,
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    if req.atr_multiplier <= 0:
        raise HTTPException(status_code=400, detail="atr_multiplier must be positive")
    existing = session.execute(
        select(SrWatch).where(
            SrWatch.user_id == _user.id,
            SrWatch.symbol == req.symbol,
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Re-adding an existing watch updates its settings rather than 409-ing — matching
        # SqueezeWatch's own re-arm convention. currently_near is intentionally reset to False
        # so a symbol re-added while already near a level fires fresh, rather than silently
        # inheriting a stale "already alerted" state from before it was removed/re-added.
        existing.atr_multiplier = req.atr_multiplier
        existing.note = req.note
        existing.currently_near = False
        session.commit()
        session.refresh(existing)
        return _sr_watch_out(existing)
    w = SrWatch(
        user_id=_user.id, symbol=req.symbol,
        atr_multiplier=req.atr_multiplier, note=req.note,
    )
    session.add(w)
    session.commit()
    session.refresh(w)
    return _sr_watch_out(w)


@router.delete("/sr-watch/{watch_id}")
def remove_sr_watch(
    watch_id: int,
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    w = session.execute(
        select(SrWatch).where(SrWatch.id == watch_id, SrWatch.user_id == _user.id)
    ).scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=404, detail="Watch not found")
    session.delete(w)
    session.commit()
    return {"status": "removed"}


# ── Earnings Alert Subscriptions (BUG-EARNINGS-IMPACT-UNSCOPED follow-up) ─────
# A durable, per-symbol opt-in for earnings result/impact alerts — deliberately independent
# of PriceAlert's one-shot trigger semantics. See EarningsAlertSubscription's own docstring in
# shared/db/models.py for why this exists alongside (not instead of) PriceAlert-based coverage.

class EarningsAlertSubCreate(BaseModel):
    symbol: str


class EarningsAlertSubOut(BaseModel):
    id: int
    symbol: str
    created_at: str


def _earnings_alert_sub_out(s: "EarningsAlertSubscription") -> EarningsAlertSubOut:
    return EarningsAlertSubOut(id=s.id, symbol=s.symbol, created_at=s.created_at.isoformat())


@router.get("/earnings-alert-subscriptions", response_model=list[EarningsAlertSubOut])
def list_earnings_alert_subscriptions(
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    rows = session.execute(
        select(EarningsAlertSubscription)
        .where(EarningsAlertSubscription.user_id == _user.id)
        .order_by(EarningsAlertSubscription.created_at.desc())
    ).scalars().all()
    return [_earnings_alert_sub_out(s) for s in rows]


@router.post("/earnings-alert-subscriptions", response_model=EarningsAlertSubOut)
def add_earnings_alert_subscription(
    req: EarningsAlertSubCreate,
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    symbol = req.symbol.upper().strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    existing = session.execute(
        select(EarningsAlertSubscription).where(
            EarningsAlertSubscription.user_id == _user.id,
            EarningsAlertSubscription.symbol == symbol,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _earnings_alert_sub_out(existing)
    s = EarningsAlertSubscription(user_id=_user.id, symbol=symbol, email=_user.email)
    session.add(s)
    session.commit()
    session.refresh(s)
    return _earnings_alert_sub_out(s)


@router.delete("/earnings-alert-subscriptions/{symbol}")
def remove_earnings_alert_subscription(
    symbol: str,
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    sym = symbol.upper().strip()
    s = session.execute(
        select(EarningsAlertSubscription).where(
            EarningsAlertSubscription.user_id == _user.id,
            EarningsAlertSubscription.symbol == sym,
        )
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    session.delete(s)
    session.commit()
    return {"status": "removed"}


# ── Stock Goals (T286-STOCK-GOALS) ─────────────────────────────────────────────
# A user-defined price/share/date target per symbol — see StockGoal's own docstring in
# shared/db/models.py for why this is a genuinely new capability, confirmed via a direct
# codebase search to have zero existing equivalent (unlike most of the roadmap doc that
# prompted this feature, whose other proposals turned out to already exist under different
# names). Progress is computed FRESH on every read from the current live/last-close price,
# never persisted — matches this app's own "don't store what can be cheaply recomputed"
# discipline used elsewhere (e.g. SqueezeAlertOutcome's own forward-return evaluator).

class StockGoalCreate(BaseModel):
    symbol: str
    title: str
    target_price: float | None = None
    target_shares: float | None = None
    target_date: str | None = None  # YYYY-MM-DD
    start_shares: float = 0.0
    notes: str | None = None


class StockGoalUpdate(BaseModel):
    title: str | None = None
    target_price: float | None = None
    target_shares: float | None = None
    target_date: str | None = None
    notes: str | None = None
    status: str | None = None  # active | achieved | cancelled


class StockGoalOut(BaseModel):
    id: int
    symbol: str
    title: str
    target_price: float | None = None
    target_shares: float | None = None
    target_date: str | None = None
    start_price: float
    start_shares: float
    notes: str | None = None
    status: str
    created_at: str
    achieved_at: str | None = None
    # Computed, never stored:
    current_price: float | None = None
    price_progress_pct: float | None = None  # 0-100+, None if no target_price set
    days_remaining: int | None = None  # None if no target_date set, negative if past due


def _compute_goal_progress(
    start_price: float, target_price: float | None, target_date_str: str | None,
    current_price: float | None,
) -> tuple[float | None, int | None]:
    """Pure function — price_progress_pct and days_remaining, both independently nullable
    depending on which targets are actually set. Pulled to module level (not an inline
    closure) so it's directly unit-testable with plain floats/strings, no DB/HTTP dependency,
    matching this file's own established _rank_screener_quotes()/_options_chain_rows()
    convention for "the one real piece of logic in an endpoint worth testing directly."

    price_progress_pct measures how far price has moved from start_price TOWARD target_price,
    as a percentage of the total distance needed — NOT current_price / target_price (which
    would misrepresent a goal that started well above zero, e.g. "go from $100 to $110" is a
    10% move, not the 90.9% a naive current/target ratio would report). Can exceed 100 if
    price has already moved past the target, and can be negative if price has moved the WRONG
    way (below start_price for an upward target) — both are real, honest signals, not clamped.

    A degenerate target_price exactly equal to start_price (zero real distance to travel) is
    reported as None rather than a division-by-zero — there is no meaningful "progress" to
    measure toward a target that was already met at goal-creation time.
    """
    price_progress_pct = None
    if target_price is not None and current_price is not None and target_price != start_price:
        price_progress_pct = round(
            (current_price - start_price) / (target_price - start_price) * 100, 1,
        )

    days_remaining = None
    if target_date_str:
        try:
            target_d = date.fromisoformat(target_date_str)
            days_remaining = (target_d - date.today()).days
        except (ValueError, TypeError):
            days_remaining = None

    return price_progress_pct, days_remaining


def _goal_current_price(session: Session, symbol: str) -> float | None:
    """Live cache first (stockai:live_prices), falling back to the last DB close — matches
    this file's own established fail-open-to-DB convention (see latest_prices()'s own
    cache-then-DB fallback a few hundred lines above)."""
    try:
        cached = _get_redis().get(_LIVE_KEY)
        if cached:
            for row in json.loads(cached):
                if row.get("symbol") == symbol:
                    return row.get("price")
    except Exception:
        pass
    for row in _latest_prices_from_db(session):
        if row.symbol == symbol:
            return row.price
    return None


def _stock_goal_out(g: "StockGoal", current_price: float | None) -> StockGoalOut:
    price_progress_pct, days_remaining = _compute_goal_progress(
        g.start_price, g.target_price, g.target_date.isoformat() if g.target_date else None,
        current_price,
    )
    return StockGoalOut(
        id=g.id, symbol=g.symbol, title=g.title,
        target_price=g.target_price, target_shares=g.target_shares,
        target_date=g.target_date.isoformat() if g.target_date else None,
        start_price=g.start_price, start_shares=g.start_shares, notes=g.notes,
        status=g.status, created_at=g.created_at.isoformat(),
        achieved_at=g.achieved_at.isoformat() if g.achieved_at else None,
        current_price=current_price, price_progress_pct=price_progress_pct,
        days_remaining=days_remaining,
    )


@router.get("/goals", response_model=list[StockGoalOut])
def list_stock_goals(
    symbol: str | None = Query(None, description="Filter to one symbol"),
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    q = select(StockGoal).where(StockGoal.user_id == _user.id)
    if symbol:
        q = q.where(StockGoal.symbol == symbol.upper().strip())
    rows = session.execute(q.order_by(StockGoal.created_at.desc())).scalars().all()
    return [_stock_goal_out(g, _goal_current_price(session, g.symbol)) for g in rows]


@router.post("/goals", response_model=StockGoalOut)
def create_stock_goal(
    req: StockGoalCreate,
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    sym = req.symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")
    if not req.title or not req.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    if req.target_price is None and req.target_shares is None and not req.target_date:
        raise HTTPException(status_code=400, detail="At least one of target_price, target_shares, target_date must be set")
    target_d = None
    if req.target_date:
        try:
            target_d = date.fromisoformat(req.target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="target_date must be YYYY-MM-DD")

    start_price = _goal_current_price(session, sym)
    if start_price is None:
        raise HTTPException(status_code=422, detail=f"No current price available for {sym} — cannot start a goal without a real reference price")

    g = StockGoal(
        user_id=_user.id, symbol=sym, title=req.title.strip(),
        target_price=req.target_price, target_shares=req.target_shares,
        target_date=target_d, start_price=start_price, start_shares=req.start_shares,
        notes=req.notes,
    )
    session.add(g)
    session.commit()
    session.refresh(g)
    return _stock_goal_out(g, start_price)


@router.put("/goals/{goal_id}", response_model=StockGoalOut)
def update_stock_goal(
    goal_id: int,
    req: StockGoalUpdate,
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    g = session.execute(
        select(StockGoal).where(StockGoal.id == goal_id, StockGoal.user_id == _user.id)
    ).scalar_one_or_none()
    if g is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    if req.status is not None:
        if req.status not in ("active", "achieved", "cancelled"):
            raise HTTPException(status_code=400, detail="status must be active, achieved, or cancelled")
        g.status = req.status
        g.achieved_at = datetime.now(timezone.utc) if req.status == "achieved" else None
    if req.title is not None:
        g.title = req.title.strip()
    if req.target_price is not None:
        g.target_price = req.target_price
    if req.target_shares is not None:
        g.target_shares = req.target_shares
    if req.target_date is not None:
        try:
            g.target_date = date.fromisoformat(req.target_date) if req.target_date else None
        except ValueError:
            raise HTTPException(status_code=400, detail="target_date must be YYYY-MM-DD")
    if req.notes is not None:
        g.notes = req.notes
    session.commit()
    session.refresh(g)
    return _stock_goal_out(g, _goal_current_price(session, g.symbol))


@router.delete("/goals/{goal_id}")
def delete_stock_goal(
    goal_id: int,
    session: Session = Depends(get_session),
    _user=Depends(get_current_user),
):
    g = session.execute(
        select(StockGoal).where(StockGoal.id == goal_id, StockGoal.user_id == _user.id)
    ).scalar_one_or_none()
    if g is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    session.delete(g)
    session.commit()
    return {"status": "removed"}


# ── Market-Wide Screener (beyond this app's own tracked universe) ────────────
# Closes the gap documented in .claude/CLAUDE.md's "Reports Tab" research (2026-07-16): every
# other screener/scanner page in this app (rankings, short-interest, short-squeeze) only
# joins against Stock — symbols this app ALREADY tracks. This is the one genuinely new
# capability: find a stock BEFORE it's on your radar at all, using yfinance's own free
# screener (yf.screen() / PREDEFINED_SCREENER_QUERIES) rather than a paid screener API.

_MARKET_SCREENER_QUERIES = ["small_cap_gainers", "aggressive_small_caps", "most_actives"]
_MARKET_SCREENER_TTL = 300  # 5 min — a real intraday-mover screen, not a slow-changing one


def _rank_screener_quotes(quotes: list[dict], tracked_symbols: set[str]) -> list[dict]:
    """Pure transform: raw yfinance screen() quote dicts -> this endpoint's own response
    shape. Pulled to module level (not an inline closure) so it's testable with a plain list
    of dicts, no real yfinance/HTTP call needed — the only real logic in market_screener()
    worth testing directly, matching _options_chain_rows()'s established convention above."""
    out = []
    seen: set[str] = set()
    for q in quotes:
        sym = q.get("symbol")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        change_pct = q.get("regularMarketChangePercent")
        volume = q.get("regularMarketVolume")
        avg_vol_3m = q.get("averageDailyVolume3Month")
        rvol = round(volume / avg_vol_3m, 2) if volume and avg_vol_3m else None
        out.append({
            "symbol": sym,
            "name": q.get("longName") or q.get("shortName") or sym,
            "price": q.get("regularMarketPrice"),
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "volume": volume,
            "rvol": rvol,
            "market_cap": q.get("marketCap"),
            "exchange": q.get("fullExchangeName") or q.get("exchange"),
            "already_tracked": sym in tracked_symbols,
        })
    out.sort(key=lambda r: (r["change_pct"] if r["change_pct"] is not None else -999), reverse=True)
    return out


@router.get("/market-screener")
def market_screener(_user=Depends(get_current_user)):
    """Market-wide screener USING yfinance's own free screening capability — finds a stock
    BEFORE it's already in your tracked universe, unlike every other screener page in this
    app. Runs 3 predefined Yahoo screens (small_cap_gainers, aggressive_small_caps,
    most_actives — chosen as the ones most likely to surface an early-stage explosive mover,
    the exact "catch something like DFNS before it's already on my radar" ask this feature was
    built for) and merges/dedupes the results, flagging which symbols this app already tracks.

    Read-only and safe for any logged-in user (not admin-gated) — it never writes anything.
    Actually adding a new symbol to this app's tracked universe still goes through the
    existing, admin-only POST /admin/add_stock endpoint — this screener only surfaces
    candidates, it does not itself mutate the Stock table.

    Cached 5 minutes (a real intraday-mover screen changes fast — not a slow-refresh cache
    like most fundamentals-driven pages in this app).
    """
    from db import SessionLocal

    cache_key = "stockai:market_screener"
    try:
        rdb = _get_redis()
        cached = rdb.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        rdb = None

    with SessionLocal() as session:
        tracked_symbols = {
            s for (s,) in session.execute(
                select(Stock.symbol).where(Stock.active.is_(True), Stock.delisted.is_(False))
            ).all()
        }

    all_quotes: list[dict] = []
    errors: list[str] = []
    for query in _MARKET_SCREENER_QUERIES:
        try:
            result = yf.screen(query, count=25)
            all_quotes.extend(result.get("quotes", []))
        except Exception as exc:
            log.warning("market_screener.query_failed", query=query, error=str(exc))
            errors.append(query)

    rows = _rank_screener_quotes(all_quotes, tracked_symbols)
    response = {"rows": rows, "queries_used": _MARKET_SCREENER_QUERIES, "queries_failed": errors}

    if rdb is not None:
        try:
            rdb.setex(cache_key, _MARKET_SCREENER_TTL, json.dumps(response))
        except Exception:
            pass

    return response


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
        # AUD265-GAMMA-ASSUMES-SORTED-EXPIRIES: sorted() makes "nearest 4 expiries" structural
        # rather than dependent on yfinance's own (undocumented) ordering of t.options.
        expiries = sorted(t.options)
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

        _whale_count = sum(1 for c in unusual if c.get("is_whale"))
        _top_whale_premium = max((c["premium"] for c in unusual), default=0)

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
            "whale_count":       _whale_count,
            "top_whale_premium": _top_whale_premium,
            # MPE-02: composite 0-100 options-pressure score — see
            # compute_options_pressure_score()'s own docstring for the weighting rationale.
            "pressure_score": compute_options_pressure_score(
                cp_ratio=cp_ratio,
                sentiment=sentiment,
                whale_count=_whale_count,
                total_call_vol=total_call_vol,
                total_put_vol=total_put_vol,
                gex=_options_flow_gex_component(sym),
            ),
        }

        try:
            rdb.setex(cache_key, _OPTIONS_TTL, json.dumps(result))
        except Exception:
            pass

        return result

    except Exception as exc:
        log.warning("options_flow.error", symbol=sym, error=str(exc))
        return {"symbol": sym, "available": False, "reason": "fetch_error"}


def _options_flow_gex_component(symbol: str) -> dict | None:
    """MPE-07: real Unusual Whales GEX proximity for compute_options_pressure_score()'s optional
    enrichment — how close the current live price sits to gamma_flip (the "zero gamma" level
    where dealer hedging flips direction). Isolated into its own small helper (rather than
    inlined into get_options_flow()) purely so compute_short_squeeze_score()'s own sibling
    caller doesn't need to duplicate this same "check availability, fetch GEX, fetch live price,
    compute proximity" sequence — kept private (leading underscore) since it's real orchestration
    logic, not a pure function meant to be unit-tested on its own the way compute_max_pain()/
    compute_short_squeeze_score()/compute_options_pressure_score() are.

    Returns None whenever Unusual Whales is unavailable, has no real GEX data for this symbol,
    or a live price can't be resolved — the caller (compute_options_pressure_score) already
    treats a None gex argument as "no enrichment," so this never needs a fallback value.
    """
    from ..services import unusual_whales as _uw
    if not _uw.is_available():
        return None
    levels = _uw.get_gex_levels(symbol)
    if levels is None or levels.gamma_flip is None:
        return None
    try:
        cached_prices = _get_redis().get(_LIVE_KEY)
        if not cached_prices:
            return None
        live_price = None
        for item in json.loads(cached_prices):
            if item.get("symbol") == symbol:
                live_price = item.get("price")
                break
        if live_price is None or live_price <= 0:
            return None
    except Exception:
        return None
    return {
        "gamma_flip": levels.gamma_flip,
        "call_wall": levels.call_wall,
        "put_wall": levels.put_wall,
        "distance_to_flip_pct": round(abs(live_price - levels.gamma_flip) / live_price * 100, 2),
        "above_flip": live_price >= levels.gamma_flip,
    }


def compute_options_pressure_score(
    cp_ratio: float | None,
    sentiment: str | None,
    whale_count: int,
    total_call_vol: int,
    total_put_vol: int,
    gex: dict | None = None,
) -> dict | None:
    """MPE-02: composite 0-100 options-pressure score, built entirely from fields
    get_options_flow() already computes above — no new data source needed for the free-tier
    score. `gex` (Unusual Whales-only, MPE-07 — see _options_flow_gex_component()) is optional
    real enrichment layered on top when a subscription is active.

    Unlike compute_short_squeeze_score() (which is inherently directional — more short-float
    pressure is always "more squeeze-y"), options pressure has no single "good" direction —
    strongly_bullish and bearish are both real signals of conviction, just opposite ones. This
    score therefore measures CONVICTION/INTENSITY (how far from neutral, how much size is
    behind it), not bullishness — a caller wanting direction should read `sentiment` (already
    returned by get_options_flow()) alongside this score, not instead of it.

    Components:
      - cp_ratio distance from 1.0 (neutral) — scored 0-40. cp_ratio is already capped at 10.0
        by get_options_flow() itself; a ratio of 1.0 (perfectly balanced call/put volume) scores
        0, ramping to a full 40 points at the two extremes (cp_ratio<=0.2 or >=5.0 — chosen as
        roughly 5x away from neutral in either direction, a genuinely lopsided real reading).
        AUD-OPTIONS6-CPRATIOASYMMETRY: scaled separately on each side of 1.0 (below-1.0 side
        divided by (1.0-0.2)=0.8, above-1.0 side by (5.0-1.0)=4.0) — a single shared linear-
        distance denominator would let cp_ratio=0.2 reach only 8/40 while cp_ratio=5.0 correctly
        reaches 40/40, since 0.2 and 5.0 are NOT equidistant from 1.0 in absolute terms (0.8 vs.
        4.0) even though both are a genuine 5x move away from neutral in fold-change terms —
        confirmed live in production affecting a real, non-trivial population of extreme-bearish
        options-flow snapshots before this fix.
      - whale_count (contracts with >$500K premium, already detected by get_options_flow()) —
        scored 0-30, 10 points per whale trade up to 3 whales (a 4th+ doesn't add more — the
        conviction signal from "multiple large trades" is already established by 3).
      - total volume (call+put combined) — scored 0-10, a simple liquidity-floor signal so a
        thinly-traded name with one big lucky print doesn't score as high as genuinely active
        options flow. Capped at 5,000 combined contracts.
      - GEX proximity (Unusual Whales only) — scored 0-20, how close the current price sits to
        gamma_flip. Price NEAR the flip level is where dealer hedging is most reactive/unstable
        (a small move can trigger a larger hedging response) — scored INVERSELY to distance:
        full 20 points within 1% of the flip, tapering to 0 at 10%+ away.

    Returns None only when cp_ratio itself is missing (get_options_flow()'s own "no options
    listed"/"no volume" cases already return available: False before this is ever called in
    practice, but the guard is real defense, not dead code, since a future caller could pass
    partial data through directly).
    """
    if cp_ratio is None:
        return None

    if cp_ratio >= 1.0:
        cpr_pts = min(40.0, max(0.0, (cp_ratio - 1.0) / (5.0 - 1.0) * 40.0))
    else:
        cpr_pts = min(40.0, max(0.0, (1.0 - cp_ratio) / (1.0 - 0.2) * 40.0))

    whale_pts = min(30.0, whale_count * 10.0)

    total_vol = (total_call_vol or 0) + (total_put_vol or 0)
    vol_pts = min(10.0, max(0.0, total_vol / 5000.0 * 10.0))

    score = round(cpr_pts + whale_pts + vol_pts, 1)

    components = {
        "cp_ratio_pts": round(cpr_pts, 1),
        "whale_pts": round(whale_pts, 1),
        "volume_pts": round(vol_pts, 1),
    }

    if gex is not None and gex.get("distance_to_flip_pct") is not None:
        gex_dist = gex["distance_to_flip_pct"]
        gex_pts = min(20.0, max(0.0, (10.0 - gex_dist) / 10.0 * 20.0))
        components["uw_gex_proximity_pts"] = round(gex_pts, 1)
        score = round(score + gex_pts, 1)

    return {"score": min(100.0, score), "components": components, "sentiment": sentiment}


_OPTIONS_CHAIN_TTL = 900  # 15-min — matches _OPTIONS_TTL's own refresh cadence


def _options_chain_rows(df) -> list[dict]:
    """Flattens one side (calls or puts) of a yfinance option_chain() DataFrame into a plain
    list of dicts, sorted by strike ascending. Pulled out to module level (not an inline
    closure) specifically so it's independently unit-testable without needing a real yfinance
    Ticker/HTTP call — the only real logic in get_options_chain() worth testing directly."""
    df = df.fillna(0)
    out = []
    for _, row in df.sort_values("strike").iterrows():
        out.append({
            "strike":       float(row["strike"]),
            "bid":          float(row.get("bid", 0)),
            "ask":          float(row.get("ask", 0)),
            "last_price":   float(row.get("lastPrice", 0)),
            "volume":       int(row.get("volume", 0)),
            "oi":           int(row.get("openInterest", 0)),
            "iv":           round(float(row.get("impliedVolatility", 0)) * 100, 1),
            "itm":          bool(row.get("inTheMoney", False)),
        })
    return out


def compute_max_pain(calls: list[dict], puts: list[dict]) -> dict | None:
    """IF-05: max pain — the strike at which options WRITERS (typically viewed as "the market")
    would owe the LEAST total intrinsic value at expiry, i.e. where the aggregate holder payout
    is minimized. Genuinely different from, and complementary to, check_gamma_unwind_alerts()'s
    own OI-concentration proxy (scheduler.py) — that one flags a lopsided position near price;
    this one computes an actual expiry-day price target from open interest alone.

    Needs only strike + open interest (both already fetched by get_options_chain() above) — no
    implied volatility, no Black-Scholes, no dealer-positioning assumption. That's why this is
    the cheaper IF-05 half to build first (see the design doc's own scoping note); a real GEX
    calculation would additionally need a dealer-sign ASSUMPTION, not just a measurement, which
    is why it's deliberately NOT attempted here.

    For each candidate strike S among the chain's own listed strikes, total payout at expiry:
      call_value(S) = sum over all call strikes K of call_OI[K] * max(0, S - K)   (ITM calls)
      put_value(S)  = sum over all put  strikes K of put_OI[K]  * max(0, K - S)   (ITM puts)
    Max pain = the S that minimizes call_value(S) + put_value(S).

    Returns None if either side has zero total open interest (nothing to compute against —
    a common case for a thin/newly-listed expiry) rather than fabricating a strike from noise.
    """
    total_oi = sum(c["oi"] for c in calls) + sum(p["oi"] for p in puts)
    if total_oi <= 0:
        return None

    # Every strike listed on EITHER side is a real candidate — a strike with only puts (or
    # only calls) listed can still be the pain-minimizing point once both sides' payouts are
    # summed against it.
    candidate_strikes = sorted({c["strike"] for c in calls} | {p["strike"] for p in puts})
    if not candidate_strikes:
        return None

    best_strike = None
    best_total_value = None
    for s in candidate_strikes:
        call_value = sum(c["oi"] * max(0.0, s - c["strike"]) for c in calls)
        put_value = sum(p["oi"] * max(0.0, p["strike"] - s) for p in puts)
        total_value = call_value + put_value
        if best_total_value is None or total_value < best_total_value:
            best_total_value = total_value
            best_strike = s

    total_call_oi = sum(c["oi"] for c in calls)
    total_put_oi = sum(p["oi"] for p in puts)
    return {
        "max_pain_strike": best_strike,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "put_call_oi_ratio": round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else None,
    }


_SQUEEZE_PRESSURE_LOW_MAX = 40.0
_SQUEEZE_PRESSURE_MEDIUM_MAX = 70.0


def _short_covering_pressure(score: float, has_uw_enrichment: bool) -> dict:
    """MPE-07: the doc's own §2 SHORT COVERING PROBABILITY ask — "Do NOT say forced covering
    WILL happen... use LOW/MEDIUM/HIGH with confidence" — a pure classification layer over the
    ALREADY-COMPUTED composite score, no new data source. Thresholds deliberately split the
    0-100 range into 3 even bands (<40 / 40-70 / >=70) rather than re-deriving a second set of
    calibrated percentiles — the score itself is already built from this app's own real,
    calibrated component thresholds (see compute_short_squeeze_score()'s own docstring); a
    second independent calibration for the tier boundaries would risk the two disagreeing with
    each other for no real benefit.

    Confidence is NOT a statistical estimate (per the doc's own explicit instruction: "Only
    calculate probability if the available historical data supports proper model training" —
    this app's squeeze-alert family has fired ~107 alerts total since launch, nowhere near
    enough to fit a real model) — it is a simple, honest reflection of how many real inputs
    actually informed the score: a free-tier-only score (no UW data) tops out lower than one
    with real UW borrow-fee/utilization enrichment behind it, since more independent real
    signals agreeing is itself more informative than one score built from fewer inputs.
    """
    if score >= _SQUEEZE_PRESSURE_MEDIUM_MAX:
        pressure = "HIGH"
    elif score >= _SQUEEZE_PRESSURE_LOW_MAX:
        pressure = "MEDIUM"
    else:
        pressure = "LOW"
    confidence = min(95.0, round((55.0 if has_uw_enrichment else 45.0) + score * 0.4, 0))
    return {"pressure": pressure, "confidence": confidence}


def compute_short_squeeze_score(
    short_percent_of_float: float | None,
    days_to_cover: float | None,
    momentum_score: float | None,
    change_pct: float | None,
    short_shares_available: float | None = None,
    fee_rate: float | None = None,
    short_interest: float | None = None,
) -> dict | None:
    """MPE-01: composite 0-100 short-squeeze score, replacing short-squeeze.tsx's own binary
    "Prime Candidate" heuristic (momentum_score > 50 and short_percent_of_float >= 15) with a
    real weighted score built entirely from data this app already fetches — no new data source
    needed for the free-tier score. `short_shares_available`/`fee_rate`/`short_interest`
    (Unusual Whales-only, MPE-07) are optional real enrichment layered on top when a
    subscription is active; the score degrades gracefully to the free-tier components alone
    when they're absent.

    Every threshold below is a REAL, cited number from this codebase's own established
    conventions, never invented for this function:
      - short_percent_of_float: the SAME 15% floor check_short_squeeze_alerts() already gates
        alerts on (_SQUEEZE_MIN_SHORT_FLOAT, scheduler.py) — scored 0 below 5%, ramping to a
        full 40 points at >=30% (roughly 2x the alert's own floor, matching this app's own
        "critical" framing pattern elsewhere, e.g. _SQUEEZE_CRITICAL_DAYS_TO_COVER being ~half
        the p25 days-to-cover reading).
      - days_to_cover: the REAL, live-calibrated percentiles from _SQUEEZE_CRITICAL_DAYS_TO_
        COVER's own 2026-08-13 production analysis (scheduler.py: p10=1.13, p25=1.92, p50=4.65
        among candidates that already clear the short-float floor) — LOWER is more acute (shorts
        can't exit quietly), so this component scores INVERSELY: full 30 points at or below the
        real p10, tapering to 0 at/above the real p50.
      - momentum_score (K-Score's own 0-100 momentum component, already computed by
        ranking-engine) — scored 0-20, a straight 1:5 scale of the already-0-100 input.
      - change_pct (today's real move) — scored 0-10, capped at a 10% move (a squeeze is
        already well underway by 10%; further upside doesn't need more score weight here).

    Returns None (never a fabricated 0) when short_percent_of_float itself is missing — the one
    genuinely load-bearing input; every other component degrades to 0 contribution when absent,
    since "unknown momentum" and "definitely zero momentum" are meaningfully different, but a
    squeeze score with NO short-interest data at all isn't measuring a squeeze at any confidence.
    """
    if short_percent_of_float is None:
        return None

    spf_pts = min(40.0, max(0.0, (short_percent_of_float - 5.0) / (30.0 - 5.0) * 40.0))

    if days_to_cover is None:
        dtc_pts = 0.0
    else:
        dtc_pts = min(30.0, max(0.0, (4.65 - days_to_cover) / (4.65 - 1.13) * 30.0))

    mom_pts = 0.0 if momentum_score is None else min(20.0, max(0.0, momentum_score / 100.0 * 20.0))

    chg_pts = 0.0 if change_pct is None else min(10.0, max(0.0, change_pct / 10.0 * 10.0))

    score = round(spf_pts + dtc_pts + mom_pts + chg_pts, 1)

    components = {
        "short_float_pts": round(spf_pts, 1),
        "days_to_cover_pts": round(dtc_pts, 1),
        "momentum_pts": round(mom_pts, 1),
        "change_pct_pts": round(chg_pts, 1),
    }

    has_uw_enrichment = False

    # MPE-07: Unusual Whales real short-shares-available / borrow-fee-rate enrichment, when a
    # subscription is active — a genuinely richer, faster-updating read of "can shorts actually
    # exit," but never required for the score itself (both default to None, contributing 0).
    uw_pts = 0.0
    if fee_rate is not None:
        # A high real borrow fee (shorts paying a premium to stay short) is itself squeeze
        # pressure — capped at 5 points so this optional enrichment can meaningfully nudge but
        # never dominate the free-tier score above. 20%+ annualized fee is a genuinely extreme
        # real-world reading (typical borrow fees on a liquid name are well under 1%).
        uw_pts = min(5.0, max(0.0, fee_rate / 20.0 * 5.0))
        components["uw_borrow_fee_pts"] = round(uw_pts, 1)
        score = round(score + uw_pts, 1)
        has_uw_enrichment = True

    # MPE-07: real short-interest UTILIZATION (shares_short / short_shares_available) — the
    # doc's own §1 ask, distinct from short_percent_of_float (which measures short interest
    # against the whole FLOAT, not against how many shares are actually left to borrow). A
    # squeeze with 30% short float but abundant shares still available to borrow is much less
    # acute than one where shares are nearly exhausted — utilization captures exactly that,
    # which short_percent_of_float alone cannot. Both inputs MUST come from the SAME source
    # (UW's own paired short_interest/short_shares_available fields) — never short_interest
    # from one provider divided by short_shares_available from a different one, which could
    # silently disagree on reporting date/methodology and produce a misleadingly-precise but
    # wrong ratio. Capped at 5 points, matching the borrow-fee component's own weight — >=90%
    # utilization is a genuinely extreme real-world reading (most liquid names sit well under
    # 50%), scored 0 below a 50% floor since moderate utilization isn't itself acute pressure.
    if short_interest is not None and short_shares_available is not None and short_shares_available > 0:
        utilization_pct = min(100.0, max(0.0, short_interest / short_shares_available * 100.0))
        util_pts = min(5.0, max(0.0, (utilization_pct - 50.0) / (90.0 - 50.0) * 5.0))
        components["uw_utilization_pts"] = round(util_pts, 1)
        components["uw_utilization_pct"] = round(utilization_pct, 1)
        score = round(score + util_pts, 1)
        has_uw_enrichment = True

    final_score = min(100.0, score)
    return {
        "score": final_score,
        "components": components,
        "covering_pressure": _short_covering_pressure(final_score, has_uw_enrichment),
    }


@router.get("/{symbol}/options-chain")
def get_options_chain(symbol: str, expiry: str | None = None):
    """T230-DATA-OPTIONS-CHAIN: full strike/expiry matrix for one expiration date.

    CORRECTION vs. this tracker item's original claim: no paid Polygon.io tier is needed —
    get_options_flow() (above) already calls yfinance's t.option_chain(exp) and fetches the
    FULL calls/puts DataFrames (strike, bid, ask, volume, openInterest, impliedVolatility,
    inTheMoney) for the nearest 4 expiries, then throws almost all of it away down to a
    top-3-per-side "unusual activity" summary. This endpoint is a second, independent fetch
    (not a shared cache with get_options_flow — a different `expiry` param means a different
    yfinance call) that returns every strike for ONE expiry, both sides, unfiltered.

    `expiry` defaults to the nearest listed expiration when omitted. Returns the full list of
    available expiries either way, so a caller can build an expiry picker without a second
    round-trip.
    """
    sym = symbol.upper()
    try:
        t = yf.Ticker(sym)
        # AUD265-GAMMA-ASSUMES-SORTED-EXPIRIES: sorted() makes "the nearest expiry" default
        # structural rather than dependent on yfinance's own (undocumented) ordering of
        # t.options.
        expiries = sorted(t.options)
        if not expiries:
            return {"symbol": sym, "available": False, "reason": "no_options_listed"}

        exp = expiry if expiry in expiries else expiries[0]
        cache_key = f"options_chain:{sym}:{exp}"
        try:
            rdb = _get_redis()
            cached = rdb.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            rdb = None

        try:
            chain = t.option_chain(exp)
        except Exception as exc:
            log.warning("options_chain.fetch_failed", symbol=sym, expiry=exp, error=str(exc))
            return {"symbol": sym, "available": False, "reason": "fetch_error"}

        _calls_rows = _options_chain_rows(chain.calls)
        _puts_rows = _options_chain_rows(chain.puts)
        result = {
            "symbol":          sym,
            "available":       True,
            "expiry":          exp,
            "expiries":        list(expiries),
            "calls":           _calls_rows,
            "puts":            _puts_rows,
            # IF-05: max pain — needs only strike + OI, both already in the rows above.
            "max_pain":        compute_max_pain(_calls_rows, _puts_rows),
        }

        if rdb is not None:
            try:
                rdb.setex(cache_key, _OPTIONS_CHAIN_TTL, json.dumps(result))
            except Exception:
                pass

        return result

    except Exception as exc:
        log.warning("options_chain.error", symbol=sym, error=str(exc))
        return {"symbol": sym, "available": False, "reason": "fetch_error"}


def compute_expiration_rollup(per_expiry: list[dict]) -> list[dict]:
    """MPE-03: per-expiration open-interest/volume rollup, classified NORMAL/ELEVATED/HIGH/
    EXTREME relative to the OTHER expiries fetched in the same call — not a fabricated
    historical baseline (this app persists no per-expiration OI time series anywhere; the
    closest table, OptionsFlowSnapshot, is a whole-symbol daily aggregate across expiries
    combined, not a per-expiration history), but a real, honest relative-to-peers comparison
    computed fresh from the exact data just fetched.

    `per_expiry` is a list of {"expiry": str, "call_oi": int, "put_oi": int, "call_volume": int,
    "put_volume": int} dicts, one per expiration date (already summed from that expiry's own
    calls/puts DataFrames by the caller). Returns the same rows enriched with `total_oi`,
    `put_call_oi_ratio`, and `concentration_pct` (this expiration's share of the TOTAL open
    interest across every expiry in the input) plus a `level` classification:
      - EXTREME: concentration_pct >= 40% of the total (a single expiration holding nearly half
        or more of all open interest across every fetched date is a real outlier)
      - HIGH: >= 25%
      - ELEVATED: >= 15%
      - NORMAL: below 15% (an unremarkable, evenly-distributed share)
    These thresholds are a straightforward "how far above an even split would be" reference — an
    evenly-distributed 4-expiry rollup would put each at 25%, so ELEVATED (15%) sits meaningfully
    below even-split and EXTREME (40%) sits meaningfully above it, not arbitrary round numbers.

    Returns an empty list (never a divide-by-zero) when total OI across every input row is zero.
    """
    total_oi = sum((r.get("call_oi") or 0) + (r.get("put_oi") or 0) for r in per_expiry)
    if total_oi <= 0:
        return []

    result = []
    for r in per_expiry:
        call_oi = r.get("call_oi") or 0
        put_oi = r.get("put_oi") or 0
        row_total = call_oi + put_oi
        concentration_pct = round(row_total / total_oi * 100, 2)

        if concentration_pct >= 40.0:
            level = "extreme"
        elif concentration_pct >= 25.0:
            level = "high"
        elif concentration_pct >= 15.0:
            level = "elevated"
        else:
            level = "normal"

        result.append({
            "expiry": r.get("expiry"),
            "call_oi": call_oi,
            "put_oi": put_oi,
            "total_oi": row_total,
            "call_volume": r.get("call_volume") or 0,
            "put_volume": r.get("put_volume") or 0,
            "put_call_oi_ratio": round(put_oi / call_oi, 3) if call_oi > 0 else None,
            "concentration_pct": concentration_pct,
            "level": level,
        })
    return result


@router.get("/{symbol}/options-expirations")
def get_options_expirations(symbol: str):
    """MPE-03: per-expiration open-interest/volume rollup across every listed expiration date
    (not just the nearest 4 get_options_flow()/get_options_chain() already fetch), with a
    NORMAL/ELEVATED/HIGH/EXTREME concentration classification per expiry (see
    compute_expiration_rollup()'s own docstring for the exact thresholds and why this is a
    relative-to-peers read, not a fabricated historical-norm comparison — no per-expiration OI
    history is persisted anywhere in this app).

    Bounded to the nearest 6 expiries (not every one yfinance lists, which can run 15-20+ out
    for a liquid large-cap) — the same yfinance rate-limit-fragility concern that already caps
    get_options_flow()/get_options_chain() at 4, widened slightly here since this endpoint's
    whole purpose is the cross-expiry comparison itself, not a single day's detail.
    """
    sym = symbol.upper()
    cache_key = f"options_expirations:{sym}"
    try:
        rdb = _get_redis()
        cached = rdb.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        rdb = None

    try:
        t = yf.Ticker(sym)
        expiries = sorted(t.options)
        if not expiries:
            return {"symbol": sym, "available": False, "reason": "no_options_listed"}

        per_expiry = []
        for exp in expiries[:6]:
            try:
                chain = t.option_chain(exp)
            except Exception:
                continue
            calls = chain.calls.fillna(0)
            puts = chain.puts.fillna(0)
            per_expiry.append({
                "expiry": exp,
                "call_oi": int(calls["openInterest"].sum()),
                "put_oi": int(puts["openInterest"].sum()),
                "call_volume": int(calls["volume"].sum()),
                "put_volume": int(puts["volume"].sum()),
            })

        rollup = compute_expiration_rollup(per_expiry)
        if not rollup:
            return {"symbol": sym, "available": False, "reason": "no_open_interest"}

        result = {"symbol": sym, "available": True, "expirations": rollup}

        if rdb is not None:
            try:
                rdb.setex(cache_key, _OPTIONS_CHAIN_TTL, json.dumps(result))
            except Exception:
                pass

        return result

    except Exception as exc:
        log.warning("options_expirations.error", symbol=sym, error=str(exc))
        return {"symbol": sym, "available": False, "reason": "fetch_error"}


@router.get("/{symbol}/gamma-exposure")
def get_gamma_exposure(symbol: str):
    """MPE-06: real, calculated dealer gamma exposure (GEX) via Unusual Whales — call_wall/
    put_wall (the strikes where dealer gamma concentrates) and gamma_flip (the "zero gamma"
    level where dealer hedging flips direction), when a real subscription is configured and
    enabled (see Settings → Market Pressure Data).

    Deliberately NOT a replacement for check_gamma_unwind_alerts()'s existing free OI-
    concentration proxy (scheduler.py) — that mechanism's own docstring already discloses it is
    NOT a real GEX calculation, just a strike-concentration heuristic computed from yfinance's
    open interest. This endpoint is the genuine article when available; `source` in the
    response tells the caller which one it's looking at, so a frontend can render an honest
    "real GEX" vs. "free proxy" distinction rather than silently presenting one as the other.

    Falls back to `available: False, source: "none"` (never a fabricated GEX value) when
    Unusual Whales is disabled/unconfigured or the symbol has no real GEX data — a caller
    should treat that the same as "keep using the existing squeeze/gamma alert family's own
    free proxy," not as an error.

    AUD-MAXPAIN: also includes real max_pain (per-expiry, the strike where option WRITERS in
    aggregate lose the least at expiry — a distinct concept from GEX's own dealer-hedging-
    pressure walls above) and oi_per_strike (the raw call/put open-interest distribution across
    strikes, which GEX's gamma-WEIGHTED walls only imply indirectly). Both independently
    fail-open to an empty list — a max-pain/OI fetch failure never blocks the GEX fields above
    from still being returned.

    AUD-NOPE: also includes a live nope reading — real, delta-weighted directional options
    pressure, genuinely different from this app's own homegrown compute_options_pressure_score()
    (premium/volume-ratio based, not delta-weighted). Deliberately fetched fresh on every call
    (60s cache, not the 15-min TTL the other UW fields on this route use) since NOPE is
    published per-MINUTE by UW — the only field this app consumes at that cadence. Fails open to
    null, same as every other optional UW enrichment here.
    """
    from ..services import unusual_whales as _uw

    sym = symbol.upper()
    if not _uw.is_available():
        return {"symbol": sym, "available": False, "source": "none", "reason": "unusual_whales_disabled"}

    levels = _uw.get_gex_levels(sym)
    if levels is None:
        return {"symbol": sym, "available": False, "source": "none", "reason": "no_data"}

    max_pain_rows = _uw.get_max_pain(sym)
    oi_rows = _uw.get_oi_per_strike(sym)
    nope = _uw.get_nope(sym)

    return {
        "symbol": sym,
        "available": True,
        "source": "unusual_whales",
        "call_wall": levels.call_wall,
        "put_wall": levels.put_wall,
        "gamma_flip": levels.gamma_flip,
        "gamma_magnet": levels.gamma_magnet,
        "as_of_date": levels.as_of_date,
        "max_pain": [
            {"expiry": r.expiry, "max_pain": r.max_pain} for r in max_pain_rows if r.max_pain is not None
        ],
        "oi_per_strike": [
            {"strike": r.strike, "call_oi": r.call_oi, "put_oi": r.put_oi}
            for r in oi_rows if r.strike is not None
        ],
        "nope": (
            {
                "nope": nope.nope, "nope_fill": nope.nope_fill,
                "call_delta": nope.call_delta, "put_delta": nope.put_delta,
                "call_vol": nope.call_vol, "put_vol": nope.put_vol,
                "stock_vol": nope.stock_vol, "timestamp": nope.timestamp,
            } if nope is not None and nope.nope is not None else None
        ),
    }


@router.get("/{symbol}/dark-pool-prints")
def get_dark_pool_prints_route(symbol: str):
    """T323-DARKPOOL: real recent off-exchange block trades via Unusual Whales' own
    `/api/darkpool/{ticker}` — genuinely new capability, not previously built anywhere in this
    app (unlike gamma-exposure/short-interest, there is no free-proxy fallback for this). See
    DarkPoolPrint's own model docstring (shared/db/models.py) for what a dark pool actually is.

    Falls back to `available: False, source: "none"` (never fabricated data) when Unusual
    Whales is disabled/unconfigured or the symbol has no real recent prints — a caller
    (MarketPressurePanel's dark-pool card) should render nothing in that case, matching every
    other optional-enrichment card's own contract.
    """
    from ..services import unusual_whales as _uw

    sym = symbol.upper()
    if not _uw.is_available():
        return {"symbol": sym, "available": False, "source": "none", "reason": "unusual_whales_disabled", "prints": []}

    rows = _uw.get_dark_pool_prints(sym)
    if not rows:
        return {"symbol": sym, "available": False, "source": "none", "reason": "no_data", "prints": []}

    return {
        "symbol": sym,
        "available": True,
        "source": "unusual_whales",
        "prints": [
            {
                "price": r.price, "size": r.size, "premium": r.premium,
                "venue": r.venue, "executed_at": r.executed_at,
            }
            for r in rows
        ],
    }


# ── T324-OPTIONSFLOW-TAB: Options Flow nav tab — screener/flow-scanner/net-flow/cached views ──

@router.get("/options-screener")
def get_options_screener_route(
    option_type: str | None = Query(None, description='"Calls" or "Puts", omit for both'),
    min_premium: float = Query(100_000, ge=0),
    max_dte: int = Query(45, ge=0, le=365),
    is_otm: bool | None = Query(None),
    min_volume: int | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """T324-OPTIONSFLOW-TAB: real, universe-wide options screener via Unusual Whales' own
    `/api/screener/option-contracts` — see get_options_screener()'s own docstring
    (unusual_whales.py) for the exact real params this passes through. Genuinely new capability:
    scans the WHOLE options-eligible universe by unusual-activity criteria, not a per-symbol
    lookup like every other options endpoint in this file.

    Falls back to `available: False` (never fabricated data) when Unusual Whales is disabled/
    unconfigured — no free-proxy equivalent exists for a universe-wide screen.
    """
    from ..services import unusual_whales as _uw

    if not _uw.is_available():
        return {"available": False, "reason": "unusual_whales_disabled", "rows": []}

    rows = _uw.get_options_screener(
        option_type=option_type, min_premium=min_premium, max_dte=max_dte,
        is_otm=is_otm, min_volume=min_volume, limit=limit,
    )
    return {
        "available": True,
        "rows": [
            {
                "ticker": r.ticker, "option_symbol": r.option_symbol, "option_type": r.option_type,
                "strike": r.strike, "expiry": r.expiry, "volume": r.volume,
                "open_interest": r.open_interest, "premium": r.premium,
                "implied_volatility": r.implied_volatility,
            }
            for r in rows
        ],
    }


@router.get("/option-trades")
def get_option_trades_route(
    max_dte: int | None = Query(None, ge=0, le=365, description="e.g. 0 for 0DTE"),
    is_multi_leg: bool | None = Query(None),
    min_premium: float = Query(50_000, ge=0),
    min_volume: int | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """T324-OPTIONSFLOW-TAB: real raw options-tape prints via Unusual Whales' own
    `/api/option-trades` — the single shared endpoint behind this app's 0DTE Flow
    (`max_dte=0`), Multi-leg Flow (`is_multi_leg=true`), and Interval Flow (no extra filter)
    frontend views. See get_option_trades()'s own docstring (unusual_whales.py) for why this is
    deliberately ONE client method + route rather than three near-duplicate copies.
    """
    from ..services import unusual_whales as _uw

    if not _uw.is_available():
        return {"available": False, "reason": "unusual_whales_disabled", "rows": []}

    rows = _uw.get_option_trades(
        max_dte=max_dte, is_multi_leg=is_multi_leg, min_premium=min_premium,
        min_volume=min_volume, limit=limit,
    )
    return {
        "available": True,
        "rows": [
            {
                "ticker": r.ticker, "option_symbol": r.option_symbol, "option_type": r.option_type,
                "strike": r.strike, "expiry": r.expiry, "price": r.price, "size": r.size,
                "premium": r.premium, "is_multi_leg": r.is_multi_leg, "volume": r.volume,
                "open_interest": r.open_interest, "executed_at": r.executed_at,
            }
            for r in rows
        ],
    }


@router.get("/market-tide")
def get_market_tide_route(interval_5m: bool = Query(False)):
    """T324-OPTIONSFLOW-TAB: real market-WIDE net call/put options premium over time via
    Unusual Whales' own `/api/market/market-tide` — this app's Net Flow page. See
    get_market_tide()'s own docstring (unusual_whales.py) for why this is built off market-tide
    rather than the per-symbol, undocumented-shape net-prem-ticks endpoint.
    """
    from ..services import unusual_whales as _uw

    if not _uw.is_available():
        return {"available": False, "reason": "unusual_whales_disabled", "rows": []}

    rows = _uw.get_market_tide(interval_5m=interval_5m)
    return {
        "available": True,
        "rows": [
            {"timestamp": r.timestamp, "net_call_premium": r.net_call_premium, "net_put_premium": r.net_put_premium}
            for r in rows
        ],
    }


@router.get("/options-flow-alerts-recent")
def get_options_flow_alerts_recent_route(
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    """T324-OPTIONSFLOW-TAB: recent flow-alert candidates for a regular (non-admin) user's new
    Options Flow tab, reusing the SAME OptionsFlowAlertOutcome query shape admin.py's
    options_flow_alert_performance() already established (not a second, independently-drifting
    query) — every candidate check_options_flow_alerts() has recorded, not just the ones an
    email was actually sent for.

    Deliberately reads from the existing DB table populated by check_options_flow_alerts()'s
    own scheduled job — NOT a fresh on-demand Unusual Whales call — per this feature's own
    design constraint (bounded API cost). This means results are scoped to whatever that job's
    own bounded symbol universe (_bounded_options_flow_symbols(): PriceAlert-subscribed symbols
    + top-K by K-Score) has scanned in its most recent cycles, not a free-text "any ticker"
    search — an honest, disclosed limitation (`scope` field below), not hidden from the caller.
    """
    from db import OptionsFlowAlertOutcome

    rows = session.execute(
        select(OptionsFlowAlertOutcome, Stock.symbol)
        .join(Stock, OptionsFlowAlertOutcome.stock_id == Stock.id)
        .order_by(OptionsFlowAlertOutcome.fired_date.desc(), OptionsFlowAlertOutcome.fired_at.desc())
        .limit(limit)
    ).all()
    return {
        "scope": "price_alert_subscribed_and_top_k_symbols",
        "alerts": [
            {
                "symbol": symbol, "option_chain": row.option_chain, "option_type": row.option_type,
                "direction": row.direction, "strike": row.strike,
                "expiry": row.expiry.isoformat() if row.expiry else None,
                "fired_date": row.fired_date.isoformat(), "fired_at": row.fired_at.isoformat() if row.fired_at else None,
                "alert_price": row.alert_price, "total_premium": row.total_premium,
                "ask_side_dominant": row.ask_side_dominant, "has_sweep": row.has_sweep,
                "volume_oi_ratio": row.volume_oi_ratio, "alert_rule": row.alert_rule,
            }
            for row, symbol in rows
        ],
    }


@router.get("/dark-pool-alerts-recent")
def get_dark_pool_alerts_recent_route(
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    """T324-OPTIONSFLOW-TAB: recent dark-pool alert candidates for the new Options Flow tab,
    matching get_options_flow_alerts_recent_route()'s own exact cached-view contract (reads
    DarkPoolAlertOutcome, populated by check_dark_pool_alerts()'s scheduled job, same bounded
    symbol scope, same honest `scope` field).
    """
    from db import DarkPoolAlertOutcome

    rows = session.execute(
        select(DarkPoolAlertOutcome, Stock.symbol)
        .join(Stock, DarkPoolAlertOutcome.stock_id == Stock.id)
        .order_by(DarkPoolAlertOutcome.fired_date.desc(), DarkPoolAlertOutcome.fired_at.desc())
        .limit(limit)
    ).all()
    return {
        "scope": "price_alert_subscribed_and_top_k_symbols",
        "alerts": [
            {
                "symbol": symbol, "fired_date": row.fired_date.isoformat(),
                "fired_at": row.fired_at.isoformat() if row.fired_at else None,
                "alert_price": row.alert_price, "premium": row.qualifying_metric,
            }
            for row, symbol in rows
        ],
    }


# ── T322-OPTIONS-GAMEPLAN: AI Signal + Options Game Plan ─────────────────────

_OPTIONS_GAME_PLAN_MIN_PUT_DTE = 25   # protective puts need enough runway to be worth the premium
_OPTIONS_GAME_PLAN_MAX_PUT_DTE = 60   # beyond ~2 months, premium decay cost rises without much extra protection
_OPTIONS_GAME_PLAN_MIN_CALL_DTE = 14  # covered calls can be shorter-dated — income, not insurance
_OPTIONS_GAME_PLAN_MAX_CALL_DTE = 45


def _nearest_expiry_in_dte_window(expiries: list[str], today: date, min_dte: int, max_dte: int) -> str | None:
    """Picks the expiry whose days-to-expiry falls closest to the CENTER of [min_dte, max_dte]
    among those actually inside the window — never just "closest to min_dte", which would bias
    toward the cheapest/least-protective contract available. Falls back to the single nearest
    expiry to the window's center if NONE fall inside it (a real gap in what's listed, e.g. a
    thinly-traded name with only weekly/far-dated expiries) rather than returning nothing —
    the caller can still show a real contract, just outside the "ideal" window, and the
    response says so explicitly via `in_target_window`.
    """
    if not expiries:
        return None
    center = (min_dte + max_dte) / 2.0
    in_window = []
    all_scored = []
    for exp in expiries:
        try:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if dte < 0:
            continue
        dist = abs(dte - center)
        all_scored.append((dist, exp))
        if min_dte <= dte <= max_dte:
            in_window.append((dist, exp))
    pool = in_window or all_scored
    if not pool:
        return None
    return min(pool, key=lambda x: x[0])[1]


def _nearest_strike(rows: list[dict], target: float) -> dict | None:
    """The listed strike closest to `target` — real strikes are discrete (e.g. $2.50/$5 apart
    for a large-cap), so "the stop-loss price" itself is essentially never a listed strike."""
    if not rows or target is None:
        return None
    return min(rows, key=lambda r: abs(r["strike"] - target))


def compute_options_game_plan(
    *,
    current_price: float,
    stop_loss: float | None,
    take_profit: float | None,
    signal: str | None,
    put_expiries: list[str],
    put_rows: list[dict],
    call_expiries: list[str],
    call_rows: list[dict],
    shares: float | None = None,
    today: date | None = None,
) -> dict:
    """MPE / T322-OPTIONS-GAMEPLAN: composes a real protective-put hedge (against `stop_loss`)
    and a real covered-call income leg (against `take_profit`) from an ALREADY-FETCHED options
    chain — never re-derives entry/stop/target itself, those come from the SAME Position Sizer/
    AI Signal numbers already shown elsewhere on the stock page (nearest support, ATR stop,
    analyst target), passed in by the caller. This function is pure — no DB/HTTP access — so it
    can be tested directly against a synthetic chain without a live yfinance/UW call.

    Deliberately does NOT recommend a strategy the user didn't ask about: a protective put only
    makes sense for someone who owns (or plans to buy) the stock and wants downside insurance; a
    covered call only makes sense for someone already holding shares willing to cap their
    upside at the target. Both legs are computed and returned independently whenever the
    underlying stop_loss/take_profit values exist — the frontend decides which to show/hint at
    based on the user's own stated position, not this function.

    Neither leg is a recommendation to buy/sell RIGHT NOW — both report REAL, currently-listed
    contract prices as of this call, framed as "here is what insuring/collecting income against
    your plan would currently cost," matching this app's own established options-honesty
    convention (max-pain, GEX, squeeze alerts all explicitly disclaim prediction).
    """
    today = today or datetime.now(timezone.utc).date()
    result: dict = {"protective_put": None, "covered_call": None}

    if stop_loss and stop_loss > 0 and put_rows and put_expiries:
        put_exp = _nearest_expiry_in_dte_window(
            put_expiries, today, _OPTIONS_GAME_PLAN_MIN_PUT_DTE, _OPTIONS_GAME_PLAN_MAX_PUT_DTE
        )
        if put_exp:
            contract = _nearest_strike(put_rows, stop_loss)
            if contract:
                mid = (contract["bid"] + contract["ask"]) / 2.0 if (contract["bid"] or contract["ask"]) else contract["last_price"]
                dte = (datetime.strptime(put_exp, "%Y-%m-%d").date() - today).days
                cost_pct = round(mid / current_price * 100, 2) if current_price > 0 else None
                effective_floor = round(contract["strike"] - mid, 2)
                result["protective_put"] = {
                    "expiry": put_exp,
                    "days_to_expiry": dte,
                    "in_target_window": _OPTIONS_GAME_PLAN_MIN_PUT_DTE <= dte <= _OPTIONS_GAME_PLAN_MAX_PUT_DTE,
                    "strike": contract["strike"],
                    "mid_price": round(mid, 2),
                    "cost_pct_of_position": cost_pct,
                    "cost_per_contract": round(mid * 100, 2),
                    "effective_floor_price": effective_floor,
                    "iv": contract["iv"],
                    "oi": contract["oi"],
                    "reference_stop_loss": stop_loss,
                }

    if take_profit and take_profit > 0 and call_rows and call_expiries:
        call_exp = _nearest_expiry_in_dte_window(
            call_expiries, today, _OPTIONS_GAME_PLAN_MIN_CALL_DTE, _OPTIONS_GAME_PLAN_MAX_CALL_DTE
        )
        if call_exp:
            contract = _nearest_strike(call_rows, take_profit)
            if contract:
                mid = (contract["bid"] + contract["ask"]) / 2.0 if (contract["bid"] or contract["ask"]) else contract["last_price"]
                dte = (datetime.strptime(call_exp, "%Y-%m-%d").date() - today).days
                credit_pct = round(mid / current_price * 100, 2) if current_price > 0 else None
                result["covered_call"] = {
                    "expiry": call_exp,
                    "days_to_expiry": dte,
                    "in_target_window": _OPTIONS_GAME_PLAN_MIN_CALL_DTE <= dte <= _OPTIONS_GAME_PLAN_MAX_CALL_DTE,
                    "strike": contract["strike"],
                    "mid_price": round(mid, 2),
                    "credit_pct_of_position": credit_pct,
                    "credit_per_contract": round(mid * 100, 2),
                    "effective_cap_price": round(contract["strike"] + mid, 2),
                    "iv": contract["iv"],
                    "oi": contract["oi"],
                    "reference_take_profit": take_profit,
                }

    result["signal"] = signal
    result["current_price"] = current_price
    result["shares"] = shares
    return result


@router.get("/{symbol}/options-game-plan")
def get_options_game_plan(
    symbol: str,
    stop_loss: float | None = Query(None),
    take_profit: float | None = Query(None),
    shares: float | None = Query(None),
    session: Session = Depends(get_session),
    _user=Depends(get_advanced_user),
):
    """T322-OPTIONS-GAMEPLAN: Advanced-tier-only. Composes compute_options_game_plan() above
    from a REAL, freshly-fetched options chain (calls for the covered-call leg, puts for the
    protective-put leg — two separate t.option_chain() calls at two independently-chosen
    expiries, since the ideal DTE window differs between the two legs). `stop_loss`/
    `take_profit` are passed in by the frontend, sourced from the SAME nearest-support/ATR-stop/
    analyst-target numbers PositionSizer already computes on the stock page — this endpoint
    never re-derives them, avoiding a second, possibly-drifting copy of that logic.

    Gated behind get_advanced_user (T322-FEATURE-TIERING) — an admin or Advanced-tier user only.
    """
    sym = symbol.upper()
    try:
        t = yf.Ticker(sym)
        expiries = sorted(t.options)
        if not expiries:
            return {"symbol": sym, "available": False, "reason": "no_options_listed"}

        current_price = _goal_current_price(session, sym)
        if current_price is None:
            hist = t.history(period="1d")
            current_price = float(hist["Close"].iloc[-1]) if not hist.empty else None
        if not current_price:
            return {"symbol": sym, "available": False, "reason": "no_price"}

        today = datetime.now(timezone.utc).date()
        put_exp = _nearest_expiry_in_dte_window(
            expiries, today, _OPTIONS_GAME_PLAN_MIN_PUT_DTE, _OPTIONS_GAME_PLAN_MAX_PUT_DTE
        )
        call_exp = _nearest_expiry_in_dte_window(
            expiries, today, _OPTIONS_GAME_PLAN_MIN_CALL_DTE, _OPTIONS_GAME_PLAN_MAX_CALL_DTE
        )

        put_rows: list[dict] = []
        call_rows: list[dict] = []
        try:
            if put_exp:
                put_rows = _options_chain_rows(t.option_chain(put_exp).puts)
        except Exception as exc:
            log.warning("options_game_plan.put_fetch_failed", symbol=sym, expiry=put_exp, error=str(exc))
        try:
            if call_exp == put_exp:
                call_rows = _options_chain_rows(t.option_chain(call_exp).calls) if call_exp else []
            elif call_exp:
                call_rows = _options_chain_rows(t.option_chain(call_exp).calls)
        except Exception as exc:
            log.warning("options_game_plan.call_fetch_failed", symbol=sym, expiry=call_exp, error=str(exc))

        plan = compute_options_game_plan(
            current_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            signal=None,
            put_expiries=[put_exp] if put_exp else [],
            put_rows=put_rows,
            call_expiries=[call_exp] if call_exp else [],
            call_rows=call_rows,
            shares=shares,
            today=today,
        )
        plan["symbol"] = sym
        plan["available"] = bool(plan["protective_put"] or plan["covered_call"])
        return plan

    except Exception as exc:
        log.warning("options_game_plan.error", symbol=sym, error=str(exc))
        return {"symbol": sym, "available": False, "reason": "fetch_error"}


@router.get("/options-game-plan/batch")
def get_options_game_plan_batch(
    symbols: str = Query(..., description="Comma-separated symbols, e.g. AAPL,MSFT,NVDA"),
    session: Session = Depends(get_session),
    _user=Depends(get_advanced_user),
):
    """AUD-OPTIONS4-GAMEPLANBATCH: bulk read of already-computed OptionsGamePlanSnapshot rows —
    for a scan-list/signals-table row showing many BUY-signal symbols at once, one batch call
    here replaces what would otherwise be N individual live yfinance fetches (the rate-limit-
    amplification shape docs/incidents/yfinance-rate-limit-amplification.md already warns
    against). Reads the most recent snapshot per symbol — computed daily by
    compute_options_game_plan_snapshots_eod() (scheduler.py) — rather than fetching live.

    Advanced-tier-gated (T322-FEATURE-TIERING), matching the interactive /{symbol}/options-
    game-plan route above. A symbol with no snapshot yet (outside the bounded EOD symbol set,
    or the job hasn't run since it became a BUY candidate) returns available: False, reason:
    "no_snapshot" — never a fabricated plan.

    Also surfaces expected_move_pct/expected_move_dte (AUD-DECIDE4-EXPECTEDMOVE) and iv_rank_1y
    — both from the SAME daily Unusual Whales IV read the snapshot job already makes, no extra
    fetch. iv_rank_1y is the "IV Rank" concept (0-100, where today's IV sits within this
    symbol's own trailing 1-year range) — a complementary read to expected_move_pct: the
    latter says how far the market expects the stock to move, iv_rank_1y says whether that IV
    reading is cheap or expensive relative to this symbol's own history. Either/both may be
    None when Unusual Whales was unavailable/had no data for this symbol on the snapshot's own
    as_of date.

    Also surfaces real per-contract Greeks (AUD-GREEKS) nested inside protective_put/
    covered_call — delta/gamma/theta/vega/vanna/charm for the EXACT strike/expiry each leg
    already selected, from the same Unusual Whales get_greeks() call the snapshot job makes.
    Closes a gap this app's own Options Trading Guide explicitly documents ("no real
    per-contract Greeks beyond implied volatility are shown"). None when Unusual Whales had no
    Greeks data for this specific strike/expiry.
    """
    from .options_game_plan_snapshot import get_latest_options_game_plan

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        return {"results": {}}

    stock_rows = session.execute(
        select(Stock.id, Stock.symbol).where(Stock.symbol.in_(sym_list))
    ).all()
    stock_id_by_symbol = {sym: sid for sid, sym in stock_rows}

    results: dict[str, dict] = {}
    for sym in sym_list:
        stock_id = stock_id_by_symbol.get(sym)
        if stock_id is None:
            results[sym] = {"available": False, "reason": "unknown_symbol"}
            continue
        snap = get_latest_options_game_plan(session, stock_id)
        if snap is None:
            results[sym] = {"available": False, "reason": "no_snapshot"}
            continue
        results[sym] = {
            "available": True,
            "as_of": snap.as_of.isoformat(),
            "underlying_close": snap.underlying_close,
            "stop_loss": snap.stop_loss,
            "take_profit": snap.take_profit,
            "expected_move_pct": snap.expected_move_pct,
            "expected_move_dte": snap.expected_move_dte,
            "iv_rank_1y": snap.iv_rank_1y,
            "protective_put": (
                {
                    "strike": snap.put_strike, "expiry": snap.put_expiry,
                    "mid_price": snap.put_mid_price, "effective_floor_price": snap.put_effective_floor_price,
                    # AUD-GREEKS: real per-contract Greeks for this exact strike/expiry, None
                    # when Unusual Whales was unavailable/had no data — never fabricated.
                    "delta": snap.put_delta, "gamma": snap.put_gamma, "theta": snap.put_theta,
                    "vega": snap.put_vega, "vanna": snap.put_vanna, "charm": snap.put_charm,
                } if snap.put_strike is not None else None
            ),
            "covered_call": (
                {
                    "strike": snap.call_strike, "expiry": snap.call_expiry,
                    "mid_price": snap.call_mid_price, "effective_cap_price": snap.call_effective_cap_price,
                    "delta": snap.call_delta, "gamma": snap.call_gamma, "theta": snap.call_theta,
                    "vega": snap.call_vega, "vanna": snap.call_vanna, "charm": snap.call_charm,
                } if snap.call_strike is not None else None
            ),
        }
    return {"results": results}


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


@router.get("/hk-connect-flow/leaderboard/top")
def hk_connect_flow_leaderboard(
    days: int = Query(5, ge=1, le=90),
    limit: int = Query(20, ge=5, le=50),
    session: Session = Depends(get_session),
):
    """T255-REPORTS-TAB: Top-N HK stocks by net Stock-Connect southbound buying over the
    last `days`. Intentionally public (no auth), matching the per-symbol endpoint above.

    Registered under a literal /leaderboard/top sub-path (not a bare /{symbol}) so it can
    never be shadowed by — or shadow — the per-symbol route above.
    """
    from ..services.hk_connect import get_flow_leaderboard
    return get_flow_leaderboard(session, days=days, limit=limit)


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
