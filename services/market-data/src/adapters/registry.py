"""Adapter registry — choose a provider by name or best-fit."""
from __future__ import annotations

from .base import DataAdapter

_registry: dict[str, DataAdapter] = {}


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
