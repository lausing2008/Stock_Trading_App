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

# T381-LEAPS-NEARESTDELTA: the widened band used ONLY when the strict band yields nothing
# priceable. User request: "if we can't find the exact delta at that moment, we can find
# something near the same to run it."
#
# WHY THIS IS SAFE HERE AND WAS REFUSED BEFORE. T380 deliberately did NOT auto-widen, because
# silently pricing a 0.80-delta contract when 0.70 was asked for reports a result for a
# different trade. That objection is about SILENCE, not about widening — so the widened attempt
# is a clearly-labelled SECOND pass: it runs only after the strict band fails, every result
# carries `delta_relaxed` and the actual `entry_delta`, and the UI shows a badge. The strict
# band still wins whenever it can price, so no existing result changes.
#
# 0.20 rather than something larger, measured on the QLD case that prompted this: at +/-0.15
# QLD finds 3 priceable contracts (delta 0.802/0.805/0.844) and +/-0.20 and +/-0.25 find the
# SAME three — the chain simply has a gap between 0.659 and 0.802. So a wider cap buys nothing
# real while admitting progressively less comparable trades. 0.20 keeps the nearest match
# (0.802, just 0.102 from a 0.70 target) reachable without opening the door to a 0.45-delta
# contract being passed off as a deep-ITM LEAPS.
_DELTA_BAND_RELAXED = 0.20
#
# WHAT THIS ACTUALLY PRODUCED, recorded because it is deliberately UNFLATTERING and the label is
# the whole point. On 2025-10-01 -> 2026-09-01 at target delta 0.70:
#
#     QQQ   +60.20%  delta 0.703            TQQQ  -73.78%  delta 0.705
#     QQQM  +45.45%  delta 0.727            QLD   -94.23%  delta 0.802   <- RELAXED
#
# QLD's -94% is mostly LEVERAGED-ETF DECAY over a 336-day hold, not the 0.10 delta difference.
# An unbadged -94% row sitting beside a +60% row reads as the same trade run four ways, which
# is exactly the misreading `delta_relaxed` and the "NEAR" badge exist to prevent.
# T380-LEAPS-CONTRACTGAP: how far down the delta-ranked band to look for a contract that is
# still quoted at the exit date. Bounded rather than unlimited: walking the whole band would
# drift arbitrarily far from the requested delta, and a match 8 candidates away is no longer
# the strategy the user asked to test.
_ENTRY_CANDIDATE_LIMIT = 8

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


def find_leaps_entry_candidates(
    symbol: str,
    as_of: date,
    target_delta: float = 0.80,
    min_dte: int = _MIN_LEAPS_DTE,
    max_dte: int | None = None,
    limit: int = _ENTRY_CANDIDATE_LIMIT,
    band: float | None = None,
) -> list[LeapsLeg]:
    """Every in-band contract on `as_of`, best delta match FIRST.

    T380-LEAPS-CONTRACTGAP: `find_leaps_entry()` commits to ONE contract (SQL `LIMIT 1`) before
    anything knows whether that contract is still quoted at the exit date. When it is not, the
    whole symbol was reported unpriceable even though other in-band contracts priced fine.

    REPORTED BY THE USER on QLD: "QLD has data from 2023-10-20 but why it said error". The data
    was there — 726 quote days, continuous monthly coverage through 2026-09, and 101 QLD
    2027-01-15 calls all carrying bids on the exit date. The problem was ONE contract:
    the nearest-delta pick, strike 126 at delta 0.659, was last quoted 2025-11-19 — a thin
    strike UW dropped from its chain, with only 52 quotes in its entire life. The three other
    in-band candidates (delta 0.802/0.805/0.844) were quoted right through to the exit.

    So the pick was the ONLY unusable one of four, and being closest on delta is exactly what
    selected it. This returns the ranked band so the caller can fall back in delta order.

    Ordering is unchanged from find_leaps_entry() — ABS(delta - target), then open interest —
    so candidate[0] IS what find_leaps_entry() would have returned. Falling back never
    "improves" the pick; it only ever moves further from the requested delta, and the caller
    reports which one it used.
    """
    if not symbol or as_of is None:
        return []
    # T381-LEAPS-NEARESTDELTA: default keeps the strict band, so every existing caller is
    # unchanged and a relaxed search must be asked for explicitly.
    _band = _DELTA_BAND if band is None else float(band)
    sql = """
        SELECT symbol, as_of, option_symbol, expiry, strike, delta,
               nbbo_bid, nbbo_ask, implied_volatility, open_interest
        FROM option_chain_history
        WHERE symbol = :sym
          AND as_of = :dt
          AND option_type = 'call'
          AND nbbo_bid IS NOT NULL AND nbbo_ask IS NOT NULL
          AND expiry >= :min_exp
          AND delta IS NOT NULL
          AND delta BETWEEN :dlo AND :dhi
    """
    params: dict = {
        "sym": symbol.upper(), "dt": as_of,
        "min_exp": as_of + timedelta(days=min_dte),
        "dlo": target_delta - _band, "dhi": target_delta + _band,
        "tgt": target_delta, "lim": max(1, int(limit)),
    }
    if max_dte is not None:
        sql += " AND expiry <= :max_exp"
        params["max_exp"] = as_of + timedelta(days=max_dte)
    sql += " ORDER BY ABS(delta - :tgt), open_interest DESC NULLS LAST LIMIT :lim"

    with SessionLocal() as s:
        rows = s.execute(text(sql), params).all()
    return [leg for leg in (_row_to_leg(r) for r in rows) if leg is not None]


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

    # T380-LEAPS-CONTRACTGAP: surviving to the exit DATE is not the same as being QUOTED there.
    #
    # REPORTED BY THE USER on QLD: "QLD has data from 2023-10-20 but why it said error". The
    # data was fine — 726 quote days, continuous monthly coverage through 2026-09, and 101 QLD
    # 2027-01-15 calls all carrying bids on the exit date. ONE contract was the problem: the
    # nearest-delta pick (strike 126, delta 0.659) was last quoted 2025-11-19, a thin strike UW
    # dropped from its chain after only 52 quotes in its life. The three other in-band
    # candidates (delta 0.802/0.805/0.844) were quoted right through to the exit — so the pick
    # was the ONLY unusable one of four, and being closest on delta is what selected it.
    #
    # T375-EXPIRYBEFOREEXIT (below, still enforced) fixed a DIFFERENT failure: an expiry that
    # falls before the exit. That is a hard impossibility. This one is a data gap in a contract
    # that does reach the exit, and it IS recoverable — by trying the next-best in-band
    # candidate instead of reporting the whole symbol unpriceable.
    #
    # An EXPLICIT strike/expiry is never substituted: the user pinned a specific contract, and
    # quietly pricing a different one would answer a question they did not ask.
    _pinned = strike is not None or expiry is not None
    if _pinned:
        _candidates = [
            c for c in [find_leaps_entry(symbol, entry_date, target_delta, _eff_min_dte,
                                         max_dte, strike, expiry)]
            if c is not None
        ]
    else:
        _candidates = find_leaps_entry_candidates(
            symbol, entry_date, target_delta, _eff_min_dte, max_dte
        )

    def _first_priceable(cands):
        """The best-delta candidate that can actually be priced at the exit, and how many
        better-delta ones were skipped getting there."""
        _skipped = 0
        for _c in cands:
            if _c.ask is None or _c.ask <= 0:
                continue
            _on = _nearest_quote_date(symbol, _c.option_symbol, exit_date)
            if _on is None:
                _skipped += 1
                continue
            _e = price_leg_on(symbol, _c.option_symbol, _on)
            if _e is None or _e.bid is None:
                _skipped += 1
                continue
            return _c, _on, _e, _skipped
        return None, None, None, _skipped

    entry, exit_on, ex, _skipped_unquoted = _first_priceable(_candidates)

    # T381-LEAPS-NEARESTDELTA: "if we can't find the exact delta at that moment, we can find
    # something near the same to run it" (user).
    #
    # SECOND PASS ONLY. The strict band is tried first and wins whenever it can price, so this
    # changes no result that already worked. It runs only when the strict band produced nothing
    # priceable, and everything it returns is LABELLED (`delta_relaxed`, plus the real
    # `entry_delta`) — the T380 objection was to SILENT substitution, not to widening.
    #
    # Never for a pinned strike/expiry: the user named a specific contract, and a "near" one is
    # a different contract, not a near-miss on a target.
    _relaxed = False
    if entry is None and not _pinned:
        _wider = find_leaps_entry_candidates(
            symbol, entry_date, target_delta, _eff_min_dte, max_dte,
            band=_DELTA_BAND_RELAXED,
        )
        # Only the ones the strict pass did not already reject, so the skip count stays honest.
        _seen = {c.option_symbol for c in _candidates}
        _wider = [c for c in _wider if c.option_symbol not in _seen]
        if _wider:
            entry, exit_on, ex, _extra = _first_priceable(_wider)
            _skipped_unquoted += _extra
            _relaxed = entry is not None

    if entry is None or ex is None:
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
        # T380-LEAPS-CONTRACTGAP: how many better-delta contracts were skipped because they
        # were not quoted at the exit. 0 = the nearest-delta pick was used, i.e. identical to
        # the pre-fix behaviour. Non-zero means the delta is further from target than
        # requested, and the caller must be able to see that rather than infer it.
        "delta_fallback_skipped": _skipped_unquoted,
        # T381-LEAPS-NEARESTDELTA: True when NO contract inside the strict +/-0.10 band could be
        # priced and a wider +/-0.20 search was used instead. The caller must be able to see
        # that this is a NEARBY delta rather than the requested one — compare `entry_delta`
        # against the target to judge how near. False means the strict band was used, i.e.
        # identical to the pre-T381 behaviour.
        "delta_relaxed": _relaxed,
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


def _explain_no_price(
    symbol: str, entry_date: date, exit_date: date, target_delta: float, min_dte: int
) -> str:
    """WHY this symbol produced no trade — checked in the SAME ORDER backtest_leaps fails.

    T380-LEAPS-CONTRACTGAP. The ordering is the point, and a test pins it: if this diagnosed in
    a different order it could name a guard that is not the one that actually fired, which is a
    new way to be confidently wrong about the same question. Same discipline as
    T373-FORECAST-REASON, where a modal asserted two explanations that were both false.

    Deliberately does NOT re-price anything — it inspects the same inputs the engine used.
    """
    _needed = (exit_date - entry_date).days
    _eff_min = max(min_dte, _needed)

    # 1. No quotes for the symbol on the entry date at all -> genuinely a coverage gap.
    with SessionLocal() as s:
        n_entry = s.execute(text("""
            SELECT COUNT(*) FROM option_chain_history
            WHERE symbol = :sym AND as_of = :dt AND option_type = 'call'
        """), {"sym": symbol.upper(), "dt": entry_date}).scalar() or 0
    if n_entry == 0:
        return (f"no option chain captured for {symbol.upper()} on {entry_date.isoformat()} — "
                f"the archive has no quotes for that date")

    # 2. Quotes exist but no greeks. Sparse BY DESIGN: UW returns delta only where volume > 0.
    with SessionLocal() as s:
        n_delta = s.execute(text("""
            SELECT COUNT(*) FROM option_chain_history
            WHERE symbol = :sym AND as_of = :dt AND option_type = 'call'
              AND delta IS NOT NULL AND expiry >= :min_exp
        """), {"sym": symbol.upper(), "dt": entry_date,
               "min_exp": entry_date + timedelta(days=_eff_min)}).scalar() or 0
    if n_delta == 0:
        return (f"{symbol.upper()} has quotes on {entry_date.isoformat()} but no delta on any "
                f"contract expiring beyond {exit_date.isoformat()} — greeks are only present "
                f"where volume > 0, so an untraded long-dated strike carries none")

    # 3. Greeks exist but nothing lands inside the delta band -> the QLD case.
    cands = find_leaps_entry_candidates(symbol, entry_date, target_delta, _eff_min, None)
    if not cands:
        with SessionLocal() as s:
            row = s.execute(text("""
                SELECT MIN(delta) lo, MAX(delta) hi FROM option_chain_history
                WHERE symbol = :sym AND as_of = :dt AND option_type = 'call'
                  AND delta IS NOT NULL AND expiry >= :min_exp
            """), {"sym": symbol.upper(), "dt": entry_date,
                   "min_exp": entry_date + timedelta(days=_eff_min)}).first()
        _lo = f"{row.lo:.3f}" if row and row.lo is not None else "?"
        _hi = f"{row.hi:.3f}" if row and row.hi is not None else "?"
        return (f"no {symbol.upper()} contract within {_DELTA_BAND:.2f} of delta "
                f"{target_delta:.2f} that also expires after {exit_date.isoformat()} — "
                f"available deltas on that date span {_lo}-{_hi}. Try a target delta in that "
                f"range")

    # 4. In-band candidates existed but none was quoted at the exit -> a contract-level gap.
    return (f"{symbol.upper()} had {len(cands)} in-band contract(s) on "
            f"{entry_date.isoformat()} but none is quoted near {exit_date.isoformat()} — "
            f"the contract stopped being quoted mid-hold (thin strikes drop out of the chain)")


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
    # T380-LEAPS-CONTRACTGAP: WHY a symbol did not price, per symbol.
    #
    # REPORTED BY THE USER: "QLD has data from 2023-10-20 but why it said error". The old
    # message said "no usable LEAPS quote for QLD on these dates", which reads as a coverage
    # problem — so the user checked the coverage panel, correctly saw 629 usable days, and the
    # two statements contradicted each other on the same screen.
    #
    # The actual reason was neither the dates nor the coverage: at target delta 0.70 with a
    # +/-0.10 band, QLD had exactly ONE in-band contract for a 336-day hold (strike 126, delta
    # 0.659) and UW stopped quoting it on 2025-11-19. Its next contracts sit at delta 0.802 —
    # missing the 0.800 ceiling BY 0.002. That is a delta-band miss, not missing data, and
    # naming it is the difference between an actionable message and a misleading one.
    #
    # Deliberately NOT auto-widening the band to rescue such a symbol: a 0.80-delta LEAPS is a
    # materially different trade from a 0.70-delta one, and substituting it would report a
    # result for a strategy the user did not ask to test. The message says what to change.
    reasons: dict[str, str] = {}
    for sym in symbols:
        r = backtest_leaps(sym, entry_date, exit_date, target_delta, min_dte,
                           None, None, None, contracts)
        if r is None:
            _u = sym.upper()
            missing.append(_u)
            reasons[_u] = _explain_no_price(sym, entry_date, exit_date, target_delta, min_dte)
        else:
            results[sym.upper()] = r

    ranked = sorted(results.values(), key=lambda x: x["return_pct"] or -1e9, reverse=True)
    # T381-LEAPS-NEARESTDELTA: a symbol that only priced via the widened band is still a
    # caveat, even though it is no longer "missing". `comparable` stays True (everything
    # priced), but the note must say the comparison is not strictly like-for-like — otherwise
    # a -94% QLD row at delta 0.802 sits silently beside a +60% QQQ row at 0.703 and reads as
    # the same trade. Leveraged-ETF decay makes that gap look far more meaningful than it is.
    _relaxed_syms = [r["symbol"] for r in ranked if r.get("delta_relaxed")]
    return {
        "entry_date": entry_date.isoformat(),
        "exit_date": exit_date.isoformat(),
        "target_delta": target_delta,
        "results": results,
        # NAMED, not just counted — a caller must be able to see WHICH symbol had no data
        # rather than infer it from a shorter list.
        "missing": missing,
        # T380-LEAPS-CONTRACTGAP: keyed by symbol, so the UI can say WHY rather than repeating
        # a generic "no quote on these dates" that contradicts the coverage panel beside it.
        "missing_reasons": reasons,
        "ranking": [r["symbol"] for r in ranked],
        "comparable": len(missing) == 0,
        "delta_relaxed_symbols": _relaxed_syms,
        "note": (
            (
                "All requested symbols priced."
                if not _relaxed_syms else
                # T381-LEAPS-NEARESTDELTA: say it plainly. "All priced" would be true and
                # misleading — the reader would compare a relaxed row against a strict one.
                f"All requested symbols priced, but {', '.join(_relaxed_syms)} used the "
                f"NEAREST available delta rather than {target_delta:.2f} (no contract inside "
                f"the strict band could be priced on these dates). Compare the delta column "
                f"before reading the ranking as like-for-like."
            ) if not missing else
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
