"""Admin endpoints: trigger ingestion + seed universe + add individual stock."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc, func, case, delete
from sqlalchemy.orm import Session
import json
import yfinance as yf
import redis as redis_lib
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from common.config import get_settings
from common.logging import get_logger
from db import (
    Exchange, Market, SessionLocal, Stock, Signal, SignalOutcome, SignalHorizon,
    FundamentalsSnapshot, OptionsFlowAlertOutcome, Price, SqueezeAlertOutcome, TimeFrame,
    Watchlist, WatchlistItem, Ranking, init_db, get_session,
)

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
# CLAUDE-API-COST-AUDIT (2026-07-28): matches scheduler.py's _AUTO_RESEARCH_ENABLED_KEY literal
# exactly — both files hardcode the same string rather than cross-importing a shared constant,
# matching this file's own established convention (see the Alpaca-key comment below).
_REDIS_AUTO_RESEARCH_ENABLED = "stockai:admin:feature:auto_research_enabled"
# T249-EARNINGS-LLM-IMPACT / macro_llm_reaction: matches scheduler.py's own hardcoded literals
# for both (_REDIS_EARNINGS_LLM_ENABLED / _REDIS_MACRO_LLM_ENABLED), same not-cross-imported
# convention as auto_research above. macro_llm_reaction_enabled defaults ON (the feature has
# been live since T249-P2, unlike auto_research/earnings_llm_impact which default OFF as new
# opt-in features) — scheduler.py's own read side treats the ABSENCE of "0" as enabled, so the
# admin-flag read/write helpers below must match that same "unset/1 = on, 0 = off" semantics.
_REDIS_MACRO_LLM_ENABLED = "stockai:admin:feature:macro_llm_reaction_enabled"
_REDIS_EARNINGS_LLM_ENABLED = "stockai:admin:feature:earnings_llm_impact_enabled"
# T270-SECTOR-THEME-FORECAST-EMAIL: matches scheduler.py's own hardcoded
# _REDIS_THEME_FORECAST_ENABLED literal exactly, same not-cross-imported convention as every
# other feature flag above — a brand-new opt-in feature, default OFF like auto_research/
# earnings_llm_impact, NOT the "unset=on" semantics macro_llm_reaction_enabled uses.
_REDIS_THEME_FORECAST_ENABLED = "stockai:admin:feature:theme_forecast_email_enabled"
# T286-TRADE-PATTERN-COACH: matches scheduler.py's own hardcoded _REDIS_TRADE_COACH_ENABLED
# literal exactly, same not-cross-imported convention and default-OFF semantics as
# theme_forecast_email_enabled above.
_REDIS_TRADE_COACH_ENABLED = "stockai:admin:feature:trade_coach_email_enabled"
# AUD-EARNINGSFORECAST: matches event-intelligence's earnings.py own hardcoded
# _REDIS_EARNINGS_FORECAST_ENABLED literal exactly, same not-cross-imported convention and
# default-OFF semantics as every other new opt-in feature above — an on-demand, user-clicked
# LLM call (not a scheduled poll), but still gets the same admin-controlled kill switch as
# every other Claude-calling feature in this app.
_REDIS_EARNINGS_FORECAST_ENABLED = "stockai:admin:feature:earnings_llm_forecast_enabled"
# T258-NEWS-INTELLIGENCE: same admin-configured-credential pattern as the Claude/DeepSeek keys
# above — matches shared/common/ai_keys.py's own _ALPACA_KEY_REDIS/_ALPACA_SECRET_REDIS
# constants exactly (kept as two separate literals here rather than importing them, matching
# this file's existing convention of defining its own Redis key constants rather than
# importing another module's private constants).
_REDIS_ALPACA_KEY       = "stockai:admin:alpaca_api_key"
_REDIS_ALPACA_SECRET    = "stockai:admin:alpaca_secret_key"
# MPE-06/MPE-07: same admin-configured-credential pattern as the Claude/DeepSeek/Alpaca keys
# above — matches shared/common/ai_keys.py's own _UW_KEY_REDIS/_UW_ENABLED_REDIS constants
# exactly, kept as separate literals here rather than importing them, matching this file's
# existing convention. A real, metered, per-request-cost API — default OFF like every other
# new opt-in feature (auto_research/earnings_llm_impact/theme_forecast/trade_coach), never
# the "unset=on" semantics macro_llm_reaction_enabled uses.
_REDIS_UW_KEY           = "stockai:admin:unusual_whales_api_key"
_REDIS_UW_ENABLED       = "stockai:admin:feature:unusual_whales_enabled"

def _get_redis():
    from common.redis_client import get_redis as _get_pool_redis
    return _get_pool_redis()

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
    claude_api_key: str | None = None
    deepseek_api_key: str | None = None
    claude_model: str | None = None
    deepseek_model: str | None = None
    broker_enabled: bool | None = None  # feature flag: show/hide broker integration UI
    # CLAUDE-API-COST-AUDIT (2026-07-28): gates _auto_trigger_research() in scheduler.py —
    # the most expensive Claude-calling feature in the app (full Sonnet report generation),
    # default OFF since it had no opt-in/opt-out anywhere before this fix.
    auto_research_enabled: bool | None = None
    # T249-EARNINGS-LLM-IMPACT: gates the new earnings-impact LLM read (default OFF — a
    # brand-new feature). macro_llm_reaction_enabled gates the existing, already-relied-upon
    # macro reaction feature (default ON — read side treats unset/None as enabled).
    macro_llm_reaction_enabled: bool | None = None
    earnings_llm_impact_enabled: bool | None = None
    # T270-SECTOR-THEME-FORECAST-EMAIL: gates the new weekly theme-signal digest (default OFF,
    # matching every other brand-new opt-in Claude-calling feature since CLAUDE-API-COST-AUDIT).
    theme_forecast_email_enabled: bool | None = None
    # T286-TRADE-PATTERN-COACH: gates the new weekly cross-trade behavioral-pattern digest
    # (default OFF, same new-opt-in-Claude-feature convention as theme_forecast_email_enabled).
    trade_coach_email_enabled: bool | None = None
    # AUD-EARNINGSFORECAST: gates the new on-demand PRE-report forecast (default OFF, same
    # new-opt-in-Claude-feature convention as every other flag above).
    earnings_llm_forecast_enabled: bool | None = None
    # Unshare: deletes the shared server-side key so other users' AI features fall back to
    # their own personal key (or "no AI" if they don't have one) — the inverse of pushing
    # claude_api_key/deepseek_api_key above. Bool, not a key value, since "clear this" is a
    # distinct action from "set this to an empty string" (which would just fail the same as
    # never having been set, without being an explicit/auditable action).
    unshare_claude_key: bool | None = None
    unshare_deepseek_key: bool | None = None
    # T258-NEWS-INTELLIGENCE: Alpaca's real-time news WebSocket needs a key+secret PAIR, not a
    # single token — both must be set together for the news-intelligence service to connect,
    # but each is independently updatable here (e.g. rotating just the secret) matching the
    # same "only touch what's explicitly provided" contract as every other field in this model.
    alpaca_api_key: str | None = None
    alpaca_secret_key: str | None = None
    unshare_alpaca_key: bool | None = None
    # MPE-06/MPE-07: a single bearer token (unlike Alpaca's key+secret pair) — a real, metered,
    # per-request-cost API (Unusual Whales), default OFF like every other new opt-in feature.
    unusual_whales_api_key: str | None = None
    unshare_unusual_whales_key: bool | None = None
    unusual_whales_enabled: bool | None = None


@router.get("/feature-flags")
def get_feature_flags(_: User = Depends(get_admin_user)):
    """Return current feature flag states (admin only)."""
    r = _get_redis()
    return {
        "broker_enabled": r.get(_REDIS_BROKER_ENABLED) == "1",
        "auto_research_enabled": r.get(_REDIS_AUTO_RESEARCH_ENABLED) == "1",
        "macro_llm_reaction_enabled": r.get(_REDIS_MACRO_LLM_ENABLED) != "0",
        "earnings_llm_impact_enabled": r.get(_REDIS_EARNINGS_LLM_ENABLED) == "1",
        "theme_forecast_email_enabled": r.get(_REDIS_THEME_FORECAST_ENABLED) == "1",
        "trade_coach_email_enabled": r.get(_REDIS_TRADE_COACH_ENABLED) == "1",
        "earnings_llm_forecast_enabled": r.get(_REDIS_EARNINGS_FORECAST_ENABLED) == "1",
        "unusual_whales_enabled": r.get(_REDIS_UW_ENABLED) == "1",
        # presence-only signal — never the real secret value — so the Settings page can show
        # "already configured" without re-displaying (or losing on refresh) a saved key.
        "unusual_whales_key_set": bool(r.exists(_REDIS_UW_KEY)),
    }


@router.get("/feature-flags/public")
def get_feature_flags_public():
    """Return feature flags that the frontend needs without auth (e.g. for settings page)."""
    r = _get_redis()
    return {
        "broker_enabled": r.get(_REDIS_BROKER_ENABLED) == "1",
        "auto_research_enabled": r.get(_REDIS_AUTO_RESEARCH_ENABLED) == "1",
        "macro_llm_reaction_enabled": r.get(_REDIS_MACRO_LLM_ENABLED) != "0",
        "earnings_llm_impact_enabled": r.get(_REDIS_EARNINGS_LLM_ENABLED) == "1",
        "theme_forecast_email_enabled": r.get(_REDIS_THEME_FORECAST_ENABLED) == "1",
        "trade_coach_email_enabled": r.get(_REDIS_TRADE_COACH_ENABLED) == "1",
        "earnings_llm_forecast_enabled": r.get(_REDIS_EARNINGS_FORECAST_ENABLED) == "1",
        "unusual_whales_enabled": r.get(_REDIS_UW_ENABLED) == "1",
        "unusual_whales_key_set": bool(r.exists(_REDIS_UW_KEY)),
    }


@router.post("/config")
def update_config(req: ConfigRequest, _: User = Depends(get_admin_user)):
    if req.polygon_api_key is not None:
        set_runtime_key("polygon", req.polygon_api_key)
    if req.alpha_vantage_api_key is not None:
        set_runtime_key("alpha_vantage", req.alpha_vantage_api_key)
    r = None
    if req.claude_api_key is not None or req.deepseek_api_key is not None or \
       req.claude_model is not None or req.deepseek_model is not None or \
       req.broker_enabled is not None or req.unshare_claude_key or req.unshare_deepseek_key or \
       req.alpaca_api_key is not None or req.alpaca_secret_key is not None or req.unshare_alpaca_key or \
       req.auto_research_enabled is not None or req.macro_llm_reaction_enabled is not None or \
       req.earnings_llm_impact_enabled is not None or req.theme_forecast_email_enabled is not None or \
       req.trade_coach_email_enabled is not None or req.earnings_llm_forecast_enabled is not None or \
       req.unusual_whales_api_key is not None or req.unshare_unusual_whales_key or \
       req.unusual_whales_enabled is not None:
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
    if req.auto_research_enabled is not None:
        r.set(_REDIS_AUTO_RESEARCH_ENABLED, "1" if req.auto_research_enabled else "0")
    if req.macro_llm_reaction_enabled is not None:
        r.set(_REDIS_MACRO_LLM_ENABLED, "1" if req.macro_llm_reaction_enabled else "0")
    if req.earnings_llm_impact_enabled is not None:
        r.set(_REDIS_EARNINGS_LLM_ENABLED, "1" if req.earnings_llm_impact_enabled else "0")
    if req.theme_forecast_email_enabled is not None:
        r.set(_REDIS_THEME_FORECAST_ENABLED, "1" if req.theme_forecast_email_enabled else "0")
    if req.trade_coach_email_enabled is not None:
        r.set(_REDIS_TRADE_COACH_ENABLED, "1" if req.trade_coach_email_enabled else "0")
    if req.earnings_llm_forecast_enabled is not None:
        r.set(_REDIS_EARNINGS_FORECAST_ENABLED, "1" if req.earnings_llm_forecast_enabled else "0")
    if req.unshare_claude_key:
        r.delete(_REDIS_CLAUDE_KEY)
    if req.unshare_deepseek_key:
        r.delete(_REDIS_DEEPSEEK_KEY)
    if req.alpaca_api_key is not None:
        r.set(_REDIS_ALPACA_KEY, req.alpaca_api_key)
    if req.alpaca_secret_key is not None:
        r.set(_REDIS_ALPACA_SECRET, req.alpaca_secret_key)
    if req.unshare_alpaca_key:
        r.delete(_REDIS_ALPACA_KEY)
        r.delete(_REDIS_ALPACA_SECRET)
    if req.unusual_whales_api_key is not None:
        r.set(_REDIS_UW_KEY, req.unusual_whales_api_key)
    if req.unshare_unusual_whales_key:
        r.delete(_REDIS_UW_KEY)
    if req.unusual_whales_enabled is not None:
        r.set(_REDIS_UW_ENABLED, "1" if req.unusual_whales_enabled else "0")
    log.info("admin.config_updated", broker_enabled=req.broker_enabled,
              auto_research_enabled=req.auto_research_enabled,
              macro_llm_reaction_enabled=req.macro_llm_reaction_enabled,
              earnings_llm_impact_enabled=req.earnings_llm_impact_enabled,
              theme_forecast_email_enabled=req.theme_forecast_email_enabled,
              trade_coach_email_enabled=req.trade_coach_email_enabled,
              earnings_llm_forecast_enabled=req.earnings_llm_forecast_enabled,
              unshared_claude=bool(req.unshare_claude_key), unshared_deepseek=bool(req.unshare_deepseek_key),
              alpaca_key_set=req.alpaca_api_key is not None, unshared_alpaca=bool(req.unshare_alpaca_key),
              unusual_whales_key_set=req.unusual_whales_api_key is not None,
              unshared_unusual_whales=bool(req.unshare_unusual_whales_key),
              unusual_whales_enabled=req.unusual_whales_enabled)
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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    # BUG-ADDSTOCK-NORETRY: yf.Ticker(symbol).info had zero retry — a single transient
    # YFRateLimitError (Yahoo's own "Too Many Requests", not a real 404) immediately failed
    # the whole add-to-universe flow with no second chance, unlike YFinanceAdapter.fetch_ohlcv
    # (the bulk-ingestion path), which already tolerates the exact same condition via this
    # identical 3-attempt/1-8s-backoff policy. YFTickerMissingError ("no data found, symbol
    # may be delisted") is excluded since retrying it can never resolve the real 404 case this
    # function's own name==symbol-and-empty-info check already handles.
    retry=retry_if_not_exception_type(yf.exceptions.YFTickerMissingError),
    reraise=True,
)
def _fetch_yf_info(symbol: str) -> dict:
    return yf.Ticker(symbol).info or {}


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
        info = _fetch_yf_info(symbol)
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


@router.get("/watchlist-performance")
def watchlist_performance(
    style: str = Query(..., regex="^(SHORT|SWING|LONG|GROWTH)$"),
    days_back: int = Query(90, ge=1, le=365),
    min_outcomes: int = Query(4, ge=1, le=50, description="Minimum resolved outcomes for a symbol to count as reliable"),
    candidate_limit: int = Query(10, ge=0, le=50, description="How many top-K-Score non-watchlist candidates to return"),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Per-style watchlist health: win rate by symbol, sector concentration, and ranked
    candidates not yet on the watchlist. Admin-only.

    Consolidates into one endpoint what previously required manually cross-referencing
    GET /signals/outcomes/summary's by_symbol field against watchlist membership and
    GET /rankings — see the same watchlist-join pattern paper_trading_engine.py's
    _scan_for_entries() already uses to pull a style's candidate pool.
    """
    from datetime import date, timedelta
    from ..services.paper_trading_engine import _DEFAULT_CONFIG

    horizon_enum = SignalHorizon(style)
    cutoff = date.today() - timedelta(days=days_back)

    # Stocks currently on any watchlist tagged with this style (same join as
    # paper_trading_engine._scan_for_entries — see AUD232 watchlist-performance notes).
    watchlist_rows = session.execute(
        select(WatchlistItem.stock_id, Stock.symbol, Stock.sector, Stock.market)
        .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
        .join(Stock, WatchlistItem.stock_id == Stock.id)
        .where(Watchlist.trading_style == style)
    ).all()
    # A stock can appear on more than one watchlist with the same style tag — dedupe by stock_id.
    watchlist_stocks: dict[int, dict] = {}
    for stock_id, symbol, sector, market in watchlist_rows:
        watchlist_stocks[stock_id] = {
            "stock_id": stock_id, "symbol": symbol,
            "sector": sector or "Unknown",
            "market": market.value if hasattr(market, "value") else str(market),
        }

    # Win rate per stock_id for this style/lookback, from resolved (is_correct is not null) outcomes.
    outcome_rows = session.execute(
        select(
            SignalOutcome.stock_id,
            func.count().label("n"),
            func.sum(case((SignalOutcome.is_correct.is_(True), 1), else_=0)).label("wins"),
            func.avg(SignalOutcome.pct_return).label("avg_return"),
        )
        .where(
            SignalOutcome.horizon == horizon_enum,
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
        )
        .group_by(SignalOutcome.stock_id)
    ).all()
    outcomes_by_stock: dict[int, dict] = {
        row.stock_id: {
            "n": row.n, "wins": row.wins,
            "win_rate": round(row.wins / row.n, 3) if row.n else None,
            "avg_return_pct": round(row.avg_return * 100, 2) if row.avg_return is not None else None,
        }
        for row in outcome_rows
    }

    # Merge: every watchlist stock, with outcome data if it has any.
    watchlist_perf = []
    for stock_id, info in watchlist_stocks.items():
        oc = outcomes_by_stock.get(stock_id)
        watchlist_perf.append({
            **info,
            "n": oc["n"] if oc else 0,
            "win_rate": oc["win_rate"] if oc else None,
            "avg_return_pct": oc["avg_return_pct"] if oc else None,
            "reliable": bool(oc and oc["n"] >= min_outcomes),
        })
    watchlist_perf.sort(key=lambda x: (x["win_rate"] is None, x["win_rate"] if x["win_rate"] is not None else 0))

    reliable = [p for p in watchlist_perf if p["reliable"]]
    avg_win_rate = round(sum(p["win_rate"] for p in reliable) / len(reliable), 3) if reliable else None

    # Sector composition of the watchlist itself.
    sector_counts: dict[str, int] = {}
    for info in watchlist_stocks.values():
        sector_counts[info["sector"]] = sector_counts.get(info["sector"], 0) + 1
    total_stocks = len(watchlist_stocks)
    sector_pct = {
        sec: round(count / total_stocks * 100, 1)
        for sec, count in sorted(sector_counts.items(), key=lambda kv: -kv[1])
    } if total_stocks else {}

    # Top-ranked candidates (most recent as_of date) not already on this style's watchlist.
    candidates: list[dict] = []
    if candidate_limit > 0:
        latest_as_of = session.execute(select(func.max(Ranking.as_of))).scalar_one_or_none()
        if latest_as_of is not None:
            excluded_ids = set(watchlist_stocks.keys())
            cand_rows = session.execute(
                select(Ranking.score, Stock.id, Stock.symbol, Stock.sector, Stock.market)
                .join(Stock, Ranking.stock_id == Stock.id)
                .where(
                    Ranking.as_of == latest_as_of,
                    Stock.active.is_(True),
                    # BUG-DELISTED-GENERATION-BLIND: a confirmed-delisted stock must never be
                    # recommended into a live watchlist rotation candidate list.
                    Stock.delisted.is_(False),
                )
                .order_by(desc(Ranking.score))
                .limit(candidate_limit + len(excluded_ids))
            ).all()
            for score, stock_id, symbol, sector, market in cand_rows:
                if stock_id in excluded_ids:
                    continue
                candidates.append({
                    "symbol": symbol, "score": score,
                    "sector": sector or "Unknown",
                    "market": market.value if hasattr(market, "value") else str(market),
                })
                if len(candidates) >= candidate_limit:
                    break

    return {
        "style": style,
        "days_back": days_back,
        "min_outcomes": min_outcomes,
        "total_watchlist_stocks": total_stocks,
        "n_reliable": len(reliable),
        "avg_win_rate": avg_win_rate,
        "sector_pct": sector_pct,
        "max_sector_pct": _DEFAULT_CONFIG.get("max_sector_pct"),
        "watchlist_perf": watchlist_perf,
        "candidates": candidates,
    }


_SQUEEZE_ALERT_TYPE_LABELS = {
    "short_squeeze": "Short Squeeze (BUY)",
    # AUD-SQUEEZE-IGNITION-DASHBOARD-OMITTED (2026-08-31): squeeze_ignition is a real, actively-
    # firing 4th alert type (T260, check_squeeze_ignition_alerts()) whose outcomes are recorded
    # into this same SqueezeAlertOutcome table via the identical _record_squeeze_alert_outcome()
    # helper every other type uses — but this dict, and the by_alert_type loop below, were both
    # hardcoded to exactly 3 names since the endpoint's own creation, silently omitting it from
    # the admin performance dashboard entirely. There was never a comment anywhere explaining
    # this as intentional (unlike squeeze_alert_backtest(), which DOES correctly and explicitly
    # document why ignition/gamma are out of scope for backtesting specifically — a genuinely
    # different, honest limitation that doesn't apply to this performance-dashboard endpoint,
    # since it only reads already-collected real outcome rows, never a historical replay).
    "squeeze_ignition": "Squeeze Ignition (Early Warning)",
    "gamma_unwind_calls": "Gamma Unwind — Calls Dominant",
    "gamma_unwind_puts": "Gamma Unwind — Puts Dominant (\"Option Sell\")",
}


@router.get("/squeeze-alert-performance")
def squeeze_alert_performance(
    days_back: int = Query(180, ge=1, le=730),
    limit: int = Query(50, ge=1, le=500, description="How many most-recent rows to return in recent_alerts"),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """T264-SQUEEZEALERT-PERFORMANCE: win rate + avg return by alert type, measuring "if I
    bought at the moment the first email alert fired" — direct user request. Admin-only.

    Same win-rate/avg-return aggregation shape as watchlist_performance() above
    (func.count/func.sum(case)/func.avg grouped by dimension), applied here to
    SqueezeAlertOutcome grouped by alert_type instead of by symbol. is_correct_10d is the
    PRIMARY win-rate metric (10 calendar days — a deliberate middle ground between the
    short-squeeze/gamma-unwind theses' typical few-day-to-few-week resolution horizon and
    SignalOutcome's own established 5d/10d/20d window set); 5d/20d are reported alongside for
    context, not as the headline number.

    gamma_unwind_puts is reported as its own row, not merged with gamma_unwind_calls — the two
    are OPPOSITE theses (bearish vs. bullish options positioning), so pooling them the way
    _retro_ev_for()'s own BUG233-RETROEV-SIGNMIX bug fix had to specifically guard against
    would silently cancel real signal in either direction. See SqueezeAlertOutcome's own
    docstring for why gamma_unwind_puts is this app's closest existing concept to "option
    sell" performance.
    """
    from datetime import date, timedelta

    cutoff = date.today() - timedelta(days=days_back)

    def _summary_for_window(window: int) -> dict[str, dict]:
        price_col = getattr(SqueezeAlertOutcome, f"return_{window}d")
        correct_col = getattr(SqueezeAlertOutcome, f"is_correct_{window}d")
        rows = session.execute(
            select(
                SqueezeAlertOutcome.alert_type,
                func.count().label("n"),
                func.sum(case((correct_col.is_(True), 1), else_=0)).label("wins"),
                func.avg(price_col).label("avg_return"),
            )
            .where(
                SqueezeAlertOutcome.fired_date >= cutoff,
                correct_col.is_not(None),
            )
            .group_by(SqueezeAlertOutcome.alert_type)
        ).all()
        return {
            row.alert_type: {
                "n": row.n, "wins": row.wins,
                "win_rate": round(row.wins / row.n, 3) if row.n else None,
                "avg_return_pct": round(row.avg_return * 100, 2) if row.avg_return is not None else None,
            }
            for row in rows
        }

    # DESIGN_SQUEEZE_ALERT_PERFORMANCE_MEASUREMENT: 1d/2d/3d added alongside the pre-existing
    # 5d/10d/20d — the primary win-rate metric stays 10d (see docstring above), these three
    # answer the narrower "will it go up the NEXT day or the day after" question directly.
    by_window = {w: _summary_for_window(w) for w in (1, 2, 3, 5, 10, 20)}

    # Total fired count per type (regardless of whether any window has resolved yet) — lets
    # the page show "N alerts fired, M outcomes resolved" rather than silently hiding a type
    # that has fired recently but hasn't had time to reach even its first resolvable window.
    fired_counts = dict(session.execute(
        select(SqueezeAlertOutcome.alert_type, func.count())
        .where(SqueezeAlertOutcome.fired_date >= cutoff)
        .group_by(SqueezeAlertOutcome.alert_type)
    ).all())

    by_alert_type = []
    for alert_type in ("short_squeeze", "squeeze_ignition", "gamma_unwind_calls", "gamma_unwind_puts"):
        by_alert_type.append({
            "alert_type": alert_type,
            "label": _SQUEEZE_ALERT_TYPE_LABELS[alert_type],
            "fired_count": fired_counts.get(alert_type, 0),
            "window_10d": by_window[10].get(alert_type),
            "window_1d": by_window[1].get(alert_type),
            "window_2d": by_window[2].get(alert_type),
            "window_3d": by_window[3].get(alert_type),
            "window_5d": by_window[5].get(alert_type),
            "window_20d": by_window[20].get(alert_type),
        })

    recent_rows = session.execute(
        select(SqueezeAlertOutcome, Stock.symbol)
        .join(Stock, SqueezeAlertOutcome.stock_id == Stock.id)
        .where(SqueezeAlertOutcome.fired_date >= cutoff)
        .order_by(desc(SqueezeAlertOutcome.fired_date), desc(SqueezeAlertOutcome.fired_at))
        .limit(limit)
    ).all()
    recent_alerts = [
        {
            "alert_type": row.alert_type,
            "symbol": symbol,
            "fired_date": row.fired_date.isoformat(),
            "alert_price": row.alert_price,
            "qualifying_metric": row.qualifying_metric,
            "entry_date": row.entry_date.isoformat() if row.entry_date else None,
            "entry_price": row.entry_price,
            "return_1d": round(row.return_1d * 100, 2) if row.return_1d is not None else None,
            "return_2d": round(row.return_2d * 100, 2) if row.return_2d is not None else None,
            "return_3d": round(row.return_3d * 100, 2) if row.return_3d is not None else None,
            "return_5d": round(row.return_5d * 100, 2) if row.return_5d is not None else None,
            "return_10d": round(row.return_10d * 100, 2) if row.return_10d is not None else None,
            "return_20d": round(row.return_20d * 100, 2) if row.return_20d is not None else None,
            "is_correct_10d": row.is_correct_10d,
        }
        for row, symbol in recent_rows
    ]

    return {
        "days_back": days_back,
        "by_alert_type": by_alert_type,
        "recent_alerts": recent_alerts,
    }


@router.get("/options-flow-alert-performance")
def options_flow_alert_performance(
    days_back: int = Query(180, ge=1, le=730),
    limit: int = Query(50, ge=1, le=500, description="How many most-recent rows to return in recent_alerts"),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """MPE-OPTIONS-FLOW-ALERT: win rate + avg return for check_options_flow_alerts(), split by
    direction (bullish/bearish) — a genuinely SEPARATE endpoint from squeeze_alert_performance()
    above rather than a retrofit into it, since OptionsFlowAlertOutcome is keyed per-CONTRACT
    (option_chain), not per-(alert_type, symbol, date) the way SqueezeAlertOutcome is — forcing
    this table's rows into that endpoint's own grouping/shape would be the exact "genuinely
    different mechanism forced into an ill-fitting shape" mistake this app's own history already
    warns against (see OptionsFlowAlertOutcome's own model docstring). Admin-only.

    is_correct_10d is the primary win-rate metric, matching squeeze_alert_performance()'s own
    choice of window for the same reason (a real middle ground between this alert's typical
    few-day resolution horizon and SignalOutcome's own established window set).
    """
    from datetime import date, timedelta

    cutoff = date.today() - timedelta(days=days_back)

    def _summary_for_window(window: int) -> dict[str, dict]:
        ret_col = getattr(OptionsFlowAlertOutcome, f"return_{window}d")
        correct_col = getattr(OptionsFlowAlertOutcome, f"is_correct_{window}d")
        rows = session.execute(
            select(
                OptionsFlowAlertOutcome.direction,
                func.count().label("n"),
                func.sum(case((correct_col.is_(True), 1), else_=0)).label("wins"),
                func.avg(ret_col).label("avg_return"),
            )
            .where(OptionsFlowAlertOutcome.fired_date >= cutoff, correct_col.is_not(None))
            .group_by(OptionsFlowAlertOutcome.direction)
        ).all()
        return {
            row.direction: {
                "n": row.n, "wins": row.wins,
                "win_rate": round(row.wins / row.n, 3) if row.n else None,
                "avg_return_pct": round(row.avg_return * 100, 2) if row.avg_return is not None else None,
            }
            for row in rows
        }

    by_window = {w: _summary_for_window(w) for w in (1, 2, 3, 5, 10, 20)}

    fired_counts = dict(session.execute(
        select(OptionsFlowAlertOutcome.direction, func.count())
        .where(OptionsFlowAlertOutcome.fired_date >= cutoff)
        .group_by(OptionsFlowAlertOutcome.direction)
    ).all())

    by_direction = []
    for direction in ("bullish", "bearish"):
        by_direction.append({
            "direction": direction,
            "fired_count": fired_counts.get(direction, 0),
            "window_10d": by_window[10].get(direction),
            "window_1d": by_window[1].get(direction),
            "window_2d": by_window[2].get(direction),
            "window_3d": by_window[3].get(direction),
            "window_5d": by_window[5].get(direction),
            "window_20d": by_window[20].get(direction),
        })

    recent_rows = session.execute(
        select(OptionsFlowAlertOutcome, Stock.symbol)
        .join(Stock, OptionsFlowAlertOutcome.stock_id == Stock.id)
        .where(OptionsFlowAlertOutcome.fired_date >= cutoff)
        .order_by(desc(OptionsFlowAlertOutcome.fired_date), desc(OptionsFlowAlertOutcome.fired_at))
        .limit(limit)
    ).all()
    recent_alerts = [
        {
            "symbol": symbol,
            "option_chain": row.option_chain,
            "option_type": row.option_type,
            "direction": row.direction,
            "strike": row.strike,
            "expiry": row.expiry.isoformat() if row.expiry else None,
            "fired_date": row.fired_date.isoformat(),
            "alert_price": row.alert_price,
            "total_premium": row.total_premium,
            "ask_side_dominant": row.ask_side_dominant,
            "has_sweep": row.has_sweep,
            "entry_date": row.entry_date.isoformat() if row.entry_date else None,
            "entry_price": row.entry_price,
            "return_1d": round(row.return_1d * 100, 2) if row.return_1d is not None else None,
            "return_2d": round(row.return_2d * 100, 2) if row.return_2d is not None else None,
            "return_3d": round(row.return_3d * 100, 2) if row.return_3d is not None else None,
            "return_5d": round(row.return_5d * 100, 2) if row.return_5d is not None else None,
            "return_10d": round(row.return_10d * 100, 2) if row.return_10d is not None else None,
            "return_20d": round(row.return_20d * 100, 2) if row.return_20d is not None else None,
            "is_correct_10d": row.is_correct_10d,
        }
        for row, symbol in recent_rows
    ]

    return {
        "days_back": days_back,
        "by_direction": by_direction,
        "recent_alerts": recent_alerts,
    }


@router.get("/squeeze-alert-backtest")
def squeeze_alert_backtest(
    weeks_back: int = Query(52, ge=1, le=260, description="How many weekly fundamentals snapshots to scan back"),
    min_samples: int = Query(15, ge=1, le=200, description="Minimum resolved samples before reporting a real win rate"),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """T264-SQUEEZEALERT-PERFORMANCE (backtest follow-up): a RETROACTIVE approximation of the
    short_squeeze alert's own filter, run against already-stored historical data — direct
    follow-up to a user asking for backtesting/walk-forward on the squeeze/gamma-unwind alerts.

    HONEST SCOPE, decided before writing this endpoint, not discovered after: this is the ONLY
    one of the two squeeze alert types that CAN be backtested at all. check_gamma_unwind_alerts()
    depends on a LIVE options-chain fetch (yfinance has no historical open-interest API, and
    this app stores none either) — there is no historical OI data to replay it against, so no
    endpoint for it exists here. Building one would mean fabricating historical OI data, which
    this app's own standing discipline explicitly refuses to do (see the CAPE/congress-data
    "verify against real data, never guess" precedent elsewhere in this codebase).

    Even for short_squeeze, this is a PROXY, not a live replay — the real alert reads
    stockai:live_prices (a 1-day Redis cache with no history) for an intraday move; this
    approximates "a real move already in progress" with the closest honest historical signal
    that actually exists: a full trading day's own return clearing the SAME +3.0% threshold
    (see fix note in the tracker for why a same-day return, not an approximated intraday one,
    is the correct conservative substitute rather than something that would quietly inflate
    results). short_percent_of_float comes from FundamentalsSnapshot, populated weekly — so a
    candidate week is "qualifying" for every trading day between one Sunday snapshot and the
    next, using that snapshot's own short-interest reading (point-in-time correct — never a
    later snapshot's value leaking backward).

    Scores forward returns using the EXACT same _squeeze_outcome_lookup_price() helper and
    _SQUEEZE_OUTCOME_WIN_HURDLE_PCT/_SQUEEZE_OUTCOME_WINDOWS constants the live evaluator uses
    (services/market-data/src/services/scheduler.py) — imported lazily, matching this file's
    own established convention for scheduler.py cross-imports (see _service_token/
    send_morning_digest/broker.py's _is_token_rejected_error above), so this can never silently
    drift into a second, differently-tuned scoring implementation.
    """
    from datetime import date, timedelta
    from ..services.scheduler import (
        _squeeze_outcome_lookup_price, _SQUEEZE_OUTCOME_WIN_HURDLE_PCT, _SQUEEZE_OUTCOME_WINDOWS,
        _SQUEEZE_MIN_SHORT_FLOAT, _SQUEEZE_MIN_INTRADAY_MOVE_PCT,
    )

    cutoff = date.today() - timedelta(weeks=weeks_back)
    snapshots = session.execute(
        select(FundamentalsSnapshot.symbol, FundamentalsSnapshot.snapshot_date, FundamentalsSnapshot.short_percent_of_float)
        .where(
            FundamentalsSnapshot.snapshot_date >= cutoff,
            FundamentalsSnapshot.short_percent_of_float.is_not(None),
            FundamentalsSnapshot.short_percent_of_float * 100 >= _SQUEEZE_MIN_SHORT_FLOAT,
        )
        .order_by(FundamentalsSnapshot.symbol, FundamentalsSnapshot.snapshot_date)
    ).all()
    if not snapshots:
        return {
            "weeks_back": weeks_back, "min_samples": min_samples,
            "n_snapshots_qualifying": 0, "n_candidate_days": 0,
            "window_10d": None, "window_5d": None, "window_20d": None,
            # AUD-SQUEEZE250725-ISSUE6: distinguishes "no stock ever cleared the short-float
            # floor" from "stocks cleared the floor but never had a qualifying intraday move" —
            # two genuinely different diagnostic signals the audit found the response couldn't
            # tell apart when both n_snapshots_qualifying and n_candidate_days were 0.
            "reason": "no_qualifying_snapshots",
            "note": "No FundamentalsSnapshot rows cleared the short-interest floor in this window.",
        }

    symbols = sorted({s for s, _, _ in snapshots})
    stock_rows = session.execute(select(Stock.id, Stock.symbol).where(Stock.symbol.in_(symbols))).all()
    stock_id_by_symbol = {sym: sid for sid, sym in stock_rows}

    bulk_prices = session.execute(
        select(Price.stock_id, Price.ts, Price.close).where(
            Price.stock_id.in_(stock_id_by_symbol.values()),
            Price.timeframe == TimeFrame.D1,
        ).order_by(Price.stock_id, Price.ts)
    ).all()
    price_map: dict[int, list[tuple]] = {}
    for stock_id, ts, close in bulk_prices:
        d = ts.date() if hasattr(ts, "date") else ts
        price_map.setdefault(stock_id, []).append((d, float(close)))

    # Each snapshot's short-interest reading qualifies the stock for every trading day between
    # THIS Sunday and the NEXT snapshot (point-in-time — never lets a later reading leak backward
    # onto an earlier week).
    by_symbol: dict[str, list[tuple]] = {}
    for sym, snap_date, spf in snapshots:
        by_symbol.setdefault(sym, []).append((snap_date, spf))

    candidate_days: list[tuple[int, str, date, float]] = []  # (stock_id, symbol, day, entry_close)
    for sym, snaps in by_symbol.items():
        stock_id = stock_id_by_symbol.get(sym)
        if stock_id is None:
            continue
        bucket = price_map.get(stock_id, [])
        if not bucket:
            continue
        for i, (snap_date, _spf) in enumerate(snaps):
            window_end = snaps[i + 1][0] if i + 1 < len(snaps) else snap_date + timedelta(days=7)
            prev_close = None
            for d, close in bucket:
                if d < snap_date or d >= window_end:
                    if d < snap_date:
                        prev_close = close
                    continue
                if prev_close is None or prev_close <= 0:
                    prev_close = close
                    continue
                day_ret = (close - prev_close) / prev_close * 100
                if day_ret >= _SQUEEZE_MIN_INTRADAY_MOVE_PCT:
                    candidate_days.append((stock_id, sym, d, close))
                prev_close = close

    def _window_summary(window: int) -> dict | None:
        rets = []
        for stock_id, _sym, day, entry_close in candidate_days:
            bucket = price_map.get(stock_id, [])
            target = day + timedelta(days=window)
            if target > date.today():
                continue
            result = _squeeze_outcome_lookup_price(bucket, target)
            if result is None:
                continue
            _, price = result
            rets.append((price - entry_close) / entry_close)
        if len(rets) < min_samples:
            return {"n": len(rets), "win_rate": None, "avg_return_pct": None,
                    "note": f"Below the {min_samples}-sample floor — not enough resolved candidates to report a reliable win rate yet."}
        wins = sum(1 for r in rets if r > _SQUEEZE_OUTCOME_WIN_HURDLE_PCT)
        return {
            "n": len(rets), "win_rate": round(wins / len(rets), 3),
            "avg_return_pct": round(sum(rets) / len(rets) * 100, 2),
        }

    windows = {f"window_{w}d": _window_summary(w) for w in _SQUEEZE_OUTCOME_WINDOWS}

    # AUD-SQUEEZE250725-ISSUE6: the OTHER zero-case — real snapshots cleared the short-float
    # floor, but none of them ever had a qualifying same-day intraday move, so no candidate day
    # exists at all (as opposed to candidate days existing but none resolved yet, which the
    # per-window "below sample floor" note already covers on its own).
    reason = "no_qualifying_moves" if not candidate_days else None

    return {
        "weeks_back": weeks_back,
        "min_samples": min_samples,
        "n_snapshots_qualifying": len(snapshots),
        "n_candidate_days": len(candidate_days),
        **windows,
        "reason": reason,
        "note": (
            "Retroactive PROXY for the short_squeeze alert's filter — uses weekly short-interest "
            "snapshots + daily-bar moves, not the live 1-minute intraday scan. gamma_unwind is not "
            "backtestable at all: yfinance has no historical options open-interest API and this app "
            "stores none, so there is no historical data to replay it against."
        ),
    }


@router.get("/watchlist-rotation-history")
def watchlist_rotation_history(
    watchlist_id: int | None = Query(None, description="Filter to one watchlist"),
    style: str | None = Query(None, description="Filter to SHORT | SWING | LONG | GROWTH"),
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(get_admin_user),
) -> dict:
    """WATCHLIST-AUTO-ROTATION: browse every add/drop the weekly rotation job has made,
    newest first, with enough detail to answer "why did this stock disappear/appear" and
    whether a given row has already been reverted (reverted_at is set) or can still be undone.
    """
    from db import TuneHistory

    with SessionLocal() as session:
        q = (
            select(TuneHistory)
            .where(TuneHistory.parameter_class == "watchlist_rotation")
            .order_by(desc(TuneHistory.ts))
            .limit(limit)
        )
        if style:
            q = q.where(TuneHistory.style == style.upper())
        rows = session.execute(q).scalars().all()
        if watchlist_id is not None:
            rows = [r for r in rows if (r.old_value or {}).get("watchlist_id") == watchlist_id
                    or (r.new_value or {}).get("watchlist_id") == watchlist_id]
        return {
            "count": len(rows),
            "rows": [
                {
                    "id": r.id, "run_id": r.run_id, "ts": r.ts.isoformat(),
                    "action": r.parameter_name,  # "add" | "drop"
                    "style": r.style, "market": r.market,
                    "old_value": r.old_value, "new_value": r.new_value,
                    "validation_ev_pct": r.validation_ev_pct,
                    "baseline_validation_ev_pct": r.baseline_validation_ev_pct,
                    "validation_n": r.validation_n,
                    "reverted": bool((r.gate_failures or []) and "reverted" in r.gate_failures),
                }
                for r in rows
            ],
        }


@router.post("/watchlist-rotation-history/{tune_history_id}/revert")
def revert_watchlist_rotation(
    tune_history_id: int,
    _: User = Depends(get_admin_user),
) -> dict:
    """Undo one specific add/drop the auto-rotation job made: re-adds a dropped stock, or
    removes an added one. Marks the TuneHistory row as reverted (via gate_failures, the only
    free-text-ish field already on this model — see the "reverted" flag in
    watchlist_rotation_history() above) rather than deleting the audit row itself, so the
    history page keeps showing what happened even after it's been undone.
    """
    from db import TuneHistory

    with SessionLocal() as session:
        row = session.execute(
            select(TuneHistory).where(
                TuneHistory.id == tune_history_id,
                TuneHistory.parameter_class == "watchlist_rotation",
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="No such watchlist_rotation history row")
        if row.gate_failures and "reverted" in row.gate_failures:
            raise HTTPException(status_code=400, detail="This action was already reverted")

        if row.parameter_name == "drop":
            info = row.old_value or {}
            watchlist_id, stock_id = info.get("watchlist_id"), info.get("stock_id")
            if watchlist_id is None or stock_id is None:
                raise HTTPException(status_code=400, detail="History row is missing watchlist_id/stock_id — cannot revert")
            already_there = session.execute(
                select(WatchlistItem.id).where(
                    WatchlistItem.watchlist_id == watchlist_id, WatchlistItem.stock_id == stock_id,
                )
            ).scalar_one_or_none()
            if already_there is None:
                session.add(WatchlistItem(stock_id=stock_id, watchlist_id=watchlist_id))
        elif row.parameter_name == "add":
            info = row.new_value or {}
            watchlist_id, stock_id = info.get("watchlist_id"), info.get("stock_id")
            if watchlist_id is None or stock_id is None:
                raise HTTPException(status_code=400, detail="History row is missing watchlist_id/stock_id — cannot revert")
            session.execute(
                delete(WatchlistItem).where(
                    WatchlistItem.watchlist_id == watchlist_id, WatchlistItem.stock_id == stock_id,
                )
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action '{row.parameter_name}' — cannot revert")

        row.gate_failures = list(row.gate_failures or []) + ["reverted"]
        session.commit()
        return {"status": "reverted", "id": tune_history_id, "action": row.parameter_name}


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


def _read_promotion_history(r, key: str) -> list:
    """T247-MLPREDICTION-PROMOTIONHISTORY-RACE: meta_trainer._record_promotion_status() now
    writes meta_model:promotion_history as a native Redis LIST (RPUSH/LTRIM, atomic under
    concurrent writers) instead of a single read-modify-write JSON blob (SETEX).
    position_scaling_gate:promotion_history (scheduler.py) still uses the old blob format —
    branch on the actual Redis type so both formats read correctly rather than assuming one
    or the other. Extracted to module level (was a local closure) so it's independently
    unit-testable.
    """
    try:
        key_type = r.type(key)
    except Exception:
        return []
    if key_type == "list":
        try:
            return [json.loads(item) for item in r.lrange(key, 0, -1)]
        except Exception:
            return []
    raw = r.get(key)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


@router.get("/promotion-history")
def promotion_history(_: User = Depends(get_admin_user)):
    """Return the last 20 promotion-gate verdicts for both model-artifact promotion gates
    (SELFIMPROVE-PROMOTION-GATES-INCOMPLETE, see docs/DESIGN_MODEL_PROMOTION_GATES_2026-07-12.md).

    meta_model_history: written directly by ml-prediction's train_meta_model() into this same
    shared Redis instance (cross-service write — see meta_trainer._record_promotion_status()
    for why this needed adding a Redis client to ml-prediction, which had none before).
    position_scaling_history: written by this service's own scheduler
    (_record_position_scaling_promotion_status(), shadow-log-only per the design doc §3.4 —
    the model is always saved regardless of the verdict shown here).
    """
    r = _get_redis()
    return {
        "meta_model_history": _read_promotion_history(r, "meta_model:promotion_history"),
        "position_scaling_history": _read_promotion_history(r, "position_scaling_gate:promotion_history"),
    }


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
        select(Stock).where(
            Stock.active.is_(True), Stock.market == "US",
            # BUG-DELISTED-GENERATION-BLIND: a confirmed-delisted stock's index membership is
            # dead metadata — skip it rather than keep it in sync with real S&P/DOW/Nasdaq lists.
            Stock.delisted.is_(False),
        )
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

