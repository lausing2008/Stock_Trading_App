"""Adapter registry — choose a provider by name or best-fit."""
from __future__ import annotations

from .base import DataAdapter

_registry: dict[str, DataAdapter] = {}
_runtime_keys: dict[str, str] = {}


def set_runtime_key(name: str, value: str) -> None:
    _runtime_keys[name] = value


def get_runtime_key(name: str) -> str | None:
    return _runtime_keys.get(name) or None


# Preferred order when multiple adapters can serve a request.
# Polygon is tried first for US because it has a real API (vs yfinance scraping).
_PRIORITY = ["polygon", "alpha_vantage", "yfinance"]


def register_adapter(adapter: DataAdapter) -> None:
    _registry[adapter.name] = adapter


def get_adapter(name: str | None = None, market: str | None = None) -> DataAdapter:
    if name and name in _registry:
        return _registry[name]
    if market:
        for a in _registry.values():
            if market in a.supported_markets:
                return a
    # Default — yfinance covers both US and HK in free tier
    if "yfinance" in _registry:
        return _registry["yfinance"]
    raise RuntimeError("No data adapter registered")


def get_adapters(market: str | None, timeframe: str | None = None) -> list[DataAdapter]:
    """Return adapters in priority order that can serve the given market+timeframe.

    The first entry is the preferred provider; callers should fall back to
    subsequent entries if the preferred one raises an exception.
    """
    candidates: list[DataAdapter] = []
    for name in _PRIORITY:
        a = _registry.get(name)
        if a is None:
            continue
        if market and timeframe and not a.supports(market, timeframe):
            continue
        if market and not timeframe and market not in a.supported_markets:
            continue
        candidates.append(a)
    if not candidates:
        if "yfinance" in _registry:
            return [_registry["yfinance"]]
        raise RuntimeError("No data adapter registered")
    return candidates
