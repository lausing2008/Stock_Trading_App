"""T411-IVHV — the stock detail page's Options tab: IV vs HV, and a 3-week performance table.

Two endpoints, deliberately separate, registered onto routes.py's own `/stocks` router:

  GET /stocks/{symbol}/iv-vs-hv              — implied vs realized volatility, any US symbol
  GET /stocks/{symbol}/options-performance   — the underlying and its ATM call/put, side by side

They are split because their AVAILABILITY is genuinely different, and collapsing them into one
response would make the whole panel fail whenever the narrower half is missing. IV vs HV works
for any symbol Unusual Whales covers (one request buys a year of daily IV — see
`unusual_whales.get_iv_history()`'s own measurement note). The performance table needs
per-contract price history, which this platform only has for the symbols its daily archive job
captures — **35 of them as of 2026-09-28**, because UW's option-chain window is a rolling one
and an uncaptured day expires permanently (see `OptionChainHistory`'s docstring and
`docs/features/options-and-institutional-data.md`, OPTHIST-1). For everything else the table
reports `available: false` with a reason naming the real cause, rather than silently rendering
the underlying column alone and letting it read as a complete answer.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import structlog
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

log = structlog.get_logger()

# How far past the end of the window the chosen contract's expiry must sit. A contract that
# expires INSIDE the table would stop being quoted partway down it, and the rows after that
# point would silently vanish — the reader would see a shorter table with no indication that the
# position had ended. Requiring a week of runway past the last session keeps one contract
# quoted across every row, so each row is the same position on a different day.
_EXPIRY_RUNWAY_DAYS = 7

# Sessions in the table. "Near 3 weeks" — 15 US trading sessions is three trading weeks.
_DEFAULT_SESSIONS = 15
_MAX_SESSIONS = 40

# Chart window, in calendar days of IV/HV history.
_DEFAULT_IVHV_DAYS = 90
_MAX_IVHV_DAYS = 365


def register(router, get_session, uw, volatility):
    """Attach both routes to `router`. Passed its dependencies rather than importing them so
    this module stays importable (and unit-testable) without routes.py's own heavy import graph.
    """

    @router.get("/{symbol}/iv-vs-hv")
    def get_iv_vs_hv(
        symbol: str,
        days: int = _DEFAULT_IVHV_DAYS,
        session: Session = Depends(get_session),
    ):
        """Implied volatility (what the options market charged) against realized volatility
        (what the stock actually did), on one axis, over the trailing `days` calendar days.

        The gap between the two lines is the point of the panel. IV persistently above HV means
        options have been expensive relative to the movement that followed — the premium-selling
        case. IV below HV means the opposite. Neither is a signal on its own and the response
        deliberately does not emit one; it reports the two measurements and their current
        spread, and leaves the read to the page.

        Both series are annualized decimal fractions (0.247 = 24.7%). IV comes from Unusual
        Whales, one cached request for a full year regardless of `days`. HV is computed here
        from this platform's own daily closes over a trailing 20-session window — see
        `services/volatility.py` for why log returns are used rather than the simple returns the
        portfolio-side helper uses, and why a partial window produces no point at all.
        """
        sym = symbol.upper()
        days = max(20, min(int(days or _DEFAULT_IVHV_DAYS), _MAX_IVHV_DAYS))

        try:
            iv_rows = uw.get_iv_history(sym)
        except Exception as exc:
            log.warning("iv_vs_hv.iv_fetch_failed", symbol=sym, error=str(exc))
            iv_rows = []

        # HV needs `days` of history PLUS a full window of returns before the first plotted
        # point, or the chart would start blank for a month.
        lookback_start = date.today() - timedelta(days=days + 2 * volatility.DEFAULT_HV_WINDOW + 10)
        try:
            bars = session.execute(text("""
                SELECT date(p.ts) AS d, p.close
                FROM prices p JOIN stocks s ON s.id = p.stock_id
                WHERE s.symbol = :sym AND p.timeframe = 'D1' AND date(p.ts) >= :start
                ORDER BY d ASC
            """), {"sym": sym, "start": lookback_start}).all()
        except Exception as exc:
            log.warning("iv_vs_hv.price_query_failed", symbol=sym, error=str(exc))
            bars = []

        hv_series = volatility.historical_volatility_series(
            [(_as_date(r[0]), r[1]) for r in bars
             if r[1] is not None and _as_date(r[0]) is not None]
        )
        hv_by_date = {d.isoformat(): v for d, v in hv_series}
        iv_by_date = {r.as_of_date: r for r in iv_rows}

        cutoff = (date.today() - timedelta(days=days)).isoformat()
        all_dates = sorted({d for d in set(hv_by_date) | set(iv_by_date) if d >= cutoff})

        points = []
        for d in all_dates:
            iv_row = iv_by_date.get(d)
            points.append({
                "date": d,
                # Round at the edge, not in the math — 4dp on a fraction is 2dp on a percent.
                "iv": round(iv_row.volatility, 4) if iv_row and iv_row.volatility is not None else None,
                "hv": round(hv_by_date[d], 4) if d in hv_by_date else None,
                "iv_rank_1y": round(iv_row.iv_rank_1y, 1) if iv_row and iv_row.iv_rank_1y is not None else None,
                "close": iv_row.close if iv_row else None,
            })

        if not points:
            return {
                "symbol": sym, "available": False,
                "reason": "no_iv_history" if not iv_rows else "no_price_history",
            }

        # The latest point where BOTH are present — a spread computed from an IV of today and an
        # HV of last Tuesday is not a spread.
        latest_both = next(
            (p for p in reversed(points) if p["iv"] is not None and p["hv"] is not None), None
        )
        spread = None
        if latest_both:
            spread = round(latest_both["iv"] - latest_both["hv"], 4)

        return {
            "symbol": sym,
            "available": True,
            "hv_window_sessions": volatility.DEFAULT_HV_WINDOW,
            "points": points,
            "latest": latest_both,
            "iv_minus_hv": spread,
            "iv_points": sum(1 for p in points if p["iv"] is not None),
            "hv_points": sum(1 for p in points if p["hv"] is not None),
        }

    @router.get("/{symbol}/options-performance")
    def get_options_performance(
        symbol: str,
        sessions: int = _DEFAULT_SESSIONS,
        session: Session = Depends(get_session),
    ):
        """Gain/loss over the last `sessions` trading days for the stock AND for the call and
        put that were at the money when the window opened.

        The two option columns are ONE contract each, fixed at the window's first session and
        held to its last — not "whatever was at the money that day". That distinction is the
        difference between a table a reader can act on and one that quietly relabels a different
        instrument every row. It is also why this shows what it shows: a stock can close green
        while the call that tracked it closes red, because the call paid theta every day and the
        stock's move did not cover it. That is the most common way an options trade that "called
        the direction right" still loses money, and a table that only showed the underlying
        would hide it completely.

        Marks are the NBBO midpoint, `(bid + ask) / 2`. A midpoint is not a fill — a real exit
        crosses the spread — so the percentages here are the contract's quoted value, and the
        response carries the spread alongside each mark so a reader can see how wide that gap
        was rather than having to assume it away.
        """
        sym = symbol.upper()
        sessions = max(2, min(int(sessions or _DEFAULT_SESSIONS), _MAX_SESSIONS))

        try:
            return _performance_payload(session, sym, sessions)
        except Exception as exc:
            log.warning("options_performance.error", symbol=sym, error=str(exc))
            return {"symbol": sym, "available": False, "reason": "query_error"}


def _as_date(v) -> date | None:
    """Coerce a driver's idea of a date into a `datetime.date`.

    psycopg2 returns a real `date` for both a DATE column and a `date(ts)` expression. Other
    drivers do not: SQLite's `date()` returns TEXT regardless of the column's declared type. The
    rows below are keyed by date and joined across two queries, so a mix of `date` and `str`
    keys does not raise — it silently matches nothing, and the table comes back empty or, worse,
    half-populated. Normalising once at the boundary makes the join independent of the driver.
    """
    if v is None:
        return None
    # datetime subclasses date, so this order matters — narrow to the calendar day first.
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _performance_payload(session, sym: str, sessions: int) -> dict:
    as_of_dates = [d for d in (_as_date(r[0]) for r in session.execute(text("""
        SELECT DISTINCT as_of FROM option_chain_history
        WHERE symbol = :sym ORDER BY as_of DESC LIMIT :n
    """), {"sym": sym, "n": sessions}).all()) if d is not None]

    if len(as_of_dates) < 2:
        # Distinguish "this platform does not archive this symbol" from "it does, but not yet
        # enough days to compare" — those call for completely different responses from a reader.
        return {
            "symbol": sym, "available": False,
            "reason": "no_option_history" if not as_of_dates else "insufficient_option_history",
            "sessions_available": len(as_of_dates),
        }

    as_of_dates = sorted(as_of_dates)
    d0, dn = as_of_dates[0], as_of_dates[-1]

    closes = {
        _as_date(r[0]): r[1] for r in session.execute(text("""
            SELECT date(p.ts) AS d, p.close
            FROM prices p JOIN stocks s ON s.id = p.stock_id
            WHERE s.symbol = :sym AND p.timeframe = 'D1'
              AND date(p.ts) BETWEEN :a AND :b
        """), {"sym": sym, "a": d0, "b": dn}).all()
    }
    entry_spot = closes.get(d0)
    if entry_spot is None or entry_spot <= 0:
        return {"symbol": sym, "available": False, "reason": "no_underlying_price"}

    legs = {}
    for side in ("call", "put"):
        leg = _select_leg(session, sym, d0, dn, side, entry_spot)
        if leg:
            legs[side] = leg

    rows = []
    prev_close = None
    prev_mark = {"call": None, "put": None}
    for d in as_of_dates:
        close = closes.get(d)
        row = {
            "date": d.isoformat(),
            "close": round(close, 2) if close is not None else None,
            "change_pct": (
                round((close / prev_close - 1) * 100, 2)
                if close is not None and prev_close not in (None, 0) else None
            ),
            "cum_pct": (
                round((close / entry_spot - 1) * 100, 2) if close is not None else None
            ),
        }
        for side, leg in legs.items():
            mark = leg["marks"].get(d)
            entry = leg["entry_mark"]
            row[side] = {
                "mark": round(mark["mid"], 2) if mark else None,
                "spread_pct": mark["spread_pct"] if mark else None,
                "change_pct": (
                    round((mark["mid"] / prev_mark[side] - 1) * 100, 2)
                    if mark and prev_mark[side] not in (None, 0) else None
                ),
                "cum_pct": (
                    round((mark["mid"] / entry - 1) * 100, 2)
                    if mark and entry not in (None, 0) else None
                ),
            }
            if mark:
                prev_mark[side] = mark["mid"]
        if close is not None:
            prev_close = close
        rows.append(row)

    return {
        "symbol": sym,
        "available": True,
        "sessions": len(rows),
        "start_date": d0.isoformat(),
        "end_date": dn.isoformat(),
        "entry_spot": round(entry_spot, 2),
        "contracts": {
            side: {
                "option_symbol": leg["option_symbol"],
                "strike": leg["strike"],
                "expiry": leg["expiry"].isoformat(),
                "entry_mark": round(leg["entry_mark"], 2),
                "marks_available": len(leg["marks"]),
            }
            for side, leg in legs.items()
        },
        "rows": rows,
        "summary": _summarize(rows, legs),
        # An empty `contracts` is a real outcome, not an error: the archive can hold sessions
        # for this symbol while holding no contract that was quoted at entry AND still had
        # runway past the window's end. Saying so explicitly stops the page from rendering an
        # underlying-only table that reads as though the option columns were simply flat.
        "contracts_reason": None if legs else "no_contract_with_runway",
        # Named so a caller can render the caveat rather than having to know it.
        "mark_basis": "NBBO midpoint — a quote, not a fill; a real exit crosses the spread.",
    }


def _select_leg(session, sym: str, d0: date, dn: date, side: str, spot: float) -> dict | None:
    """The contract that was closest to the money on `d0`, among those still quoted well past
    the end of the window.

    Ordering is `expiry ASC, |strike - spot| ASC` — nearest surviving expiry first, then nearest
    strike. Requiring a two-sided quote (`bid > 0 AND ask > 0`) at entry excludes contracts that
    were listed but not really tradeable, whose midpoint would be a number without a market
    behind it.
    """
    row = session.execute(text("""
        SELECT option_symbol, strike, expiry, nbbo_bid, nbbo_ask
        FROM option_chain_history
        WHERE symbol = :sym AND as_of = :d0 AND option_type = :side
          AND expiry >= :min_expiry AND nbbo_bid > 0 AND nbbo_ask > 0
        ORDER BY expiry ASC, abs(strike - :spot) ASC
        LIMIT 1
    """), {
        "sym": sym, "d0": d0, "side": side, "spot": spot,
        "min_expiry": dn + timedelta(days=_EXPIRY_RUNWAY_DAYS),
    }).first()
    if not row:
        return None

    option_symbol, strike, expiry, bid, ask = row
    marks = {}
    for m_date, m_bid, m_ask in session.execute(text("""
        SELECT as_of, nbbo_bid, nbbo_ask FROM option_chain_history
        WHERE symbol = :sym AND option_symbol = :osym AND as_of BETWEEN :a AND :b
        ORDER BY as_of ASC
    """), {"sym": sym, "osym": option_symbol, "a": d0, "b": dn}).all():
        if m_bid is None or m_ask is None or m_ask <= 0:
            continue
        mid = (m_bid + m_ask) / 2.0
        if mid <= 0:
            continue
        marks[_as_date(m_date)] = {
            "mid": mid,
            "spread_pct": round((m_ask - m_bid) / mid * 100, 1),
        }

    entry = marks.get(d0)
    if not entry:
        return None
    return {
        "option_symbol": option_symbol,
        "strike": strike,
        "expiry": expiry,
        "entry_mark": entry["mid"],
        "marks": marks,
    }


def _summarize(rows: list[dict], legs: dict) -> dict:
    """Up/down session counts and the window's total move, per column.

    Counts use each row's own day-over-day change and deliberately EXCLUDE the first row, whose
    change is undefined (there is no prior session inside the window). Counting it as flat would
    quietly inflate the denominator by one on every table.
    """
    def _tally(values: list[float | None]) -> dict:
        real = [v for v in values if v is not None]
        return {
            "up": sum(1 for v in real if v > 0),
            "down": sum(1 for v in real if v < 0),
            "flat": sum(1 for v in real if v == 0),
            "measured_sessions": len(real),
        }

    out = {"underlying": _tally([r.get("change_pct") for r in rows])}
    last = rows[-1] if rows else {}
    out["underlying"]["total_pct"] = last.get("cum_pct")
    for side in legs:
        out[side] = _tally([r.get(side, {}).get("change_pct") for r in rows])
        out[side]["total_pct"] = (last.get(side) or {}).get("cum_pct")
    return out
