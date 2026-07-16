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
    Numeric,
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
    GROWTH = "GROWTH"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # T230-ALERTING-SLACK-DISCORD-FIX: this field was referenced by scheduler.py's signal-alert
    # webhook delivery since 2026-07-01 (via getattr(alert.user, "notification_webhook", None))
    # but never actually existed on this model — the getattr fallback meant that code path
    # always silently no-op'd, discovered while wiring T230-ALERTING-PUSH-NOTIFICATIONS into
    # the same call site. Set via PUT /auth/me (reuses alerts.py's _validate_webhook_url SSRF
    # guard — https-only, no private/internal IP targets).
    notification_webhook: Mapped[str | None] = mapped_column(String(2048), nullable=True)
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
    push_subscriptions: Mapped[list["PushSubscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")


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
    delisted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cik: Mapped[str | None] = mapped_column(String(16), nullable=True)  # T208: SEC EDGAR CIK
    index_membership: Mapped[str | None] = mapped_column(String(256), nullable=True)  # T11: comma-separated index names
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
    # T230-CHARTING-PREMARKET: 'PRE' | 'REGULAR' | 'POST' for intraday timeframes (yfinance
    # prepost=True bars); always 'REGULAR' for daily/weekly bars. Plain String, not a new
    # Postgres enum type, to keep the ALTER TABLE this needs (existing, populated table —
    # create_all() won't add it) a single column add with no new type to manage.
    session: Mapped[str] = mapped_column(String(8), default="REGULAR", server_default="REGULAR")

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
    ts: Mapped[datetime] = mapped_column(DateTime, index=True, server_default=func.now())
    signal: Mapped[SignalType] = mapped_column(SAEnum(SignalType))
    horizon: Mapped[SignalHorizon] = mapped_column(SAEnum(SignalHorizon))
    confidence: Mapped[float] = mapped_column(Float)  # 0-100
    bullish_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="signal-engine")
    __table_args__ = (
        Index("ix_signals_stock_ts", "stock_id", "ts"),
        Index("ix_signals_stock_horizon_ts", "stock_id", "horizon", "ts"),
        # DB also has: UNIQUE (stock_id, horizon, date_trunc('day', ts)) — uq_signals_stock_horizon_day
        # This is a function-based index, not expressible as UniqueConstraint in SQLAlchemy.
        # Created manually: CREATE UNIQUE INDEX uq_signals_stock_horizon_day ON signals
        #   USING btree (stock_id, horizon, date_trunc('day', ts));
    )


class Ranking(Base):
    __tablename__ = "rankings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    score: Mapped[float] = mapped_column(Float)  # K-Score 0-100
    technical: Mapped[float] = mapped_column(Float)
    momentum: Mapped[float] = mapped_column(Float)
    # T232-RANKSTALE: value/growth were NOT NULL, but compute_kscore legitimately returns
    # None for stocks lacking sufficient fundamentals data (KS-4) — every bulk ranking
    # refresh batch containing even one such stock failed the whole INSERT with
    # NotNullViolation, silently (no logging existed at the time) stalling rankings for
    # both markets for 10+ days. Made nullable to match what the scoring layer produces.
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    growth: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User | None"] = relationship(back_populates="watchlist_items")
    watchlist: Mapped["Watchlist | None"] = relationship(back_populates="items")


class AlertCondition(str, enum.Enum):
    ABOVE = "above"
    BELOW = "below"
    CROSS_ABOVE_EMA = "cross_above_ema"   # threshold = EMA period (20/50/200)
    CROSS_BELOW_EMA = "cross_below_ema"
    NEW_52WK_HIGH   = "new_52wk_high"     # threshold unused (store 0)
    NEW_52WK_LOW    = "new_52wk_low"
    GOLDEN_CROSS         = "golden_cross"          # EMA50 crosses above EMA200; threshold unused
    DEATH_CROSS          = "death_cross"           # EMA50 crosses below EMA200; threshold unused
    MACD_BULLISH_CROSS   = "macd_bullish_cross"    # MACD line crosses above signal; threshold unused
    RSI_OVERSOLD_BOUNCE  = "rsi_oversold_bounce"   # RSI crosses above 30 from below; threshold unused
    DOUBLE_BOTTOM        = "double_bottom"         # W-pattern detected; threshold unused
    BREAKOUT             = "breakout"              # Price closes above 20-day high with volume surge
    VOLUME_SPIKE         = "volume_spike"          # threshold = multiplier of 20-day avg volume (e.g. 3.0)
    PCT_BELOW_52WK_HIGH  = "pct_below_52wk_high"   # threshold = % below 52-week high to trigger (e.g. 10)


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
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # T230-ALERTING-COMPOUND-CONDITIONS: optional list of extra AND-conditions evaluated
    # alongside the base condition/threshold above. Each item is
    # {"metric": "volume_ratio"|"rsi"|"signal", "op": "gte"|"lte"|"eq", "value": float|str}.
    # ALL must pass (AND) for the alert to fire — the base condition is always required too.
    # NULL/empty = old single-condition behavior, unchanged.
    compound_conditions: Mapped[list | None] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship(back_populates="price_alerts")


class SignalAlert(Base):
    __tablename__ = "signal_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_signal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # "all" = all signal transitions; "buy_only" = only transitions to/from BUY
    alert_mode: Mapped[str] = mapped_column(String(16), server_default="all")
    # horizon this subscription tracks: SHORT / SWING / LONG / GROWTH
    horizon: Mapped[str] = mapped_column(String(16), server_default="SWING")
    # when True, only fire if ≥2 horizons agree on the new direction
    require_consensus: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="signal_alerts")

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "horizon", name="uq_signal_alerts_user_symbol_horizon"),
    )


class PushSubscription(Base):
    """T230-ALERTING-PUSH-NOTIFICATIONS: one browser/device Web Push subscription per user.
    A user can have multiple (one per browser/device they've enabled push on). Populated by
    the frontend's service worker registration via POST /push/subscribe; consumed by
    send_push_notification() in email_service.py alongside every existing email/webhook
    alert delivery path.
    """
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(512), unique=True)
    p256dh_key: Mapped[str] = mapped_column(String(256))
    auth_key: Mapped[str] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="push_subscriptions")


class UserPosition(Base):
    __tablename__ = "user_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    shares: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))
    avg_cost: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="positions")
    trades: Mapped[list["PositionTrade"]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_user_positions_user_symbol"),
        Index("ix_user_positions_user_symbol", "user_id", "symbol"),
    )


class PositionTrade(Base):
    __tablename__ = "position_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("user_positions.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(8))  # BUY | SELL
    shares: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))
    price: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))
    date: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    position: Mapped["UserPosition"] = relationship(back_populates="trades")


class UserCash(Base):
    __tablename__ = "user_cash"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(8))
    amount: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False), default=0.0)

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
    # INT-8: Multi-window forward returns (filled independently as windows close)
    price_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_5d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_10d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_20d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # INT-8: Research alignment at signal time (from research engine cache)
    # T247-SIGNALENGINE-RESEARCHREC-TOOSHORT: was String(16) — research-engine's real
    # recommendation vocabulary includes "INSUFFICIENT DATA" (17 chars), which raised an
    # unhandled psycopg2.errors.StringDataRightTruncation on every occurrence, silently
    # failing the ENTIRE batch insert of up to 25 signal_outcomes rows in
    # evaluate_signal_outcomes() (confirmed happening repeatedly in production 2026-07-14).
    # Widened with margin above the longest current value (10-char "STRONG BUY").
    research_rec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    research_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts_evaluated: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # T232-OC6: set when the hold window closed but no exit price was ever found (delisting,
    # halt, or ingestion gap) — is_correct/pct_return/exit_date stay NULL. NULL means normal,
    # fully-evaluated outcome. Written after a grace period so a brief ingestion delay isn't
    # mistaken for a permanent loss of price data.
    skip_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

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


# ── Broker Integration ────────────────────────────────────────────────────────

class BrokerConnection(Base):
    """A user's configured connection to a real brokerage account.

    broker_type values:
      'etrade'          — E*Trade production API (OAuth 1.0a)
      'etrade_sandbox'  — E*Trade sandbox (paper money, same API)
      'fidelity_manual' — No API; trade instructions shown for manual execution
    config stores OAuth credentials and account info as JSON:
      E*Trade: {consumer_key, consumer_secret, oauth_token, oauth_token_secret,
                request_token, request_token_secret, account_id_key}
      Fidelity manual: {account_number, notes}
    Credentials are stored at-rest in the DB (same security boundary as the
    JWT secret). Do NOT expose them through any API endpoint response.
    """
    __tablename__ = "broker_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))          # display label, e.g. "My E*Trade"
    broker_type: Mapped[str] = mapped_column(String(32))    # 'etrade' | 'etrade_sandbox' | 'fidelity_manual'
    account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)   # broker account ID (public)
    config: Mapped[dict] = mapped_column(JSON, default=dict) # credentials — never return to frontend
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False)  # OAuth complete?
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


# ── WF-2: Paper Trading Engine ────────────────────────────────────────────────

class PaperPortfolio(Base):
    """Configuration and running cash balance for an autonomous paper portfolio."""
    __tablename__ = "paper_portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="Paper Portfolio")
    initial_capital: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))
    current_cash: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))
    # JSON config — see paper_trading_engine.py _DEFAULT_CONFIG
    config: Mapped[dict] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Broker connection — null means paper-only simulation
    broker_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    trades: Mapped[list["PaperTrade"]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")
    equity_curve: Mapped[list["PaperEquityCurve"]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")


class PaperTrade(Base):
    """One simulated paper trade — open or closed."""
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id", ondelete="SET NULL"), nullable=True)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="SET NULL"), nullable=True, index=True)  # PT-H2: for double-top mid-trade detection
    trading_style: Mapped[str] = mapped_column(String(16), default="GROWTH")  # GROWTH|SWING|LONG|SHORT
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)    # H-SECTOR: snapshotted at entry for PA-D1

    # Entry
    entry_date: Mapped[date] = mapped_column(Date, index=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    entry_price: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))
    shares: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))
    stop_loss: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))    # initial hard stop
    take_profit: Mapped[float | None] = mapped_column(Numeric(20, 6, asdecimal=False), nullable=True)
    current_stop: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False))  # trails up

    # Decision quality at entry
    entry_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_decision_notes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    confidence_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    kscore_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    rr_ratio_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_regime_at_entry: Mapped[str | None] = mapped_column(String(16), nullable=True)
    entry_reasons: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Signal.reasons snapshot

    # Live tracking
    current_price: Mapped[float | None] = mapped_column(Numeric(20, 6, asdecimal=False), nullable=True)
    highest_price: Mapped[float | None] = mapped_column(Numeric(20, 6, asdecimal=False), nullable=True)
    stage: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open|closed
    hold_days: Mapped[int] = mapped_column(Integer, default=0)

    # T232-PT6: realized P&L from scale-out partial exits, accumulated as they happen.
    # Folded into `pnl` at final close so a trade that scaled out profitably then trailed
    # to breakeven on the remainder is scored as a win, not a loser. entry_shares is the
    # original position size before any scale-outs shrank `shares` — needed to compute a
    # cost-basis-correct pct_return once part of the position has already been sold.
    realized_pnl: Mapped[float] = mapped_column(Numeric(20, 6, asdecimal=False), default=0.0)
    entry_shares: Mapped[float | None] = mapped_column(Numeric(20, 6, asdecimal=False), nullable=True)

    # Exit (null until closed)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Numeric(20, 6, asdecimal=False), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_reasons: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Numeric(20, 6, asdecimal=False), nullable=True)
    pct_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    # PA-G3: signal lifecycle — which signal was active at exit (for walk-forward attribution)
    signal_at_exit_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id", ondelete="SET NULL"), nullable=True)
    signal_at_exit_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # BUY/HOLD/SELL/WAIT

    # Real-broker execution tracking (null for paper-only portfolios)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    portfolio: Mapped["PaperPortfolio"] = relationship(back_populates="trades")

    __table_args__ = (
        Index("ix_paper_trades_portfolio_stage", "portfolio_id", "stage"),
        Index("ix_paper_trades_signal_at_exit", "signal_at_exit_id"),
    )


class PaperEquityCurve(Base):
    """Daily equity snapshots for the paper portfolio equity curve chart."""
    __tablename__ = "paper_equity_curve"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    equity: Mapped[float] = mapped_column(Float)             # cash + open position value
    cash: Mapped[float] = mapped_column(Float)
    open_positions_value: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions_count: Mapped[int] = mapped_column(Integer, default=0)
    spy_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    qqq_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    hsi_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_regime: Mapped[str | None] = mapped_column(String(16), nullable=True)  # PT-A2

    portfolio: Mapped["PaperPortfolio"] = relationship(back_populates="equity_curve")

    __table_args__ = (
        UniqueConstraint("portfolio_id", "date", name="uq_paper_equity_portfolio_date"),
    )


class Fundamental(Base):
    """Snapshot of company fundamentals — one row per stock per fetch date.

    Persisted from yfinance whenever the /fundamentals endpoint is called.
    Used as static ML features (broadcast to all price rows for a stock during
    training/inference). Updated at most once per day via the (stock_id, as_of)
    unique constraint.
    """
    __tablename__ = "fundamentals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    # Valuation
    trailing_pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    forward_pe: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_to_book: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Profitability
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_on_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_on_assets: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Growth
    revenue_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    earnings_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Cash flow / valuation
    free_cashflow: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Sentiment
    short_percent_of_float: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Analyst consensus
    recommendation_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    number_of_analysts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Phase 1 additions — valuation
    peg_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_to_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    # T217-B: DDM — trailing annual dividend yield (dividend_rate / price), 0–1 scale
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("stock_id", "as_of", name="uq_fundamentals_stock_date"),
    )


# ── Event Intelligence Platform ───────────────────────────────────────────────

class EconomicEvent(Base):
    __tablename__ = "economic_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    country: Mapped[str] = mapped_column(String(8), index=True)
    event_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    importance: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # T249-MARKETMOVER-P2: LLM-generated reaction read, written once actual_value lands for
    # a release-day-armed fast-poll-tracked event (CPI/PPI/NFP/GDP/PCE via FRED, FOMC via the
    # Fed's press_monetary.xml RSS feed). reaction_sent_at is separate from reaction_generated_at
    # so market-data's alert-fan-out job (which polls this table) can tell "generated but not
    # yet emailed" apart from "already emailed" without a third status column — NULL means
    # not yet sent. New columns on an existing, already-populated table need a manual
    # ALTER TABLE in every environment; create_all() will not add these automatically
    # (see this repo's standing create_all()-gap discipline).
    reaction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reaction_generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reaction_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("event_type", "country", "event_date", name="uq_economic_event"),
    )


class EarningsEvent(Base):
    __tablename__ = "earnings_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_quarter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    eps_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    surprise_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_surprise_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    earnings_strength_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    post_earnings_return_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    post_earnings_return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("stock_id", "fiscal_year", "fiscal_quarter", name="uq_earnings_stock_period"),
        Index("ix_earnings_stock_date", "stock_id", "report_date"),
        Index("ix_earnings_report_date", "report_date"),
    )


class InsiderTransaction(Base):
    __tablename__ = "insider_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    insider_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    insider_role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transaction_type: Mapped[str] = mapped_column(String(32))
    shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    price_per_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    transaction_date: Mapped[date] = mapped_column(Date, index=True)
    filing_date: Mapped[date] = mapped_column(Date, index=True)
    accession_number: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("accession_number", name="uq_insider_accession"),
        Index("ix_insider_stock_date", "stock_id", "transaction_date"),
    )


class CongressTrade(Base):
    __tablename__ = "congress_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    politician_name: Mapped[str] = mapped_column(String(255), index=True)
    party: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chamber: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="SET NULL"), nullable=True, index=True)
    transaction_type: Mapped[str] = mapped_column(String(32))
    amount_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    disclosure_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("politician_name", "ticker", "trade_date", "transaction_type", name="uq_congress_trade"),
        Index("ix_congress_ticker_date", "ticker", "trade_date"),
    )


class InstitutionalHolding(Base):
    __tablename__ = "institutional_holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fund_name: Mapped[str] = mapped_column(String(255), index=True)
    fund_cik: Mapped[str] = mapped_column(String(32), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    period_date: Mapped[date] = mapped_column(Date, index=True)
    shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    value_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("fund_cik", "stock_id", "period_date", name="uq_inst_holding"),
        Index("ix_inst_holding_value", "value_usd"),
    )


class InstitutionalTransaction(Base):
    __tablename__ = "institutional_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fund_name: Mapped[str] = mapped_column(String(255), index=True)
    fund_cik: Mapped[str] = mapped_column(String(32))
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    period_date: Mapped[date] = mapped_column(Date, index=True)
    change_type: Mapped[str] = mapped_column(String(32))
    shares_change: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    value_change_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("fund_cik", "stock_id", "period_date", name="uq_inst_txn"),
    )


class PoliticalEvent(Base):
    __tablename__ = "political_events"
    __table_args__ = (
        UniqueConstraint("stock_id", "event_type", "event_date", "agency", name="uq_political_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    agency: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    impact: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StockConnectFlow(Base):
    """Daily Stock Connect southbound flow per HK stock (mainland investors buying HK)."""
    __tablename__ = "stock_connect_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    flow_date: Mapped[date] = mapped_column(Date, index=True)
    net_shares: Mapped[float | None] = mapped_column(Float, nullable=True)   # daily change in mainland holdings (shares)
    net_hkd_m: Mapped[float | None] = mapped_column(Float, nullable=True)    # net buy value in HKD millions
    holdings_shares: Mapped[float | None] = mapped_column(Float, nullable=True)  # total mainland holdings (shares)
    holdings_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # % of total issued shares held by mainland
    score: Mapped[float | None] = mapped_column(Float, nullable=True)         # 0-100 southbound momentum score
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("stock_id", "flow_date", name="uq_stock_connect_flow"),
    )


class CatalystScore(Base):
    __tablename__ = "catalyst_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    catalyst_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    earnings_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    insider_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    congress_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    institutional_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    economic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    earnings_days_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_insider_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_congress_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("stock_id", name="uq_catalyst_stock"),
    )


# ── T208: SEC EDGAR 8-K Filings ───────────────────────────────────────────────

class SecFiling(Base):
    """SEC EDGAR 8-K filing record — one row per unique accession number.

    Ingested daily (post-US-close) for tracked US stocks. HK stocks have no
    EDGAR filings and are skipped automatically in the ingest function.
    is_material=True when the filing touches items 1.01, 2.01, 2.06, 5.02, or
    8.01 — the items most likely to move stock prices materially.
    """
    __tablename__ = "sec_filings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    cik: Mapped[str] = mapped_column(String(16), nullable=False)
    accession: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    form: Mapped[str] = mapped_column(String(16), nullable=False, default="8-K")
    filed_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    items: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_material: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_sec_filings_symbol_date", "symbol", "filed_date"),
    )


# ── T209: HKEX Stock Connect Southbound Flows ─────────────────────────────────

class HkConnectFlow(Base):
    """Daily HKEX Stock Connect southbound flow per HK stock (symbol-keyed).

    Populated by hk_connect.ingest_southbound_flows() — called once daily after
    HK market close. Unlike StockConnectFlow (which uses a stock_id FK), this
    table uses the symbol string directly so the ingest function does not require
    a stocks table lookup for each symbol.
    """
    __tablename__ = "hk_connect_flows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    net_buy_hkd: Mapped[float | None] = mapped_column(Float, nullable=True)    # net buy in HKD
    buy_hkd: Mapped[float | None] = mapped_column(Float, nullable=True)        # gross buy in HKD
    sell_hkd: Mapped[float | None] = mapped_column(Float, nullable=True)       # gross sell in HKD
    quota_used_pct: Mapped[float | None] = mapped_column(Float, nullable=True) # daily quota utilisation %
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_hk_connect_flow"),
    )


# ── T220-F: Fundamentals Snapshot for Earnings Revision Momentum ──────────────

class FundamentalsSnapshot(Base):
    """Weekly snapshot of per-symbol fundamentals for revision momentum tracking.

    Populated every Sunday by the fundamentals_snapshot_weekly scheduler job.
    Used by the ML feature builder to compute eps_revision_direction — the
    direction of analyst recommendation changes over the prior 8 snapshots.
    """
    __tablename__ = "fundamentals_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    recommendation_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    eps_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    earnings_growth: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_on_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    # T234-ML-FUND-BROADCAST-LEAKAGE: added so builder.py can point-in-time join these
    # columns (merge_asof) instead of broadcasting today's value across all historical
    # training rows. History accumulates going forward only — rows before this column
    # existed have NULL here, which builder.py's PIT join treats as NaN (XGBoost-safe).
    gross_margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    fcf_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_ratio_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_percent_of_float: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_to_book: Mapped[float | None] = mapped_column(Float, nullable=True)
    peg_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    debt_to_equity: Mapped[float | None] = mapped_column(Float, nullable=True)
    ddm_discount: Mapped[float | None] = mapped_column(Float, nullable=True)
    piotroski_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("symbol", "snapshot_date", name="uq_fundamentals_snapshot_sym_date"),)


# ── T233-SELFIMPROVE-PHASE3: Tune History ──────────────────────────────────────

class TuneHistory(Base):
    """One row per attempted tuning candidate — promoted or rejected.

    See docs/DESIGN_PROMOTION_GATE_PHASE3_2026-07-05.md for the full design. Written by
    services/market-data/src/backtest/promotion_gate.py. Every call to evaluate_and_record()
    writes exactly one row regardless of outcome, so "we tried X and it didn't help" is
    always visible without reconstructing state from container logs across services — the
    gap that let the CAL-1 corrupted-threshold incident go undetected.
    """
    __tablename__ = "tune_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)  # uuid4, groups a multi-style run
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    parameter_class: Mapped[str] = mapped_column(String(32))  # "gate_threshold" for Phase 3
    parameter_name: Mapped[str] = mapped_column(String(64))   # e.g. "min_entry_score"
    style: Mapped[str] = mapped_column(String(16))
    market: Mapped[str] = mapped_column(String(8))
    old_value: Mapped[dict] = mapped_column(JSON)
    new_value: Mapped[dict] = mapped_column(JSON)
    train_window_start: Mapped[date] = mapped_column(Date)
    train_window_end: Mapped[date] = mapped_column(Date)
    validation_window_start: Mapped[date] = mapped_column(Date)
    validation_window_end: Mapped[date] = mapped_column(Date)
    train_ev_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_ev_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_validation_ev_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_n: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Deliberately NOT a true portfolio-equity drawdown — see the design doc §1/§3 for why a
    # faithful version needs Phase 2b's full equity-curve replay. This is the largest single
    # trade loss in the validation-slice return list, a narrower question than real drawdown.
    approx_worst_trade_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_worst_trade_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoted: Mapped[bool] = mapped_column(Boolean)
    gate_failures: Mapped[list] = mapped_column(JSON, default=list)
    triggered_by: Mapped[str] = mapped_column(String(16), default="manual")  # manual | scheduled (Phase 5)
    # SELFIMPROVE-NO-RETRO-FEEDBACK-LOOP: real win-rate/EV realized in SignalOutcome data
    # AFTER this row's promoted change took effect — populated by a monthly backfill job,
    # NULL until enough time + samples have accumulated to compute it (or if promoted=False,
    # since a rejected change never affected live trading and has nothing to retro-check).
    # This is what closes the loop from "we predicted this would help" (validation_ev_pct
    # above) to "did it actually help" — every prior mechanism recorded only the former.
    realized_ev_pct_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_n_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    realized_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CapeReading(Base):
    """CAPE (Shiller cyclically-adjusted P/E) ratio for the S&P 500 — macro valuation
    context feeding the AI-bubble-warning indicator.

    Source is multpl.com's shiller-pe feed/table, NOT Yale's own ie_data.xls — that file
    is real but was found stale (Last-Modified Oct 2023, ~2.75 years old at investigation
    time) and Shiller's site was mid-migration to a new Yale SOM page with no working
    direct download found. multpl.com publishes a genuine, site-wide Atom feed
    (multpl.com/{indicator}/atom, confirmed identical pattern across multiple indicator
    pages, not a one-off) plus a stable `id="datatable"` HTML table
    (multpl.com/shiller-pe/table/by-month) for historical backfill — both verified live
    and current before choosing this over a same-page HTML scrape. Still an unofficial
    third-party source (same fragility CLASS as the dead-congress-data incident, just a
    more stable access pattern), so staleness must be monitored the same way via
    dq_check:cape_reading, not assumed reliable forever.
    """
    __tablename__ = "cape_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reading_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    cape_value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="multpl")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
