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

# Fix B (2026-10-23): symbols the provider cannot serve, recorded EXPLICITLY so they are neither
# retried every night nor counted as archive gaps. HK listings are excluded by market (Unusual
# Whales has no HK option chains).
PROVIDER_EXCLUDED: dict[str, str] = {
    "BRK-A": "Unusual Whales returns 422 for BRK-A option chains (every session 2026-10-13..22)",
}


def provider_excluded(symbol: str) -> str | None:
    """Why the provider cannot serve `symbol`'s chain, or None."""
    sym = symbol.upper()
    if sym.endswith(".HK"):
        return "no Unusual Whales option chains for HK listings"
    return PROVIDER_EXCLUDED.get(sym)


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


def _coverage_for(session: Session, symbol: str, as_of: date) -> dict | None:
    """Stored pagination evidence for this exact symbol/session, if it exists."""
    try:
        row = session.execute(text("""
            SELECT status, pages_fetched, page_size, rows_returned, completion_reason,
                   source, retrieved_at
            FROM option_chain_coverage
            WHERE symbol = :sym AND as_of = :as_of
        """), {"sym": symbol.upper(), "as_of": as_of}).first()
    except Exception:
        # Supports rolling upgrades where the new coverage table has not been created yet.
        return None
    return dict(row._mapping) if row else None


def _measured_count(v) -> tuple[int | None, str]:
    """(value, status) for a contract count: 'ok', 'missing' or 'invalid' (negative).

    Audit R04 (2026-10-22): `int(v or 0)` turned an absent count into a measured zero, so "100%
    calls" could include puts nobody measured. A genuine 0 stays 0. Malformed strings were
    already coerced to NULL at capture (`_opthist_i`), so they arrive here as 'missing'."""
    if v is None:
        return None, "missing"
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None, "invalid"
    if n < 0:
        return None, "invalid"
    return n, "ok"


def _measured_iv(v) -> tuple[float | None, str]:
    """IV in percent, or None. Unknown IV is not zero volatility (R04): UW leaves IV null for
    contracts that did not trade that session, which is about half of a typical chain."""
    if v is None:
        return None, "missing"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None, "invalid"
    if f < 0 or f != f:
        return None, "invalid"
    return round(f * 100, 1), "ok"


def moneyness_for(option_type: str, strike: float, reference: float | None) -> str | None:
    """'ITM', 'ATM' or 'OTM' against a reference underlying price, or None when there is none.

    Audit R03 (2026-10-22): `itm` defaulted to False without a reference, so every contract read
    as OTM. At exactly the strike a contract is ATM, not OTM."""
    if reference is None or not (reference > 0) or not strike:
        return None
    if reference == strike:
        return "ATM"
    is_call = (option_type or "").lower().startswith("c")
    return "ITM" if ((reference > strike) if is_call else (reference < strike)) else "OTM"


def session_reference_close(session: Session, symbol: str, as_of: date) -> dict | None:
    """The underlying's daily close FOR THE CHAIN'S OWN SESSION, or None.

    Audit R03: historical moneyness may only be measured against the same session's price.
    Today's price against a settled chain mixes two times, so this never falls back to an
    earlier bar or to a live quote — no matching bar means moneyness is unknown."""
    if session is None:
        return None
    try:
        row = session.execute(text("""
            SELECT p.close FROM prices p
            JOIN stocks s ON s.id = p.stock_id
            WHERE s.symbol = :sym AND p.timeframe = 'D1' AND p.close IS NOT NULL
              AND DATE(p.ts) = :d
            ORDER BY p.ts DESC LIMIT 1
        """), {"sym": symbol.upper(), "d": as_of}).first()
    except Exception as exc:  # noqa: BLE001
        log.warning("uw_chain.reference_close_failed", symbol=symbol, as_of=str(as_of), error=str(exc))
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
    if not row or row.close is None or not (float(row.close) > 0):
        return None
    return {"price": float(row.close), "session": as_of.isoformat(), "source": "daily_close"}


# R03 price basis (2026-10-23): several ingest paths download daily bars with auto_adjust=True,
# so a stored close can be split- (and dividend-) adjusted after the fact while the archived
# strikes stay in the terms of their own session. The close is used for moneyness only when the
# chain itself agrees with it: put-call parity near the money gives the underlying in the
# chain's OWN strike terms. A split moves the ratio by 25% or more (5-for-4); 7.5% leaves room
# for carry and dividends in near-dated parity while still catching any split.
_PARITY_TOLERANCE = 0.075
_PARITY_PAIRS = 3


def _two_sided_mid(r: dict) -> float | None:
    try:
        bid, ask = float(r.get("nbbo_bid")), float(r.get("nbbo_ask"))
    except (TypeError, ValueError):
        return None
    if not (bid > 0 and ask >= bid):
        return None
    return (bid + ask) / 2.0


def chain_implied_spot(rows: list[dict]) -> dict | None:
    """The underlying price implied by the chain itself, in its own strike terms, or None.

    For each (expiry, strike) with two-sided call AND put quotes, S ~ K + C - P. The
    `_PARITY_PAIRS` pairs nearest the money (smallest |C - P|) are used, nearest expiry first,
    and their median is returned."""
    by_key: dict = {}
    for r in rows:
        kind = (r.get("option_type") or "").lower()[:1]
        mid = _two_sided_mid(r)
        if kind not in ("c", "p") or mid is None or r.get("strike") is None:
            continue
        by_key.setdefault((r.get("expiry"), float(r["strike"])), {})[kind] = mid
    pairs = [(exp, k, q["c"], q["p"]) for (exp, k), q in by_key.items() if "c" in q and "p" in q]
    if not pairs:
        return None
    pairs.sort(key=lambda t: (abs(t[2] - t[3]), str(t[0])))
    est = sorted(k + c - p for _, k, c, p in pairs[:_PARITY_PAIRS])
    n = len(est)
    med = est[n // 2] if n % 2 else (est[n // 2 - 1] + est[n // 2]) / 2.0
    return {"price": round(med, 4), "pairs": n} if med > 0 else None


def reference_basis(close: float, implied: dict | None) -> dict:
    """Whether a stored close and the chain's strikes can be shown to share a price basis."""
    if implied is None:
        return {"status": "unverifiable", "chain_implied": None, "ratio": None,
                "reason": "no two-sided call/put pair to derive the chain's own price"}
    ratio = close / implied["price"]
    ok = abs(ratio - 1.0) <= _PARITY_TOLERANCE
    return {"status": "compatible" if ok else "incompatible", "chain_implied": implied["price"],
            "ratio": round(ratio, 4), "pairs": implied["pairs"],
            "reason": None if ok else
            f"stored close {close:.2f} vs chain-implied {implied['price']:.2f} (ratio {ratio:.3f}): different price bases, e.g. a split adjustment"}


def _to_chain_row(r: dict, spot: float | None) -> dict:
    """UW row -> the exact shape `_options_chain_rows()` produced from a yfinance DataFrame, so
    every existing consumer (max-pain, the flow summary, the strategy matrix) works unchanged.

    `last_price` is 0.0: UW has no last-traded field here.

    O02 (2026-10-08): "nothing downstream should prefer it" was not true. `get_options_flow`
    computed `premium = volume * lastPrice * 100`, so every UW-derived row had ZERO premium and
    a FALSE whale flag regardless of activity — and that count fed the options-pressure score
    and the stored ML feature `opt_whale_count`, where a missing input was trained on as a
    measured zero.

    The 0.0 stays, because callers do float arithmetic on this field and a None would move the
    failure rather than remove it; `last_price_available` is the thing to test, and the route
    now reads THAT rather than inferring absence from a zero. A zero price and an absent price
    are different facts and only one of them is a measurement.
    """
    strike = float(r["strike"] or 0)
    otype = (r.get("option_type") or "").lower()
    # `spot` must be the SAME session's underlying price (see session_reference_close); with
    # none, moneyness is unknown — None, never False (R03).
    moneyness = moneyness_for(otype, strike, spot)
    volume, volume_status = _measured_count(r.get("volume"))
    oi, oi_status = _measured_count(r.get("open_interest"))
    iv, iv_status = _measured_iv(r.get("implied_volatility"))
    return {
        "strike": strike,
        "bid": float(r.get("nbbo_bid") or 0),
        "ask": float(r.get("nbbo_ask") or 0),
        "last_price": 0.0,
        #: THE FIELD TO TEST. This source carries no last-trade price, so no premium actually
        #: paid can be computed from it — distinct from a contract that genuinely traded at 0.
        "last_price_available": False,
        # R04: None when unmeasured, never 0. A genuine 0 is kept as 0.
        "volume": volume,
        "oi": oi,
        # yfinance gave IV as a fraction and the old adapter multiplied by 100; UW gives a
        # fraction too, so the same conversion keeps every downstream threshold valid.
        "iv": iv,
        "field_status": {"volume": volume_status, "oi": oi_status, "iv": iv_status},
        # R03: True/False only when measured against the same session's price.
        "itm": None if moneyness is None else moneyness == "ITM",
        "moneyness": moneyness,
        # Beyond what yfinance ever provided — real per-contract greeks.
        "delta": r.get("delta"), "gamma": r.get("gamma"), "theta": r.get("theta"),
        "vega": r.get("vega"), "rho": r.get("rho"),
        "option_symbol": r.get("option_symbol"),
        "expiry": r.get("expiry").isoformat() if r.get("expiry") else None,
    }


def _release_read_transaction(session: Session, *, symbol: str) -> bool:
    """End a READ-ONLY transaction before a slow external call. Returns whether it was released.

    `rollback()` and not `commit()`: there is nothing to commit on a read path, and rollback
    cannot flush anything the caller did not ask to be written. Both are refused outright when
    the session has pending state, which is what makes this safe to call from a shared session.
    """
    if session.new or session.dirty or session.deleted:
        log.info("uw_chain.transaction_held", symbol=symbol,
                 reason="caller has pending changes; not releasing their transaction")
        return False
    try:
        session.rollback()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("uw_chain.release_failed", symbol=symbol, error=str(exc))
        return False


def get_chain(
    session: Session,
    symbol: str,
    *,
    expiry: date | None = None,
    allow_fetch: bool = True,
    as_of: date | None = None,
) -> dict:
    """The chain for `symbol`, from the archive, fetching+persisting from UW on a miss.

    Returns {available, as_of, is_settled_session, source, calls, puts, expiries, reason}.
    `calls`/`puts` are in the legacy `_options_chain_rows()` shape. Never raises.

    `as_of` pins the source session (options review B1): a caller assembling several expiry
    slices passes the session it started from, so a capture landing mid-request cannot mix
    two sessions into one summary. A pinned read never fetches.

    There is deliberately no `spot` argument (audit R03, 2026-10-22): moneyness is measured
    against the chain session's own close, looked up here, so a caller cannot pair a settled
    chain with today's price.
    """
    sym = symbol.upper()
    if as_of is not None:
        return _chain_for_session(session, sym, as_of, expiry)
    # AUD-T409-UTCDATEBOUNDARY: ET, not a naive UTC truncation (see the same finding in
    # options_income_engine._today_et()) — otherwise the staleness check and the fetch
    # window both run one day ahead of the real US trading day for part of every evening.
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    as_of = latest_as_of(session, sym)

    # Fetch when we have nothing, or nothing recent enough to be worth showing.
    stale = as_of is None or (today - as_of).days > _MAX_LOOKBACK_DAYS
    excluded = provider_excluded(sym)
    if stale and allow_fetch and excluded:
        # Fix B: the provider cannot serve this symbol; a page view must not retry it every time.
        log.info("uw_chain.fetch_skipped_provider_excluded", symbol=sym, reason=excluded)
    elif stale and allow_fetch:
        # PROVIDER REQUESTS DO NOT BELONG INSIDE A DATABASE TRANSACTION (2026-10-12).
        # `latest_as_of()` above is a SELECT, and in SQLAlchemy a SELECT opens a transaction
        # that stays open until commit or rollback. The capture below then makes Unusual Whales
        # requests — so the connection sat `idle in transaction` for the whole round trip,
        # holding its locks and its MVCC snapshot while waiting on somebody else's network.
        # Observed in production at 30-35 seconds per symbol, which is also exactly the shape
        # the 2026-10-12 migration convoy's orphan had.
        #
        # Released conservatively: only when this session has nothing pending, because the
        # session belongs to the CALLER and discarding their uncommitted work to tidy up a read
        # would be a far worse bug than the one being fixed. A session with pending state keeps
        # its transaction and the reason is recorded rather than silently skipped.
        _release_read_transaction(session, symbol=sym)
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

    if as_of is None and excluded:
        return {"available": False, "reason": "provider_excluded", "reason_detail": excluded, "symbol": sym,
                "as_of": None, "is_settled_session": True, "source": "unusual_whales",
                "calls": [], "puts": [], "expiries": []}
    if as_of is None:
        return {"available": False, "reason": "no_chain_for_symbol", "symbol": sym,
                "as_of": None, "is_settled_session": True, "source": "unusual_whales",
                "calls": [], "puts": [], "expiries": []}
    return _chain_for_session(session, sym, as_of, expiry)


def field_coverage(rows: list[dict]) -> dict:
    """Per-field valid/missing/invalid/total counts for adapted rows (R04). A share or a sum
    over a field is a complete-scope statement only when its `missing + invalid` is zero."""
    out = {}
    for f in ("volume", "oi", "iv"):
        counts = {"known": 0, "missing": 0, "invalid": 0}
        for r in rows:
            st = (r.get("field_status") or {}).get(f, "ok" if r.get(f) is not None else "missing")
            counts["known" if st == "ok" else st] += 1
        out[f] = {**counts, "total": len(rows)}
    return out


def _chain_for_session(session: Session, sym: str, as_of: date, expiry: date | None) -> dict:
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
    rows = _rows_for(session, sym, as_of, expiry)
    if not rows:
        return {"available": False, "reason": "no_contracts_for_expiry", "symbol": sym,
                "as_of": as_of.isoformat(), "is_settled_session": True,
                "source": "unusual_whales", "calls": [], "puts": [], "expiries": []}

    # R03: moneyness against THIS session's close only — never a caller's current price — and
    # only when the chain itself confirms the close is on the strikes' basis.
    session_rows = _rows_for(session, sym, as_of)
    reference = session_reference_close(session, sym, as_of)
    moneyness_unknown_reason = None
    if reference is None:
        moneyness_unknown_reason = "no daily close stored for the chain's session"
    else:
        basis = reference_basis(reference["price"], chain_implied_spot(session_rows))
        reference = {**reference, "basis_check": basis}
        if basis["status"] != "compatible":
            moneyness_unknown_reason = basis["reason"]
            reference = None
    spot = reference["price"] if reference else None
    calls = [_to_chain_row(r, spot) for r in rows if (r.get("option_type") or "").lower().startswith("c")]
    puts  = [_to_chain_row(r, spot) for r in rows if (r.get("option_type") or "").lower().startswith("p")]
    all_exp = sorted({r["expiry"].isoformat() for r in session_rows if r.get("expiry")})

    coverage = _coverage_for(session, sym, as_of)
    legacy_status = "unverified_legacy_cap" if len(rows) == 500 else "unverified_legacy"
    return {
        "available": True, "symbol": sym,
        "coverage_status": coverage["status"] if coverage else legacy_status,
        "coverage_reason": coverage["completion_reason"] if coverage else (
            "Exactly 500 archived rows may be a pre-pagination partial capture."
            if len(rows) == 500 else
            "This archive predates stored pagination evidence; missing contracts cannot be inferred absent."
        ),
        "coverage": ({
            "pages_fetched": coverage["pages_fetched"],
            "page_size": coverage["page_size"],
            "rows_returned": coverage["rows_returned"],
            "retrieved_at": coverage["retrieved_at"].isoformat() if coverage.get("retrieved_at") else None,
        } if coverage else None),
        "returned_contracts": len(rows),
        "actionable": False,
        "actionability_reason": "Settled archive quotes are research evidence, not fresh executable quotes.",
        "as_of": as_of.isoformat(),
        # The whole point of carrying this: these are SETTLED closes, not live quotes.
        "is_settled_session": True,
        "session_age_days": (today - as_of).days,
        "source": "unusual_whales",
        "moneyness_reference": reference,
        "moneyness_unknown_reason": moneyness_unknown_reason,
        "field_coverage": {"calls": field_coverage(calls), "puts": field_coverage(puts)},
        "calls": sorted(calls, key=lambda x: x["strike"]),
        "puts": sorted(puts, key=lambda x: x["strike"]),
        "expiries": all_exp,
    }


def get_expiries(session: Session, symbol: str, *, allow_fetch: bool = True) -> list[str]:
    """Listed expiries from the most recent captured session — the UW replacement for
    `sorted(yf.Ticker(sym).options)`."""
    chain = get_chain(session, symbol, allow_fetch=allow_fetch)
    return chain["expiries"] if chain["available"] else []


def get_session_expiries(session: Session, symbol: str, *, allow_fetch: bool = True) -> tuple[date | None, list[str]]:
    """The source session and ITS expiries, from one read, so the two cannot disagree."""
    chain = get_chain(session, symbol, allow_fetch=allow_fetch)
    if not chain["available"]:
        return None, []
    return date.fromisoformat(chain["as_of"]), chain["expiries"]
