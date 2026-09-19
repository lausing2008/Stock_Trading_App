"""T404-OPTIONS-UW-MIGRATION — serve every option chain from Unusual Whales, not yfinance.

WHY. The Options tab's chain, flow, expirations and game plan all ran on yfinance
(`yf.Ticker(sym).option_chain(exp)`). On 2026-09-15 Yahoo's options endpoint began returning
empty for EVERY symbol — the crumb fetch is 429'd, and without a crumb the options call yields
nothing. Four panels went dark for three days and nobody noticed, because an empty chain was
reported as "no_options_listed", which is indistinguishable from a stock that simply has no
options (AUD-T403). Meanwhile this app pays for Unusual Whales, whose chain was current the
whole time and which the options-income engine was already using happily.

WHAT UW GIVES THAT YFINANCE DID NOT: real per-contract greeks. yfinance supplied only implied
volatility, and the Option Trading Guide documented "no real Greeks" as a known limitation.
UW rows carry delta/gamma/theta/vega/rho directly.

THE ONE REAL COST, stated everywhere it matters: UW's chain endpoint is HISTORICAL — it serves
the chain as it stood at the close of a SETTLED session. yfinance quotes were intraday-ish. So
every response here carries `as_of` and `is_settled_session`, and callers must surface it. A
settled bid presented as a live quote is exactly the A06 finding already on the tracker
("archived bids treated as current fills"); this module makes that visible rather than
repeating it silently.

QUOTA. Measured 2026-09-17: 9,773 UW calls that day against a 120,000/day allowance — 8%. A
per-symbol chain fetch is affordable. It is also mostly free in practice: rows are persisted on
first fetch, so the second view of the same symbol/session costs nothing, and the archive grows
toward the symbols people actually look at rather than a hardcoded list.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

log = structlog.get_logger()

# How far back to accept a settled chain before giving up. A long weekend plus a holiday is 4
# days; 7 covers that without silently serving something a week and a half stale.
_MAX_LOOKBACK_DAYS = 7


def _rows_for(session: Session, symbol: str, as_of: date, expiry: date | None = None) -> list[dict]:
    q = """
        SELECT strike, option_type, expiry, open_interest, volume, nbbo_bid, nbbo_ask,
               implied_volatility, delta, gamma, theta, vega, rho, option_symbol
        FROM option_chain_history
        WHERE symbol = :sym AND as_of = :as_of
    """
    params: dict = {"sym": symbol.upper(), "as_of": as_of}
    if expiry is not None:
        q += " AND expiry = :expiry"
        params["expiry"] = expiry
    q += " ORDER BY expiry, strike"
    return [dict(r._mapping) for r in session.execute(text(q), params).all()]


def latest_as_of(session: Session, symbol: str, not_after: date | None = None) -> date | None:
    """Most recent captured session for this symbol, at or before `not_after`."""
    row = session.execute(text("""
        SELECT max(as_of) AS d FROM option_chain_history
        WHERE symbol = :sym AND (:cap IS NULL OR as_of <= :cap)
    """), {"sym": symbol.upper(), "cap": not_after}).first()
    return row.d if row and row.d else None


def _to_chain_row(r: dict, spot: float | None) -> dict:
    """UW row -> the exact shape `_options_chain_rows()` produced from a yfinance DataFrame, so
    every existing consumer (max-pain, the flow summary, the strategy matrix) works unchanged.

    `last_price` is 0.0: UW has no last-traded field here. Nothing downstream should prefer it
    anyway — mid of nbbo_bid/nbbo_ask is a better mark, and the callers already fall back to it
    only when bid and ask are both absent.
    """
    strike = float(r["strike"] or 0)
    otype = (r.get("option_type") or "").lower()
    itm = False
    if spot and strike:
        itm = (spot > strike) if otype.startswith("c") else (spot < strike)
    iv = r.get("implied_volatility")
    return {
        "strike": strike,
        "bid": float(r.get("nbbo_bid") or 0),
        "ask": float(r.get("nbbo_ask") or 0),
        "last_price": 0.0,
        "volume": int(r.get("volume") or 0),
        "oi": int(r.get("open_interest") or 0),
        # yfinance gave IV as a fraction and the old adapter multiplied by 100; UW gives a
        # fraction too, so the same conversion keeps every downstream threshold valid.
        "iv": round(float(iv) * 100, 1) if iv is not None else 0.0,
        "itm": itm,
        # Beyond what yfinance ever provided — real per-contract greeks.
        "delta": r.get("delta"), "gamma": r.get("gamma"), "theta": r.get("theta"),
        "vega": r.get("vega"), "rho": r.get("rho"),
        "option_symbol": r.get("option_symbol"),
        "expiry": r.get("expiry").isoformat() if r.get("expiry") else None,
    }


def get_chain(
    session: Session,
    symbol: str,
    *,
    expiry: date | None = None,
    spot: float | None = None,
    allow_fetch: bool = True,
) -> dict:
    """The chain for `symbol`, from the archive, fetching+persisting from UW on a miss.

    Returns {available, as_of, is_settled_session, source, calls, puts, expiries, reason}.
    `calls`/`puts` are in the legacy `_options_chain_rows()` shape. Never raises.
    """
    sym = symbol.upper()
    # AUD-T409-UTCDATEBOUNDARY: ET, not a naive UTC truncation (see the same finding in
    # options_income_engine._today_et()) — otherwise the staleness check and the fetch
    # window both run one day ahead of the real US trading day for part of every evening.
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    as_of = latest_as_of(session, sym)

    # Fetch when we have nothing, or nothing recent enough to be worth showing.
    stale = as_of is None or (today - as_of).days > _MAX_LOOKBACK_DAYS
    if stale and allow_fetch:
        try:
            from .scheduler import capture_option_chain_history
            # Yesterday back MAX_LOOKBACK: the chain for a session settles after its close, so
            # today is never expected to exist. capture is idempotent and skips what it has.
            end = today - timedelta(days=1)
            start = end - timedelta(days=_MAX_LOOKBACK_DAYS)
            res = capture_option_chain_history([sym], start, end, skip_existing=True)
            log.info("uw_chain.fetched_on_demand", symbol=sym, **{k: v for k, v in res.items() if k != "errors_detail"})
            as_of = latest_as_of(session, sym)
        except Exception as exc:
            log.warning("uw_chain.fetch_failed", symbol=sym, error=str(exc))

    if as_of is None:
        return {"available": False, "reason": "no_chain_for_symbol", "symbol": sym,
                "as_of": None, "is_settled_session": True, "source": "unusual_whales",
                "calls": [], "puts": [], "expiries": []}

    rows = _rows_for(session, sym, as_of, expiry)
    if not rows:
        return {"available": False, "reason": "no_contracts_for_expiry", "symbol": sym,
                "as_of": as_of.isoformat(), "is_settled_session": True,
                "source": "unusual_whales", "calls": [], "puts": [], "expiries": []}

    calls = [_to_chain_row(r, spot) for r in rows if (r.get("option_type") or "").lower().startswith("c")]
    puts  = [_to_chain_row(r, spot) for r in rows if (r.get("option_type") or "").lower().startswith("p")]
    all_exp = sorted({r["expiry"].isoformat() for r in _rows_for(session, sym, as_of) if r.get("expiry")})

    return {
        "available": True, "symbol": sym,
        "as_of": as_of.isoformat(),
        # The whole point of carrying this: these are SETTLED closes, not live quotes.
        "is_settled_session": True,
        "session_age_days": (today - as_of).days,
        "source": "unusual_whales",
        "calls": sorted(calls, key=lambda x: x["strike"]),
        "puts": sorted(puts, key=lambda x: x["strike"]),
        "expiries": all_exp,
    }


def get_expiries(session: Session, symbol: str, *, allow_fetch: bool = True) -> list[str]:
    """Listed expiries from the most recent captured session — the UW replacement for
    `sorted(yf.Ticker(sym).options)`."""
    chain = get_chain(session, symbol, allow_fetch=allow_fetch)
    return chain["expiries"] if chain["available"] else []
