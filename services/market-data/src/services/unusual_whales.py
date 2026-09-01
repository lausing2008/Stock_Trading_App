"""MPE-06/MPE-07: a real Unusual Whales API client — genuine dealer gamma exposure (GEX) and
real short-interest data (borrow fee, shares available, days-to-cover), replacing this app's own
free-data proxies where a subscription is active.

Not a `DataAdapter` (services/market-data/src/adapters/base.py) — that ABC's whole contract is
strictly OHLCV bars (`fetch_ohlcv(symbol, timeframe, start, end) -> OHLCV`); Unusual Whales'
response shapes (options Greeks, short-interest fields, GEX levels) have nothing in common with
a price bar, so forcing this into the adapter registry would mean either lying about the return
type or bolting on unrelated methods no other adapter has. This module is deliberately its own,
separate thing, matching the same "a genuinely new data shape needs a genuinely new module, not
a plugin into an ABC built for something else" judgment already made for T259-NEWS-INTELLIGENCE.

Every real call is gated behind BOTH a configured API key AND the admin enabled flag (see
shared/common/ai_keys.py's get_unusual_whales_key()/is_unusual_whales_enabled() — a key existing
does not by itself mean the feature is turned on) — this is a real, metered, per-request-cost
API, so every function here fails open to `None` rather than raising, and callers must always
have a free-data fallback path ready (never assume Unusual Whales data will be present).

Real endpoint shapes below were verified directly against the live
https://api.unusualwhales.com/api/openapi spec (not guessed/assumed) before being coded against:
  - /api/stock/{ticker}/gex-levels     — call_wall, put_wall, gamma_flip, gamma_magnet
  - /api/stock/{ticker}/greek-exposure — call_gamma, put_gamma, call_delta, put_delta, ...
  - /api/shorts/{ticker}/interest-float/v2 — days_to_cover, fee_rate, rebate_rate,
    short_interest, short_shares_available, si_float, total_float
  - /api/shorts/{ticker}/data           — fee_rate, rebate_rate, short_shares_available
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

import structlog

from common.ai_keys import get_unusual_whales_key, is_unusual_whales_enabled

log = structlog.get_logger()

_BASE_URL = "https://api.unusualwhales.com"

# Real Redis cache TTLs — GEX/greek-exposure are recomputed by UW throughout the trading day
# (short, intraday-relevant TTL); short-interest settles ~2x/month with a real reporting lag,
# so it can be cached far longer without ever serving genuinely stale data relative to its own
# real update cadence.
_GEX_TTL = 900          # 15 min — matches this app's own options-flow cache cadence exactly
_SHORT_INTEREST_TTL = 21600  # 6h — short interest itself only updates ~2x/month


class UnusualWhalesRateLimitError(Exception):
    """A 429 from Unusual Whales — caller should back off, never retry immediately."""


class UnusualWhalesAuthError(Exception):
    """A 401/403 — the configured key is invalid/expired. Never retried (retrying a bad key
    wastes the request budget on an error that can't self-resolve)."""


@dataclass
class GexLevels:
    call_wall: float | None
    put_wall: float | None
    gamma_flip: float | None
    gamma_magnet: float | None
    as_of_date: str | None


@dataclass
class ShortInterestData:
    short_interest: float | None
    short_shares_available: float | None
    days_to_cover: float | None
    fee_rate: float | None
    rebate_rate: float | None
    si_float: float | None
    total_float: float | None
    market_date: str | None


def _get_redis():
    from common.redis_client import get_redis as _get_pool_redis
    return _get_pool_redis()


def is_available() -> bool:
    """True only when BOTH a real key is configured AND the admin has turned the feature on —
    the single check every caller should make before attempting any real fetch, so a caller
    never has to separately reason about the key-vs-flag distinction itself."""
    return is_unusual_whales_enabled() and bool(get_unusual_whales_key())


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    reraise=True,
    retry=retry_if_not_exception_type((UnusualWhalesRateLimitError, UnusualWhalesAuthError)),
)
def _get(path: str) -> dict | None:
    """A single authenticated GET against the real Unusual Whales API. Returns the parsed
    `data` field of the response (UW's own real response envelope, confirmed live), or `None`
    on any real absence of data. Raises UnusualWhalesRateLimitError/UnusualWhalesAuthError for
    the two error classes that must never be blindly retried — every other transient failure
    (timeout, 5xx, connection error) retries up to 3x with exponential backoff via tenacity,
    matching polygon_adapter.py's own established retry convention exactly.
    """
    key = get_unusual_whales_key()
    if not key:
        return None
    with httpx.Client(timeout=15) as client:
        r = client.get(
            f"{_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        if r.status_code == 429:
            log.warning("unusual_whales.rate_limit", path=path)
            raise UnusualWhalesRateLimitError(f"Unusual Whales rate limit on {path}")
        if r.status_code in (401, 403):
            log.warning("unusual_whales.auth_error", path=path, status=r.status_code)
            raise UnusualWhalesAuthError(f"Unusual Whales auth failed on {path} ({r.status_code})")
        if r.status_code == 404:
            # A real, expected "no data for this symbol/expiry" case (e.g. no listed options,
            # not delisted-in-UW's-own-sense) — never an error worth logging as one.
            return None
        r.raise_for_status()
        body = r.json()
        return body.get("data") if isinstance(body, dict) else None


def get_gex_levels(symbol: str) -> GexLevels | None:
    """Real, calculated gamma exposure levels for `symbol` — call_wall/put_wall (the strikes
    where dealer gamma concentrates on each side) and gamma_flip (the "zero gamma" price level
    dealers' own hedging flips direction at). Redis-cached 15 min. Returns None if Unusual
    Whales is disabled/unconfigured, the fetch fails for any reason, or the symbol has no real
    GEX data (e.g. no listed options) — every failure mode fails open, never raises, so a
    caller can always fall back to the existing free OI-concentration proxy
    (check_gamma_unwind_alerts()) without special-casing which failure occurred.
    """
    if not is_available():
        return None
    sym = symbol.upper()
    cache_key = f"stockai:uw:gex:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            d = json.loads(cached)
            return GexLevels(**d) if d else None
    except Exception:
        pass

    try:
        data = _get(f"/api/stock/{sym}/gex-levels")
    except Exception as exc:
        log.warning("unusual_whales.gex_levels_failed", symbol=sym, error=str(exc))
        return None

    result: GexLevels | None
    if not data:
        result = None
    else:
        # UW's real response is a list of per-expiry rows (confirmed via the live spec) —
        # the nearest/most-relevant row is the first one; a symbol with zero listed options
        # returns an empty list, not a dict, so this must handle both shapes defensively.
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if not row:
            result = None
        else:
            result = GexLevels(
                call_wall=_to_float(row.get("call_wall")),
                put_wall=_to_float(row.get("put_wall")),
                gamma_flip=_to_float(row.get("gamma_flip")),
                gamma_magnet=_to_float(row.get("gamma_magnet")),
                as_of_date=row.get("date"),
            )

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _GEX_TTL, json.dumps(asdict(result) if result else None))
    except Exception:
        pass
    return result


def get_short_interest(symbol: str) -> ShortInterestData | None:
    """Real, exchange-reported short-interest data for `symbol` (borrow fee, shares available,
    days-to-cover) from Unusual Whales' short-interest-float endpoint — a materially richer,
    faster-updating source than yfinance's own `shortPercentOfFloat`/`shortRatio` fields, which
    this app's squeeze-alert family currently relies on exclusively. Redis-cached 6h, matching
    the real ~2x/month settlement cadence of the underlying exchange data — no point re-fetching
    more often than the source itself updates. Fail-open, same contract as get_gex_levels().
    """
    if not is_available():
        return None
    sym = symbol.upper()
    cache_key = f"stockai:uw:short_interest:{sym}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            import json
            d = json.loads(cached)
            return ShortInterestData(**d) if d else None
    except Exception:
        pass

    try:
        data = _get(f"/api/shorts/{sym}/interest-float/v2")
    except Exception as exc:
        log.warning("unusual_whales.short_interest_failed", symbol=sym, error=str(exc))
        return None

    result: ShortInterestData | None
    if not data:
        result = None
    else:
        row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
        if not row:
            result = None
        else:
            result = ShortInterestData(
                short_interest=_to_float(row.get("short_interest")),
                short_shares_available=_to_float(row.get("short_shares_available")),
                days_to_cover=_to_float(row.get("days_to_cover")),
                fee_rate=_to_float(row.get("fee_rate")),
                rebate_rate=_to_float(row.get("rebate_rate")),
                si_float=_to_float(row.get("si_float")),
                total_float=_to_float(row.get("total_float")),
                market_date=row.get("market_date"),
            )

    try:
        import json
        from dataclasses import asdict
        _get_redis().setex(cache_key, _SHORT_INTEREST_TTL, json.dumps(asdict(result) if result else None))
    except Exception:
        pass
    return result


def _to_float(v) -> float | None:
    """UW returns several numeric fields as strings (confirmed live) — a plain float() cast
    with a None/empty/non-numeric guard, matching this app's own established `_consensus_num()`/
    `_growth_num()` NaN-safety convention elsewhere (never let a malformed numeric field either
    crash the caller or silently become a fabricated 0.0)."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if f == f else None  # NaN self-inequality guard, same as _consensus_num()
    except (TypeError, ValueError):
        return None
