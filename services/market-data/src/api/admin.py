"""Admin endpoints: trigger ingestion + seed universe + add individual stock."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.orm import Session
import json
import yfinance as yf
import redis as redis_lib

from common.config import get_settings
from common.logging import get_logger
from db import Exchange, Market, SessionLocal, Stock, Signal, SignalOutcome, init_db, get_session

from ..adapters.registry import set_runtime_key
from ..services.ingestion import ingest_symbol, ingest_universe
from ..services.seed_universe import seed
from .auth import User, get_admin_user

router = APIRouter(prefix="/admin", tags=["admin"])
log = get_logger("admin")
_settings = get_settings()


def _trigger_new_stock_refresh(symbol: str, market: str) -> None:
    """ALERT-F2: close the gap where a newly-added stock has no K-Score until the next
    scheduled 5x/day (or weekly) rankings refresh — the conviction gate hard-blocks alerts
    on missing K-Score, so SNDK-style spin-offs got silently gated out for hours/days.

    Registered as a SECOND BackgroundTasks.add_task() after ingest_symbol — FastAPI runs
    background tasks sequentially in registration order, so this only fires once ingestion
    (price history backfill) has actually completed, not concurrently with it.
    Scoped to just this stock's market (not a full-universe refresh) since only one new
    stock needs picking up — matches the existing per-market refresh pattern already used
    by _weekly_full_refresh in scheduler.py.
    """
    import httpx
    from ..services.scheduler import _service_token
    try:
        tok = _service_token()
        headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        httpx.post(f"{_settings.ranking_engine_url}/rankings/refresh", params={"market": market}, headers=headers, timeout=10)
        httpx.post(f"{_settings.signal_engine_url}/signals/refresh", params={"market": market}, headers=headers, timeout=10)
        log.info("add_stock.refresh_triggered", symbol=symbol, market=market)
    except Exception as exc:
        log.warning("add_stock.refresh_failed", symbol=symbol, market=market, error=str(exc))


_REDIS_CLAUDE_KEY       = "stockai:admin:claude_api_key"
_REDIS_DEEPSEEK_KEY     = "stockai:admin:deepseek_api_key"
_REDIS_CLAUDE_MODEL     = "stockai:admin:claude_model"
_REDIS_DEEPSEEK_MODEL   = "stockai:admin:deepseek_model"
_REDIS_BROKER_ENABLED   = "stockai:admin:feature:broker_enabled"

def _get_redis():
    return redis_lib.from_url(_settings.redis_url, decode_responses=True)

_EXCHANGE_MAP: dict[str, Exchange] = {
    "NMS": Exchange.NASDAQ, "NGM": Exchange.NASDAQ, "NCM": Exchange.NASDAQ,
    "NYQ": Exchange.NYSE,   "NYS": Exchange.NYSE,
    "HKG": Exchange.HKEX,
}

_HK_NAME_ZH: dict[str, str] = {
    "0700.HK": "騰訊控股", "0005.HK": "匯豐控股", "0939.HK": "建設銀行",
    "1299.HK": "友邦保險", "9988.HK": "阿里巴巴", "3690.HK": "美團",
    "0388.HK": "香港交易所", "1810.HK": "小米集團", "0001.HK": "長和",
    "0002.HK": "中電控股", "0003.HK": "香港中華煤氣", "0006.HK": "電能實業",
    "0011.HK": "恒生銀行", "0012.HK": "恒基地產", "0016.HK": "新鴻基地產",
    "0017.HK": "新世界發展", "0019.HK": "太古股份", "0027.HK": "銀河娛樂",
    "0066.HK": "港鐵公司", "0101.HK": "恒隆地產", "0175.HK": "吉利汽車",
    "0241.HK": "阿里健康", "0267.HK": "中信股份", "0288.HK": "萬洲國際",
    "0386.HK": "中國石油化工", "0489.HK": "東風集團", "0669.HK": "創科實業",
    "0688.HK": "中國海外發展", "0762.HK": "中國聯通", "0823.HK": "領展房產基金",
    "0857.HK": "中國石油天然氣", "0883.HK": "中國海洋石油", "0941.HK": "中國移動",
    "1038.HK": "長江基建集團", "1044.HK": "恒安國際", "1093.HK": "石藥集團",
    "1109.HK": "華潤置地", "1113.HK": "長實集團", "1177.HK": "中國生物製藥",
    "1211.HK": "比亞迪", "1288.HK": "農業銀行", "1308.HK": "海豐國際",
    "1398.HK": "工商銀行", "1997.HK": "九龍倉集團", "2007.HK": "碧桂園",
    "2018.HK": "瑞聲科技", "2020.HK": "安踏體育", "2269.HK": "藥明生物",
    "2313.HK": "申洲國際", "2318.HK": "中國平安", "2319.HK": "蒙牛乳業",
    "2328.HK": "中國人保", "2382.HK": "舜宇光學科技", "2388.HK": "中銀香港",
    "2628.HK": "中國人壽", "3328.HK": "交通銀行", "3333.HK": "中國恒大",
    "3988.HK": "中國銀行", "6098.HK": "碧桂園服務", "6862.HK": "海底撈",
    "9618.HK": "京東集團", "9888.HK": "百度", "9999.HK": "網易",
    "0981.HK": "中芯國際", "9961.HK": "攜程集團",
    "6082.HK": "壁仞科技", "6613.HK": "藍思科技",
}


class ConfigRequest(BaseModel):
    polygon_api_key: str | None = None
    alpha_vantage_api_key: str | None = None
    quiver_api_key: str | None = None
    claude_api_key: str | None = None
    deepseek_api_key: str | None = None
    claude_model: str | None = None
    deepseek_model: str | None = None
    broker_enabled: bool | None = None  # feature flag: show/hide broker integration UI


@router.get("/feature-flags")
def get_feature_flags(_: User = Depends(get_admin_user)):
    """Return current feature flag states (admin only)."""
    r = _get_redis()
    return {
        "broker_enabled": r.get(_REDIS_BROKER_ENABLED) == "1",
    }


@router.get("/feature-flags/public")
def get_feature_flags_public():
    """Return feature flags that the frontend needs without auth (e.g. for settings page)."""
    r = _get_redis()
    return {
        "broker_enabled": r.get(_REDIS_BROKER_ENABLED) == "1",
    }


@router.post("/config")
def update_config(req: ConfigRequest, _: User = Depends(get_admin_user)):
    if req.polygon_api_key is not None:
        set_runtime_key("polygon", req.polygon_api_key)
    if req.alpha_vantage_api_key is not None:
        set_runtime_key("alpha_vantage", req.alpha_vantage_api_key)
    if req.quiver_api_key is not None:
        from .congress import set_quiver_key
        set_quiver_key(req.quiver_api_key)
    r = None
    if req.claude_api_key is not None or req.deepseek_api_key is not None or \
       req.claude_model is not None or req.deepseek_model is not None or \
       req.broker_enabled is not None:
        r = _get_redis()
    if req.claude_api_key is not None:
        r.set(_REDIS_CLAUDE_KEY, req.claude_api_key)
    if req.deepseek_api_key is not None:
        r.set(_REDIS_DEEPSEEK_KEY, req.deepseek_api_key)
    if req.claude_model is not None:
        r.set(_REDIS_CLAUDE_MODEL, req.claude_model)
    if req.deepseek_model is not None:
        r.set(_REDIS_DEEPSEEK_MODEL, req.deepseek_model)
    if req.broker_enabled is not None:
        r.set(_REDIS_BROKER_ENABLED, "1" if req.broker_enabled else "0")
    log.info("admin.config_updated", broker_enabled=req.broker_enabled)
    return {"status": "ok"}


class IngestRequest(BaseModel):
    symbols: list[str]
    timeframe: str = "1d"
    force: bool = False


class AddStockRequest(BaseModel):
    symbol: str


@router.post("/seed")
def run_seed(_: User = Depends(get_admin_user)):
    count = seed()
    return {"status": "ok", "inserted": count}


@router.post("/ingest")
def run_ingest(req: IngestRequest, tasks: BackgroundTasks, _: User = Depends(get_admin_user)):
    """Single-symbol: synchronous. Multi-symbol: background task to avoid timeouts."""
    if len(req.symbols) == 1:
        try:
            result = ingest_symbol(req.symbols[0], timeframe=req.timeframe, force=req.force)
            return {"status": "done", "symbols": 1, "result": result}
        except Exception as exc:
            log.error("ingest.symbol_failed", symbol=req.symbols[0], error=str(exc))
            raise HTTPException(500, str(exc))

    def _run():
        try:
            ingest_universe(req.symbols, req.timeframe, force=req.force)
        except Exception as exc:
            log.error("ingest.universe_failed", error=str(exc))

    tasks.add_task(_run)
    return {"status": "queued", "symbols": len(req.symbols), "queued": req.symbols}


@router.delete("/stocks/{symbol}")
def delete_stock(symbol: str, _: User = Depends(get_admin_user)):
    """Soft-delete (deactivate) a stock — sets active=False, preserves price history."""
    sym = symbol.upper().strip()
    with SessionLocal() as session:
        stock = session.execute(select(Stock).where(Stock.symbol == sym)).scalar_one_or_none()
        if not stock:
            raise HTTPException(404, f"Unknown symbol: {sym}")
        stock.active = False
        session.commit()
    log.info("delete_stock.done", symbol=sym)
    return {"status": "deactivated", "symbol": sym}


@router.post("/add_stock")
def add_stock(req: AddStockRequest, tasks: BackgroundTasks, _: User = Depends(get_admin_user)):
    symbol = req.symbol.upper().strip()
    log.info("add_stock.start", symbol=symbol)

    # Check if already in DB
    with SessionLocal() as session:
        existing = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
        if existing:
            tasks.add_task(ingest_symbol, symbol, existing.market.value)
            tasks.add_task(_trigger_new_stock_refresh, symbol, existing.market.value)
            return {"status": "exists", "symbol": symbol, "name": existing.name}

    # Fetch metadata from yfinance
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
    except Exception as exc:
        raise HTTPException(502, f"yfinance error: {exc}")

    name = info.get("longName") or info.get("shortName") or symbol
    if name == symbol and not info:
        raise HTTPException(404, f"Symbol not found: {symbol}")

    sector = info.get("sector")
    industry = info.get("industry")
    currency = info.get("currency", "USD")
    exchange_code = info.get("exchange", "")
    market = Market.HK if symbol.endswith(".HK") else Market.US
    exchange = _EXCHANGE_MAP.get(exchange_code, Exchange.NASDAQ if market == Market.US else Exchange.HKEX)
    name_zh = _HK_NAME_ZH.get(symbol) if market == Market.HK else None

    with SessionLocal() as session:
        stock = Stock(
            symbol=symbol, name=name, name_zh=name_zh, market=market, exchange=exchange,
            sector=sector, industry=industry, currency=currency, active=True,
        )
        session.add(stock)
        session.commit()

    log.info("add_stock.done", symbol=symbol, name=name)
    market_val = "HK" if symbol.endswith(".HK") else "US"
    tasks.add_task(ingest_symbol, symbol, market_val)
    tasks.add_task(_trigger_new_stock_refresh, symbol, market_val)
    return {"status": "added", "symbol": symbol, "name": name, "sector": sector}


# ── SL-1: Admin signal log ────────────────────────────────────────────────────

@router.get("/signal-log")
def admin_signal_log(
    symbol: str | None = Query(None),
    signal_type: str | None = Query(None, description="BUY, SELL, HOLD, WAIT"),
    horizon: str | None = Query(None, description="SHORT, SWING, LONG, GROWTH"),
    days_back: int = Query(90, ge=1, le=365),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """SL-1: Paginated system signal log with outcomes. Admin-only."""
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(days=days_back)

    q = (
        select(Signal, Stock, SignalOutcome)
        .join(Stock, Signal.stock_id == Stock.id)
        .outerjoin(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .where(Signal.ts >= cutoff)
    )

    if symbol:
        q = q.where(Stock.symbol == symbol.upper())
    if signal_type:
        q = q.where(Signal.signal == signal_type.upper())
    if horizon:
        q = q.where(Signal.horizon == horizon.upper())

    q = q.order_by(desc(Signal.ts))

    total = session.execute(
        select(Signal.id)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff)
    ).all()
    total_count = len(total)

    offset = (page - 1) * limit
    rows = session.execute(q.offset(offset).limit(limit)).all()

    results = []
    for sig, stock, outcome in rows:
        results.append({
            "id": sig.id,
            "symbol": stock.symbol,
            "name": stock.name,
            "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
            "signal": sig.signal.value if hasattr(sig.signal, "value") else str(sig.signal),
            "horizon": sig.horizon.value if hasattr(sig.horizon, "value") else str(sig.horizon),
            "confidence": sig.confidence,
            "bullish_probability": sig.bullish_probability,
            "reasons": sig.reasons,
            "source": sig.source,
            "generated_at": sig.ts.isoformat(),
            # Outcome fields (null until hold window closes)
            "outcome_pct": outcome.pct_return if outcome else None,
            "is_correct": outcome.is_correct if outcome else None,
            "entry_price": outcome.entry_price if outcome else None,
            "exit_price": outcome.exit_price if outcome else None,
            "exit_date": outcome.exit_date.isoformat() if (outcome and outcome.exit_date) else None,
        })

    return {
        "total": total_count,
        "page": page,
        "limit": limit,
        "pages": max(1, (total_count + limit - 1) // limit),
        "items": results,
    }


@router.post("/send-morning-digest")
def trigger_morning_digest(
    background_tasks: BackgroundTasks,
    market: str = Query("US", regex="^(US|HK)$"),
    _: User = Depends(get_admin_user),
):
    """Manually trigger the morning digest email for a market (admin only). Runs in background.

    T232-UI2: send_morning_digest(markets: list | None) iterates `for _mkt in markets` — passing
    the bare `market` string here (a leftover from the old two-job design) iterated its
    characters ('U', 'S') instead of treating it as one market, silently producing an empty
    digest. Wrap it in a list.
    """
    from ..services.scheduler import send_morning_digest
    background_tasks.add_task(send_morning_digest, [market])
    return {"status": "queued", "market": market, "message": f"Morning digest [{market}] is being sent to all users with email configured."}


@router.get("/scheduler-status")
def scheduler_status(_: User = Depends(get_admin_user)):
    """Return last-run status for all tracked scheduler jobs (from Redis)."""
    r = _get_redis()
    keys = sorted(r.keys("scheduler:job:*"))
    jobs = []
    for key in keys:
        val = r.get(key)
        if val:
            try:
                jobs.append(json.loads(val))
            except Exception:
                pass
    return {"jobs": jobs}


@router.get("/dq-status")
def data_quality_status(_: User = Depends(get_admin_user)):
    """Return the latest result of each data-quality staleness check (from Redis).

    Distinct from /scheduler-status: that reports whether a JOB ran; this reports
    whether the DATA that job was supposed to produce is actually fresh. See
    run_data_quality_checks() in scheduler.py for why the two can diverge (the
    2026-07-03 rankings incident: the job "ran" and returned 200 for 10+ days while
    silently writing zero rows).
    """
    r = _get_redis()
    keys = sorted(r.keys("dq_check:*"))
    checks = []
    for key in keys:
        if key in ("dq_check:last_alert_ts",):
            continue
        val = r.get(key)
        if val:
            try:
                checks.append(json.loads(val))
            except Exception:
                pass
    return {"checks": checks}


@router.post("/backfill-index-membership")
def backfill_index_membership(
    session: Session = Depends(get_session),
    _: User = Depends(get_admin_user),
):
    """Backfill stocks.index_membership for US stocks in DOW_30, NASDAQ_100, SP500."""
    from .index_members import DOW_30, NASDAQ_100, SP500

    index_map: dict[str, list[str]] = {}
    for sym in DOW_30:
        index_map.setdefault(sym, []).append("DOW_30")
    for sym in NASDAQ_100:
        index_map.setdefault(sym, []).append("NASDAQ_100")
    for sym in SP500:
        index_map.setdefault(sym, []).append("SP500")

    stocks = session.execute(
        select(Stock).where(Stock.active.is_(True), Stock.market == "US")
    ).scalars().all()

    updated = 0
    for stock in stocks:
        indices = index_map.get(stock.symbol, [])
        new_val = ",".join(sorted(set(indices))) if indices else None
        if stock.index_membership != new_val:
            stock.index_membership = new_val
            updated += 1

    session.commit()
    return {"status": "ok", "updated": updated}

