"""T323-DARKPOOL: Unusual Whales' real `/api/congress/recent-trades` feed, factored out into
shared/common/ (rather than living only in services/market-data/src/services/unusual_whales.py,
where the rest of the UW client lives) because the ONE consumer of congress data —
services/event-intelligence/src/services/congress.py — runs in a separate container that never
mounts market-data's own src/ tree, only shared/. Matches ai_keys.py's own reason for living
here: a genuinely cross-service dependency, not a market-data-only concern.

market-data's own unusual_whales.py module re-exports get_congress_trades/CongressTradeRow from
here rather than keeping a second, independently-drifting copy, so both services call the exact
same implementation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import httpx
import structlog

from .ai_keys import get_unusual_whales_key, is_unusual_whales_enabled
from .redis_client import get_redis

log = structlog.get_logger()

_BASE_URL = "https://api.unusualwhales.com"
_CONGRESS_TTL = 21600  # 6h — matches unusual_whales.py's own short-interest cadence; congress
# disclosures have a real multi-day filing lag (STOCK Act gives 45 days), nothing here changes
# minute to minute the way options flow-alerts does.


@dataclass
class CongressTradeRow:
    """One row from UW's real `/api/congress/recent-trades`. Field names deliberately mirror
    this app's own existing CongressTrade DB model (politician_name/transaction_type/
    amount_min/amount_max/trade_date/disclosure_date) so sync_congress_trades() can upsert
    either source through the identical write path with no shape-translation layer of its own."""
    politician_name: str
    party: str | None
    chamber: str | None
    ticker: str
    transaction_type: str  # normalized to purchase|sale|exchange|unknown
    amount_min: float | None
    amount_max: float | None
    trade_date: str | None  # ISO date
    disclosure_date: str | None  # ISO date


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _normalize_congress_txn_type(raw: str | None) -> str:
    """Matches services/event-intelligence/src/services/congress.py's own _normalize_txn_type()
    exactly — both sources must feed the identical vocabulary _congress_score_from_trades()
    scores against, or a source-dependent scoring bug would exist."""
    if not raw:
        return "unknown"
    raw = raw.lower()
    if "purchase" in raw or "buy" in raw:
        return "purchase"
    if "sale" in raw or "sell" in raw:
        return "sale"
    if "exchange" in raw:
        return "exchange"
    return raw[:32]


def is_available() -> bool:
    return is_unusual_whales_enabled() and bool(get_unusual_whales_key())


def get_congress_trades(*, since: str, limit: int = 200) -> list[CongressTradeRow]:
    """Real Congressional trade disclosures from UW's own dedicated feed, replacing
    event-intelligence's own EI-CONGRESS1 fallback (kadoa-org/congress-trading-monitor's
    unofficial, rolling ~5000-row GitHub mirror) when a subscription is configured.

    `since` is a bare YYYY-MM-DD date — UW's date-filter params only accept bare dates/
    epoch-seconds, never a full ISO datetime (confirmed live for the sibling flow-alerts
    endpoint's own newer_than param; applied defensively here too rather than assumed identical
    without having tested this specific endpoint's own params against a real subscription yet).

    Not per-symbol — returns the whole recent-activity feed filtered by date, matching
    sync_congress_trades()'s existing "pull the full feed, upsert by identity key" shape.

    Redis-cached 6h. Fails open (empty list) on any error/disabled/unconfigured state.
    """
    if not is_available():
        return []
    cache_key = f"stockai:uw:congress:{since}"
    try:
        cached = get_redis().get(cache_key)
        if cached:
            import json
            rows = json.loads(cached)
            return [CongressTradeRow(**r) for r in rows]
    except Exception:
        pass

    key = get_unusual_whales_key()
    result: list[CongressTradeRow] = []
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(
                f"{_BASE_URL}/api/congress/recent-trades",
                params={"date": since, "limit": limit},
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
            if r.status_code in (401, 403, 429):
                log.warning("uw_congress.auth_or_rate_limit", status=r.status_code)
                return []
            if r.status_code == 404:
                return []
            r.raise_for_status()
            body = r.json()
            data = body.get("data") if isinstance(body, dict) else body
    except Exception as exc:
        log.warning("uw_congress.fetch_failed", since=since, error=str(exc))
        return []

    if isinstance(data, list):
        for row in data:
            try:
                ticker = (row.get("ticker") or "").upper()
                if not ticker:
                    continue
                result.append(CongressTradeRow(
                    politician_name=row.get("politician_name") or row.get("reporter") or row.get("name") or "Unknown",
                    party=row.get("party"),
                    chamber=row.get("chamber"),
                    ticker=ticker,
                    transaction_type=_normalize_congress_txn_type(row.get("transaction_type") or row.get("type")),
                    amount_min=_to_float(row.get("amount_min") or row.get("amounts_min")),
                    amount_max=_to_float(row.get("amount_max") or row.get("amounts_max")),
                    trade_date=row.get("transaction_date") or row.get("trade_date"),
                    disclosure_date=row.get("filing_date") or row.get("disclosure_date"),
                ))
            except Exception:
                continue  # one malformed row must never drop the rest of a real response

    try:
        import json
        get_redis().setex(cache_key, _CONGRESS_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result
