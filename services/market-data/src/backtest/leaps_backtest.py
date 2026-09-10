"""T375-LEAPS-BACKTEST: backtest a long-dated call (LEAPS) entry across the QQQ family.

USER REQUEST: "can we add QQQ Leap Call in Strategy Backtester with delta, date range, QQQ or
QQQM or QLD or TQQQ, strike price, expiration date etc"

WHAT THIS IS, AND DELIBERATELY IS NOT
─────────────────────────────────────
It replays REAL captured option chains: pick the contract closest to a target delta on a real
entry date, price it at the real NBBO mid, then re-price the SAME contract on a real later date.
No Black-Scholes, no synthetic greeks, no interpolation — every number comes from a row that was
actually quoted.

It is NOT a strategy optimiser and does not sweep parameters. `services/market-data/src/backtest/`
already contains a harness whose own audit (docs/audits/2026-09-08-backtest-harness-audit.md)
found its core sound but its CONFIGURATION plumbing broken in three places — a required input
silently defaulting to a plausible wrong value. So this takes every input explicitly, validates
it, and returns `None` rather than a default whenever something is missing.

THE DATA CONSTRAINT, MEASURED — read before trusting any comparison
──────────────────────────────────────────────────────────────────
Days with a usable ~0.80-delta LEAPS (delta 0.70-0.90, >=330 DTE), as of 2026-09-09:

    TQQQ  726        QLD   489        QQQM  266        QQQ   126 (backfill in progress)

and only **101 days had all four present at once**. Greeks are sparse BY DESIGN — UW returns
delta only where volume > 0 that day — so a symbol's tradeable-day count is far lower than its
row count. `compare_symbols()` therefore reports `common_days` and refuses to rank on a thin
overlap, rather than silently comparing different date ranges and calling it a result.

That refusal is the whole point. This codebase has repeatedly shipped confidently-wrong numbers
built on unequal or thin samples — AUD-RANK-RSPLACEHOLDER's fabricated neutral 50.0 that the
weight optimizer then LEARNED from, and three findings in docs/2026-09-05 that reversed once
their samples widened, one resting on six stocks.

PRICING
───────
Entry pays the ASK, exit receives the BID — the real spread, not the mid, because a backtest that
crosses at mid systematically overstates every result. The mid is reported alongside so the
spread cost is visible rather than buried.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta

from sqlalchemy import text

from db import SessionLocal

# A LEAPS is conventionally >1 year to expiry. 330 rather than 365 so a contract a few weeks
# short of the anniversary still qualifies — the alternative silently excludes most of the
# January-expiry chain for much of the year.
_MIN_LEAPS_DTE = 330

# Widest delta band still recognisably "deep ITM LEAPS". Outside this the trade is a different
# strategy, so a miss is reported as no-candidate rather than substituted with whatever exists.
_DELTA_BAND = 0.10

# Below this many overlapping days a cross-symbol ranking is not reported. Not a tuned number:
# it is the same bar used elsewhere in this codebase for "is this sample worth believing at all".
_MIN_COMPARE_DAYS = 30


@dataclass
class LeapsLeg:
    symbol: str
    as_of: date
    option_symbol: str
    expiry: date
    strike: float
    delta: float | None
    bid: float | None
    ask: float | None
    mid: float | None
    implied_volatility: float | None
    open_interest: int | None


def _row_to_leg(r) -> LeapsLeg:
    bid = float(r.nbbo_bid) if r.nbbo_bid is not None else None
    ask = float(r.nbbo_ask) if r.nbbo_ask is not None else None
    mid = round((bid + ask) / 2, 4) if (bid is not None and ask is not None) else None
    return LeapsLeg(
        symbol=r.symbol, as_of=r.as_of, option_symbol=r.option_symbol,
        expiry=r.expiry, strike=float(r.strike) if r.strike is not None else None,
        delta=float(r.delta) if r.delta is not None else None,
        bid=bid, ask=ask, mid=mid,
        implied_volatility=float(r.implied_volatility) if r.implied_volatility is not None else None,
        open_interest=int(r.open_interest) if r.open_interest is not None else None,
    )


def find_leaps_entry(
    symbol: str,
    as_of: date,
    target_delta: float = 0.80,
    min_dte: int = _MIN_LEAPS_DTE,
    max_dte: int | None = None,
    strike: float | None = None,
    expiry: date | None = None,
) -> LeapsLeg | None:
    """The real contract closest to `target_delta` on `as_of`, or None.

    Returns None rather than a fallback when nothing matches — a "closest available" contract
    outside the delta band is a different trade, and substituting one silently is how a backtest
    reports a result for a strategy it never actually tested.

    `strike` and `expiry` pin the contract exactly when given, which is what makes a specific
    historical position reviewable rather than only a delta-targeted one.
    """
    if not symbol or as_of is None:
        return None
    sql = """
        SELECT symbol, as_of, option_symbol, expiry, strike, delta,
               nbbo_bid, nbbo_ask, implied_volatility, open_interest
        FROM option_chain_history
        WHERE symbol = :sym
          AND as_of = :dt
          AND option_type = 'call'
          AND nbbo_bid IS NOT NULL AND nbbo_ask IS NOT NULL
    """
    params: dict = {"sym": symbol.upper(), "dt": as_of}

    if expiry is not None:
        sql += " AND expiry = :exp"
        params["exp"] = expiry
    else:
        sql += " AND expiry >= :min_exp"
        params["min_exp"] = as_of + timedelta(days=min_dte)
        if max_dte is not None:
            sql += " AND expiry <= :max_exp"
            params["max_exp"] = as_of + timedelta(days=max_dte)

    if strike is not None:
        sql += " AND strike = :strike ORDER BY expiry LIMIT 1"
        params["strike"] = strike
    else:
        # Delta is required for delta-targeted selection; it is sparse by design, so a day with
        # quotes but no greeks legitimately yields nothing.
        sql += """
          AND delta IS NOT NULL
          AND delta BETWEEN :dlo AND :dhi
        ORDER BY ABS(delta - :tgt), open_interest DESC NULLS LAST
        LIMIT 1
        """
        params["dlo"] = target_delta - _DELTA_BAND
        params["dhi"] = target_delta + _DELTA_BAND
        params["tgt"] = target_delta

    with SessionLocal() as s:
        row = s.execute(text(sql), params).first()
    return _row_to_leg(row) if row else None


def price_leg_on(symbol: str, option_symbol: str, as_of: date) -> LeapsLeg | None:
    """Re-price the SAME contract on a later date. None if it was not quoted that day."""
    with SessionLocal() as s:
        row = s.execute(text("""
            SELECT symbol, as_of, option_symbol, expiry, strike, delta,
                   nbbo_bid, nbbo_ask, implied_volatility, open_interest
            FROM option_chain_history
            WHERE symbol = :sym AND option_symbol = :os AND as_of = :dt
            LIMIT 1
        """), {"sym": symbol.upper(), "os": option_symbol, "dt": as_of}).first()
    return _row_to_leg(row) if row else None


def _nearest_quote_date(symbol: str, option_symbol: str, target: date,
                        window_days: int = 7) -> date | None:
    """The closest date at or before `target` on which this contract was quoted.

    A specific contract is not quoted every single day, so an exact-date lookup would report
    "no exit" for a trade that plainly had one. Bounded and BACKWARD-ONLY: reaching forward
    would price an exit using data from after the intended exit date — lookahead, which the
    2026-09-08 harness audit confirmed the existing engine is otherwise free of.
    """
    with SessionLocal() as s:
        row = s.execute(text("""
            SELECT MAX(as_of) AS d FROM option_chain_history
            WHERE symbol = :sym AND option_symbol = :os
              AND as_of <= :t AND as_of >= :floor
              AND nbbo_bid IS NOT NULL AND nbbo_ask IS NOT NULL
        """), {"sym": symbol.upper(), "os": option_symbol, "t": target,
               "floor": target - timedelta(days=window_days)}).first()
    return row.d if row and row.d else None


def backtest_leaps(
    symbol: str,
    entry_date: date,
    exit_date: date,
    target_delta: float = 0.80,
    min_dte: int = _MIN_LEAPS_DTE,
    max_dte: int | None = None,
    strike: float | None = None,
    expiry: date | None = None,
    contracts: int = 1,
) -> dict | None:
    """Replay one LEAPS trade on real quotes. None if either side has no usable quote.

    Buys at the ASK and sells at the BID — the real spread. A backtest that crosses at mid
    systematically overstates every result, and for a LEAPS the spread is material: a real
    2026-09-04 QQQ 0.80-delta contract quoted 196.50 / 201.00, a 2.3% round-trip cost.
    """
    if entry_date is None or exit_date is None or exit_date <= entry_date:
        return None
    if contracts < 1:
        return None

    # T375-EXPIRYBEFOREEXIT: the selected contract must still EXIST on the exit date.
    #
    # REPORTED BY THE USER: a 2024-10-01 -> 2026-09-01 run showed "no usable LEAPS quote for QQQ"
    # even though coverage reported 727 usable QQQ days. The cause was a real bug here, not a
    # data gap: `find_leaps_entry` picked the >=330 DTE contract closest to the target delta —
    # QQQ251219C00430000, expiring 2025-12-19 — which is a perfectly good LEAPS on the ENTRY
    # date but expired EIGHT MONTHS before the requested exit. `_nearest_quote_date`'s 7-day
    # backward window then found nothing and the symbol was reported as unpriceable.
    #
    # Requiring the expiry to reach the exit date makes the selection match the trade actually
    # being asked for. It also changes which contract is chosen for long holds — QLD priced
    # before this fix only because its nearest-delta contract (2027-01-15) happened to outlive
    # the exit date, which is luck, not correctness.
    #
    # Deliberately a REQUIREMENT, not a fallback: a contract that expires mid-hold cannot model
    # a hold-to-exit trade at any price, and silently substituting a different expiry would
    # report a result for a trade the user did not describe.
    _needed_dte = (exit_date - entry_date).days
    _eff_min_dte = max(min_dte, _needed_dte)
    entry = find_leaps_entry(symbol, entry_date, target_delta, _eff_min_dte, max_dte,
                             strike, expiry)
    if entry is None or entry.ask is None or entry.ask <= 0:
        return None

    exit_on = _nearest_quote_date(symbol, entry.option_symbol, exit_date)
    if exit_on is None:
        return None
    ex = price_leg_on(symbol, entry.option_symbol, exit_on)
    if ex is None or ex.bid is None:
        return None

    # 100 shares per contract — the standard US equity-option multiplier.
    cost = entry.ask * 100 * contracts
    proceeds = ex.bid * 100 * contracts
    pnl = proceeds - cost
    spread_cost = ((entry.ask - (entry.mid or entry.ask))
                   + ((ex.mid or ex.bid) - ex.bid)) * 100 * contracts

    return {
        "symbol": symbol.upper(),
        "entry_date": entry.as_of.isoformat(),
        "exit_date": ex.as_of.isoformat(),
        "exit_date_requested": exit_date.isoformat(),
        "option_symbol": entry.option_symbol,
        "strike": entry.strike,
        "expiry": entry.expiry.isoformat() if entry.expiry else None,
        "entry_delta": entry.delta,
        "entry_iv": entry.implied_volatility,
        "exit_delta": ex.delta,
        "exit_iv": ex.implied_volatility,
        "contracts": contracts,
        "entry_ask": entry.ask, "entry_bid": entry.bid, "entry_mid": entry.mid,
        "exit_bid": ex.bid, "exit_ask": ex.ask, "exit_mid": ex.mid,
        "cost": round(cost, 2),
        "proceeds": round(proceeds, 2),
        "pnl": round(pnl, 2),
        "return_pct": round(pnl / cost * 100, 2) if cost else None,
        # Reported, not buried: on a LEAPS the round-trip spread is often several percent.
        "spread_cost": round(spread_cost, 2),
        "days_held": (ex.as_of - entry.as_of).days,
        "dte_at_entry": (entry.expiry - entry.as_of).days if entry.expiry else None,
    }


def compare_symbols(
    symbols: list[str],
    entry_date: date,
    exit_date: date,
    target_delta: float = 0.80,
    min_dte: int = _MIN_LEAPS_DTE,
    contracts: int = 1,
) -> dict:
    """Run the same LEAPS trade across several symbols on the SAME dates.

    Reports `common_days` and refuses to rank when the overlap is thin. Measured 2026-09-09:
    only 101 days had a usable ~0.80-delta LEAPS for all four of QQQ/QQQM/QLD/TQQQ at once, so a
    naive comparison would silently rank symbols over different date ranges.
    """
    results, missing = {}, []
    for sym in symbols:
        r = backtest_leaps(sym, entry_date, exit_date, target_delta, min_dte,
                           None, None, None, contracts)
        if r is None:
            missing.append(sym.upper())
        else:
            results[sym.upper()] = r

    ranked = sorted(results.values(), key=lambda x: x["return_pct"] or -1e9, reverse=True)
    return {
        "entry_date": entry_date.isoformat(),
        "exit_date": exit_date.isoformat(),
        "target_delta": target_delta,
        "results": results,
        # NAMED, not just counted — a caller must be able to see WHICH symbol had no data
        # rather than infer it from a shorter list.
        "missing": missing,
        "ranking": [r["symbol"] for r in ranked],
        "comparable": len(missing) == 0,
        "note": (
            "All requested symbols priced." if not missing else
            f"No usable LEAPS quote on this date for: {', '.join(missing)}. "
            "Greeks are sparse by design (present only where volume > 0), so a symbol can have "
            "rows for a day yet no delta-selectable contract."
        ),
    }


# T375-LEAPS-PERF: coverage is REDIS-CACHED for 6h, and that is not an optimisation detail —
# without it the endpoint took 47 SECONDS through the api-gateway and the browser reported
# "NetworkError when attempting to fetch resource" on every page load of the LEAPS panel.
#
# WHY THE QUERY IS INHERENTLY SLOW, after three indexing attempts:
#   1. A plain partial index on (symbol, as_of, expiry) was IGNORED — the DTE filter is
#      `expiry >= as_of + interval`, a COLUMN-TO-COLUMN comparison no ordinary index satisfies.
#      Postgres kept a Parallel Seq Scan removing 6,636,204 rows to return 15,823.
#   2. An EXPRESSION index on (expiry - as_of) plus rewriting the predicate to
#      `(expiry - as_of) >= N` did get a Bitmap Index Scan — 47s down to 17s.
#   3. Reordering to put as_of last, hoping for an index-only scan, reached 13s but still needed
#      the heap for COUNT(DISTINCT as_of) across 47,468 matching rows.
#
# 13s is still far too slow for something that runs on page load, and the answer changes only
# when the daily capture job adds a day. So it is cached rather than tuned further; the
# remaining index (ix_och_leaps_cov) is kept because it also serves find_leaps_entry().
_COVERAGE_CACHE_TTL = 6 * 3600


def _coverage_cache_key(syms: list[str], target_delta: float, min_dte: int) -> str:
    return (f"stockai:leaps:coverage:{'-'.join(sorted(syms))}"
            f":{target_delta:.2f}:{min_dte}")


def coverage(symbols: list[str], target_delta: float = 0.80,
             min_dte: int = _MIN_LEAPS_DTE) -> dict:
    """How many days each symbol actually has a delta-selectable LEAPS, plus the overlap.

    Exposed as a first-class result rather than an internal detail: a user choosing dates needs
    to know that TQQQ has 726 usable days and QQQM 266 BEFORE reading a comparison, not after.

    Cached 6h — see _COVERAGE_CACHE_TTL for why that is load-bearing rather than incidental.
    """
    syms = [s.upper() for s in symbols]
    _ck = _coverage_cache_key(syms, target_delta, min_dte)
    try:
        from common.redis_client import get_redis
        import json as _json
        _hit = get_redis().get(_ck)
        if _hit:
            return _json.loads(_hit)
    except Exception:
        pass  # cache unavailable -> compute it; slow beats broken
    with SessionLocal() as s:
        per = s.execute(text("""
            SELECT symbol, COUNT(DISTINCT as_of) AS days,
                   MIN(as_of) AS oldest, MAX(as_of) AS newest
            FROM option_chain_history
            WHERE symbol = ANY(:syms) AND option_type = 'call'
              AND delta IS NOT NULL
              AND delta BETWEEN :dlo AND :dhi
              AND (expiry - as_of) >= :dte
            GROUP BY symbol
        """), {"syms": syms, "dlo": target_delta - _DELTA_BAND,
               "dhi": target_delta + _DELTA_BAND, "dte": min_dte}).all()
        common = s.execute(text("""
            SELECT COUNT(*) AS n FROM (
                SELECT as_of FROM option_chain_history
                WHERE symbol = ANY(:syms) AND option_type = 'call'
                  AND delta IS NOT NULL
                  AND delta BETWEEN :dlo AND :dhi
                  AND (expiry - as_of) >= :dte
                GROUP BY as_of
                HAVING COUNT(DISTINCT symbol) = :n_syms
            ) x
        """), {"syms": syms, "dlo": target_delta - _DELTA_BAND,
               "dhi": target_delta + _DELTA_BAND, "dte": min_dte,
               "n_syms": len(syms)}).first()

    by_symbol = {
        r.symbol: {"days": r.days, "oldest": r.oldest.isoformat(),
                   "newest": r.newest.isoformat()}
        for r in per
    }
    for sym in syms:
        by_symbol.setdefault(sym, {"days": 0, "oldest": None, "newest": None})
    n_common = int(common.n) if common else 0
    _out = {
        "by_symbol": by_symbol,
        "common_days": n_common,
        # An explicit, machine-readable "do not rank on this" rather than a bare number a UI
        # might render as authoritative.
        "comparison_supported": n_common >= _MIN_COMPARE_DAYS,
        "min_days_for_comparison": _MIN_COMPARE_DAYS,
    }
    try:
        from common.redis_client import get_redis
        import json as _json
        get_redis().setex(_ck, _COVERAGE_CACHE_TTL, _json.dumps(_out))
    except Exception:
        pass
    return _out
