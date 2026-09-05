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


# T322-FEATURE-TIERING: a genuinely separate axis from UserRole above — role gates ADMIN-only
# operations (user management, config, restricted symbols, ...); tier gates which TRADING
# FEATURES a regular user sees at all (e.g. the Options Game Plan below). An ADMIN's own tier
# is independent — an admin isn't automatically "advanced", and an advanced non-admin user
# still can't touch admin-only routes. Deliberately a plain 2-value enum (not a per-feature
# flag set) per the explicit design choice made for this first tiered feature — extend this
# enum, not a second parallel mechanism, if a 3rd tier is ever needed.
class UserTier(str, enum.Enum):
    BASIC = "BASIC"
    ADVANCED = "ADVANCED"


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
    tier: Mapped[UserTier] = mapped_column(SAEnum(UserTier), default=UserTier.BASIC)
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
    squeeze_watches: Mapped[list["SqueezeWatch"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sr_watches: Mapped[list["SrWatch"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    earnings_alert_subscriptions: Mapped[list["EarningsAlertSubscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    push_subscriptions: Mapped[list["PushSubscription"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    stock_goals: Mapped[list["StockGoal"]] = relationship(back_populates="user", cascade="all, delete-orphan")


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
    # AUD-SIGNAL3-EVALSELECTIONBIAS (2026-09-02): this row is upserted ~77x/trading day (every
    # /signals/refresh cycle) via ON CONFLICT (stock_id, horizon, date_trunc('day', ts)) DO
    # UPDATE — signal/confidence/bullish_probability/reasons above are the LIVE, ever-changing
    # display state, correctly always reflecting the current signal. But
    # evaluate_signal_outcomes() (outcomes.py) previously read those same live columns for
    # BOTH selecting which signals to score AND what confidence/reasons to score them with —
    # meaning (1) a signal that was BUY at 10am but faded to HOLD by close was invisible to
    # evaluation entirely (the WHERE clause filters Signal.signal.in_([BUY, SELL]), the FINAL
    # state), and (2) even a signal that stayed BUY all day was scored using its 4pm
    # confidence/reasons, not the state that actually fired the trade thesis being measured.
    # These 5 columns capture the state at first_buy_sell_at, the FIRST time signal transitions
    # to BUY/SELL on a given calendar day — set ONCE via COALESCE in the upsert (see routes.py's
    # own upsert SQL), frozen for the rest of that day regardless of how many times the live
    # columns above are subsequently overwritten. A new calendar day's first upsert starts a
    # fresh capture (the day boundary is the same date_trunc('day', ts) the live row's own
    # conflict target already uses). Nullable because a HOLD-only day never populates them.
    # first_buy_sell_bullish_probability mirrors bullish_probability (not just confidence) —
    # SignalOutcome.fused_prob is load-bearing for calibration.py's ML-weight-cap re-simulation
    # grids and analytics.py's rank-IC computation; leaving it unfrozen would silently corrupt
    # those the same way the un-fixed bug corrupted confidence/reasons.
    first_buy_sell_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_buy_sell_signal: Mapped[SignalType | None] = mapped_column(SAEnum(SignalType), nullable=True)
    first_buy_sell_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_buy_sell_bullish_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_buy_sell_reasons: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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


class ConditionalOrder(Base):
    """T286-CONDITIONAL-ORDER: a single-hop "if TRIGGER then ACTION" order on ONE portfolio's
    ONE symbol — the item deliberately deferred from the earlier Tier 287 batch pending its own
    dedicated design pass. Deliberately named ConditionalOrder, not "chain": single trigger,
    single action, no multi-step state — a chain of these is just several separate rows the
    user creates individually, matching the reasoning that motivated this scoping.

    Portfolio-scoped, NOT user-scoped: PaperPortfolio has no user_id (paper portfolios are
    app-wide, not per-user, per this repo's own long-documented fact) — a conditional order
    modifies how/when a SPECIFIC portfolio acts on a symbol, so it must be anchored to that
    portfolio, not a bare user. Notification email follows the same PriceAlert-subscriber
    audience convention every other portfolio-wide alert in this app already uses.

    Same-symbol only, single-hop only: this is a deliberate scope decision (real-money-adjacent
    feature) — no cross-symbol triggers ("if SPY breaks down, sell my NVDA"), no multi-step
    chains. A BUY action never bypasses the real entry pipeline — it requires a real,
    already-existing BUY-eligible Signal for the symbol and is scored through the SAME
    _should_enter() gate every organic entry already goes through; a conditional order only
    ever decides WHEN to act, never WHETHER the underlying setup itself is a valid entry.
    """
    __tablename__ = "conditional_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    # "buy" | "sell_partial" | "sell_all" | "tighten_stop" | "close_position" | "alert_only"
    action_type: Mapped[str] = mapped_column(String(24))
    # buy: unused (sizing follows the portfolio's own normal risk-based sizing, same as an
    #   organic entry). sell_partial: fraction of CURRENT shares to sell (0-1). tighten_stop:
    #   the new stop price (must be tighter than the trade's current stop, enforced monotonic
    #   the same way scale-out/trailing-stop logic already is). sell_all/close_position/
    #   alert_only: unused.
    action_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # List of condition dicts, SAME shape as PriceAlert.compound_conditions:
    # {"metric": "price"|"rsi"|"volume_ratio"|"signal"|"position_pnl_pct"|"time", "op": "gte"|
    # "lte"|"eq", "value": float|str}. trigger_logic controls how the list combines.
    conditions: Mapped[list] = mapped_column(JSON)
    trigger_logic: Mapped[str] = mapped_column(String(8), server_default="AND")  # "AND" | "OR"
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # "pending" | "triggered" | "failed" | "expired" | "cancelled"
    status: Mapped[str] = mapped_column(String(16), server_default="pending", index=True)
    status_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set once the order actually fires — the PaperTrade a "buy" action created, or the
    # PaperTrade a sell_partial/sell_all/tighten_stop/close_position action was applied to.
    # Never set for alert_only.
    resulting_trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("paper_trades.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SqueezeWatch(Base):
    """T260-BEARISH-PUTS-WATCHLIST: a user manually adds a short-squeeze-page candidate here to
    track its short-pressure state over time and get a one-shot email the moment that pressure
    fades — deliberately a NEW, dedicated table rather than reusing PriceAlert/SignalAlert, since
    neither is aware of the specific squeeze-candidate metrics (short %, puts OI concentration)
    that need to be captured at add-time to detect a genuine reversal later.

    watch_type distinguishes which scan the candidate came from: "short_squeeze" (classic short-
    interest-of-float squeeze, from short_squeeze.tsx) or "bearish_puts" (the puts-heavy options-
    expiry watch, from _bearish_puts_watch_candidates()). Each has its own revert condition,
    checked in check_squeeze_watch_reverts() (scheduler.py):
      - short_squeeze: reverts when short_percent_of_float drops back below the ADD-TIME value's
        own qualifying threshold, OR price recovers back above the price captured at add-time.
      - bearish_puts: reverts when the puts-OI concentration drops back below the alert's own
        55% threshold, OR price recovers back above the price captured at add-time.
    Both use an OR, not an AND — per the user's own explicit choice, either signal alone is a
    legitimate sign the short-side pressure has faded, not something both must show together.
    """
    __tablename__ = "squeeze_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    watch_type: Mapped[str] = mapped_column(String(16))  # "short_squeeze" | "bearish_puts"
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    price_at_add: Mapped[float | None] = mapped_column(Float, nullable=True)
    metric_at_add: Mapped[float | None] = mapped_column(Float, nullable=True)  # short_percent_of_float OR puts concentration_pct, whichever watch_type applies
    reverted: Mapped[bool] = mapped_column(Boolean, default=False)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revert_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)

    user: Mapped["User"] = relationship(back_populates="squeeze_watches")

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "watch_type", name="uq_squeeze_watch_user_symbol_type"),
    )


class SrWatch(Base):
    """SR-WATCH-PROXIMITY-ALERT: a user picks a stock and gets a one-shot email the moment
    price gets close (within an ATR-scaled band) to its nearest support or resistance level —
    "watch and decide whether to buy/sell yourself" rather than an automated trade signal.
    Deliberately a NEW table, not a PriceAlert/SignalAlert row, for the same reason
    SqueezeWatch is its own table: neither existing alert type is aware of computed S/R levels
    or ATR, both of which need to be captured/recomputed by check_sr_watch_reverts()
    (scheduler.py) against live technical-analysis data, not a single fixed target price.

    Genuinely different lifecycle from SqueezeWatch's permanent one-shot `reverted` flag: the
    user explicitly asked for "fire once per approach, then reset once price moves away and
    comes back" — so `currently_near` tracks the CURRENT state (True while price sits inside
    the ATR band, False once it moves back out), and an alert only sends on the False->True
    transition, never on every cycle price stays inside the band. `last_alert_at`/
    `last_alert_level_kind`/`last_alert_level_price` record the most recent firing purely for
    display/audit — they are NOT the dedup mechanism itself (`currently_near` is).
    """
    __tablename__ = "sr_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    atr_multiplier: Mapped[float] = mapped_column(Float, default=1.0)  # "close" = within N x ATR(14) of a level
    currently_near: Mapped[bool] = mapped_column(Boolean, default=False)
    last_alert_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_alert_level_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "support" | "resistance"
    last_alert_level_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)

    user: Mapped["User"] = relationship(back_populates="sr_watches")

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_sr_watch_user_symbol"),
    )


class EarningsAlertSubscription(Base):
    """BUG-EARNINGS-IMPACT-UNSCOPED follow-up (2026-08-05): a dedicated, DURABLE per-symbol
    opt-in for earnings result/impact alerts — deliberately a NEW table rather than continuing
    to piggyback on PriceAlert.

    The gap this closes: check_earnings_reactions()/check_earnings_impact_alerts() previously
    (and, additively, still) treat any un-triggered PriceAlert on a symbol as earnings-alert
    consent. But PriceAlert is fundamentally a ONE-SHOT trigger mechanism — `triggered=True`
    once the price crosses the set threshold, dropping the row out of every future query that
    filters `PriceAlert.triggered.is_(False)`. A user could have a price alert cross (for any
    reason, unrelated to earnings) hours or days before a real earnings print and silently lose
    coverage for that report with no warning at all. Found live, the day before a heavy
    earnings-release day.

    This table has no `triggered`/one-shot concept at all — subscribing here means "always
    alert me for this symbol's earnings," full stop, until the user explicitly unsubscribes.
    Deliberately ADDITIVE, not a replacement: a symbol qualifies for earnings alerts if EITHER
    an active PriceAlert exists on it OR a row exists here — nobody who was already relying on
    PriceAlert coverage loses it, and this becomes the more reliable path going forward.
    """
    __tablename__ = "earnings_alert_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="earnings_alert_subscriptions")

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_earnings_alert_sub_user_symbol"),
    )


class StockGoal(Base):
    """T286-STOCK-GOALS: a user-defined price/share/date target for a symbol, with progress
    tracked against the real, already-fetched current price — genuinely new, confirmed via a
    direct code search to have zero existing equivalent anywhere in this app (unlike most of
    docs/FEATURE_ROADMAP_PYRAMID_GOALS_2026-08-16.md's other proposals, which turned out to
    already exist under different names — see that tracker item's own verification note).

    Deliberately simple relative to the roadmap doc's own proposed schema — no goal_type enum,
    no separate accumulation/income categories. A goal is just "I want this symbol to reach
    (some combination of) a price / a share count / a date," and progress is computed FRESH on
    read from whichever targets are actually set, never stored/staled. This mirrors this app's
    own established "don't persist a value that can be cheaply recomputed from live data"
    discipline (e.g. SqueezeAlertOutcome's own forward-return evaluator recomputes rather than
    trusting a stored intermediate).

    Exactly one of target_price / target_shares may be meaningfully "the" goal a user is
    tracking at a time in the UI (the progress bar needs one dominant metric to show), but both
    columns are independent and nullable — a user can set either, both, or neither (a bare
    target_date with no numeric target is a valid "just remind me to check in on this date"
    goal). notes is a free-text field for a plain-language description ("build a full position
    for the dividend").
    """
    __tablename__ = "stock_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))  # e.g. "Build 100-share position for dividend income"
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    start_price: Mapped[float] = mapped_column(Float)  # price at goal-creation time, for progress math
    start_shares: Mapped[float] = mapped_column(Float, default=0.0)  # shares already held at goal-creation time
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | achieved | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    achieved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="stock_goals")


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
    # T230-PORTFOLIO-BROKER-SYNC: NULL = manually entered by the user (default, unchanged
    # behavior for every existing row). Non-NULL = this row is synced FROM that broker
    # connection's live positions and must not be hand-edited via the manual CRUD routes —
    # the next sync cycle will just overwrite it. This is the provenance marker that lets a
    # sync job tell "safe to overwrite" (already broker-owned) apart from "would silently
    # clobber a manual entry" (NULL) without needing a separate table.
    broker_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    broker_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

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
    # T232-SIG10-SELLGATE: bearish-pillar count (0-4) for this signal, backfilled from
    # historical Price rows as-of signal_date via POST /signals/backfill_bearish_pillars —
    # NOT copied live from Signal.reasons at evaluation time like market_regime is, because
    # signals is upsert-per-(stock_id, horizon, day) and reasons gets overwritten on every
    # refresh, so the vast majority of older resolved outcomes never had a chance to capture
    # this field before it was overwritten. NULL means not yet backfilled/computed (a BUY row,
    # or a SELL row not yet covered by a backfill run) — never treated as 0 real pillars.
    bearish_pillars_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    # AUD262-ENTRY-EXIT-COMMISSION-EXCLUDED-FROM-PNL: entry commission was deducted from cash
    # at open but never stored anywhere on the trade, so pnl couldn't reconcile to it at close.
    entry_commission: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    # AUD-PT1-BROKERPOLLNEVERCLEARS: broker_order_id's presence is separately relied on by
    # _place_broker_exit() to decide whether a position needs a real broker SELL on exit, so it
    # can never be cleared once a fill is confirmed. This flag is the actual "still needs
    # polling for a fill" signal, consulted by poll_broker_order_fills()'s own query — without
    # it, every broker-entered position gets silently re-polled against the broker API forever,
    # not just until its fill is confirmed.
    broker_fill_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Set when a broker-linked portfolio's entry/exit order placement genuinely fails (e.g. a
    # real E*Trade rejection) — distinguishes "attempted and failed" from "never attempted"
    # (both otherwise leave broker_order_id null, making the two indistinguishable without a
    # log dig). Cleared back to None the moment a later attempt on the SAME leg (entry or exit)
    # succeeds — a stale failure reason must never linger after a real recovery.
    broker_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

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
    # AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED: exchange short interest settles ~2x/month with
    # a 1-2 week reporting lag, so a shortPercentOfFloat reading can legitimately be up to ~6
    # weeks stale by the time a user sees it. Yahoo's own quoteSummary schema (the same module
    # shortPercentOfFloat/sharesShort/shortRatio all come from) carries a settlement date
    # (dateShortInterest) alongside the figures — captured here so downstream consumers
    # (alerts, screeners) can finally distinguish "measured 3 days ago" from "measured 6 weeks
    # ago" instead of treating every reading as equally fresh.
    short_interest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Analyst consensus
    recommendation_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    number_of_analysts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # TIER82-FMP-ANALYST-ESTIMATES: analyst mean price target (yfinance targetMeanPrice) —
    # was already fetched into get_fundamentals()'s live response but never persisted, so it
    # could never be joined against historical price for a PIT-safe ML feature. See
    # analyst_pt_upside in ml-prediction's builder.py, which needs BOTH this value AND the
    # stock's own historical close price at the same snapshot date.
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    # T258-MACRO-SECTOR-IMPACT: structured sector-impact lists from the SAME LLM call that
    # produces reaction_text (no second call) — JSON-encoded string lists, matching
    # reaction_text's TEXT-column style rather than a Postgres array/JSONB type, since this
    # repo's other LLM-output columns (reasons/entry_decision_notes elsewhere) already use this
    # JSON-in-TEXT convention. Same manual-ALTER-TABLE requirement as reaction_text above — a
    # new column on an existing, already-populated table.
    sectors_helped: Mapped[str | None] = mapped_column(Text, nullable=True)
    sectors_hurt: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("event_type", "country", "event_date", name="uq_economic_event"),
    )


class CrossAssetReading(Base):
    """IF-04: daily cross-asset market readings (yield curve, credit spreads, dollar index) —
    a genuinely different SHAPE from EconomicEvent's row-per-release-event structure. One row
    per calendar day, all fields continuous numeric series, sourced from FRED (the same
    already-configured API key sync_fred()/sync_fred_release_dates() use).

    Deliberately scoped to what a real, verified FRED sync can populate — DGS10/DGS2/T10Y2Y
    (treasury yields + the 2s10s spread, the standard yield-curve-inversion signal) and
    BAMLH0A0HYM2 (high-yield OAS credit spread) and DTWEXBGS (broad trade-weighted dollar
    index). Gold/oil/commodities and VIX term structure are yfinance-sourced, not FRED, and
    intentionally deferred to a separate follow-on rather than adding a new cross-service
    dependency (event-intelligence has no yfinance dependency today) to this first slice.
    See .claude/CLAUDE.md's IF-04 review entry for the full scoping rationale.

    A brand-new table — create_all() handles it automatically, no manual ALTER TABLE needed.
    """
    __tablename__ = "cross_asset_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, unique=True, index=True)

    yield_2y: Mapped[float | None] = mapped_column(Float, nullable=True)
    yield_10y: Mapped[float | None] = mapped_column(Float, nullable=True)
    yield_curve_2s10s: Mapped[float | None] = mapped_column(Float, nullable=True)  # 10y - 2y, FRED's own T10Y2Y series
    hy_spread: Mapped[float | None] = mapped_column(Float, nullable=True)  # BAMLH0A0HYM2, high-yield OAS in %
    dxy: Mapped[float | None] = mapped_column(Float, nullable=True)  # DTWEXBGS, broad trade-weighted dollar index

    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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
    # T249-EARNINGS-LLM-IMPACT: LLM-generated impact read, mirroring EconomicEvent's
    # reaction_text/reaction_generated_at/reaction_sent_at + sectors_helped/sectors_hurt exactly
    # (same field names, same JSON-encoded-string-list convention for the sector lists) — see
    # generate_earnings_impact() in services/event-intelligence/src/services/earnings.py.
    # New columns on an existing, already-populated table need a manual ALTER TABLE in every
    # environment; create_all() will not add these automatically.
    impact_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    impact_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sectors_helped: Mapped[str | None] = mapped_column(Text, nullable=True)
    sectors_hurt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AUD-TRANSCRIPT: a genuinely qualitative read (was management confident/defensive/evasive)
    # from real earnings-call transcript excerpts (Unusual Whales, requires its own Advanced+
    # tier — see get_earnings_transcript()'s own docstring), grounded in the actual words used,
    # never invented. NULL when no transcript was available for this report (the common case
    # until/unless an Advanced+ UW subscription is active) — a missing qualitative read is a
    # real, different state from an empty one, never silently conflated.
    management_tone: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH: uniqueness used to be keyed
        # on (fiscal_year, fiscal_quarter), inferred via a naive (month-1)//3+1 calendar-month
        # bucket. This mislabeled every calendar-year company's report one quarter ahead (the
        # ANNOUNCEMENT lands ~1-6 weeks after the fiscal period it covers, often crossing into
        # the next calendar-quarter bucket) — and because it was the uniqueness key, two genuine
        # reports landing in the same calendar quarter would silently upsert-overwrite each
        # other, losing a real quarter of history. report_date is the genuinely unique,
        # unambiguous identity of a specific earnings event — see earnings.py's
        # _fetch_earnings_for_symbol() for how a shifting calendar-projected report_date is kept
        # from creating duplicate rows (finds and updates the existing eps_actual IS NULL
        # pending row in place, rather than relying on this constraint alone).
        UniqueConstraint("stock_id", "report_date", name="uq_earnings_stock_report_date"),
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
    # AUD-10B51: Form 4's own real <aff10b5One> boolean tag — the FILER'S OWN attestation to
    # the SEC of whether this filing's transactions were made under a pre-scheduled Rule 10b5-1
    # trading plan, confirmed present via 2 real live filings (AAPL, MSFT) before this field was
    # added, including one whose <remarks> independently corroborated it in free text. A real,
    # meaningful signal gap this app's insider pipeline had before this: an insider's DISCRETIONARY,
    # unscheduled sale (a real, timely signal) was previously indistinguishable from the same
    # insider executing a sale scheduled 6+ months earlier under an existing plan (which reveals
    # nothing about their view of the stock right now). NULL for pre-existing rows ingested before
    # this field existed, and for any (rare) filing this tag genuinely can't be parsed from — never
    # backfilled or guessed, matching this table's own existing nullable-field conventions.
    # Considered wiring Unusual Whales' own is_10b5_1 field as a second source/cross-check, but its
    # own documentation gives no derivation method at all, versus this field being the filer's own
    # direct, first-party attestation on the actual form — shipped as free-source-only for now;
    # UW's version remains a candidate future cross-check once a live subscription allows directly
    # comparing the two on real overlapping filings, not assumed to be either the same or different.
    is_10b5_1: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

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


class DarkPoolPrint(Base):
    """T323-DARKPOOL: real off-exchange block trades from Unusual Whales' `/api/darkpool/{ticker}`
    (per-symbol, on-demand fetch, Redis-cached — see get_dark_pool_prints() in
    services/market-data/src/services/unusual_whales.py) and `/api/darkpool/recent`
    (market-wide, feeding check_dark_pool_alerts() below).

    A "dark pool" is a private trading venue where large institutional block orders execute
    OFF the public exchange tape, reported afterward under FINRA's own trade-reporting rules —
    real, exchange-adjacent activity (not OTC/pink-sheet trades), just not visible on a normal
    Level 2 quote screen the way a lit-exchange trade is. UW's own field for this is the
    `market_center` code (e.g. "L" for a FINRA ADF dark venue) — persisted here as `venue`
    verbatim rather than this app inventing its own taxonomy on top of UW's real classification.

    One row per (symbol, executed_at, price, size) — matches UW's own print-level granularity
    (no aggregation at ingest time; rollups for the stock-detail card and ML feature are
    computed FROM this table, not baked into it, so the raw prints stay available for either
    consumer to aggregate differently later without a second ingest path).
    """
    __tablename__ = "dark_pool_prints"
    __table_args__ = (
        UniqueConstraint("symbol", "executed_at", "price", "size", name="uq_dark_pool_print"),
        Index("ix_dark_pool_symbol_executed", "symbol", "executed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="SET NULL"), nullable=True, index=True)
    price: Mapped[float] = mapped_column(Float)
    size: Mapped[int] = mapped_column(BigInteger)
    premium: Mapped[float | None] = mapped_column(Float, nullable=True)  # price * size, UW's own field when present
    venue: Mapped[str | None] = mapped_column(String(16), nullable=True)  # UW's market_center code, verbatim
    executed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DarkPoolAlertOutcome(Base):
    """T323-DARKPOOL: forward-return tracking for check_dark_pool_alerts(), matching
    SqueezeAlertOutcome's own established one-row-per-(alert_type, stock_id, fired_date)
    discipline exactly — this repo's standing rule that no alert ships as "trust it" with no
    way to later check whether it actually helped (see SqueezeAlertOutcome's own docstring for
    the full rationale, unrepeated here).

    alert_type is always "dark_pool_block" today (a single mechanism, unlike the squeeze/gamma
    table's multi-type split) — kept as a real column rather than hardcoded so a future second
    dark-pool alert variant (e.g. "sustained accumulation over N days") can share this table
    the same way gamma_unwind_calls/gamma_unwind_puts share SqueezeAlertOutcome.
    """
    __tablename__ = "dark_pool_alert_outcomes"
    __table_args__ = (
        UniqueConstraint("alert_type", "stock_id", "fired_date", name="uq_dark_pool_alert_outcome_type_stock_date"),
        Index("ix_dark_pool_alert_outcomes_type_date", "alert_type", "fired_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(24), index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    fired_date: Mapped[date] = mapped_column(Date, index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    alert_price: Mapped[float] = mapped_column(Float)
    qualifying_metric: Mapped[float | None] = mapped_column(Float, nullable=True)  # the print's own premium ($) at fire time
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_1d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_2d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_3d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_5d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_10d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_20d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    # TIER82-FMP-ANALYST-ESTIMATES: analyst mean price target as of this snapshot date.
    # Joined (PIT-safe, merge_asof) against the stock's own historical close price at this
    # same snapshot_date in builder.py to compute analyst_pt_upside — history accumulates
    # going forward only, rows before this column existed have NULL here (NaN-safe for XGBoost).
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("symbol", "snapshot_date", name="uq_fundamentals_snapshot_sym_date"),)


# ── wsz-analyst-accuracy-weighting: Per-Firm Historical Price Target Tracking ──

class AnalystPriceTarget(Base):
    """One row per (symbol, firm, grade_date) analyst price-target action, captured from
    yfinance's ticker.upgrades_downgrades DataFrame — which already carries currentPriceTarget/
    priorPriceTarget per firm, per action, but this app's existing analyst_actions ingestion
    (get_fundamentals() in market-data/src/api/routes.py) discarded both fields, keeping only
    the qualitative Firm/ToGrade/FromGrade/Action columns.

    Used to compute each firm's own historical accuracy (was current_price_target achieved
    within outcome_window_days of grade_date, per _check_target_achieved()'s own tolerance) —
    the raw material an accuracy-weighted consensus needs. History accumulates going forward
    only; scoring a firm requires enough elapsed time since grade_date for the outcome window
    to have closed AND real Price rows covering that window, so a fresh table starts with zero
    scoreable rows and needs real calendar time (not a backfill) before any weighting can occur.
    """
    __tablename__ = "analyst_price_targets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    firm: Mapped[str] = mapped_column(String(128), index=True)
    grade_date: Mapped[date] = mapped_column(Date, index=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)          # up|down|main|init|reit
    to_grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_price_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    prior_price_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Outcome (filled once outcome_window_days has elapsed since grade_date AND Price rows
    # covering that window exist — see _evaluate_analyst_target_outcomes() in
    # services/market-data/src/services/scheduler.py)
    outcome_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    target_achieved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    max_price_in_window: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("stock_id", "firm", "grade_date", name="uq_analyst_price_target_stock_firm_date"),
        Index("ix_analyst_price_target_firm_evaluated", "firm", "outcome_evaluated_at"),
    )


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


class VolumeAreaLevel(Base):
    """T252-VALUE-AREA-BREAKDOWN-ALERT: server-side POC/VAH/VAL per symbol/date, a straight
    Python port of frontend/src/lib/volumeProfile.ts's computeVolumeProfile() (client-only
    until this table). Computed daily from a rolling lookback window of daily bars — see
    compute_volume_area_levels() in services/market-data/src/services/volume_area.py, the
    single source of truth for this math on the backend (do not reimplement the bucket/
    value-area-expansion algorithm a second time; port changes to volumeProfile.ts's logic
    here too if the two ever need to agree, though as of this table's creation there is no
    shared module between the TS and Python versions — they are two independent ports of the
    same documented algorithm, not one shared implementation).
    """
    __tablename__ = "volume_area_levels"
    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_volume_area_level_stock_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    poc: Mapped[float] = mapped_column(Float)
    vah: Mapped[float] = mapped_column(Float)
    val: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SectorRotationSnapshot(Base):
    """T258-SECTOR-ROTATION-TRAJECTORY: dated history for _compute_sector_rotation()'s weekly
    K-Score-momentum-by-sector computation (services/market-data/src/services/scheduler.py).

    Before this table, _compute_sector_rotation() only ever wrote ONE Redis key
    (stockai:sector_rotation, 3-day TTL) — each week's run overwrote the prior one, so nothing
    could answer "is this sector's leadership rising or fading over the last several weeks,"
    only "what does this week's snapshot say." Persisting each week's row here (in addition to,
    not instead of, the existing Redis cache — nothing that already reads that key needs to
    change) makes a rank-vs-N-weeks-ago trajectory classification possible for the first time.

    Keyed by (sector, as_of) rather than a stock_id FK, since a sector name is not itself a row
    in `stocks` — matches how _compute_sector_rotation()'s own query already groups by
    `s.sector` (a plain string column on Stock), not a dedicated sectors table.
    """
    __tablename__ = "sector_rotation_snapshots"
    __table_args__ = (UniqueConstraint("sector", "as_of", name="uq_sector_rotation_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sector: Mapped[str] = mapped_column(String(64), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    recent_kscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    prior_kscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    momentum: Mapped[int] = mapped_column(Integer)  # +1 / 0 / -1, same convention as the Redis payload
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1 = highest recent_kscore that week
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OptionsFlowSnapshot(Base):
    """T257-OVERNIGHT-FLOW-BRIEF Phase 2: end-of-day persisted options-flow read per symbol/date.

    Before this table, GET /{symbol}/options-flow (services/market-data/src/api/routes.py) was
    live-only, 15-minute Redis cache, no history — nothing could answer "what did yesterday's
    late-day flow look like" the way the pre-market brief's design always intended to report.
    Deliberately scoped to a BOUNDED symbol set (PriceAlert-subscribed + top-K by K-Score, NOT
    the whole universe) — yfinance's options-chain endpoint is the most rate-limit-fragile call
    this app makes (see check_volume_anomalies()'s own docstring for the same rate-limit
    discipline applied to a different feature), so this table is never intended to cover every
    stock, only the ones a real recipient could plausibly care about in tomorrow's brief.

    Reuses get_options_flow()'s own response shape/field names directly (cp_ratio, sentiment,
    call_volume/put_volume, whale_count/top_whale_premium) rather than inventing a parallel
    vocabulary — call_premium/put_premium are the two fields that endpoint does NOT already
    aggregate (it only tracks per-contract premium inside its top-10 "unusual activity" list),
    so the EOD job computes those two directly from the full option chain, not by re-deriving
    them from get_options_flow()'s own truncated "unusual" list.
    """
    __tablename__ = "options_flow_snapshots"
    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_options_flow_snapshot_stock_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    cp_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    # AUD265-CPRATIO-CENSORED-BREAKS-RANKING: cp_ratio above is capped at 10.0 (see
    # options_flow_snapshot.py's compute_options_flow()) — every symbol whose real call/put
    # ratio exceeds 10.0 collapses to the identical stored value, so ranking by cp_ratio can't
    # tell a 10x-lopsided flow from a 500x one. cp_ratio_uncapped preserves the real, unclamped
    # ratio for ranking/display; cp_ratio (capped) stays the sentiment-classification input,
    # since the sentiment ladder's own tier boundaries were chosen against the capped scale.
    cp_ratio_uncapped: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    put_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    call_premium: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_premium: Mapped[float | None] = mapped_column(Float, nullable=True)
    whale_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    top_whale_premium: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GexSnapshot(Base):
    """MPE-10: end-of-day real gamma-exposure (GEX) snapshot persistence, mirroring
    OptionsFlowSnapshot's own established pattern (bounded symbol set, ON CONFLICT upsert on
    (stock_id, as_of), one commit per batch job).

    Before this table, get_gex_levels() (services/market-data/src/services/unusual_whales.py)
    was LIVE-ONLY with no history — the same gap OptionsFlowSnapshot closed for options-flow
    data, now closed identically for real GEX. Built specifically so this table starts
    accumulating real history TODAY, ahead of any actual need — MPE-04's own feature-ablation
    harness cannot use a GEX feature group until real point-in-time history exists here, and
    the sooner this table starts populating, the sooner it clears the same data-age bar
    fundamentals_snapshot/options_flow_snapshots are still waiting on (both ~1-2 months old as
    of this table's own creation — see feature_ablation.py's own AUD-MPE04-TRAINCOVERAGE note
    for why that bar matters).

    Gated entirely behind unusual_whales.is_available() — unlike OptionsFlowSnapshot (which
    always has yfinance as a free fallback data source), real GEX has no free-tier equivalent
    at all; this table is simply empty when no UW subscription is active, never backfilled from
    a proxy.
    """
    __tablename__ = "gex_snapshots"
    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_gex_snapshot_stock_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    call_wall: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_wall: Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma_flip: Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma_magnet: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The underlying's own close on `as_of` — needed by the ablation harness's future PIT join
    # to compute a scale-invariant feature (e.g. distance-to-flip as a % of price), since raw
    # dollar strike levels aren't comparable across symbols the way a ratio/percent is.
    underlying_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OptionsGamePlanSnapshot(Base):
    """AUD-OPTIONS4-GAMEPLANBATCH: end-of-day Options Game Plan snapshot, mirroring
    OptionsFlowSnapshot's/GexSnapshot's own established pattern (bounded symbol set, ON
    CONFLICT upsert on (stock_id, as_of), one commit per batch job) — built so a BUY signal's
    options play can be shown on a scan-list row or in the BUY-signal email WITHOUT a live,
    uncached yfinance options-chain fetch per row/recipient, the exact rate-limit-amplification
    shape docs/incidents/yfinance-rate-limit-amplification.md already warns against.

    Deliberately computed with different stop-loss/take-profit inputs than
    compute_options_game_plan()'s own live route (which uses nearest-support/analyst-target,
    sourced from the requesting frontend page) — this batch job instead reuses
    _build_game_plan_for_style()'s real ATR-based SWING-style entry/stop/target, the SAME
    function the Short Squeeze alert's own _squeeze_game_plan() already calls, since that math
    needs no live yfinance call and is already proven safe at this 1-minute alert's own cadence.
    Two legitimate, independently-documented methods for two different contexts (one interactive
    page view vs. one daily batch job over many symbols), not a conflict — the numbers here will
    not always exactly match what a user sees on the stock detail page for the same symbol.

    NULL protective_put_*/covered_call_* fields mean that leg had no real listed contract in the
    target DTE window on this run (matches compute_options_game_plan()'s own None-leg contract),
    not a computation failure.
    """
    __tablename__ = "options_game_plan_snapshots"
    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_options_game_plan_snapshot_stock_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    underlying_close: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_expiry: Mapped[str | None] = mapped_column(String(10), nullable=True)
    put_mid_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_effective_floor_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_expiry: Mapped[str | None] = mapped_column(String(10), nullable=True)
    call_mid_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_effective_cap_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # AUD-DECIDE4-EXPECTEDMOVE: a real, market-implied expected-move (nearest-ATM contract's own
    # implied_volatility, standard expected_move = price * iv * sqrt(dte/365) formula), computed
    # from the SAME options chain this batch job already fetches for the put/call legs above —
    # no new fetch. Replaces the fabricated fixed-percentage stop/target
    # _build_game_plan_for_style() falls back to when reading this snapshot (paper_trading_
    # engine.py), a real gap Domain 2's audit flagged ("the dominant real reject reason is a
    # fabricated 2.00:1 R:R from a missing-ATR fallback game plan, not a measured setup
    # property"). NULL when no near-ATM contract with a real IV was found (same fail-open
    # contract as the put/call legs — a caller falls back to the existing fixed-percentage
    # logic, never a fabricated expected move).
    expected_move_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_move_dte: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # iv_rank_1y: where TODAY's IV sits within this symbol's own trailing 1-year IV range,
    # 0-100 (0 = lowest IV all year, 100 = highest). A genuinely different, complementary
    # signal from expected_move_pct above — expected_move_pct says how far the market expects
    # this symbol to move; iv_rank_1y says whether that IV reading is cheap or expensive
    # RELATIVE TO THIS SYMBOL'S OWN HISTORY (e.g. 30% IV could be a high IV Rank for a normally
    # sleepy utility, or a low IV Rank for a name that's always volatile). Same UW /iv-rank
    # fetch as expected_move_pct's own volatility field — no extra API call. NULL under the
    # same fail-open conditions (UW unavailable/no data for this symbol).
    iv_rank_1y: Mapped[float | None] = mapped_column(Float, nullable=True)
    # AUD-GREEKS: real per-strike Greeks (Unusual Whales' /greeks endpoint) for the EXACT put/
    # call strike this snapshot already selected above — closes a gap this app's own Options
    # Trading Guide explicitly documents ("no real per-contract Greeks beyond implied
    # volatility are shown"). vanna/charm are real second-order Greeks (delta's sensitivity to
    # IV, and to time, respectively) never surfaced anywhere in this app before. NULL under the
    # same fail-open conditions as every other UW-sourced field on this table (unavailable, no
    # data for this specific strike/expiry).
    put_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_gamma: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_vega: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_vanna: Mapped[float | None] = mapped_column(Float, nullable=True)
    put_charm: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_gamma: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_vega: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_vanna: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_charm: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RealtimeNewsItem(Base):
    """New news-intelligence service (port 8011) — real-time financial headline ingestion.

    Replaces the abandoned DESIGN_REALTIME_NEWS_FEED_2026-07-25.md design (built around a
    Stock Titan RSS URL that was verified DEAD — a genuine 404, not just rate-limited — before
    any code was written against it). This table backs 3 independently-pollable sources instead:
    PR Newswire RSS (~under 30s observed latency), GlobeNewswire RSS (~2min observed latency),
    SEC EDGAR's real-time filing Atom feed (~2min observed latency, replacing the existing
    daily-batch 8-K sync as a SEPARATE, faster-latency source — not a replacement for it), plus
    Alpaca's real-time news WebSocket (near-instant, natively ticker-tagged, the only push-based
    source of the four). All 3 non-Alpaca sources were verified live via direct HTTP request
    before this table was designed, not assumed reachable from documentation alone.

    A single row may have multiple (symbol, headline) combinations if a headline mentions
    several tickers — deliberately denormalized (one row per symbol-headline pair, matching
    the abandoned design doc's own schema for this exact reason) rather than a separate join
    table, since headlines rarely mention more than 1-2 tickers and a join table would add
    real complexity for a case this rare.
    """
    __tablename__ = "realtime_news_items"
    __table_args__ = (
        UniqueConstraint("source", "url", "symbol", name="uq_realtime_news_source_url_symbol"),
        Index("ix_realtime_news_symbol_published", "symbol", "published_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # None = macro/market-wide
    headline: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))  # 'pr_newswire' | 'globenewswire' | 'sec_edgar' | 'alpaca'
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100, 50=neutral — SAME
    # scale as market-data's existing SentimentResponse.score (news.py), deliberately, so a
    # future consumer never has to remember "which news source uses which sentiment scale."
    sentiment_label: Mapped[str | None] = mapped_column(String(16), nullable=True)  # positive|negative|neutral
    is_material: Mapped[bool] = mapped_column(Boolean, default=False)  # earnings/FDA/M&A/upgrade/downgrade/etc.
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)  # earnings|fda|ma|analyst|macro|other
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ResearchReportCache(Base):
    """Durable persistence for research-engine's AI research reports (fundamentals,
    technicals, DCF valuation, catalysts, position sizing) — generated by generate_research()
    in services/research-engine/src/api/routes.py.

    Before this table, the ONLY place a generated report lived was an in-memory Python dict
    (routes.py's module-level `_cache`) — every report, whether manually requested or fired
    by market-data's auto-research trigger (see CLAUDE-API-COST-AUDIT), vanished completely on
    ANY research-engine restart (a routine deploy, a crash, an EC2 reboot). Found live: a
    2026-07-28 fix that restarted this container wiped every report generated that day
    (RXT/SMTC/MU/UNH), and the stock detail page + /research/{symbol} page both silently fell
    back to "Generate Report" with no indication a report had ever existed — there was no way
    to distinguish "never generated" from "generated, then lost."

    One row per symbol (a fresh generation overwrites the prior row for that symbol — matches
    the in-memory _cache's own semantics of "one report per symbol, most recent wins").
    `report_json` stores the full ResearchReport response dict verbatim — this is the exact
    same shape the frontend's ResearchReport TypeScript type expects, so the read path can
    return it directly with zero reshaping. Deliberately a plain JSON column, not JSONB-typed
    query filters — nothing needs to query INSIDE this blob server-side; it's read back out
    whole, the same way the in-memory dict was.
    """
    __tablename__ = "research_report_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    portfolio_size: Mapped[float] = mapped_column(Float)
    max_risk_pct: Mapped[float] = mapped_column(Float)
    report_json: Mapped[dict] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ThemeSignalSnapshot(Base):
    """T270-SECTOR-THEME-FORECAST-EMAIL: weekly persisted "themes with real supporting signals
    today" read — services/market-data/src/services/theme_signals.py is the single source of
    truth for the aggregation math and the hand-curated theme->symbol mapping.

    Deliberately NOT a forecast of what a theme will do next — every existing "trend" feature
    in this app (CAPE bubble warning, options-flow sentiment, sector-rotation trajectory) is
    already scoped to a measured, backward-looking fact rather than a prediction, and this
    table follows that same discipline: it stores what was ALREADY TRUE about a theme's real
    price momentum / K-Score breadth / signal breadth as of as_of, plus an LLM-written prose
    summary grounded in those numbers (mirroring generate_reaction()'s/generate_earnings_
    impact()'s exact skeleton in event-intelligence). The LLM is never asked to predict; it is
    asked to explain already-measured numbers in readable prose.

    There is no existing GICS sub-industry taxonomy in this app fine-grained enough for themes
    like "GPU" vs "packaging" vs "Space" — Stock.sector is broad ("Semiconductors," "Healthcare"),
    not narrow enough for what was asked. theme_signals.py's own _THEMES dict is therefore a
    hand-curated (theme_name -> representative symbols) mapping, not derived from Stock.sector.

    Keyed by (theme, as_of) rather than a stock_id FK, matching SectorRotationSnapshot's exact
    precedent for the same reason (a theme name is not itself a row in `stocks`).
    """
    __tablename__ = "theme_signal_snapshots"
    __table_args__ = (UniqueConstraint("theme", "as_of", name="uq_theme_signal_snapshot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theme: Mapped[str] = mapped_column(String(64), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    avg_return_5d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_kscore: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_signal_count: Mapped[int] = mapped_column(Integer, default=0)
    sell_signal_count: Mapped[int] = mapped_column(Integer, default=0)
    symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    top_symbols_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[dict] — per-symbol detail behind the aggregate, for the email's own drill-down
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # LLM-written prose grounded in the numeric fields above; None if the LLM call failed/was skipped


class SqueezeAlertOutcome(Base):
    """T264-SQUEEZEALERT-PERFORMANCE: forward-return tracking for check_short_squeeze_alerts()
    and check_gamma_unwind_alerts() — direct user request: "measure the option sell and short
    squeeze performance and win rates if I buy from the signal, the first email alert."

    Before this table, NEITHER alert-emitting function persisted anything about a fire beyond
    a short-TTL Redis dedup key (see AUD266-DEDUP-KEY-SET-BEFORE-SEND / the gamma job's
    stockai:gamma_unwind_sent:{uid} set) — the moment a candidate stopped qualifying, all
    record of it having fired at all was gone. There was no way to answer "did this alert type
    actually make money" without a new, durable per-fire snapshot.

    One row per (alert_type, symbol, fired_date) — deliberately keyed on the FIRST time a
    symbol transitions into "newly qualifying" for a given day (matching each alert function's
    own existing newly_qualifying/dedup-transition logic exactly, so this table's "first email
    alert" moment is provably the SAME moment the user actually received an email, not a
    separately-computed approximation of it), not re-written on every subsequent cycle the
    symbol stays a candidate. alert_price at fire time is the "if I bought right when the
    email arrived" entry price the user explicitly asked to measure against.

    direction distinguishes the two mechanistically-different alert types this table covers:
    "short_squeeze" (check_short_squeeze_alerts, always BUY-thesis) and "gamma_unwind_calls" /
    "gamma_unwind_puts" (check_gamma_unwind_alerts, split by dominant_side — puts-dominant is
    the closest existing concept in this app to "option sell" the user's request named, per
    check_gamma_unwind_alerts()'s own docstring framing it as a directional options-positioning
    read rather than a stock-borrowing short). Forward returns for the calls/puts split are
    scored the SAME way SignalOutcome scores SELL rows elsewhere in this app (win = price fell
    for the puts-dominant/bearish read) — see is_correct_Nd below.

    Forward-return columns mirror SignalOutcome's own established 5d/10d/20d convention exactly
    (same column names, same nullable-until-window-closes semantics) rather than inventing a
    new vocabulary, filled by a dedicated evaluator job using the same T+1-entry / bisect-
    nearest-bar-with-a-grace-window discipline already proven there.
    """
    __tablename__ = "squeeze_alert_outcomes"
    __table_args__ = (
        UniqueConstraint("alert_type", "stock_id", "fired_date", name="uq_squeeze_alert_outcome_type_stock_date"),
        Index("ix_squeeze_alert_outcomes_type_date", "alert_type", "fired_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(24), index=True)  # short_squeeze | gamma_unwind_calls | gamma_unwind_puts
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    fired_date: Mapped[date] = mapped_column(Date, index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    alert_price: Mapped[float] = mapped_column(Float)  # the live price captured at the moment this alert first fired
    # Snapshot of the metric that qualified the candidate, for later human review — short %
    # of float for short_squeeze, OI concentration_pct for gamma_unwind_*.
    qualifying_metric: Mapped[float | None] = mapped_column(Float, nullable=True)
    # AUD-GEXCORROBORATE-UNMEASURED: whether real Unusual Whales GEX levels corroborated this
    # alert at fire time. check_gamma_unwind_alerts() has computed this since AUD-GEXCORROBORATE
    # but only ever DISPLAYED it in the email — it was never persisted, so the obvious question
    # ("do GEX-corroborated alerts actually outperform uncorroborated ones?") could not be
    # answered from stored data at all, and the free OI-concentration proxy kept gating every
    # candidate on faith.
    #
    # This matters because the proxy is explicitly NOT a real gamma calculation (see
    # check_gamma_unwind_alerts()'s own HONEST LIMITATION docstring): the same open interest
    # amplifies moves when dealers are short gamma and DAMPENS them when dealers are long
    # gamma, and only real GEX (gamma_flip in particular) can distinguish the two. Recording
    # this makes "should gamma_flip gate the alert rather than decorate it?" a measurement
    # instead of an argument — see docs/2026-09-05/GAMMA_SQUEEZE_CAPABILITY_REVIEW.md.
    #
    # NULL means "not evaluated" (a pre-fix row, UW disabled, or a lookup failure) and is
    # deliberately distinct from False ("evaluated, no real GEX level sits near price") — the
    # two must never be pooled when this is analysed, or unmeasured rows would silently count
    # as negative evidence.
    gex_corroborated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Distance from alert_price to the NEAREST corroborating GEX level, as a signed fraction
    # ((level - price) / price). Kept alongside the boolean because "corroborated" is a
    # threshold on this underlying continuous quantity — storing only the boolean would bake
    # today's _GEX_CORROBORATE_BAND_PCT into the historical record and make re-testing a
    # different band impossible without re-firing every alert.
    gex_nearest_level_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # T+1 trading day close used as the actual entry fill
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # DESIGN_SQUEEZE_ALERT_PERFORMANCE_MEASUREMENT: the 1d/2d/3d windows this table originally
    # lacked — added specifically to answer the user's own original question ("will the price
    # go up the other day or later") without waiting the full 5 calendar days the pre-existing
    # windows require. Same T+1-entry / bisect-nearest-bar-with-grace-window discipline as the
    # 5d/10d/20d columns below, filled by the SAME evaluator loop (_SQUEEZE_OUTCOME_WINDOWS).
    price_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_1d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_2d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_3d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_5d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_10d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_20d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # last time the evaluator touched this row


class PreBreakoutAlertOutcome(Base):
    """T264-SHORTSQUEEZE-PREBREAKOUT: forward-return tracking for the NEW "coiling, high-
    short-interest stock about to break out" alert — direct user follow-up request: "predict
    the short sell not able to recover and send me the alert BEFORE it starts to breakout...
    using daily volume and trading data along with the option call and sell data expiry."

    Deliberately a SEPARATE table from SqueezeAlertOutcome (T264-SQUEEZEALERT-PERFORMANCE),
    not a reuse of it — that table measures "did the ALREADY-FIRING squeeze/gamma alert make
    money," a fundamentally different moment than this one (BEFORE the move has started at
    all, while the stock is still compressing). Mixing the two would conflate "did a breakout
    already in progress continue" with "did we correctly predict a breakout was coming" —
    genuinely different questions with different false-positive/false-negative tradeoffs.

    One row per (stock_id, fired_date) — same first-fire-of-the-day semantics as
    SqueezeAlertOutcome. rule_gate_passed records whether the RULE-BASED half (coiling +
    short-interest floor) fired on its own — this can be True even when the model wasn't
    trained/available yet (e.g. a symbol with insufficient price history for the ML model),
    so the two verdicts are tracked independently rather than collapsed into one boolean.
    model_confidence is the trained model's own P(sustained breakout within N days) — None
    when no model was available for this symbol at fire time (a real, honest state, not an
    error). model_version lets a later retrain's outcomes be distinguished from an earlier
    one's when reviewing historical accuracy — see docs/DESIGN convention elsewhere in this
    app for per-model-version outcome tracking (TuneHistory's own promoted/rejected pattern).
    options_modifier_applied records whether the (currently thin, ~2-week-history) options
    call/put positioning data was actually available and used to adjust confidence for this
    fire — explicitly tracked rather than silently assumed, since most fires won't have it.

    T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE (2026-08-15): a squeeze-BREAKOUT-specific
    classifier (the model_confidence/model_version columns above) remains deliberately
    untrained — re-investigated and confirmed the constraint is unchanged: only ~68
    historical candidate days / 17 positive labels exist (FundamentalsSnapshot only started
    2026-07-05), which fails this app's own gate_harness.py promotion-margin discipline
    (MIN_SAMPLES_PER_SPLIT=15 per class per split, plus an EV-lift/SD-ratio margin) by a wide
    margin, and won't clear it for well over a year at the current weekly-snapshot pace.
    Rather than leave "model prediction with confidence" entirely unaddressed, two honestly-
    scoped signals were added instead of a fabricated one:

    ml_price_direction_confidence / ml_price_direction_model_version — reuses ml-prediction's
    EXISTING, already-trained, already-promoted per-symbol SWING-style direction model (the
    same one behind POST /ml/predict, used live elsewhere in this app) as a genuinely
    independent second read. This is deliberately NOT named model_confidence/model_version —
    it answers "what does the app's general price-direction model think," never "will this
    specific squeeze setup break out," and mislabeling it would repeat exactly the kind of
    false-precision mistake _MIN_PROMOTION_LIFT_SD_RATIO exists to prevent elsewhere in this
    app. None when no trained artifact exists for that symbol/style (a routine, expected 404,
    not an error) — never fabricated.

    calibrated_win_rate / calibrated_win_rate_count — a MEASURED historical win rate for
    prebreakout-alert outcomes, bucketed the same way signal-engine's own
    _build_confidence_calibration()/check_top3_conviction() already do it: a real fraction of
    past rule-gate-passing fires (in the same short-interest-floor band) that actually went on
    to a qualifying breakout, with a real n= sample count, and None below a 30-sample floor
    rather than a fabricated rate. This is what most directly answers "how confident should I
    be," using the one dimension this alert already has enough real resolved-outcome data to
    measure honestly, rather than pretending a classifier exists.

    Forward-return columns mirror SqueezeAlertOutcome's/SignalOutcome's own established
    5d/10d/20d convention exactly, scored BUY-direction (win = price rose — the correct
    direction for "shorts forced to cover" thesis, unlike SqueezeAlertOutcome's own
    gamma_unwind_puts row, which is deliberately the opposite).
    """
    __tablename__ = "prebreakout_alert_outcomes"
    __table_args__ = (
        UniqueConstraint("stock_id", "fired_date", name="uq_prebreakout_alert_outcome_stock_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    fired_date: Mapped[date] = mapped_column(Date, index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    alert_price: Mapped[float] = mapped_column(Float)  # live price at the moment this alert first fired
    rule_gate_passed: Mapped[bool] = mapped_column(Boolean)  # coiling + short-interest floor, independent of the model
    short_percent_of_float: Mapped[float | None] = mapped_column(Float, nullable=True)
    bb_width_pctile: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr_pctile: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_dried_up: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    model_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-1, None if no model available for this symbol
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    options_modifier_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    options_cp_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)  # snapshot of the options signal, if used
    ml_price_direction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ml_price_direction_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibrated_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibrated_win_rate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    # DESIGN_SQUEEZE_ALERT_PERFORMANCE_MEASUREMENT — same 1d/2d/3d addition as
    # SqueezeAlertOutcome above, filled by the shared evaluate_prebreakout_alert_outcomes()
    # loop (also driven by _SQUEEZE_OUTCOME_WINDOWS).
    price_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_1d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_2d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_3d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_5d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_10d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_20d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OptionsFlowAlertOutcome(Base):
    """MPE-OPTIONS-FLOW-ALERT: forward-return tracking for check_options_flow_alerts() — a real
    Unusual Whales unusual-options-activity alert (rule-based repeated-hits/sweep detection over
    the full options tape, GET /api/option-trades/flow-alerts), direct follow-up to the user's
    own request: "an alert for options call or sell, predict the expiration date and the
    direction."

    Deliberately a SEPARATE table from SqueezeAlertOutcome, not a reuse — that table's rows are
    keyed per (alert_type, stock_id, fired_date), one row per UNDERLYING symbol per day, which
    fits short_squeeze/gamma_unwind_* (both symbol-level phenomena) but not this alert: a single
    underlying can legitimately have multiple, genuinely distinct flow alerts fire the same day
    (a bullish call sweep on one expiry AND a bearish put sweep on a different expiry are two
    real, separate signals, not the same event twice) — this table is keyed per CONTRACT
    (option_chain), not per underlying, matching PreBreakoutAlertOutcome's own "a genuinely
    different mechanism needs its own table" precedent rather than forcing a shape mismatch.

    direction is derived from BOTH option_type (call/put) AND which side of the market was
    aggressive (ask-side buying vs. bid-side selling) — the real signal UW's own ask/bid premium
    split provides, not merely "call = bullish": a large ask-side-heavy CALL sweep is bullish
    (aggressive buying of upside), a large ask-side-heavy PUT sweep is bearish (aggressive buying
    of downside protection/a bet on a drop), a large BID-side-heavy sweep on either side is the
    mirror (aggressive SELLING of that contract, the "option sell" half of the user's own
    request) — four real, distinct reads, not a naive two-way call/put split. See
    check_options_flow_alerts()'s own docstring in scheduler.py for the exact derivation.

    Forward returns scored the same is_correct_Nd convention as SqueezeAlertOutcome — win means
    price moved in the DIRECTION this alert implied (up for bullish, down for bearish), using
    the same T+1-entry / bisect-nearest-bar-with-grace-window discipline as every other outcome
    table in this app, filled by a dedicated evaluator loop over _SQUEEZE_OUTCOME_WINDOWS.
    """
    __tablename__ = "options_flow_alert_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "option_chain", "fired_date",
            name="uq_options_flow_alert_outcome_contract_date",
        ),
        Index("ix_options_flow_alert_outcomes_stock_date", "stock_id", "fired_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    option_chain: Mapped[str] = mapped_column(String(64), index=True)  # UW's own per-contract symbol
    option_type: Mapped[str] = mapped_column(String(8))  # "call" | "put"
    direction: Mapped[str] = mapped_column(String(8))  # "bullish" | "bearish" — see class docstring
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    fired_date: Mapped[date] = mapped_column(Date, index=True)
    fired_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    alert_price: Mapped[float] = mapped_column(Float)  # underlying's live price at the moment this alert first fired
    total_premium: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask_side_dominant: Mapped[bool] = mapped_column(Boolean)  # True = aggressive buying, False = aggressive selling
    volume_oi_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_sweep: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_rule: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibrated_win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibrated_win_rate_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_1d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_1d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_2d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_2d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_3d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_3d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_5d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_10d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_10d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    price_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_correct_20d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PortfolioRiskMetric(Base):
    """IF-01: persisted VaR/CVaR snapshot for a user's real position book, closing the gap this
    tracker item's own review found — portfolio-optimizer's GET /portfolio-risk/risk was
    entirely request-scoped, computed fresh and discarded on every call, with no time series to
    trend, alert on, or backtest a VaR model's own breach rate against (a risk figure is only
    trustworthy once you can measure how often reality actually exceeded it).

    Scoped per (user_id, as_of) rather than per-portfolio — the /portfolio-risk/risk endpoint
    takes an arbitrary comma-separated symbol/weight list from portfolio.tsx's real
    UserPosition holdings, not a PaperPortfolio (paper portfolios are a separate, app-wide
    concept with their own risk metrics already — see PaperEquityCurve/_portfolio_risk_metrics
    in paper_portfolio.py, which computes Sharpe/Sortino/CAGR/drawdown, genuinely different
    figures from VaR/CVaR). symbols_json is a plain sorted JSON list of the symbols this
    snapshot was computed over, kept for display/audit purposes (not part of the uniqueness
    key — a user changing their exact position list intraday still gets one row per day,
    reflecting their CURRENT holdings at snapshot time).

    Computed via portfolio-optimizer's own compute_var_cvar() (an HTTP call — portfolio-
    optimizer has no DB access of its own, market-data does, matching the established
    cross-service compute-then-persist pattern already used for OptionsFlowSnapshot/
    SectorRotationSnapshot). Currently written on-demand (a user-triggered "save snapshot"
    action), not yet a scheduled daily job — see this item's own tracker note for why that
    phase was deliberately deferred.
    """
    __tablename__ = "portfolio_risk_metrics"
    __table_args__ = (UniqueConstraint("user_id", "as_of", name="uq_portfolio_risk_metric_user_date"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    symbols_json: Mapped[str] = mapped_column(Text)  # sorted JSON list, e.g. ["AAPL","MSFT"]
    portfolio_beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    var_95_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # pre-existing parametric 1d/95%
    var_95_1d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    var_99_1d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    var_95_10d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    var_99_10d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cvar_95_1d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cvar_99_1d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cvar_95_10d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cvar_99_10d_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StressTestResult(Base):
    """IF-01: persisted result of applying a predefined historical stress scenario to a user's
    real position book — see PortfolioRiskMetric's own docstring for the shared architectural
    reasoning (scoped per user, not per-portfolio; computed via an HTTP call to portfolio-
    optimizer's run_stress_test(), which market-data then persists since it has real DB access
    and portfolio-optimizer does not).

    One row per (user_id, as_of, scenario) — a user can run multiple scenarios against the same
    day's holdings, each getting its own row rather than overwriting a single day's slot.
    """
    __tablename__ = "stress_test_results"
    __table_args__ = (UniqueConstraint("user_id", "as_of", "scenario", name="uq_stress_test_result_user_date_scenario"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    scenario: Mapped[str] = mapped_column(String(64))
    scenario_label: Mapped[str] = mapped_column(String(256))
    symbols_json: Mapped[str] = mapped_column(Text)
    benchmark_move_pct: Mapped[float] = mapped_column(Float)
    portfolio_impact_pct: Mapped[float] = mapped_column(Float)
    per_position_impact_json: Mapped[str] = mapped_column(Text)  # {"AAPL": -34.2, ...}
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RestrictedSymbol(Base):
    """IF-12 (P3): a user-maintained no-trade list — genuinely useful for a single-user app
    (e.g. blocking a stock already held in a real brokerage account, or one deliberately
    avoided for personal reasons) without needing the full ComplianceRule/surveillance-layer
    design the source doc proposed, which the tracker's own review explicitly rejected as
    over-engineering for this app's single-user shape.

    Deliberately GLOBAL, not per-portfolio — PaperPortfolio itself has no user_id (paper
    portfolios are app-wide, per this repo's own established convention), and "I've decided to
    avoid this stock" is naturally a decision that should apply everywhere the symbol could be
    traded, not just in one portfolio. Enforced as one more hard-reject check in
    _scan_for_entries()'s existing candidate loop — the FIRST check, before any other
    computation is spent on a symbol the user has explicitly banned.
    """
    __tablename__ = "restricted_symbols"
    __table_args__ = (UniqueConstraint("symbol", name="uq_restricted_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PaperTradeDecisionLog(Base):
    """IF-12 (P3): an append-only, NEVER-mutated audit trail written once at entry and once at
    exit — the genuinely missing half of this app's existing decision-audit story.
    paper_trades.entry_decision_notes/confidence_at_entry/kscore_at_entry/etc. (models.py,
    PaperTrade) already capture a rich decision snapshot, but that ROW is mutated throughout
    the trade's lifecycle (current_price, current_stop, hold_days all update in place) — so the
    existing trail is rich but not immutable, a real distinction for audit purposes the
    tracker's own review flagged as the one piece with standalone value regardless of any
    formal compliance requirement.

    Two rows per completed trade lifecycle: one written at entry (action="entry"), one at exit
    (action="exit") — each a genuine INSERT, never an UPDATE to a prior row. Denormalizes the
    key entry/exit facts directly onto this table (rather than only a trade_id FK) so the log
    stays readable/queryable even if the source PaperTrade row is ever deleted, and so it never
    depends on joining back to a row that, by definition, keeps changing after this snapshot
    was taken.
    """
    __tablename__ = "paper_trade_decision_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("paper_trades.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id", ondelete="CASCADE"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(16))  # "entry" | "exit"
    price: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # entry_decision_notes joined, or exit_reason
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # full snapshot at this moment
    logged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class FixRecord(Base):
    """T325-FIXEFFECTIVENESS: "did this fix actually work" tracking for significant bug fixes
    (as opposed to TuneHistory, which tracks TUNING PARAMETER changes with its own before/
    after realized_ev_pct_after backfill — this is the equivalent for CODE-LEVEL fixes found
    during an audit, e.g. the AI Signal deep audit's AUD-SIGNAL3-EVALSELECTIONBIAS fix).

    Direct user request (2026-09-02, after the AI Signal deep audit): "I would like to have a
    dashboard to show the performance after we applied the fix so that we can compare later and
    see if the fix really works." Deliberately a general mechanism, not a one-off AI-Signal-only
    table — the AI Signal fix is FixRecord #1, but any future significant fix from this or a
    later audit domain (Decision-Making, Paper Trading, Model Training, Short Squeeze, Options)
    registers here the same way.

    One row per tracked fix. baseline_metrics_json is the "before" snapshot — captured ONCE,
    at fix time, from the exact same queries the audit itself already ran (so the comparison is
    apples-to-apples against a real, already-published number, not re-derived differently
    later). Re-measurements over time live in FixSnapshot (1-to-many) — this row itself never
    changes after creation, matching TuneHistory's own "written once, referenced forever"
    convention.
    """
    __tablename__ = "fix_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fix_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # e.g. "AUD-SIGNAL3-EVALSELECTIONBIAS"
    domain: Mapped[str] = mapped_column(String(32), index=True)  # "ai_signal" | "decision_making" | "paper_trading" | "model_training" | "short_squeeze" | "options"
    title: Mapped[str] = mapped_column(String(255))
    fixed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    audit_doc_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. "docs/audits/2026-09-02-....md"
    # The exact metric definitions + baseline values, as a flat dict — deliberately JSON, not
    # fixed columns, since different fixes measure genuinely different things (AI Signal:
    # win_rate_5d/avg_return_5d per horizon+direction; a future Paper Trading fix might measure
    # Sharpe/max-drawdown instead) — forcing every future fix into the SAME fixed schema would
    # be the same "genuinely different shape forced into an ill-fitting table" mistake this
    # repo's own OptionsFlowAlertOutcome docstring already warns against.
    baseline_metrics_json: Mapped[dict] = mapped_column(JSON)
    # Free-text describing what "success" looks like for this specific fix, set at fix time —
    # e.g. "win_rate_5d should rise toward 50%+ as the eval-selection-bias correction accumulates
    # fresh, uncorrupted signal_outcomes rows." Read by a human comparing baseline vs. snapshots;
    # never programmatically evaluated (no fix is generic enough to auto-grade its own success).
    success_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    recheck_after_days: Mapped[int] = mapped_column(Integer, default=14)  # user's own "couple of weeks" cadence, as a real, adjustable default

    snapshots: Mapped[list["FixSnapshot"]] = relationship(back_populates="fix_record", cascade="all, delete-orphan")


class FixSnapshot(Base):
    """One re-measurement of a FixRecord's own baseline_metrics_json shape, at a later point in
    time — see FixRecord's own docstring for the full rationale. Written by a scheduled job
    (signal-engine's own /fix-effectiveness/{fix_id}/snapshot for AI-Signal-domain fixes;
    future domains' fixes register their own equivalent snapshot-producing endpoint) on a
    recurring cadence (FixRecord.recheck_after_days), not on every request — a snapshot is a
    deliberate, timestamped checkpoint, not a live/on-demand query result.

    metrics_json uses the SAME keys as its own FixRecord.baseline_metrics_json (enforced by
    convention, not a DB constraint, since JSON has no schema to enforce) so a UI can zip the
    two dicts together directly without a translation layer.
    """
    __tablename__ = "fix_snapshots"
    __table_args__ = (
        Index("ix_fix_snapshots_fix_record_taken", "fix_record_id", "taken_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fix_record_id: Mapped[int] = mapped_column(ForeignKey("fix_records.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    metrics_json: Mapped[dict] = mapped_column(JSON)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)  # e.g. rows this snapshot's metrics are computed over, for eyeballing statistical confidence
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. "still below the 30-sample floor for 3 of 8 buckets"

    fix_record: Mapped["FixRecord"] = relationship(back_populates="snapshots")
