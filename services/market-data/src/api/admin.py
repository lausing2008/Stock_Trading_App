"""Admin endpoints: trigger ingestion + seed universe + add individual stock."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
import yfinance as yf

from common.logging import get_logger
from db import Exchange, Market, SessionLocal, Stock, init_db

from ..adapters.registry import set_runtime_key
from ..services.ingestion import ingest_symbol, ingest_universe
from ..services.seed_universe import seed
from .auth import User, get_admin_user

router = APIRouter(prefix="/admin", tags=["admin"])
log = get_logger("admin")

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


@router.post("/config")
def update_config(req: ConfigRequest, _: User = Depends(get_admin_user)):
    if req.polygon_api_key is not None:
        set_runtime_key("polygon", req.polygon_api_key)
    if req.alpha_vantage_api_key is not None:
        set_runtime_key("alpha_vantage", req.alpha_vantage_api_key)
    log.info("admin.config_updated")
    return {"status": "ok"}


class IngestRequest(BaseModel):
    symbols: list[str]
    timeframe: str = "1d"
    force: bool = False


class AddStockRequest(BaseModel):
    symbol: str


@router.post("/seed")
def run_seed():
    count = seed()
    return {"status": "ok", "inserted": count}


@router.post("/ingest")
def run_ingest(req: IngestRequest, tasks: BackgroundTasks):
    """Queue ingest in the background — returns immediately to avoid proxy timeouts."""
    def _run():
        if len(req.symbols) == 1:
            try:
                ingest_symbol(req.symbols[0], timeframe=req.timeframe, force=req.force)
            except Exception as exc:
                log.error("ingest.symbol_failed", symbol=req.symbols[0], error=str(exc))
        else:
            try:
                ingest_universe(req.symbols, req.timeframe, force=req.force)
            except Exception as exc:
                log.error("ingest.universe_failed", error=str(exc))

    tasks.add_task(_run)
    return {"status": "queued", "symbols": len(req.symbols), "queued": req.symbols}


@router.delete("/stocks/{symbol}")
def delete_stock(symbol: str):
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
def add_stock(req: AddStockRequest, tasks: BackgroundTasks):
    symbol = req.symbol.upper().strip()
    log.info("add_stock.start", symbol=symbol)

    # Check if already in DB
    with SessionLocal() as session:
        existing = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
        if existing:
            tasks.add_task(ingest_symbol, symbol)
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
    tasks.add_task(ingest_symbol, symbol)
    return {"status": "added", "symbol": symbol, "name": name, "sector": sector}
