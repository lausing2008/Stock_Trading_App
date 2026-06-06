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


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    USER = "USER"


class SignalType(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WAIT = "WAIT"


class SignalHorizon(str, enum.Enum):
    SHORT = "SHORT"
    SWING = "SWING"
    LONG = "LONG"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    watchlist_items: Mapped[list["WatchlistItem"]] = relationship(back_populates="user")
    watchlists: Mapped[list["Watchlist"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    price_alerts: Mapped[list["PriceAlert"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    signal_alerts: Mapped[list["SignalAlert"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    trade_journal: Mapped[list["TradeJournal"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    trade_plans: Mapped[list["TradePlan"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    positions: Mapped[list["UserPosition"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    cash_balances: Mapped[list["UserCash"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    app_notifications: Mapped[list["AppNotification"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[Market] = mapped_column(SAEnum(Market), index=True)
    exchange: Mapped[Exchange] = mapped_column(SAEnum(Exchange))
    name: Mapped[str] = mapped_column(String(256))
    name_zh: Mapped[str | None] = mapped_column(String(256), nullable=True)
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
    __table_args__ = (Index("ix_signals_stock_ts", "stock_id", "ts"),)
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
    rs_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_rank_stock_date"),)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    owner: Mapped[str] = mapped_column(String(128), default="system")
    rule_dsl: Mapped[dict] = mapped_column(JSON)  # parsed rule tree
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    backtests: Mapped[list["Backtest"]] = relationship(back_populates="strategy", cascade="all, delete-orphan")


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


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    trading_style: Mapped[str | None] = mapped_column(String(16), nullable=True)  # SHORT|SWING|LONG|None=global
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="watchlists")
    items: Mapped[list["WatchlistItem"]] = relationship(back_populates="watchlist", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    watchlist_id: Mapped[int | None] = mapped_column(
        ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=True, index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User | None"] = relationship(back_populates="watchlist_items")
    watchlist: Mapped["Watchlist | None"] = relationship(back_populates="items")

    __table_args__ = (UniqueConstraint("user_id", "stock_id", name="uq_watchlist_user_stock"),)


class AlertCondition(str, enum.Enum):
    ABOVE = "above"
    BELOW = "below"
    CROSS_ABOVE_EMA = "cross_above_ema"   # threshold = EMA period (20/50/200)
    CROSS_BELOW_EMA = "cross_below_ema"
    NEW_52WK_HIGH   = "new_52wk_high"     # threshold unused (store 0)
    NEW_52WK_LOW    = "new_52wk_low"
    GOLDEN_CROSS    = "golden_cross"      # EMA50 crosses above EMA200; threshold unused
    DEATH_CROSS     = "death_cross"       # EMA50 crosses below EMA200; threshold unused


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    condition: Mapped[AlertCondition] = mapped_column(SAEnum(AlertCondition, name="alertcondition"))
    threshold: Mapped[float] = mapped_column(Float)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="price_alerts")


class SignalAlert(Base):
    __tablename__ = "signal_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_signal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="signal_alerts")


class UserPosition(Base):
    __tablename__ = "user_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    shares: Mapped[float] = mapped_column(Float)
    avg_cost: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="positions")
    trades: Mapped[list["PositionTrade"]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )


class PositionTrade(Base):
    __tablename__ = "position_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("user_positions.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(8))  # BUY | SELL
    shares: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    date: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    position: Mapped["UserPosition"] = relationship(back_populates="trades")


class UserCash(Base):
    __tablename__ = "user_cash"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(8))
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    user: Mapped["User"] = relationship(back_populates="cash_balances")

    __table_args__ = (UniqueConstraint("user_id", "currency", name="uq_cash_user_currency"),)


class AppNotification(Base):
    __tablename__ = "app_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    alert_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(String(512))
    triggered_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    user: Mapped["User"] = relationship(back_populates="app_notifications")


class TradeJournal(Base):
    __tablename__ = "trade_journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(16))  # BUY | SELL_SHORT
    shares: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signal_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="trade_journal")


class SignalOutcome(Base):
    """Forward-tracking table: one row per evaluated BUY/SELL signal.

    Written by POST /signals/outcomes/evaluate (runs post-close via scheduler).
    Captures entry price, exit price, and actual return after the hold window
    closes. Used for signal accuracy calibration and parameter tuning via Optuna.
    """
    __tablename__ = "signal_outcomes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), unique=True, index=True
    )
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    horizon: Mapped[SignalHorizon] = mapped_column(SAEnum(SignalHorizon), index=True)
    signal_direction: Mapped[str] = mapped_column(String(8))        # BUY | SELL
    signal_date: Mapped[date] = mapped_column(Date, index=True)
    confidence: Mapped[float] = mapped_column(Float)                # 0–100
    fused_prob: Mapped[float | None] = mapped_column(Float, nullable=True)      # 0–1
    ta_score: Mapped[float | None] = mapped_column(Float, nullable=True)        # 0–1
    ml_prob: Mapped[float | None] = mapped_column(Float, nullable=True)         # 0–1
    ml_auc: Mapped[float | None] = mapped_column(Float, nullable=True)          # 0–1
    market_regime: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Trade outcome (filled when hold window closes)
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    hold_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pct_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ts_evaluated: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_signal_outcomes_horizon_correct", "horizon", "is_correct"),
    )


class TradePlan(Base):
    """Kanban board card — persisted AI game plan or forecast pick."""
    __tablename__ = "trade_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(20), default="watch")  # watch|planning|active|closed
    game_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # gameplan|forecast|manual
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    trading_style: Mapped[str | None] = mapped_column(String(16), nullable=True)  # SHORT|SWING|LONG
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="trade_plans")
