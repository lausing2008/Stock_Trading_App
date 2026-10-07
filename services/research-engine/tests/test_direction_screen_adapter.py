"""Exercise the actual endpoint SQL and session filtering against SQLite, not a fake ledger.

This tests query semantics and row adaptation; it is not a production/PostgreSQL browser test.
"""
import importlib
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def test_endpoint_excludes_forming_bars_and_ranks_full_population(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "screen_test_calendar", root.parents[1] / "shared/common/market_calendar.py")
    calendar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(calendar)
    monkeypatch.setitem(sys.modules, "common.market_calendar", calendar)
    # conftest replaces FastAPI with MagicMock; keep endpoint decorators transparent.
    import fastapi
    router = SimpleNamespace(get=lambda *a, **k: lambda f: f,
                             post=lambda *a, **k: lambda f: f)
    monkeypatch.setattr(fastapi, "APIRouter", lambda **k: router)
    routes = importlib.import_module("src.api.quality_value_routes")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 6, 15, tzinfo=timezone.utc)

    Base = declarative_base()

    class Stock(Base):
        __tablename__ = "stocks"
        id = Column(Integer, primary_key=True)
        symbol = Column(String)
        market = Column(String)
        name = Column(String)
        sector = Column(String)
        currency = Column(String)
        active = Column(Boolean)
        delisted = Column(Boolean)

    class Price(Base):
        __tablename__ = "prices"
        id = Column(Integer, primary_key=True)
        stock_id = Column(Integer)
        ts = Column(DateTime)
        timeframe = Column(String)
        high = Column(Float)
        low = Column(Float)
        close = Column(Float)
        volume = Column(Float)
        adj_close = Column(Float)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(routes, "SessionLocal", factory)
    monkeypatch.setattr(routes, "Stock", Stock)
    monkeypatch.setattr(routes, "Price", Price)
    monkeypatch.setattr(routes, "TimeFrame", SimpleNamespace(D1="1d"))
    monkeypatch.setattr(routes, "datetime", Clock)
    monkeypatch.setattr(routes, "is_trading_day", calendar.is_trading_day)
    monkeypatch.setattr(routes, "MIN_COVERAGE_YEAR", 2027)
    with factory() as session:
        for i in range(206):
            venue = "HK" if i == 205 else "US"
            session.add(Stock(id=i + 1, symbol=f"S{i:03}", market=venue, name="Test",
                              sector="Tech", currency="HKD" if venue == "HK" else "USD",
                              active=i != 0, delisted=False))
            days, day = [], datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
            while len(days) < 21:
                if calendar.is_trading_day(venue, day): days.append(day.replace(tzinfo=None, hour=0))
                day -= timedelta(days=1)
            for d in days:
                close = 106 if d == days[0] else 100
                session.add(Price(stock_id=i + 1, ts=d, timeframe="1d", high=max(105, close),
                                  low=95, close=close, adj_close=close,
                                  volume=1000 * (i + 1) if d == days[0] else 1000))
            # Must never enter the range, volume baseline or tested close.
            session.add(Price(stock_id=i + 1, ts=datetime(2026, 10, 6), timeframe="1d",
                              high=10000, low=1, close=1, adj_close=1, volume=9999999))
        session.commit()
    result = routes.setups(market="ALL", direction="all", limit=20,
                           sector=None, symbols=None, _user="test")
    assert result["scanned"] == 205  # active only, no old 200-name cap
    assert len(result["rows"]) == 21  # 20 US plus one HK
    assert result["rows"][0]["symbol"] == "S204"  # ranked before truncating
    assert all(r["setup"]["direction"] == "breakout" for r in result["rows"])
    assert all(r["setup"]["close"] == 106 for r in result["rows"])
    assert all(r["setup"]["resistance"] == 105 for r in result["rows"])
    engine.dispose()
