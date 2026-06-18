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


class BrokerInterface(ABC):
    """All broker adapters must implement these methods.

    Error handling convention:
    - Raise RuntimeError with a human-readable message on any broker API failure.
    - Raise NotImplementedError for features the broker does not support.
    - Never swallow exceptions silently — let the caller decide on retry/fallback.
    """

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
