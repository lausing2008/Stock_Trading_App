"""Seed initial universe: S&P 500 majors + HKEX blue chips.

Run via `python -m src.services.seed_universe`.
"""
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.logging import get_logger
from db import Exchange, Market, SessionLocal, Stock, init_db

log = get_logger("seed")

_US_SEED = [
    ("AAPL", Exchange.NASDAQ, "Apple Inc.", "Technology"),
    ("MSFT", Exchange.NASDAQ, "Microsoft Corp.", "Technology"),
    ("NVDA", Exchange.NASDAQ, "NVIDIA Corp.", "Technology"),
    ("GOOGL", Exchange.NASDAQ, "Alphabet Inc.", "Communication Services"),
    ("AMZN", Exchange.NASDAQ, "Amazon.com Inc.", "Consumer Cyclical"),
    ("META", Exchange.NASDAQ, "Meta Platforms", "Communication Services"),
    ("TSLA", Exchange.NASDAQ, "Tesla Inc.", "Consumer Cyclical"),
    ("JPM", Exchange.NYSE, "JPMorgan Chase", "Financial"),
    ("V", Exchange.NYSE, "Visa Inc.", "Financial"),
    ("WMT", Exchange.NYSE, "Walmart Inc.", "Consumer Defensive"),
]

_HK_SEED = [
    ("0700.HK", Exchange.HKEX, "Tencent Holdings", "Technology", "騰訊控股"),
    ("0005.HK", Exchange.HKEX, "HSBC Holdings", "Financial", "匯豐控股"),
    ("0939.HK", Exchange.HKEX, "China Construction Bank", "Financial", "建設銀行"),
    ("1299.HK", Exchange.HKEX, "AIA Group", "Financial", "友邦保險"),
    ("9988.HK", Exchange.HKEX, "Alibaba Group", "Consumer Cyclical", "阿里巴巴"),
    ("3690.HK", Exchange.HKEX, "Meituan", "Consumer Cyclical", "美團"),
    ("0388.HK", Exchange.HKEX, "HKEX", "Financial", "香港交易所"),
    ("1810.HK", Exchange.HKEX, "Xiaomi", "Technology", "小米集團"),
]


def seed() -> int:
    init_db()
    rows = []
    for symbol, exch, name, sector in _US_SEED:
        rows.append(
            {"symbol": symbol, "exchange": exch, "market": Market.US, "name": name, "sector": sector, "currency": "USD"}
        )
    for symbol, exch, name, sector, name_zh in _HK_SEED:
        rows.append(
            {"symbol": symbol, "exchange": exch, "market": Market.HK, "name": name, "name_zh": name_zh, "sector": sector, "currency": "HKD"}
        )

    with SessionLocal() as session:
        stmt = pg_insert(Stock).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "exchange"])
        result = session.execute(stmt)
        session.commit()
    log.info("seed.done", inserted=result.rowcount, total=len(rows))
    return result.rowcount


if __name__ == "__main__":
    seed()
