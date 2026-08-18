from .interface import (
    AuthStyle, BrokerAccount, BrokerInterface, BrokerOrder, BrokerPosition, BrokerQuote,
    ConfigField, OrderSide, OrderType,
)
from .etrade_broker import EtradeBroker
from .manual_broker import ManualBroker
from .alpaca_broker import AlpacaBroker

__all__ = [
    "AuthStyle", "BrokerInterface", "OrderSide", "OrderType", "BrokerOrder", "BrokerPosition",
    "BrokerAccount", "BrokerQuote", "ConfigField",
    "EtradeBroker", "ManualBroker", "AlpacaBroker",
    "get_broker", "broker_class_for_type", "SUPPORTED_BROKER_TYPES", "broker_metadata",
]

# TIER84-BROKER-PORTABILITY: the SINGLE place a new broker adapter (Schwab, a real Fidelity
# API if one ever ships, etc.) needs to be registered — every other broker-aware piece of code
# in this app (api/broker.py's CreateBrokerRequest validation + route guards, the frontend's
# credential form) derives its behavior from this list plus each class's own BROKER_TYPES/
# AUTH_STYLE/CONFIG_FIELDS declarations, rather than hardcoding a parallel if/elif chain that
# could silently drift from this one. Adding a broker = write the adapter class (implementing
# BrokerInterface, declaring the 3 class attributes) + append it here. Nothing else to edit.
_ADAPTER_CLASSES: tuple[type[BrokerInterface], ...] = (EtradeBroker, ManualBroker, AlpacaBroker)


def _type_to_class() -> dict[str, type[BrokerInterface]]:
    mapping: dict[str, type[BrokerInterface]] = {}
    for cls in _ADAPTER_CLASSES:
        for t in cls.BROKER_TYPES:
            mapping[t] = cls
    return mapping


SUPPORTED_BROKER_TYPES: tuple[str, ...] = tuple(_type_to_class().keys())


def broker_class_for_type(broker_type: str) -> type[BrokerInterface]:
    cls = _type_to_class().get(broker_type)
    if cls is None:
        raise ValueError(f"Unknown broker_type: {broker_type!r}")
    return cls


def broker_metadata(broker_type: str) -> dict:
    """Auth style + required config fields for a broker_type — what api/broker.py's
    create_connection() validates against and what the frontend renders as the credential
    form, without either needing its own hardcoded copy of this information."""
    cls = broker_class_for_type(broker_type)
    return {
        "broker_type": broker_type,
        "auth_style": cls.AUTH_STYLE.value,
        "config_fields": [
            {"key": f.key, "label": f.label, "secret": f.secret, "placeholder": f.placeholder}
            for f in cls.CONFIG_FIELDS
        ],
    }


def get_broker(broker_type: str, config: dict) -> BrokerInterface:
    """Factory — returns the right broker adapter for the given type and credentials.

    Each adapter's own constructor signature still varies slightly (EtradeBroker/AlpacaBroker
    both take a second positional-ish flag distinguishing sandbox/paper from prod/live, under
    a different keyword name each) — that ONE remaining per-broker difference is handled here,
    rather than forcing every adapter into an identical constructor shape that would fight
    each broker's own natural terminology (E*Trade calls it "sandbox", Alpaca calls it "paper").
    """
    cls = broker_class_for_type(broker_type)
    if cls is EtradeBroker:
        return EtradeBroker(config, sandbox=(broker_type == "etrade_sandbox"))
    if cls is AlpacaBroker:
        return AlpacaBroker(config, paper=(broker_type == "alpaca_paper"))
    return cls(config)
