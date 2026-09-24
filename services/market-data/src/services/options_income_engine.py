"""T398-OPTIONS-INCOME-ENGINE: systematic covered-call / cash-secured-put income engine.

Two halves, sharing one source of truth for "what's a good income trade right now":

Phase 1 (screening) — rank_income_candidates() scores the current best covered-call/CSP
contract per symbol across a fixed universe, reading ONLY the already-archived EOD chain
(option_chain_history, the OPTHIST-1 archive) — never a live per-symbol yfinance/UW fetch, the
same rate-limit-amplification guard every other systematic scan in this app already follows
(docs/incidents/yfinance-rate-limit-amplification.md). Exposed via GET
/options-income/candidates for a human to act on manually.

Phase 2 (autonomous engine) — run_options_income_step() is the scheduler entry point. It
settles any OptionsIncomePosition whose expiry has arrived (assignment vs OTM, per each
strategy's own real economics), then opens new positions on active OptionsIncomePortfolio rows
from the SAME ranked candidates Phase 1 produces — never a second, independently-drifting copy
of "what's a good trade."

Both strategies are modeled as closing AT EXPIRY, never rolled or held past it:
  - COVERED_CALL is a synthetic buy-write: shares assumed bought at entry (collateral_reserved
    = underlying_entry_price * 100 * contracts), sold either via assignment at strike (if the
    close price is above it) or liquidated at the market close on expiry day otherwise.
  - CASH_SECURED_PUT reserves strike * 100 * contracts in cash at entry; if assigned (close
    price below strike), the assigned shares are immediately marked to market and liquidated
    the same way, rather than held.
This keeps the engine bounded — no open-ended stock inventory drifting across future expiry
cycles — while still producing each strategy's real economics (premium collected, plus
whatever the underlying did between entry and close).
"""
from __future__ import annotations

import structlog
from datetime import date, datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from db.models import (
    OptionsIncomePortfolio, OptionsIncomePosition, OptionsIncomeEquityCurve,
    Stock, Price, TimeFrame,
)
from db.session import SessionLocal
from common.market_calendar import NYSE_HOLIDAYS

log = structlog.get_logger()

# The same liquid, daily-archived-chain universe OPTHIST already covers — see scheduler.py's
# _OPTHIST_SYMBOLS for the full selection rationale (mega-cap/index, deep option chains, no
# live-fetch dependency). Income-selling doesn't need that list's "strong uptrend" LEAPS
# criteria specifically, but does need the one thing this subset already guarantees: a full
# daily-archived chain with real bid/ask/delta/OI for every contract.
_INCOME_UNIVERSE = [
    "SPY", "QQQ", "META", "TSLA", "AMD", "NVDA", "MSFT", "AAPL", "AMZN", "PLTR",
    "QQQM", "QLD", "TQQQ",
]

# Covered calls can be shorter-dated than protective puts — income, not insurance (same framing
# _OPTIONS_GAME_PLAN_MIN/MAX_CALL_DTE uses in routes.py's single-symbol display endpoint).
_INCOME_MIN_DTE = 14
_INCOME_MAX_DTE = 45
# Standard income-selling delta band: far enough OTM that assignment is the minority outcome,
# close enough that the premium is worth collecting.
_INCOME_MIN_ABS_DELTA = 0.15
_INCOME_MAX_ABS_DELTA = 0.35
_INCOME_MIN_OPEN_INTEREST = 50
# AUD-T398-THINCUSHION: a contract can pass the delta band and still sit a rounding error from
# the money once the underlying has moved — measured live, the engine was surfacing a QQQ put
# 0.08% out of the money and an SPY put 0.18% out, both effectively at-the-money trades being
# presented as ~0.33-delta ones. Require a real buffer between the strike and today's price.
_INCOME_MIN_CUSHION_PCT = 2.0

# Quality-score normalisation points — the value at which each component earns full marks.
# These are deliberate, documented judgement calls for RANKING candidates against each other,
# not a validated predictive edge: nothing in this engine has enough resolved outcomes yet to
# claim one (see the guide's own "no track record" caveat). Ranking by raw yield alone is
# actively misleading, because the richest premium in the universe is rich precisely because
# it carries the most risk — that is adverse selection, not opportunity.
_Q_FULL_YIELD_PCT = 40.0    # 40% annualised earns full marks; more adds nothing
_Q_FULL_CUSHION_PCT = 10.0  # 10% out-of-the-money earns full marks
_Q_FULL_OPEN_INTEREST = 2000
# Cushion is weighted equal to yield on purpose: for a premium SELLER, distance-to-strike is
# the thing that actually prevents the bad outcome, so it deserves the same weight as the
# reward it is being traded against.
# T399-WEIGHTDERIV (2026-09-17): these were 0.40/0.40/0.20 — a documented judgement call. They
# are now DERIVED from 50,787 settled backtest contracts via a chronological train/test split
# (see backtest/options_income_weights.py). Chosen on the TRAIN slice and validated on a
# held-out one: mean return on collateral 2.33% -> 3.16% out of sample (+0.83pp against a
# 0.10pp required margin), win rate 65.9% -> 69.0%.
#
# CUSHION DOMINATES. Every one of the top 8 train configurations put cushion at 0.65-0.95, and
# the original equal weighting was the problem: yield and cushion push assignment risk in
# OPPOSITE directions (measured — assignment rises 10.4% -> 28.7% across yield bands, and falls
# 66.7% -> 19.4% across cushion bands), so weighting them equally made them cancel.
#
# LIQUIDITY GOES TO ZERO, for a non-obvious reason worth stating so nobody "fixes" it back:
# open interest is NEGATIVELY correlated with cushion in this pool (OI>=2000 averages 8.13%
# cushion; OI<2000 averages 11.18%), so weighting liquidity pulls selection toward THINNER
# cushion and fights the strongest signal. Adding even 0.05 of it cost most of the gain
# (3.16% -> 2.40% out of sample). This does NOT mean illiquid contracts are fine — that risk is
# already handled by the hard _INCOME_MIN_OPEN_INTEREST floor, which is a filter, not a weight.
#
# Deliberately kept the TRAIN winner rather than the slightly better test winner (0/100/0,
# 3.20%): picking the configuration that won the held-out slice would turn that slice into a
# training slice, which is the whole thing the split exists to prevent.
_Q_WEIGHT_YIELD = 0.25
_Q_WEIGHT_CUSHION = 0.75
_Q_WEIGHT_LIQUIDITY = 0.0

# AUD-T398-LEVERAGEPENALTY: leveraged ETFs carry structurally richer option premium because the
# underlying itself moves 2-3x as hard — so they rank top on any yield-based measure BY
# CONSTRUCTION, without that premium representing a better trade. Measured live: TQQQ took the
# #1 and #5 slots purely on this effect. The cushion term does not correct for it either, since
# a 10% cushion on a 3x product is roughly a 3.3% move in the underlying index.
#
# Penalty is the reciprocal of the leverage multiple: a 3x product needs ~3x the headline yield
# to rank alongside an unleveraged one, which is the honest comparison. This scales the FINAL
# score rather than any single component, because leverage inflates yield and deflates the real
# meaning of cushion simultaneously.
_LEVERAGED_SYMBOLS: dict[str, float] = {
    "TQQQ": 3.0,   # 3x Nasdaq-100
    "QLD": 2.0,    # 2x Nasdaq-100
}

# AUD-A19-EQUITYMIXEDBASIS: the definition of "equity" every curve row written from here on uses.
# Stamped onto each row so a later reader can tell whether two rows are actually comparable.
# Change this string ONLY together with a change to the equity formula in
# _snapshot_income_equity_curve, and ship a migration that labels the pre-existing rows — never
# silently redefine equity in place, which is exactly how the 2026-09-16/09-17 mixed pair arose.
_EQUITY_BASIS = "cash_collateral_less_liability"
# The OPTHIST daily capture self-heals short gaps (a 5-day backfill window on every run), so a
# healthy pipeline never approaches this. A chain older than this means the capture job itself
# has been failing for a while — treat it as a data-pipeline outage, not a green light to trade.
_INCOME_MAX_CHAIN_STALENESS_DAYS = 5

_DEFAULT_INCOME_CONFIG = {
    "strategies": ["COVERED_CALL", "CASH_SECURED_PUT"],
    "symbols": _INCOME_UNIVERSE,
    "max_positions": 10,
    "max_positions_per_symbol": 1,
    "contracts_per_position": 1,
    "min_annualized_yield_pct": 8.0,
    "max_entries_per_day": 3,
    # A single high-priced-stock CSP (e.g. a $500 stock needs $50,000 collateral for just ONE
    # contract) can otherwise consume an entire portfolio's cash in one trade, leaving nothing
    # for diversification regardless of max_positions — measured live: on a $50k portfolio, one
    # AMD CSP used the full $50k, capping the whole book at 1 position. Sized against
    # initial_capital (a stable denominator), matching how max_position_pct already caps
    # concentration on every stock PaperPortfolio.
    "max_collateral_pct_per_position": 0.25,
}


def _latest_chain_as_of(session: Session, symbol: str) -> date | None:
    row = session.execute(
        text("SELECT MAX(as_of) AS d FROM option_chain_history WHERE symbol = :sym"),
        {"sym": symbol.upper()},
    ).first()
    return row.d if row and row.d else None


def _chain_as_of_on_or_before(session: Session, symbol: str, as_of: date) -> date | None:
    """The newest archived chain date at or BEFORE `as_of` — the lookahead guard for the
    backtest. `<=` is the entire point: selecting a contract on date D must never see a chain
    captured after D, which is exactly the class of bug
    docs/incidents/backtest-wall-clock-and-lookahead-bugs.md exists to record.
    """
    row = session.execute(
        text("SELECT MAX(as_of) AS d FROM option_chain_history WHERE symbol = :sym AND as_of <= :as_of"),
        {"sym": symbol.upper(), "as_of": as_of},
    ).first()
    return row.d if row and row.d else None


def leverage_factor(symbol: str) -> float:
    """1.0 for an ordinary underlying; 1/leverage for a leveraged ETF (TQQQ -> 0.33).

    Pure and separately testable so the penalty can be reasoned about on its own.
    """
    return 1.0 / _LEVERAGED_SYMBOLS.get(symbol.upper(), 1.0)


def quality_score(
    *, annualized_yield_pct: float, otm_cushion_pct: float, open_interest: int | None,
    symbol: str | None = None,
) -> float:
    """A 0-100 risk-adjusted ranking score. Pure — no DB access, so it is directly testable.

    Blends the reward (yield) against the two things that most determine whether that reward is
    actually keepable: how far the strike sits from today's price (cushion), and whether the
    contract can be traded at the quoted price at all (open interest). Each component saturates
    at its own _Q_FULL_* point so a single extreme value cannot dominate — specifically so a
    60%-annualised contract on a name about to gap cannot outrank a 25% one with real buffer.

    This is a transparent heuristic for ORDERING candidates, not a claim of predictive edge.
    """
    y = min(max(annualized_yield_pct, 0.0) / _Q_FULL_YIELD_PCT, 1.0)
    c = min(max(otm_cushion_pct, 0.0) / _Q_FULL_CUSHION_PCT, 1.0)
    liq = min(max(open_interest or 0, 0) / _Q_FULL_OPEN_INTEREST, 1.0)
    raw = 100.0 * (_Q_WEIGHT_YIELD * y + _Q_WEIGHT_CUSHION * c + _Q_WEIGHT_LIQUIDITY * liq)
    return round(raw * (leverage_factor(symbol) if symbol else 1.0), 1)


def _next_earnings_by_symbol(session: Session, symbols: list[str], today: date) -> dict[str, date]:
    """The next upcoming earnings report date per symbol, for the earnings-window filter.

    ONE query for the whole universe, not one per symbol — this runs inside the candidate scan.
    A symbol with no known upcoming report is simply absent from the result, which the caller
    treats as "no earnings constraint" rather than as "safe": an ETF genuinely has no earnings,
    but a missing row could equally mean the calendar just hasn't been populated for that name,
    and failing OPEN here matches every other fail-open path in this module.
    """
    if not symbols:
        return {}
    rows = session.execute(text("""
        SELECT s.symbol, MIN(e.report_date) AS next_report
        FROM earnings_events e
        JOIN stocks s ON s.id = e.stock_id
        WHERE s.symbol = ANY(:syms) AND e.report_date >= :today
        GROUP BY s.symbol
    """), {"syms": [s.upper() for s in symbols], "today": today}).all()
    return {r.symbol.upper(): r.next_report for r in rows if r.next_report is not None}


def _score_contract(
    *, strategy: str, strike: float, premium_bid: float, current_price: float, dte: int,
) -> dict:
    """Pure yield/economics math for one contract — no DB access, so it can be tested
    directly against synthetic numbers (matching compute_options_game_plan()'s own established
    "pure function" precedent in routes.py). `premium_bid` must be the real bid — selling
    (writing) an option is filled at the bid, never the ask or the mid."""
    annualized_yield_pct = round(premium_bid / current_price * (365.0 / dte) * 100.0, 2)
    result = {
        "annualized_yield_pct": annualized_yield_pct,
        "premium_per_contract": round(premium_bid * 100, 2),
        "premium": round(premium_bid, 2),
    }
    if strategy == "COVERED_CALL":
        result["effective_cap_price"] = round(strike + premium_bid, 2)
        result["collateral_required"] = round(current_price * 100, 2)
    else:
        result["effective_purchase_price"] = round(strike - premium_bid, 2)
        result["collateral_required"] = round(strike * 100, 2)
    return result


def settle_position_economics(
    *, strategy: str, strike: float, premium_collected: float, contracts: int,
    underlying_entry_price: float, close_price: float,
) -> dict:
    """Pure settlement math for one position at expiry — no DB access. Returns
    {assigned, settle_price, pnl, cash_released}.

    COVERED_CALL is a synthetic buy-write: assigned (close_price > strike) sells the shares at
    strike; otherwise they're liquidated at the market close. CASH_SECURED_PUT reserves
    strike*100*contracts at entry; assigned (close_price < strike) marks the assigned shares to
    market and liquidates them immediately rather than holding — see the module docstring for
    why both strategies are modeled as closing at expiry, never rolled.
    """
    if strategy == "COVERED_CALL":
        assigned = close_price > strike
        settle_price = strike if assigned else close_price
        pnl = premium_collected + (settle_price - underlying_entry_price) * 100 * contracts
    else:  # CASH_SECURED_PUT
        assigned = close_price < strike
        settle_price = close_price if assigned else strike
        pnl = (
            premium_collected + (close_price - strike) * 100 * contracts
            if assigned else premium_collected
        )
    return {
        "assigned": assigned,
        "settle_price": settle_price,
        "pnl": pnl,
        "cash_released": settle_price * 100 * contracts,
    }


def _today_et() -> date:
    """AUD-T409-UTCDATEBOUNDARY: 'today' for every date computation in this module, as a US
    market participant means it — the calendar date in America/New_York, NOT a truncation of
    the current UTC instant.

    `datetime.now(timezone.utc).date()` is wrong for roughly 4-5 hours of every single evening.
    UTC crosses midnight at 8pm EDT / 7pm EST, while the US trading day these dates are meant
    to describe does not end until well after that. Confirmed live 2026-09-18: at 8:40pm ET
    (00:40 UTC, already Sept 19 in UTC), the naive UTC version reported the freshest chain
    (Thursday 09-17's settled close — Friday's own close is not captured until Monday's job
    sweeps back through it, by design) as "2 days old" instead of the correct 1. The banner was
    accurate given ITS OWN inputs; the inputs were one day wrong.

    THE MORE SERIOUS CONSEQUENCE, dormant until DST ends (~Nov 1): the scheduled options-income
    step fires at 19:00 America/New_York, which is 00:00 UTC the FOLLOWING day during EST
    (winter). On a true Friday, the naive UTC date at that moment is Saturday —
    `today.weekday() >= 5` — so the entire Friday evening settlement/entry run would have been
    silently skipped as "the weekend" every single EST-season Friday, indefinitely, with no
    error and no log line distinguishing it from a genuine non-trading day.

    This is the SAME conversion `_is_us_trading_day()` already performs correctly elsewhere in
    this codebase (scheduler.py) — this module just never adopted it, despite computing `today`
    at three separate call sites with the naive (wrong) version.
    """
    return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()


def rank_income_candidates(
    session: Session,
    symbols: list[str] | None = None,
    strategies: list[str] | None = None,
    current_prices: dict[str, float] | None = None,
    point_in_time: date | None = None,
    all_contracts: bool = False,
) -> list[dict]:
    """Best current covered-call/CSP candidate per (symbol, strategy), ranked by annualized
    yield. Reads each symbol's own LATEST archived chain — never a live fetch — so this is
    cheap enough to call for the whole universe on a schedule, not just interactively.
    """
    from .paper_trading_engine import _fetch_live_prices

    symbols = [s.upper() for s in (symbols or _INCOME_UNIVERSE)]
    strategies = strategies or ["COVERED_CALL", "CASH_SECURED_PUT"]
    current_prices = current_prices if current_prices is not None else _fetch_live_prices(symbols)

    # T399-INCOME-BACKTEST: `point_in_time` turns this into the backtest's own selector without
    # forking it. The whole value of a backtest here is that it exercises the REAL ranking, so
    # there must not be a second "backtest version" of this function to drift against the live
    # one — the same mistake the engine deliberately avoided by having the screener and the
    # autonomous engine share this single source of truth. When set, "today" becomes that date
    # and each symbol's chain is the one AS OF that date (never a later one), which is what
    # makes the replay lookahead-free.
    today = point_in_time or _today_et()
    earnings_by_symbol = _next_earnings_by_symbol(session, symbols, today)
    out: list[dict] = []
    for sym in symbols:
        price = current_prices.get(sym)
        if not price or price <= 0:
            continue
        as_of = _chain_as_of_on_or_before(session, sym, today) if point_in_time else _latest_chain_as_of(session, sym)
        if as_of is None:
            continue
        # AUD-T398-STALECHAIN: the daily OPTHIST capture job self-heals short gaps, but if it
        # ever stopped running for a week+ (an outage, a broken credential), this would
        # otherwise silently keep pricing candidates off a week-old chain against TODAY's live
        # price with no warning at all. A stale chain's strike/delta/premium no longer describe
        # a contract that's actually tradeable at that price today.
        if (today - as_of).days > _INCOME_MAX_CHAIN_STALENESS_DAYS:
            log.warning("options_income.stale_chain_skipped", symbol=sym, as_of=as_of.isoformat(),
                       days_stale=(today - as_of).days)
            continue
        for strategy in strategies:
            opt_type = "call" if strategy == "COVERED_CALL" else "put"
            rows = session.execute(text("""
                SELECT option_symbol, expiry, strike, delta, nbbo_bid, nbbo_ask,
                       implied_volatility, open_interest
                FROM option_chain_history
                WHERE symbol = :sym AND as_of = :as_of AND option_type = :opt_type
                  AND expiry IS NOT NULL AND strike IS NOT NULL
                  AND expiry > :as_of AND expiry <= :max_exp
                  AND nbbo_bid IS NOT NULL AND nbbo_bid > 0
                  AND delta IS NOT NULL AND ABS(delta) BETWEEN :min_delta AND :max_delta
                  AND open_interest >= :min_oi
            """), {
                "sym": sym, "as_of": as_of, "opt_type": opt_type,
                "max_exp": as_of + timedelta(days=_INCOME_MAX_DTE),
                "min_delta": _INCOME_MIN_ABS_DELTA, "max_delta": _INCOME_MAX_ABS_DELTA,
                "min_oi": _INCOME_MIN_OPEN_INTEREST,
            }).all()

            best: dict | None = None
            for r in rows:
                # AUD-T398-DTEFROMSTALE: days-to-expiry MUST be measured from today, not from
                # the chain's as_of date. A position is opened NOW, so the real holding period
                # is today -> expiry. Measuring from as_of silently stops enforcing the stated
                # minimum the moment the chain is even one day stale — measured live on a
                # 5-day-old chain, every "14 DTE" candidate the engine offered was really a
                # 9-day trade, i.e. below its own documented floor.
                dte = (r.expiry - today).days
                if dte < _INCOME_MIN_DTE:
                    continue

                # AUD-T398-EARNINGSWINDOW: never sell premium across an earnings report. The
                # whole premise of these strategies is that assignment is the minority outcome;
                # an earnings gap is precisely the event that inverts that, and it is exactly
                # WHY the richest-looking premium is often rich. Skip any contract whose life
                # spans a known upcoming report for this symbol.
                _er = earnings_by_symbol.get(sym)
                if _er is not None and today <= _er <= r.expiry:
                    continue

                # AUD-T398-STALEMONEYNESS: the delta/strike were computed against the price on
                # `as_of`. If the underlying has moved since, a contract selected as a ~0.30
                # delta OTM trade can already be IN the money at today's price — measured live:
                # a 500-strike AMD put was opened as a "0.35 delta" trade with the stock at
                # 493.41, i.e. already ITM at entry, carrying roughly double the recorded risk.
                # Re-check moneyness against the CURRENT price and drop anything no longer OTM.
                strike_f = float(r.strike)
                if strategy == "COVERED_CALL" and strike_f <= price:
                    continue
                if strategy == "CASH_SECURED_PUT" and strike_f >= price:
                    continue

                cushion_pct = round(
                    ((strike_f - price) / price * 100) if strategy == "COVERED_CALL"
                    else ((price - strike_f) / price * 100), 2)
                # AUD-T398-THINCUSHION: OTM by a rounding error is not meaningfully OTM.
                if cushion_pct < _INCOME_MIN_CUSHION_PCT:
                    continue

                premium = float(r.nbbo_bid)  # sold at the bid — the real, conservative fill
                metrics = _score_contract(
                    strategy=strategy, strike=strike_f, premium_bid=premium,
                    current_price=price, dte=dte,
                )
                cand = {
                    "symbol": sym, "strategy": strategy, "as_of": as_of.isoformat(),
                    "days_stale": (today - as_of).days,
                    "option_symbol": r.option_symbol, "expiry": r.expiry,
                    "days_to_expiry": dte, "strike": strike_f,
                    "delta": round(float(r.delta), 4),
                    "open_interest": r.open_interest,
                    "iv": round(float(r.implied_volatility), 4) if r.implied_volatility is not None else None,
                    "current_price": price,
                    "otm_cushion_pct": cushion_pct,
                    **metrics,
                }
                cand["quality_score"] = quality_score(
                    annualized_yield_pct=cand["annualized_yield_pct"],
                    otm_cushion_pct=cushion_pct,
                    open_interest=r.open_interest,
                    symbol=sym,
                )
                cand["leverage_mult"] = _LEVERAGED_SYMBOLS.get(sym, 1.0)
                # Best-per-symbol is chosen on the RISK-ADJUSTED score, not raw yield — picking
                # the highest-yielding contract per symbol just re-introduces the same adverse
                # selection one level down, before the cross-symbol ranking ever sees it.
                # T399-WEIGHTDERIV: `all_contracts` emits every qualifying contract instead of
                # reducing to one per symbol. Needed specifically so the weight-derivation study
                # can re-rank an UNBIASED pool — the best-per-symbol reduction below is itself
                # decided by quality_score, so a pool built with it already excludes everything
                # the current weights disliked, which would make any re-derivation circular.
                if all_contracts:
                    out.append(cand)
                elif best is None or cand["quality_score"] > best["quality_score"]:
                    best = cand
            if best is not None and not all_contracts:
                out.append(best)

    out.sort(key=lambda c: c["quality_score"], reverse=True)
    return out


def expected_settlement_session(expiry: date) -> date:
    """The trading session an expiry actually settles against.

    Pure and separately testable. An expiry landing on a weekend or NYSE holiday legitimately
    settles on the preceding session; an expiry on a normal trading day settles on ITSELF and
    on no other day. Distinguishing those two cases is the whole point — see
    _settlement_close() for why substituting any nearby close is unsafe.
    """
    d = expiry
    for _ in range(10):
        if d.weekday() < 5 and d not in NYSE_HOLIDAYS:
            return d
        d -= timedelta(days=1)
    return expiry  # pathological input; caller still has to find a real close for it


# DA-05 (2026-09-24): a D1 bar existing is NOT the same as that session having ENDED.
#
# This repo writes daily bars DURING the session — that is the premise of
# BUG-VOLANOM-STALEMARKET and of every "mutable intraday D1" guard elsewhere in the codebase.
# _settlement_close() already refuses to substitute a NEARBY session's close
# (AUD-T400-SETTLESUBSTITUTE), but it still accepted the CORRECT session's bar while that
# session was still trading. The scheduled settlement run is in the evening and is therefore
# safe; the admin /run-step route is callable at any hour and reaches the identical code.
#
# The failure is permanent, not transient: a $100 short put read against an unfinished $101
# print settles as expired-worthless (+$100 premium) and is excluded from every later retry —
# even if the session actually closes at $90 and the real outcome is an assignment worth -$900.
#
# 16:15 ET is deliberately conservative: regular close is 16:00, early closes are 13:00, so
# this is final on BOTH kinds of day plus an ingestion buffer. Waiting the extra hours on an
# early-close day costs nothing, because the only scheduled settlement run is in the evening
# anyway. Being late is recoverable; settling on a moving price is not.
_SETTLEMENT_FINALITY_ET = dt_time(16, 15)


def settlement_session_is_final(want: date, now_et: datetime | None = None) -> bool:
    """True only when `want`'s exchange session has definitively ended."""
    now = now_et or datetime.now(timezone.utc)
    # CONVERT, never assume. Reading `.date()` and the time-of-day off whatever zone the caller
    # happened to pass is the T409 UTC-vs-ET bug in miniature: 18:00 UTC on expiry day is 14:00
    # ET — still trading — but compares as 18:00 and would clear a 16:15 cutoff. A naive value
    # is taken to be exchange time, since that is the only zone this comparison is meaningful in.
    if now.tzinfo is not None:
        now = now.astimezone(ZoneInfo("America/New_York"))
    today = now.date()
    if want < today:
        return True
    if want > today:
        return False  # a session that has not happened yet
    return now.time() >= _SETTLEMENT_FINALITY_ET


def _settlement_close(session: Session, stock_id: int, expiry: date) -> tuple[float, date] | None:
    """The close for the EXACT expected settlement session, or None.

    AUD-T400-SETTLESUBSTITUTE: this previously accepted any close within 7 days before expiry
    and then closed the position permanently on it. That silently conflates two different
    situations — "the expiry was a holiday, so the prior session IS the settlement session"
    (legitimate) and "the settlement session's data just hasn't loaded yet" (not legitimate).
    In the second case a stale close decides assignment, and the position is closed forever on
    it: a $100 short put settled against a $101 close from a day earlier books as expired
    worthless even if the real settlement close was $90 and it should have been assigned.
    Returning None instead leaves the position open so the next run can settle it correctly.
    """
    want = expected_settlement_session(expiry)
    # DA-05: refuse to settle against a session that is still trading. Returning None leaves
    # the position OPEN for the next run, which is exactly how this function already handles
    # "the settlement bar has not loaded yet" — a deliberately identical, recoverable outcome.
    if not settlement_session_is_final(want):
        log.info("options_income.settlement_deferred_session_not_final",
                 stock_id=stock_id, expiry=str(expiry), expected_session=str(want))
        return None
    row = session.execute(
        select(Price.close).where(
            Price.stock_id == stock_id,
            Price.timeframe == TimeFrame.D1,
            func.date(Price.ts) == want,
        ).limit(1)
    ).first()
    return (float(row.close), want) if row and row.close is not None else None


def _closing_price_on_or_before(session: Session, stock_id: int, target_date: date, window_days: int = 7) -> float | None:
    """The most recent daily close at or before target_date — bounded backward-only, matching
    leaps_backtest.py's _nearest_quote_date convention (a specific date may be a holiday; never
    reach forward past the intended settlement date)."""
    row = session.execute(
        select(Price.close).where(
            Price.stock_id == stock_id,
            Price.timeframe == TimeFrame.D1,
            func.date(Price.ts) <= target_date,
            func.date(Price.ts) >= target_date - timedelta(days=window_days),
        ).order_by(Price.ts.desc()).limit(1)
    ).first()
    return float(row.close) if row else None


def settle_expired_positions(session: Session, portfolio: OptionsIncomePortfolio, as_of: date | None = None) -> int:
    """Close every OPEN position on this portfolio whose expiry has arrived. A position whose
    settlement price isn't available yet (price data lags the expiry date) is left open and
    retried on the next call — never guessed.

    AUD-T409-UTCDATEBOUNDARY: the default falls back to _today_et(), not a naive UTC .date().
    The only live caller already passes `today` explicitly (itself now ET-correct), so this
    default is currently unexercised in production — but a wrong default here is a dormant
    footgun for the next direct/manual/test caller, and `expiry <= as_of` computed one day too
    high would settle a position a full day BEFORE its real expiry, against a session that
    has not happened yet for that contract."""
    as_of = as_of or _today_et()
    open_positions = session.execute(
        select(OptionsIncomePosition).where(
            OptionsIncomePosition.portfolio_id == portfolio.id,
            OptionsIncomePosition.stage == "open",
            OptionsIncomePosition.expiry <= as_of,
        )
    ).scalars().all()

    # AUD-A17: `settled_count` (the integer this function contracts to return) and
    # `settlement` (the (price, session_date) tuple from _settlement_close) MUST be separate
    # names. They were the same name until 2026-09-17, so the tuple clobbered the counter and
    # `settled += 1` raised TypeError on the FIRST successful settlement every time.
    settled_count = 0
    try:
        for pos in open_positions:
            if pos.stock_id is None:
                # AUD-T398-ZOMBIEPOSITION: a position whose Stock lookup failed at entry time
                # (symbol not yet in the Stock table then) would otherwise never settle — nothing
                # else ever re-resolves stock_id, so it would stay "open" and its collateral
                # permanently locked forever. Re-resolve here too, once, before giving up.
                stock = session.execute(select(Stock).where(Stock.symbol == pos.symbol)).scalar_one_or_none()
                if stock is None:
                    log.error("options_income.settle_missing_stock", portfolio_id=portfolio.id,
                              position_id=pos.id, symbol=pos.symbol)
                    continue
                pos.stock_id = stock.id
            settlement = _settlement_close(session, pos.stock_id, pos.expiry)
            if settlement is None:
                # Left OPEN on purpose and retried next run — never settled against a substitute
                # close from a different session. Logged so a genuinely stuck position is visible
                # rather than quietly skipped forever.
                log.warning("options_income.settlement_session_missing", position_id=pos.id,
                            symbol=pos.symbol, expiry=str(pos.expiry),
                            expected_session=str(expected_settlement_session(pos.expiry)))
                continue
            close_price, _session_date = settlement

            econ = settle_position_economics(
                strategy=pos.strategy, strike=float(pos.strike),
                premium_collected=float(pos.total_premium_collected), contracts=pos.contracts,
                underlying_entry_price=float(pos.underlying_entry_price), close_price=close_price,
            )

            portfolio.current_cash = float(portfolio.current_cash) + econ["cash_released"]
            pos.stage = "closed"
            pos.close_date = pos.expiry
            pos.underlying_close_price = close_price
            pos.assigned = econ["assigned"]
            pos.pnl = round(econ["pnl"], 2)
            pos.pct_return_on_collateral = (
                round(econ["pnl"] / float(pos.collateral_reserved) * 100, 2) if pos.collateral_reserved else None
            )
            pos.close_reason = "assigned" if econ["assigned"] else "expired_otm"
            settled_count += 1
    except Exception:
        # AUD-A17: a mid-batch failure leaves ORM objects already mutated (cash released,
        # stage="closed") but uncommitted. run_options_income_step's own `except` only LOGS —
        # so without this rollback that partial state stays live in the session, and the next
        # unconditional commit (_snapshot_income_equity_curve's) silently persists a settlement
        # this function reported as failed. Roll the whole batch back: all-or-nothing.
        session.rollback()
        log.error("options_income.settle_batch_failed", portfolio_id=portfolio.id,
                  settled_before_failure=settled_count, exc_info=True)
        raise

    if settled_count:
        session.commit()
        log.info("options_income.settled", portfolio_id=portfolio.id, count=settled_count)
    return settled_count


def open_income_positions(
    session: Session, portfolio: OptionsIncomePortfolio, candidates: list[dict] | None = None,
) -> int:
    """Open new positions on this portfolio from ranked candidates, respecting its own config
    (max_positions, per-symbol cap, min yield, daily entry cap, available cash, and a
    per-position concentration cap so one high-priced-stock CSP can't consume the whole book)."""
    if not portfolio.is_active:
        return 0
    cfg = {**_DEFAULT_INCOME_CONFIG, **(portfolio.config or {})}

    open_positions = session.execute(
        select(OptionsIncomePosition).where(
            OptionsIncomePosition.portfolio_id == portfolio.id,
            OptionsIncomePosition.stage == "open",
        )
    ).scalars().all()
    if len(open_positions) >= cfg["max_positions"]:
        return 0
    held_symbols: dict[str, int] = {}
    for p in open_positions:
        held_symbols[p.symbol] = held_symbols.get(p.symbol, 0) + 1

    if candidates is None:
        candidates = rank_income_candidates(
            session, symbols=cfg.get("symbols", _INCOME_UNIVERSE), strategies=cfg.get("strategies"),
        )

    opened = 0
    today = _today_et()
    # AUD-T398-PERCALL-NOT-PERDAY: max_entries_per_day names a CALENDAR-DAY budget, but was
    # only ever enforced per function CALL — a second same-day invocation (the admin /run-step
    # endpoint, a scheduler misfire retry, manual debugging) would silently open another full
    # batch on top, exceeding the portfolio's own configured entry pace. Count what this
    # portfolio has ALREADY opened today and subtract it from today's remaining budget.
    already_opened_today = session.execute(
        select(func.count()).select_from(OptionsIncomePosition).where(
            OptionsIncomePosition.portfolio_id == portfolio.id,
            OptionsIncomePosition.entry_date == today,
        )
    ).scalar_one()
    todays_remaining_budget = max(0, cfg.get("max_entries_per_day", 3) - already_opened_today)
    max_new = min(todays_remaining_budget, cfg["max_positions"] - len(open_positions))
    for cand in candidates:
        if opened >= max_new:
            break
        if cand["strategy"] not in cfg.get("strategies", _DEFAULT_INCOME_CONFIG["strategies"]):
            continue
        if cand["annualized_yield_pct"] < cfg.get("min_annualized_yield_pct", 8.0):
            continue
        if held_symbols.get(cand["symbol"], 0) >= cfg.get("max_positions_per_symbol", 1):
            continue

        contracts = cfg.get("contracts_per_position", 1)
        collateral = cand["collateral_required"] * contracts
        if collateral > float(portfolio.current_cash):
            continue
        max_collateral = cfg.get("max_collateral_pct_per_position", 0.25) * float(portfolio.initial_capital)
        if collateral > max_collateral:
            continue

        stock = session.execute(select(Stock).where(Stock.symbol == cand["symbol"])).scalar_one_or_none()
        total_premium = cand["premium_per_contract"] * contracts
        session.add(OptionsIncomePosition(
            portfolio_id=portfolio.id, symbol=cand["symbol"], stock_id=stock.id if stock else None,
            strategy=cand["strategy"], option_symbol=cand["option_symbol"], strike=cand["strike"],
            expiry=cand["expiry"], contracts=contracts,
            entry_date=today, entry_time=datetime.now(timezone.utc),
            underlying_entry_price=cand["current_price"], delta_at_entry=cand["delta"], iv_at_entry=cand.get("iv"),
            premium_per_contract=cand["premium_per_contract"], total_premium_collected=total_premium,
            collateral_reserved=collateral, stage="open",
        ))
        portfolio.current_cash = float(portfolio.current_cash) - collateral + total_premium
        held_symbols[cand["symbol"]] = held_symbols.get(cand["symbol"], 0) + 1
        opened += 1

    if opened:
        session.commit()
        log.info("options_income.opened", portfolio_id=portfolio.id, count=opened)
    return opened


def short_option_liability(*, strategy: str, strike: float, underlying_price: float,
                           contracts: int, quote_ask: float | None = None) -> tuple[float, str]:
    """What it would COST to buy back the short option — a real liability, not zero.

    Pure, so the accounting is testable without a DB. Returns (liability, mark_source).

    AUD-T400-SHORTLIABILITY: the equity curve previously counted collected premium as cash and
    added back the full collateral, while never deducting the obligation that premium was
    payment for. Selling a put for $150 therefore "created" $150 of equity the instant it was
    opened, and a short put moving against the portfolio stayed invisible until settlement.
    Opening a short option must be roughly equity-NEUTRAL: you receive cash and simultaneously
    owe a position of about the same value.

    Marked at the ASK when a real quote is available, because closing a SHORT means BUYING it
    back and a buyer pays the ask — the conservative direction for a liability. With no quote,
    falls back to INTRINSIC value, which is always computable from the underlying and can never
    be stale in the way a quote can; it understates the liability by whatever time value
    remains, which is why the source is returned and recorded rather than hidden.
    """
    if strategy == "COVERED_CALL":
        intrinsic = max(0.0, underlying_price - strike)
    else:
        intrinsic = max(0.0, strike - underlying_price)

    # DA-06 (2026-09-24): an ask can only be USED if it is consistent with the underlying it is
    # being combined with. The quote came from an archive with no age check while the underlying
    # is a live price, so the two could be days apart — and any non-negative value, including
    # zero, used to win outright.
    #
    # The decisive guard is arithmetic, not a timestamp: AN OPTION CANNOT BE WORTH LESS THAN ITS
    # INTRINSIC VALUE. Buying back a $100 put with the stock at $80 costs at least $20/share by
    # arbitrage, so a $2 ask is not a cheap close — it is a quote from before the move. Taking it
    # reported a $200 liability against a $2,000 floor and inflated reported equity by $1,800.
    # Freshness metadata can be missing or wrong; this bound cannot.
    if quote_ask is not None and quote_ask >= intrinsic >= 0:
        return round(quote_ask * 100 * contracts, 2), "quote_ask"
    if quote_ask is not None and quote_ask >= 0:
        # A real quote that is BELOW intrinsic is stale, crossed or suspect. Fall back to the
        # floor and SAY SO, rather than silently accepting a mark that cannot be executed.
        return round(intrinsic * 100 * contracts, 2), "intrinsic_quote_below_floor"
    return round(intrinsic * 100 * contracts, 2), "intrinsic"


# DA-06: how old an archived quote may be before it is refused outright. The option-chain
# archive is written daily, so anything beyond a few sessions means the contract stopped being
# quoted — and a mark from a week ago combined with a live underlying is a mixed-time valuation
# whatever its value happens to be.
_MAX_ASK_AGE_DAYS = 5


def _latest_option_ask(session: Session, option_symbol: str,
                       as_of: date | None = None) -> float | None:
    """Most recent archived ask for one contract, or None when it is missing or too old.

    DA-06: this previously returned the most recent non-null ask with no age check and no way
    for the caller to know WHEN it was quoted. A row from an arbitrarily distant past therefore
    overrode the intrinsic fallback. It also never bounded the future: an as_of ahead of the
    valuation date is a look-ahead read in any historical snapshot.
    """
    ref = as_of or _today_et()
    row = session.execute(text("""
        SELECT nbbo_ask, as_of FROM option_chain_history
        WHERE option_symbol = :os AND nbbo_ask IS NOT NULL
          AND as_of <= :ref AND as_of >= :floor
        ORDER BY as_of DESC LIMIT 1
    """), {"os": option_symbol, "ref": ref,
           "floor": ref - timedelta(days=_MAX_ASK_AGE_DAYS)}).first()
    return float(row.nbbo_ask) if row and row.nbbo_ask is not None else None


def _snapshot_income_equity_curve(session: Session, portfolios: list[OptionsIncomePortfolio], as_of: date) -> None:
    from .paper_trading_engine import _fetch_live_prices

    for portfolio in portfolios:
        open_positions = session.execute(
            select(OptionsIncomePosition).where(
                OptionsIncomePosition.portfolio_id == portfolio.id,
                OptionsIncomePosition.stage == "open",
            )
        ).scalars().all()
        collateral_committed = 0.0
        short_liability = 0.0
        mark_sources: dict[str, int] = {}
        if open_positions:
            # Every open position needs a live underlying now, not just the covered calls — a
            # short put's liability moves with the underlying too.
            all_syms = sorted({p.symbol for p in open_positions})
            live_prices = _fetch_live_prices(all_syms) if all_syms else {}
            for p in open_positions:
                px = live_prices.get(p.symbol) or float(p.underlying_entry_price)
                if p.strategy == "COVERED_CALL":
                    collateral_committed += px * 100 * p.contracts
                else:
                    collateral_committed += float(p.collateral_reserved)
                liab, _src = short_option_liability(
                    strategy=p.strategy, strike=float(p.strike), underlying_price=px,
                    contracts=p.contracts,
                    # Bounded by the snapshot's own date: a historical row must never reach
                    # forward for a quote that did not exist yet.
                    quote_ask=_latest_option_ask(session, p.option_symbol, as_of=as_of),
                )
                short_liability += liab
                # DA-06: provenance was discarded, so a persisted equity row could not explain
                # whether it was marked on a real quote or on the intrinsic floor. Counted here
                # and logged with the snapshot rather than dropped.
                mark_sources[_src] = mark_sources.get(_src, 0) + 1

        # AUD-T400-SHORTLIABILITY: equity is assets MINUS the outstanding short obligation.
        # Without the final term, opening a short option manufactured equity equal to the
        # premium and a position moving against the book stayed invisible until settlement.
        equity = float(portfolio.current_cash) + collateral_committed - short_liability
        if mark_sources:
            log.info("options_income.equity_mark_sources", portfolio_id=portfolio.id,
                     as_of=str(as_of), sources=mark_sources)
        existing = session.execute(
            select(OptionsIncomeEquityCurve).where(
                OptionsIncomeEquityCurve.portfolio_id == portfolio.id,
                OptionsIncomeEquityCurve.date == as_of,
            )
        ).scalar_one_or_none()
        if existing:
            existing.equity = equity
            existing.equity_basis = _EQUITY_BASIS
            existing.cash = float(portfolio.current_cash)
            existing.open_positions_count = len(open_positions)
            existing.collateral_committed = collateral_committed
        else:
            session.add(OptionsIncomeEquityCurve(
                portfolio_id=portfolio.id, date=as_of, equity=equity, cash=float(portfolio.current_cash),
                open_positions_count=len(open_positions), collateral_committed=collateral_committed,
                equity_basis=_EQUITY_BASIS,
            ))
    session.commit()


_INCOME_STEP_LOCK_KEY = "stockai:lock:options_income_step"
# Generous: a real run settles expiries, ranks the universe and opens positions, and the last
# measured duration was 344s. A TTL shorter than the work it guards is not a lock, it is a
# delay — the second caller would simply take it mid-run.
_INCOME_STEP_LOCK_TTL = 1800


def run_options_income_step() -> dict:
    """Scheduler entry point: settle expired positions, then open new ones, across every
    active OptionsIncomePortfolio. Ranks candidates ONCE across the union of every active
    portfolio's own symbol universe, not once per portfolio — matching the established
    once-per-batch convention every other systematic scan in this codebase already follows."""
    # AUD-A07-INCOMECONCURRENCY: TWO entry points reach this function — the 19:00 ET scheduled
    # job and the admin POST /options-income/run-step escape hatch — and it does read/check/write
    # on portfolio cash, collateral and positions with no isolation between them. Two concurrent
    # runs can both read the same `current_cash`, both decide the same candidate is affordable,
    # and both open it: the per-position concentration cap and the daily entry cap are enforced
    # in Python against a snapshot, so neither would notice.
    #
    # The lock lives HERE rather than in the scheduler wrapper deliberately. Guarding only the
    # scheduled path would leave the admin route — the one a human triggers impatiently, most
    # likely while the scheduled run is already going — completely unprotected, which is
    # precisely the collision worth preventing.
    # DA-05/DA-07 (2026-09-24): this lock had BOTH failure modes T232-PT5 already fixed for the
    # paper-trading lock, and the comment below used to claim it was "matching every other
    # locked job in this codebase" — the opposite of the truth.
    #
    #   1. The value was the literal "1" and release was an unconditional DELETE. If this run
    #      exceeds the TTL and a second run acquires a fresh lease, this run's `finally` deletes
    #      the SECOND run's lock, which lets a third start while the second still believes it is
    #      exclusive. Fixed with a unique token and an atomic compare-and-delete.
    #   2. It failed OPEN on a Redis error, then still ran the unconditional delete — so a
    #      transient outage could both start a concurrent run AND destroy the other run's lease.
    #      This function mutates portfolio cash, collateral and positions, which is the same
    #      class of state the paper-trading lock deliberately fails CLOSED to protect. Losing one
    #      evening's run is recoverable on the next tick; double-opening positions against the
    #      same cash is not.
    import uuid

    token = uuid.uuid4().hex
    try:
        if not _get_income_redis().set(_INCOME_STEP_LOCK_KEY, token, nx=True, ex=_INCOME_STEP_LOCK_TTL):
            log.info("options_income.step_skipped_already_running")
            return {"ok": False, "skipped": "already_running"}
    except Exception:
        log.error("options_income.step_skipped_lock_unavailable", exc_info=True)
        return {"ok": False, "skipped": "lock_unavailable"}

    try:
        return _run_options_income_step_locked()
    finally:
        _release_income_lock(token)


# Matches scheduler.py's own _LOCK_RELEASE_LUA exactly. An atomic script is required because
# "GET then DEL" is two round trips, and the lease can expire and be re-acquired between them —
# which is the very race an ownership check is supposed to close.
_INCOME_LOCK_RELEASE_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""


def _release_income_lock(token: str) -> bool:
    """Release the step lock only if THIS run still owns it. Returns whether it was released."""
    try:
        released = _get_income_redis().eval(_INCOME_LOCK_RELEASE_LUA, 1, _INCOME_STEP_LOCK_KEY, token)
        if not released:
            # Not an error we can act on, but it means this run overran its lease and someone
            # else now holds it — worth seeing in logs before it becomes a double-open incident.
            log.warning("options_income.lock_not_owned_at_release", token=token)
        return bool(released)
    except Exception:
        log.warning("options_income.lock_release_failed", exc_info=True)
        return False


def _get_income_redis():
    from common.redis_client import get_redis
    return get_redis()


def _run_options_income_step_locked() -> dict:
    """The real body. Separated so the lock's acquire/release stays readable and so a test can
    exercise the work without needing Redis."""
    today = _today_et()
    if today.weekday() >= 5:
        return {"ok": True, "skipped": "weekend"}  # option_chain_history has no newer as_of

    with SessionLocal() as session:
        portfolios = session.execute(
            select(OptionsIncomePortfolio).where(OptionsIncomePortfolio.is_active.is_(True))
        ).scalars().all()
        if not portfolios:
            return {"ok": True, "skipped": "no_active_portfolios"}

        for portfolio in portfolios:
            try:
                settle_expired_positions(session, portfolio, today)
            except Exception:
                log.error("options_income.settle_failed", portfolio_id=portfolio.id, exc_info=True)

        all_symbols: set[str] = set()
        for p in portfolios:
            all_symbols.update((p.config or {}).get("symbols", _INCOME_UNIVERSE))
        candidates = rank_income_candidates(session, symbols=sorted(all_symbols)) if all_symbols else []

        for portfolio in portfolios:
            try:
                cfg_symbols = set((portfolio.config or {}).get("symbols", _INCOME_UNIVERSE))
                own_candidates = [c for c in candidates if c["symbol"] in cfg_symbols]
                open_income_positions(session, portfolio, candidates=own_candidates)
            except Exception:
                log.error("options_income.open_failed", portfolio_id=portfolio.id, exc_info=True)

        try:
            _snapshot_income_equity_curve(session, portfolios, today)
        except Exception:
            log.error("options_income.equity_snapshot_failed", exc_info=True)

        return {"ok": True, "portfolios": len(portfolios)}
