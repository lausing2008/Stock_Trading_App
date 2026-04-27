"""Admin endpoints: trigger ingestion + seed universe + add individual stock."""
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
import yfinance as yf

from common.logging import get_logger
from db import Exchange, Market, SessionLocal, Stock, init_db

from ..services.ingestion import ingest_symbol, ingest_universe
from ..services.seed_universe import seed

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
}


class IngestRequest(BaseModel):
    symbols: list[str]
    timeframe: str = "1d"


class AddStockRequest(BaseModel):
    symbol: str


@router.post("/seed")
def run_seed():
    count = seed()
    return {"status": "ok", "inserted": count}


@router.post("/ingest")
def run_ingest(req: IngestRequest):
    """Synchronously ingest all requested symbols (parallel for multi-symbol)."""
    if len(req.symbols) == 1:
        result = ingest_symbol(req.symbols[0], timeframe=req.timeframe)
        return {"status": "ok", "symbols": 1, "results": [result]}
    results = ingest_universe(req.symbols, req.timeframe)
    ok = sum(1 for r in results if "error" not in r)
    return {"status": "ok", "symbols": ok, "total": len(results), "results": results}


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
