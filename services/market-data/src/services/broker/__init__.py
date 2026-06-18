from .interface import BrokerInterface, OrderSide, OrderType, BrokerOrder, BrokerPosition, BrokerAccount
from .etrade_broker import EtradeBroker
from .manual_broker import ManualBroker

__all__ = [
    "BrokerInterface", "OrderSide", "OrderType", "BrokerOrder", "BrokerPosition", "BrokerAccount",
    "EtradeBroker", "ManualBroker",
]


def get_broker(broker_type: str, config: dict) -> BrokerInterface:
    """Factory — returns the right broker adapter for the given type and credentials."""
    if broker_type in ("etrade", "etrade_sandbox"):
        return EtradeBroker(config, sandbox=(broker_type == "etrade_sandbox"))
    if broker_type == "fidelity_manual":
        return ManualBroker(config)
    raise ValueError(f"Unknown broker_type: {broker_type!r}")
