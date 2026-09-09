"""Adapter registry — choose a provider by name or best-fit."""
from __future__ import annotations

from .base import DataAdapter

_registry: dict[str, DataAdapter] = {}

# AUD-PROVIDERKEY-INMEMORY: set_runtime_key()/get_runtime_key() used to be backed by a plain
# in-process dict — unlike every other admin-configured provider credential (Claude/DeepSeek/
# Alpaca/Unusual Whales, all in shared/common/ai_keys.py), which persists to Redis. A key
# entered on the Settings page reached the adapter for the life of the current uvicorn process,
# then silently vanished on the next deploy/restart with no error or "unset" signal anywhere —
# discovered 2026-09-04 when a routine market-data restart (for an unrelated fix) wiped both
# keys minutes after they were re-confirmed set on the Settings page. Now Redis-backed, matching
# every sibling provider's persistence contract; the function names/signatures are unchanged so
# neither call site (admin.py's writes, the two adapters' reads) needed to change.
_REDIS_KEY_PREFIX = "stockai:admin:provider_key:"


def _redis():
    from common.redis_client import get_redis
    return get_redis()


def set_runtime_key(name: str, value: str) -> None:
    try:
        _redis().set(f"{_REDIS_KEY_PREFIX}{name}", value)
    except Exception:
        pass


def get_runtime_key(name: str) -> str | None:
    try:
        return (_redis().get(f"{_REDIS_KEY_PREFIX}{name}") or "").strip() or None
    except Exception:
        return None


# Preferred order when multiple adapters can serve a request.
#
# AUD-ING-POLYGONDELAYED (2026-09-09): Polygon USED to be first here, on the reasoning quoted
# below ("a real API vs yfinance scraping"). That reasoning is sound about the interface and
# wrong about the DATA: the configured key is on a `DELAYED` plan roughly 2 trading days behind,
# and it answers an out-of-range window with an empty HTTP 200 rather than an error. Being first
# in this list meant it got to answer "no bars" authoritatively for any symbol already caught up
# to its cutoff, silently stalling 4 US symbols for 4 days. See unusual_whales_adapter.py's
# module docstring for the full postmortem.
#
# Unusual Whales is now first for US: authenticated, paid, metered at 120k requests/day (655/day
# at 131 symbols x 5 refreshes = 0.55% of budget), and verified current to the same trading day
# as yfinance. yfinance stays as the general fallback and remains the ONLY HK source — it is an
# unauthenticated scrape with no quota guarantee and returned HTTP 429 on every retry during the
# incident that motivated this change, which is exactly why it should not be the sole US source
# either. Polygon is kept LAST rather than removed: it is a genuine second opinion for backfills
# well inside its coverage window, and it is now additionally guarded at the fetch level (see
# PolygonAdapter's `status == "DELAYED"` check) so it can no longer answer for dates it cannot
# actually see.
_PRIORITY = ["unusual_whales", "yfinance", "alpha_vantage", "polygon"]


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
