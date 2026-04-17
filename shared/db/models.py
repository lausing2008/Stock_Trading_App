"""Core data model — single source of truth for every service.

Tables: stocks, prices, indicators, signals, rankings, strategies, backtests,
portfolios, portfolio_holdings. Designed so new markets (crypto) plug in by
adding a Market enum value; no schema change required.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Market(str, enum.Enum):
    US = "US"
    HK = "HK"
    # Future: CRYPTO = "CRYPTO"


class Exchange(str, enum.Enum):
    NYSE = "NYSE"
    NASDAQ = "NASDAQ"
    HKEX = "HKEX"


class TimeFrame(str, enum.Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"
    W1 = "1w"


class SignalType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalHorizon(str, enum.Enum):
    SHORT = "SHORT"
    SWING = "SWING"
    LONG = "LONG"


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[Market] = mapped_column(SAEnum(Market), index=True)
    exchange: Mapped[Exchange] = mapped_column(SAEnum(Exchange))
    name: Mapped[str] = mapped_column(String(256))
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    prices: Mapped[list["Price"]] = relationship(back_populates="stock")

    __table_args__ = (UniqueConstraint("symbol", "exchange", name="uq_stock_symbol_exch"),)


class Price(Base):
    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    timeframe: Mapped[TimeFrame] = mapped_column(SAEnum(TimeFrame), default=TimeFrame.D1)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)

    stock: Mapped[Stock] = relationship(back_populates="prices")

    __table_args__ = (
        UniqueConstraint("stock_id", "ts", "timeframe", name="uq_prices_stock_ts_tf"),
        Index("ix_prices_stock_tf_ts", "stock_id", "timeframe", "ts"),
    )


class Indicator(Base):
    __tablename__ = "indicators"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    timeframe: Mapped[TimeFrame] = mapped_column(SAEnum(TimeFrame), default=TimeFrame.D1)
    name: Mapped[str] = mapped_column(String(64))  # e.g. rsi_14, macd, sma_50
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("stock_id", "ts", "timeframe", "name", name="uq_ind_stock_ts_name"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True, default=func.now())
    signal: Mapped[SignalType] = mapped_column(SAEnum(SignalType))
    horizon: Mapped[SignalHorizon] = mapped_column(SAEnum(SignalHorizon))
    confidence: Mapped[float] = mapped_column(Float)  # 0-100
    bullish_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="signal-engine")


class Ranking(Base):
    __tablename__ = "rankings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    score: Mapped[float] = mapped_column(Float)  # K-Score 0-100
    technical: Mapped[float] = mapped_column(Float)
    momentum: Mapped[float] = mapped_column(Float)
    value: Mapped[float] = mapped_column(Float)
    growth: Mapped[float] = mapped_column(Float)
    volatility: Mapped[float] = mapped_column(Float)
    fair_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_rank_stock_date"),)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    owner: Mapped[str] = mapped_column(String(128), default="system")
    rule_dsl: Mapped[dict] = mapped_column(JSON)  # parsed rule tree
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    backtests: Mapped[list["Backtest"]] = relationship(back_populates="strategy")


class Backtest(Base):
    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    universe: Mapped[list] = mapped_column(JSON)  # list of symbols
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    timeframe: Mapped[TimeFrame] = mapped_column(SAEnum(TimeFrame), default=TimeFrame.D1)
    # Metrics
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    cagr: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity_curve: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trades: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    strategy: Mapped[Strategy] = relationship(back_populates="backtests")


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    owner: Mapped[str] = mapped_column(String(128), default="system")
    method: Mapped[str] = mapped_column(String(64), default="mean_variance")
    cash_weight: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    holdings: Mapped[list["PortfolioHolding"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    weight: Mapped[float] = mapped_column(Float)

    portfolio: Mapped[Portfolio] = relationship(back_populates="holdings")
