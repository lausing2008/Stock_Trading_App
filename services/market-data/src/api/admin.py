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

    with SessionLocal() as session:
        stock = Stock(
            symbol=symbol, name=name, market=market, exchange=exchange,
            sector=sector, industry=industry, currency=currency, active=True,
        )
        session.add(stock)
        session.commit()

    log.info("add_stock.done", symbol=symbol, name=name)
    tasks.add_task(ingest_symbol, symbol)
    return {"status": "added", "symbol": symbol, "name": name, "sector": sector}
