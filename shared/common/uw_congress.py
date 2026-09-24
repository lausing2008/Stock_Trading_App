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

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import httpx
import structlog

from .ai_keys import get_unusual_whales_key, is_unusual_whales_enabled
from .redis_client import get_redis

log = structlog.get_logger()

_BASE_URL = "https://api.unusualwhales.com"

# AUD-UWUSAGE: mirrors unusual_whales.py's own _incr_call_counter() exactly (same key prefix/
# format, so both modules' counts aggregate into one per-endpoint daily total on the dashboard)
# — kept as an independent copy rather than importing from unusual_whales.py, since this module
# specifically exists to avoid a market-data-only import for event-intelligence's sake (see the
# module docstring above).
_CALL_COUNTER_PREFIX = "stockai:metric:uw_calls"
_CALL_COUNTER_TTL_S = 25 * 3600


def _incr_call_counter(path: str) -> None:
    try:
        r = get_redis()
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        key = f"{_CALL_COUNTER_PREFIX}:{path}:{day}"
        r.incr(key)
        if r.ttl(key) == -1:
            r.expire(key, _CALL_COUNTER_TTL_S)
    except Exception:
        pass
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
    # AUD-UWCONGRESS-FIELDNAMES: UW's own display band ("$1,001 - $15,000"). Stored verbatim in
    # CongressTrade.amount_range alongside the parsed numeric pair, because the band IS the
    # disclosure — a reader seeing "$1,001 - $15,000" learns more than one seeing 1001.0.
    # Defaulted so market-data's re-export and any existing constructor call still work.
    amount_range_label: str | None = None


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
    scores against, or a source-dependent scoring bug would exist.

    AUD-UWCONGRESS-FIELDNAMES: UW's `txn_type` enum has NINE spellings with inconsistent casing
    — Buy, Purchase, Sell, "Sell (partial)", "Sell (PARTIAL)", "Sale (Partial)", "Sale (Full)",
    Exchange, Receive. The substring matching below covers the first eight. `Receive` is handled
    explicitly: it is a grant/transfer IN, not an open-market purchase, and letting it fall
    through to `raw[:32]` would put the literal string "receive" into the vocabulary that
    _congress_score_from_trades() and get_smart_money_leaderboard() both match against — a
    silent third category neither knows about. It maps to `exchange`, this schema's existing
    bucket for "a position changed hands without being a directional bet"."""
    if not raw:
        return "unknown"
    raw = raw.lower()
    if "purchase" in raw or "buy" in raw:
        return "purchase"
    if "sale" in raw or "sell" in raw:
        return "sale"
    if "exchange" in raw or "receive" in raw:
        return "exchange"
    return raw[:32]


def _parse_amount_range(raw: str | None) -> tuple[float | None, float | None]:
    """UW reports position size as a display STRING range — "$1,001 - $15,000" — not as the
    numeric amount_min/amount_max pair the DB model stores. Congressional disclosure is banded
    by statute, so a range is all that exists; there is no exact figure being discarded here.

    Returns (min, max). A single open-ended value ("$1,000,001+") yields (1000001.0, None),
    which is honest: the upper bound genuinely is not disclosed. Never raises — a parse failure
    returns (None, None) so one odd band cannot drop an otherwise-good trade row."""
    if not raw or not isinstance(raw, str):
        return (None, None)
    nums = re.findall(r"[\d,]+(?:\.\d+)?", raw)
    vals: list[float] = []
    for n in nums:
        try:
            vals.append(float(n.replace(",", "")))
        except ValueError:
            continue
    if not vals:
        return (None, None)
    if len(vals) == 1:
        return (vals[0], None)
    return (min(vals), max(vals))


def _parse_congress_rows(data) -> list[CongressTradeRow]:
    """Map UW's payload rows onto CongressTradeRow.

    Extracted from get_congress_trades()'s HTTP path so the field mapping can be tested against
    real captured payloads without a network call. That matters more than usual here: the whole
    AUD-UWCONGRESS-FIELDNAMES bug was a mapping error that no test could see, because the only
    code that knew UW's key spellings lived inside an un-runnable function.
    """
    result: list[CongressTradeRow] = []
    if isinstance(data, list):
        for row in data:
            try:
                ticker = (row.get("ticker") or "").upper()
                if not ticker:
                    continue
                # AUD-UWCONGRESS-FIELDNAMES: the key names below are UW's ACTUAL payload keys,
                # verified against both a live response and the published OpenAPI spec. The
                # previous mapping probed `transaction_type`/`filing_date`/`amount_min`/
                # `chamber` — none of which UW sends — so every field silently defaulted and
                # 7,691 of 9,453 stored rows (81%) had no direction, no disclosure date and no
                # amount. Nothing was ever missing from the feed; it was all dropped at parse.
                # The legacy spellings are kept as fallbacks so a kadoa-shaped dict or a future
                # UW rename still parses.
                amounts_label = row.get("amounts") or None
                amt_min, amt_max = _parse_amount_range(amounts_label)
                if amt_min is None and amt_max is None:
                    amt_min = _to_float(row.get("amount_min") or row.get("amounts_min"))
                    amt_max = _to_float(row.get("amount_max") or row.get("amounts_max"))
                # `name` is the standard form ("Pete Sessions"); `reporter` is the filing-style
                # variant ("Hon. Pete Sessions", 184 of 200 sampled rows). Preferring `reporter`
                # is what split one person's history across two spellings and made the UW and
                # kadoa feeds fail to join — see get_smart_money_leaderboard()'s own caveat.
                name = row.get("name") or row.get("politician_name") or row.get("reporter")
                result.append(CongressTradeRow(
                    politician_name=name or "Unknown",
                    # UW's trade rows carry NO party at all; it comes from the politicians
                    # roster, joined by the caller. Never invent one here.
                    party=row.get("party"),
                    chamber=row.get("member_type") or row.get("chamber"),
                    ticker=ticker,
                    transaction_type=_normalize_congress_txn_type(
                        row.get("txn_type") or row.get("transaction_type") or row.get("type")
                    ),
                    amount_min=amt_min,
                    amount_max=amt_max,
                    trade_date=row.get("transaction_date") or row.get("trade_date"),
                    disclosure_date=(
                        row.get("filed_at_date") or row.get("filing_date") or row.get("disclosure_date")
                    ),
                    amount_range_label=amounts_label,
                ))
            except Exception:
                continue  # one malformed row must never drop the rest of a real response
    return result


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
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(
                f"{_BASE_URL}/api/congress/recent-trades",
                params={"date": since, "limit": limit},
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            )
            _incr_call_counter("/api/congress/recent-trades")
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

    result = _parse_congress_rows(data)

    try:
        import json
        get_redis().setex(cache_key, _CONGRESS_TTL, json.dumps([asdict(r) for r in result]))
    except Exception:
        pass
    return result


_ROSTER_TTL = 86400  # 24h — a chamber roster changes at elections, not intraday.


def get_congress_roster() -> dict[str, dict]:
    """UW's `/api/congress/politicians` — the identity table for the trade feed.

    WHY THIS EXISTS. UW's trade rows carry no `party` field at all (verified: 0 of 200 sampled
    rows have the key), which is why all 7,691 UW-sourced rows in `congress_trades` have a NULL
    party while the kadoa-sourced rows do not. The roster is the only place that information
    lives, and it also carries `politician_id` / `bioguide_id` — stable identifiers that are the
    correct long-term join key between feeds, rather than matching on a display name that each
    feed spells differently.

    Keyed by LOWER-CASED NAME rather than by politician_id, deliberately: the trade rows this
    enriches are matched by name at the call site, and the id is carried in the value for a
    future schema that can store it. Storing politician_id on CongressTrade would need a real
    column migration and is deliberately out of scope for the parse fix.

    Redis-cached 24h. Fails open (empty dict) — a roster outage must degrade party to NULL,
    exactly today's behaviour, never drop a trade.
    """
    if not is_available():
        return {}
    cache_key = "stockai:uw:congress:roster"
    try:
        cached = get_redis().get(cache_key)
        if cached:
            import json
            return json.loads(cached)
    except Exception:
        pass

    out: dict[str, dict] = {}
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(
                f"{_BASE_URL}/api/congress/politicians",
                headers={
                    "Authorization": f"Bearer {get_unusual_whales_key()}",
                    "Accept": "application/json",
                },
            )
            _incr_call_counter("/api/congress/politicians")
            if r.status_code in (401, 403, 404, 429):
                log.warning("uw_congress.roster_unavailable", status=r.status_code)
                return {}
            r.raise_for_status()
            body = r.json()
            data = body.get("data") if isinstance(body, dict) else body
    except Exception as exc:
        log.warning("uw_congress.roster_fetch_failed", error=str(exc))
        return {}

    if isinstance(data, list):
        for row in data:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            out[name.lower()] = {
                # UW spells these "democrat"/"republican"; the DB's existing kadoa-sourced rows
                # use the single-letter form. Normalise here so one column never carries two
                # vocabularies — get_smart_money_leaderboard() groups by party.
                "party": _normalize_party(row.get("party")),
                "chamber": row.get("chamber"),
                # Carried so canonicalize_politician_name() can rewrite a feed's spelling onto
                # the roster's own, rather than matching roster entries by their dict key.
                "canonical_name": name,
                "politician_id": row.get("politician_id"),
                "bioguide_id": row.get("bioguide_id"),
            }

    try:
        import json
        get_redis().setex(cache_key, _ROSTER_TTL, json.dumps(out))
    except Exception:
        pass
    return out


def _normalize_party(raw: str | None) -> str | None:
    """UW sends "democrat"/"republican"/"independent"; the kadoa feed already stored "D"/"R"/"I".
    Both land in the same column, so they must agree or a GROUP BY party splits one party into
    two rows. Returns None for anything unrecognised rather than guessing a letter."""
    if not raw:
        return None
    r = raw.strip().lower()
    if r.startswith("d"):
        return "D"
    if r.startswith("r"):
        return "R"
    if r.startswith("i"):
        return "I"
    return None


def _name_parts(raw: str) -> tuple[str, str] | None:
    """(given, surname), lower-cased, with honorifics/suffixes/middle initials stripped.

    Returns None when there is no usable surname, so callers can leave the name untouched
    rather than merge on a fragment."""
    if not raw:
        return None
    s = raw.strip().lower()
    s = re.sub(r"^(hon\.?|mr\.?|mrs\.?|ms\.?|dr\.?|rep\.?|sen\.?|senator|representative)\s+", "", s)
    s = re.sub(r"[,.]", " ", s)
    toks = [t for t in s.split() if t]
    # Drop generational suffixes and bare middle initials — "David J. Taylor" and "David Taylor"
    # must reduce to the same pair, or the merge this feeds would never fire.
    toks = [t for t in toks if t not in {"jr", "sr", "ii", "iii", "iv", "dr", "hon"} and len(t) > 1]
    if len(toks) < 2:
        return None
    return (toks[0], toks[-1])


def canonicalize_politician_name(raw: str, chamber: str | None, roster: dict) -> str:
    """Map one feed's spelling of a member onto the roster's canonical name.

    WHY. The same person reaches this table under three spellings — UW's own `name`
    ("Ro Khanna"), the kadoa feed's ("Rohit Khanna"), and the honorific `reporter` form
    ("Hon. David J. Taylor"). Left alone they rank as separate traders with different and
    contradictory returns, which is worse than the caveat it replaced: a reader comparing
    "+5.26% on 23 buys" against "+3.30% on 55 buys" is comparing one person to himself.

    DELIBERATELY CONSERVATIVE, because a wrong merge silently pools two people's returns and is
    far worse than leaving a duplicate visible. A name is rewritten ONLY when all three hold:

      1. the surname matches exactly one roster member in the same chamber,
      2. the given names are prefix-compatible in either direction ("rohit" vs "ro"), and
      3. a chamber is known — an unknown chamber cannot disambiguate two same-surname members.

    Anything ambiguous is returned unchanged. That is why "Marjorie Taylor Greene" is safe: her
    surname is "greene", so she never competes with the two Taylors.
    """
    if not raw or not roster:
        return raw
    parts = _name_parts(raw)
    if not parts:
        return raw
    given, surname = parts
    ch = (chamber or "").strip().lower()
    if not ch:
        return raw

    # Surname + chamber narrows the field; the GIVEN name then has to single one out. Order
    # matters: filtering on surname uniqueness first would refuse to merge "David J. Taylor"
    # purely because a Nicholas Taylor also sits in the House, even though the given name
    # settles it unambiguously.
    candidates = []
    for canon in roster.values():
        cname = canon.get("canonical_name")
        if not cname or (canon.get("chamber") or "").strip().lower() != ch:
            continue
        cparts = _name_parts(cname)
        if not cparts or cparts[1] != surname:
            continue
        cgiven = cparts[0]
        if given == cgiven or given.startswith(cgiven) or cgiven.startswith(given):
            candidates.append(cname)

    # Exactly one survivor, or nothing happens. Two compatible candidates ("Jo" against both a
    # John and a Joseph in the same chamber) is precisely the case where guessing pools two
    # people's returns into one fabricated track record.
    if len(set(candidates)) != 1:
        return raw
    return candidates[0]
