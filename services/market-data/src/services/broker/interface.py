"""Abstract broker interface — every real and simulated broker implements this contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


@dataclass
class BrokerOrder:
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    order_type: OrderType
    status: str              # 'pending' | 'filled' | 'partially_filled' | 'cancelled' | 'rejected'
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    message: str = ""        # broker-specific status message
    placed_at: str | None = None  # ISO8601 or broker-native timestamp string, when available


@dataclass
class BrokerQuote:
    symbol: str
    last_price: float | None
    bid: float | None = None
    ask: float | None = None
    prev_close: float | None = None
    volume: float | None = None


@dataclass
class BrokerPosition:
    symbol: str
    qty: float
    avg_cost: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


@dataclass
class BrokerAccount:
    account_id: str
    broker_type: str
    cash_available: float
    equity: float            # total account value
    buying_power: float
    day_trading_buying_power: float = 0.0
    open_positions: list[BrokerPosition] = field(default_factory=list)


class AuthStyle(str, Enum):
    """How a broker adapter authenticates. Drives what api/broker.py's routes/UI show for a
    given broker_type WITHOUT that file needing its own hardcoded per-type branch — see
    BrokerInterface.CONFIG_FIELDS/AUTH_STYLE below."""
    OAUTH1 = "oauth1"          # 3-legged OAuth 1.0a (E*Trade) — needs start/complete/renew steps
    KEY_SECRET = "key_secret"  # a static key/secret pair, authorized immediately (Alpaca)
    MANUAL = "manual"          # no API at all — instructions only (Fidelity manual)


@dataclass(frozen=True)
class ConfigField:
    """One credential field a broker adapter needs at connection-creation time — drives both
    CreateBrokerRequest validation and the frontend's dynamically-rendered credential form,
    so adding a new broker never requires editing api/broker.py's request schema or
    settings.tsx's form JSX by hand for the NEW broker's own fields."""
    key: str            # the CreateBrokerRequest field name (also the stored config dict key)
    label: str           # frontend form label, e.g. "Consumer Key"
    secret: bool = False  # render as a password input if True
    placeholder: str = ""


class BrokerInterface(ABC):
    """All broker adapters must implement these methods.

    Error handling convention:
    - Raise RuntimeError with a human-readable message on any broker API failure.
    - Raise NotImplementedError for features the broker does not support.
    - Never swallow exceptions silently — let the caller decide on retry/fallback.

    TIER84-BROKER-PORTABILITY: every concrete adapter must also declare 3 class attributes so
    api/broker.py can drive connection-creation, config validation, and the frontend's
    credential form GENERICALLY — adding a new broker (Schwab, Fidelity's real API if one ever
    ships, etc.) means adding one new adapter class + registering it in
    broker/__init__.py's _REGISTRY, never editing api/broker.py's own routes:

      BROKER_TYPES : tuple[str, ...] — the broker_type string(s) this class handles (e.g.
                     ("etrade", "etrade_sandbox") — one class, two type strings differing only
                     by a constructor flag).
      AUTH_STYLE   : AuthStyle       — drives whether the frontend/backend show an OAuth
                     start/complete flow, a plain credential form, or neither.
      CONFIG_FIELDS: tuple[ConfigField, ...] — the credential fields CreateBrokerRequest must
                     accept and validate for this broker_type; empty for AUTH_STYLE.MANUAL
                     brokers with no real credentials, or OAUTH1 brokers whose fields are
                     consumer_key/consumer_secret (still declared, since the OAuth flow itself
                     still needs them collected upfront).
    """

    BROKER_TYPES: tuple[str, ...] = ()
    AUTH_STYLE: AuthStyle = AuthStyle.KEY_SECRET
    CONFIG_FIELDS: tuple[ConfigField, ...] = ()

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        qty: float,
        side: OrderSide,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: str = "day",
        account_id: str | None = None,
    ) -> BrokerOrder:
        """Submit an order and return the broker's order record."""

    @abstractmethod
    def cancel_order(self, order_id: str, account_id: str | None = None) -> bool:
        """Cancel an open order. Returns True if successfully cancelled."""

    @abstractmethod
    def get_order(self, order_id: str, account_id: str | None = None) -> BrokerOrder:
        """Retrieve the current status of an order."""

    @abstractmethod
    def get_account(self, account_id: str | None = None) -> BrokerAccount:
        """Return account balance, equity, and positions."""

    @abstractmethod
    def is_market_open(self) -> bool:
        """Return True if the US stock market is currently open for trading."""

    # Optional — brokers that don't support this raise NotImplementedError
    def list_orders(self, account_id: str | None = None, status: str = "open") -> list[BrokerOrder]:
        raise NotImplementedError(f"{type(self).__name__} does not support list_orders")

    # T230-DATA-BROKERQUOTE: optional — a broker whose account is already authenticated can
    # sometimes serve real-time quotes on that same session at zero extra integration cost
    # vs. onboarding a whole new market-data provider. Brokers that don't support this raise
    # NotImplementedError, matching list_orders()'s own established convention exactly.
    def get_quote(self, symbols: list[str]) -> list[BrokerQuote]:
        raise NotImplementedError(f"{type(self).__name__} does not support get_quote")
