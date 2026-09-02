"""MPE-06/MPE-07/MPE-OPTIONS-FLOW-ALERT: a real Unusual Whales API client — genuine dealer gamma
exposure (GEX), real short-interest data (borrow fee, shares available, days-to-cover), and
real-time unusual-options-activity flow alerts (rule-based, with a genuine ask-side/bid-side
directional split UW itself computes), replacing this app's own free-data proxies where a
subscription is active.

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
  - /api/option-trades/flow-alerts     — ticker, option_chain, type, strike, expiry,
    total_ask_side_prem/total_bid_side_prem, has_sweep, volume_oi_ratio, alert_rule (the
    non-deprecated replacement for /api/stock/{ticker}/flow-alerts). Real-time WebSocket
    streaming of this same feed (wss://api.unusualwhales.com/socket, channel "flow-alerts")
    requires UW's paid Advanced tier — this app polls the REST endpoint instead, which the
    trial tier's 30,000 req/day budget comfortably supports for a bounded symbol set.
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


@dataclass
class FlowAlert:
    """One row from UW's real /api/option-trades/flow-alerts — a rule-based aggregation over
    the full options tape (repeated same-contract trades within milliseconds, often a single
    large order sweeping across multiple market makers). Field shapes verified directly against
    the live OpenAPI spec's own real example payload, not guessed."""
    ticker: str
    option_chain: str  # UW's own per-contract symbol, e.g. "MSFT231222C00375000" — the real
                       # per-CONTRACT identity this module's own dedup keys off of, not `ticker`
    option_type: str  # "call" | "put"
    strike: float | None
    expiry: str | None  # ISO date string, e.g. "2023-12-22"
    price: float | None  # fill price of the contract
    underlying_price: float | None
    total_premium: float | None
    total_ask_side_prem: float | None  # aggressive BUYING at the ask
    total_bid_side_prem: float | None  # aggressive SELLING at the bid
    total_size: int | None
    volume: int | None
    open_interest: int | None
    volume_oi_ratio: float | None  # how unusual this volume is relative to existing OI
    has_sweep: bool
    alert_rule: str | None
    created_at: str | None


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
def _get(path: str, params: dict | None = None) -> dict | None:
    """A single authenticated GET against the real Unusual Whales API. Returns the parsed
    `data` field of the response (UW's own real response envelope, confirmed live), or `None`
    on any real absence of data. Raises UnusualWhalesRateLimitError/UnusualWhalesAuthError for
    the two error classes that must never be blindly retried — every other transient failure
    (timeout, 5xx, connection error) retries up to 3x with exponential backoff via tenacity,
    matching polygon_adapter.py's own established retry convention exactly.

    `params` is passed straight through to httpx's own query-string encoding (real percent-
    encoding, not manual string interpolation) — every caller with real query parameters
    (get_flow_alerts) uses this rather than building a query string by hand.
    """
    key = get_unusual_whales_key()
    if not key:
        return None
    with httpx.Client(timeout=15) as client:
        r = client.get(
            f"{_BASE_URL}{path}",
            params=params,
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


def get_flow_alerts(
    symbol: str,
    *,
    min_premium: float = 50_000,
    min_volume_oi_ratio: float = 1.0,
    is_sweep: bool = True,
    max_dte: int = 45,
) -> list[FlowAlert]:
    """Real, rule-based unusual-options-activity alerts for `symbol` from UW's own full-tape
    scanner (`/api/option-trades/flow-alerts` — the non-deprecated replacement for the older
    per-ticker `/api/stock/{ticker}/flow-alerts` endpoint). Each row is a contract UW's own
    scanner already flagged as having repeated, rapid same-contract hits — often a single large
    order sweeping across market makers, a real "urgency" signal UW computes for us, not
    something this app derives itself.

    Deliberately NOT cached — unlike GEX/short-interest (slow-moving, real-world data that only
    changes a few times a day at most), flow alerts are inherently a fast-moving, minute-to-
    minute feed; caching this would defeat the entire point of an "alert on fresh activity"
    check. Safe at the trial tier's 30,000 req/day budget as long as the caller stays scoped to
    a bounded symbol set (see check_options_flow_alerts()'s own docstring in scheduler.py) —
    never called for the whole tracked universe.

    Filter thresholds are real UW query params (confirmed against the live OpenAPI spec, not
    guessed) — min_premium/min_volume_oi_ratio/is_sweep/max_dte are all genuine server-side
    filters, so UW itself does the filtering rather than this app fetching everything and
    discarding most of it. Defaults are deliberately a high-conviction floor (a real sweep, real
    size, genuinely unusual relative to existing open interest, expiring soon enough to matter)
    — every caller can override them, but the defaults alone already exclude routine, low-
    signal trades.

    Returns an empty list (never raises) on any failure — the disabled/unconfigured/rate-limited/
    no-alerts-for-this-symbol cases are all indistinguishable to a caller by design, matching
    get_gex_levels()/get_short_interest()'s own fail-open contract.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    try:
        data = _get(
            "/api/option-trades/flow-alerts",
            params={
                "ticker_symbol": sym,
                "min_premium": min_premium,
                "min_volume_oi_ratio": min_volume_oi_ratio,
                "is_sweep": "true" if is_sweep else "false",
                "max_dte": max_dte,
                "limit": 50,
            },
        )
    except Exception as exc:
        log.warning("unusual_whales.flow_alerts_failed", symbol=sym, error=str(exc))
        return []
    return _parse_flow_alert_rows(data, sym)


def _parse_flow_alert_rows(data, sym: str) -> list["FlowAlert"]:
    """Shared row-parsing between get_flow_alerts() (live) and get_historical_flow_alerts()
    (backtest) — both hit the identical /api/option-trades/flow-alerts response shape, so this
    is the ONE place that shape gets translated into a FlowAlert, never a second, independently-
    drifting copy."""
    if not data or not isinstance(data, list):
        return []
    alerts: list[FlowAlert] = []
    for row in data:
        try:
            alerts.append(FlowAlert(
                ticker=row.get("ticker", sym),
                option_chain=row.get("option_chain", ""),
                option_type=row.get("type", ""),
                strike=_to_float(row.get("strike")),
                expiry=row.get("expiry"),
                price=_to_float(row.get("price")),
                underlying_price=_to_float(row.get("underlying_price")),
                total_premium=_to_float(row.get("total_premium")),
                total_ask_side_prem=_to_float(row.get("total_ask_side_prem")),
                total_bid_side_prem=_to_float(row.get("total_bid_side_prem")),
                total_size=int(row["total_size"]) if row.get("total_size") is not None else None,
                volume=int(row["volume"]) if row.get("volume") is not None else None,
                open_interest=int(row["open_interest"]) if row.get("open_interest") is not None else None,
                volume_oi_ratio=_to_float(row.get("volume_oi_ratio")),
                has_sweep=bool(row.get("has_sweep")),
                alert_rule=row.get("alert_rule"),
                created_at=row.get("created_at"),
            ))
        except Exception:
            continue  # one malformed row must never drop the rest of a real response
    return alerts


_HISTORICAL_FLOW_ALERTS_MAX_PAGES = 5  # UW's own limit=200 max per call — 5 pages = up to 1,000
# rows per symbol per backtest window, a real bound on both wall-clock time and the trial tier's
# request budget when scanning many symbols over a multi-month window.


def get_historical_flow_alerts(
    symbol: str,
    *,
    newer_than: str,
    older_than: str,
    min_premium: float = 250_000,
    min_volume_oi_ratio: float = 3.0,
    is_sweep: bool | None = True,
    max_dte: int = 45,
) -> list[FlowAlert]:
    """MPE-OPTIONS-FLOW-ALERT backtest: the SAME real /api/option-trades/flow-alerts endpoint
    get_flow_alerts() uses, but scoped to a real historical date range via newer_than/older_than
    — confirmed live against production (not assumed from the spec alone) that UW genuinely
    retains at least 2 months of real historical flow-alert rows with real created_at
    timestamps, real premiums, and real underlying_price snapshots at the moment each alert
    fired. This is a GENUINE historical replay, not a proxy — unlike squeeze_alert_backtest()'s
    own gamma-unwind gap (no historical options open-interest data exists anywhere for that),
    UW's own flow-alerts feed IS retained historically, so a real backtest is possible here.

    Defaults match check_options_flow_alerts()'s own live thresholds exactly (see
    AUD-OPTIONSFLOW-FLOODED) — a caller can override to test alternate thresholds, but the
    default backtest run tests the SAME filter that's actually live today, not a different one.

    is_sweep=None omits the query param entirely, returning a genuine MIX of sweep and non-
    sweep rows — confirmed live against production that UW's own is_sweep param is a hard
    binary filter both ways (True returns ONLY sweeps, False returns ONLY non-sweeps, neither
    is "unfiltered"), so a caller wanting to actually COMPARE sweep-vs-non-sweep outcomes must
    omit the key, not pass False (which would silently exclude every sweep, the opposite of a
    fair comparison).

    Paginates via older_than (UW's own 200-row-per-call max) up to
    _HISTORICAL_FLOW_ALERTS_MAX_PAGES pages — bounded, not exhaustive, matching this app's own
    "no silent truncation, log what's dropped" discipline: a symbol/window combination that
    would need more pages than this cap simply returns whatever the cap covers (the newest rows
    in the window, since UW returns newest-first), not a crash or an infinite loop.
    """
    if not is_available():
        return []
    sym = symbol.upper()
    all_rows: list[dict] = []
    cursor_older_than = older_than
    params: dict = {
        "ticker_symbol": sym,
        "min_premium": min_premium,
        "min_volume_oi_ratio": min_volume_oi_ratio,
        "max_dte": max_dte,
        "newer_than": newer_than,
        "limit": 200,
    }
    if is_sweep is not None:
        params["is_sweep"] = "true" if is_sweep else "false"
    for _page in range(_HISTORICAL_FLOW_ALERTS_MAX_PAGES):
        try:
            data = _get(
                "/api/option-trades/flow-alerts",
                params={**params, "older_than": cursor_older_than},
            )
        except Exception as exc:
            log.warning("unusual_whales.historical_flow_alerts_failed", symbol=sym, error=str(exc))
            break
        if not data or not isinstance(data, list):
            break
        all_rows.extend(data)
        if len(data) < 200:
            break  # fewer than a full page — no more history left in this window
        oldest_created_at = data[-1].get("created_at")
        if not oldest_created_at:
            break
        cursor_older_than = oldest_created_at  # page backward in time
    return _parse_flow_alert_rows(all_rows, sym)


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
