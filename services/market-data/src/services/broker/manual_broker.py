"""Manual broker adapter — for brokers without a public API (e.g. Fidelity).

Trades are NOT automatically executed. Instead the app shows human-readable
trade instructions ("Buy 50 shares of NVDA at market, stop $120") that the
user manually enters in their broker's platform.

Calling place_order() records the intended trade and returns a synthetic order
ID so the paper engine can track it. Actual fill prices are supplied by the
paper engine's slippage simulation — not from a real broker fill report.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .interface import (
    BrokerAccount, BrokerInterface, BrokerOrder, BrokerPosition, OrderSide, OrderType,
)


class ManualBroker(BrokerInterface):
    """Broker adapter for manually-executed accounts (Fidelity, TD Ameritrade direct, etc.).

    config keys:
      account_number — e.g. "Z12345678" (display only, never used for API calls)
      notes          — optional freeform note shown in the UI
    """

    BROKER_TYPE = "fidelity_manual"

    def __init__(self, config: dict):
        self._config = config

    def place_order(
        self, symbol, qty, side, order_type=OrderType.MARKET,
        limit_price=None, stop_price=None, time_in_force="day", account_id=None,
    ) -> BrokerOrder:
        # Generate a synthetic order ID so the engine can reference this trade.
        order_id = f"manual-{uuid.uuid4().hex[:12]}"
        action   = "BUY" if side == OrderSide.BUY else "SELL"
        type_str = {
            OrderType.MARKET:     "MARKET",
            OrderType.LIMIT:      f"LIMIT @ ${limit_price:.2f}" if limit_price else "LIMIT",
            OrderType.STOP:       f"STOP @ ${stop_price:.2f}" if stop_price else "STOP",
            OrderType.STOP_LIMIT: (
                f"STOP-LIMIT stop=${stop_price:.2f} limit=${limit_price:.2f}"
                if stop_price and limit_price else "STOP-LIMIT"
            ),
        }[order_type]
        account_num = self._config.get("account_number", "—")
        message = (
            f"[Manual] {action} {int(qty)} shares of {symbol} — {type_str} "
            f"(account {account_num}). Please execute manually in your broker platform."
        )
        return BrokerOrder(
            order_id   = order_id,
            symbol     = symbol,
            side       = side,
            qty        = qty,
            order_type = order_type,
            # Manual orders are treated as immediately 'filled' for paper engine purposes —
            # the actual fill price is the paper engine's slippage-adjusted price.
            status          = "filled",
            filled_qty      = qty,
            limit_price     = limit_price,
            stop_price      = stop_price,
            message         = message,
        )

    def cancel_order(self, order_id: str, account_id: str | None = None) -> bool:
        # Nothing to cancel in a real broker via API — user must cancel manually.
        return True

    def get_order(self, order_id: str, account_id: str | None = None) -> BrokerOrder:
        raise NotImplementedError(
            "ManualBroker cannot retrieve order status from Fidelity — no public API."
        )

    def get_account(self, account_id: str | None = None) -> BrokerAccount:
        return BrokerAccount(
            account_id     = self._config.get("account_number", "manual"),
            broker_type    = self.BROKER_TYPE,
            cash_available = 0.0,   # not available without API
            equity         = 0.0,
            buying_power   = 0.0,
        )

    def is_market_open(self) -> bool:
        now = datetime.now(timezone.utc)
        if now.weekday() >= 5:
            return False
        return 1430 <= now.hour * 100 + now.minute <= 2100
