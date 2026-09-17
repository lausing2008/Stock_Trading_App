"""T399-INCOME-BACKTEST — replay the options income engine over the archived option chains.

WHY THIS EXISTS. The live engine had ZERO resolved outcomes, so every claim about it — that the
strategy makes money, that `quality_score` ranks anything usefully, that the 40/40/20 weights or
the 2% cushion floor are right — was untested. Waiting for live paper trades would take months
and would only ever sample one market regime. The chain archive holds 724 trading days across
29 symbols (2023-10-23 -> 2026-09-11), which can produce hundreds of resolved outcomes today,
across real drawdowns — and drawdowns are precisely where premium-selling strategies fail.

NO LOOKAHEAD — the whole exercise is worthless without this, and this repo has scar tissue
proving it (docs/incidents/backtest-wall-clock-and-lookahead-bugs.md, and the 2026-09-08 harness
audit). Three guarantees:

  1. Candidates for entry date D come from `rank_income_candidates(point_in_time=D)`, which
     resolves each symbol's chain via `_chain_as_of_on_or_before()` — strictly `<= D`.
  2. The underlying price used for selection is D's own CLOSE, never a later one.
  3. The only post-D data touched is the settlement close ON the expiry date, which is the
     outcome being measured, not an input to the decision.

IT REPLAYS THE REAL SELECTOR. `rank_income_candidates` is the same function the live engine and
the screener call — there is deliberately no "backtest version" to drift against it. A backtest
of a parallel implementation measures the parallel implementation.

WHAT IT DOES NOT MODEL, and therefore what its numbers are optimistic about:
  - Fills are assumed at the quoted bid. A real order may not fill at all.
  - No slippage, no commission, no market impact.
  - No EARLY assignment — American options can be assigned any time, especially calls before a
    dividend. Settlement here happens only at expiry, exactly like the live engine.
  - Survivorship: the archive only contains symbols the platform chose to capture.
These are the same simplifications the live engine makes, so the backtest measures the engine as
built — it does not measure what a real brokerage account would have returned.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..services.options_income_engine import (
    rank_income_candidates,
    settle_position_economics,
    expected_settlement_session,
    _INCOME_UNIVERSE,
)

log = structlog.get_logger()

# Entries are attempted every N calendar days rather than daily: a 14-45 DTE strategy holding to
# expiry cannot act on most days anyway, and stepping weekly keeps one symbol from contributing
# dozens of near-identical overlapping trades that would make the sample look larger than the
# number of genuinely independent decisions behind it.
_DEFAULT_STEP_DAYS = 7


def _closes_by_symbol_date(session: Session, symbols: list[str], start: date, end: date) -> dict[tuple[str, date], float]:
    """Every daily close for these symbols in one query, keyed (symbol, date).

    One query, not one per lookup: a replay over ~150 entry dates x 29 symbols would otherwise
    issue tens of thousands of round-trips.
    """
    if not symbols:
        return {}
    rows = session.execute(text("""
        SELECT s.symbol AS symbol, p.ts::date AS d, p.close AS close
        FROM prices p JOIN stocks s ON s.id = p.stock_id
        WHERE s.symbol = ANY(:syms) AND p.timeframe = 'D1'
          AND p.ts::date BETWEEN :start AND :end
    """), {"syms": [s.upper() for s in symbols], "start": start, "end": end}).all()
    return {(r.symbol.upper(), r.d): float(r.close) for r in rows if r.close is not None}


def _close_on_or_before(closes: dict, symbol: str, target: date, window: int = 7) -> tuple[float, date] | None:
    """Close at or before `target` — used for ENTRY pricing, where the most recent available
    close before the entry date is exactly what a live run would have seen. BACKWARD ONLY:
    reaching forward would price an entry using data from after the decision.

    NOT used for settlement — see _settlement_close_bt() for why.
    """
    for back in range(window + 1):
        d = target - timedelta(days=back)
        px = closes.get((symbol.upper(), d))
        if px is not None:
            return px, d
    return None


def _settlement_close_bt(closes: dict, symbol: str, expiry: date) -> tuple[float, date] | None:
    """Settlement close for the EXACT expected session, or None.

    AUD-T400-SETTLESUBSTITUTE: settlement previously reused `_close_on_or_before`, so a missing
    expiry-session close silently settled the trade against an earlier day. That is fine for
    ENTRY pricing (any recent close is a fair stand-in for "what was it worth when we decided")
    and wrong for SETTLEMENT, where the specific session determines assignment and therefore the
    outcome. Sharing one helper for both is what let the defect exist in two places at once.

    Uses the same `expected_settlement_session()` as the live engine, so a backtested outcome
    and a live one resolve the same session for the same expiry.
    """
    want = expected_settlement_session(expiry)
    px = closes.get((symbol.upper(), want))
    return (px, want) if px is not None else None


def backtest_options_income(
    session: Session,
    start: date,
    end: date,
    symbols: list[str] | None = None,
    strategies: list[str] | None = None,
    step_days: int = _DEFAULT_STEP_DAYS,
    max_per_date: int = 3,
    contracts: int = 1,
    all_contracts: bool = False,
) -> dict:
    """Replay the engine's own candidate selection across [start, end] and settle every trade.

    `max_per_date` mirrors the live engine's daily entry cap so the replay takes the same top-N
    ranked candidates a real run would have taken, rather than every qualifying contract.
    """
    symbols = [s.upper() for s in (symbols or _INCOME_UNIVERSE)]

    # Prices for selection AND settlement, fetched once. Padded past `end` so trades entered
    # near the end of the window can still find their expiry close.
    closes = _closes_by_symbol_date(session, symbols, start - timedelta(days=10), end + timedelta(days=90))
    if not closes:
        return {"error": "no price history for these symbols in this window", "trades": []}

    trades: list[dict] = []
    skipped_no_settlement = 0
    entry_date = start
    while entry_date <= end:
        # Point-in-time prices: each symbol's OWN close on the entry date (or the last close
        # before it). This is what the engine would have seen; never a later price.
        pit_prices: dict[str, float] = {}
        for sym in symbols:
            hit = _close_on_or_before(closes, sym, entry_date)
            if hit:
                pit_prices[sym] = hit[0]

        if pit_prices:
            try:
                cands = rank_income_candidates(
                    session, symbols=symbols, strategies=strategies,
                    current_prices=pit_prices, point_in_time=entry_date,
                    all_contracts=all_contracts,
                )
            except Exception as exc:
                log.warning("income_backtest.rank_failed", entry_date=str(entry_date), error=str(exc))
                cands = []

            for cand in cands[:max_per_date]:
                expiry = cand["expiry"] if isinstance(cand["expiry"], date) else date.fromisoformat(str(cand["expiry"]))
                settle = _settlement_close_bt(closes, cand["symbol"], expiry)
                if settle is None:
                    # No close at/near expiry (e.g. expiry beyond the price history) — dropped
                    # rather than guessed, and COUNTED so the drop rate stays visible.
                    skipped_no_settlement += 1
                    continue
                close_price, close_date = settle

                total_premium = cand["premium_per_contract"] * contracts
                collateral = cand["collateral_required"] * contracts
                econ = settle_position_economics(
                    strategy=cand["strategy"], strike=cand["strike"],
                    premium_collected=total_premium, contracts=contracts,
                    underlying_entry_price=cand["current_price"], close_price=close_price,
                )
                trades.append({
                    "entry_date": entry_date.isoformat(),
                    "symbol": cand["symbol"],
                    "strategy": cand["strategy"],
                    "strike": cand["strike"],
                    "expiry": expiry.isoformat(),
                    "settle_date": close_date.isoformat(),
                    "days_held": (close_date - entry_date).days,
                    "entry_price": cand["current_price"],
                    "close_price": close_price,
                    "quality_score": cand["quality_score"],
                    "annualized_yield_pct": cand["annualized_yield_pct"],
                    "otm_cushion_pct": cand["otm_cushion_pct"],
                    "open_interest": cand["open_interest"],
                    "premium_collected": round(total_premium, 2),
                    "collateral": round(collateral, 2),
                    "assigned": econ["assigned"],
                    "pnl": round(econ["pnl"], 2),
                    "return_on_collateral_pct": round(econ["pnl"] / collateral * 100, 3) if collateral else None,
                })
        entry_date += timedelta(days=step_days)

    return {
        "window": [start.isoformat(), end.isoformat()],
        "symbols": symbols,
        "step_days": step_days,
        "max_per_date": max_per_date,
        "trades_settled": len(trades),
        "dropped_no_settlement_price": skipped_no_settlement,
        **_summarise(trades),
        "trades": trades,
    }


def _stats(trades: list[dict]) -> dict:
    """Headline stats for a set of trades. Returns zeros/None on an empty set rather than
    raising — an empty bucket is a real, reportable outcome, not an error."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_rate_pct": None, "assignment_rate_pct": None,
                "total_pnl": 0.0, "avg_return_on_collateral_pct": None}
    wins = sum(1 for t in trades if t["pnl"] > 0)
    assigned = sum(1 for t in trades if t["assigned"])
    rets = [t["return_on_collateral_pct"] for t in trades if t["return_on_collateral_pct"] is not None]
    return {
        "n": n,
        "win_rate_pct": round(wins / n * 100, 1),
        "assignment_rate_pct": round(assigned / n * 100, 1),
        "total_pnl": round(sum(t["pnl"] for t in trades), 2),
        "avg_return_on_collateral_pct": round(sum(rets) / len(rets), 3) if rets else None,
        "worst_pnl": round(min(t["pnl"] for t in trades), 2),
        "best_pnl": round(max(t["pnl"] for t in trades), 2),
    }


def _summarise(trades: list[dict]) -> dict:
    """Overall stats, plus the breakdowns that answer the questions the backtest exists for."""
    by_strategy = defaultdict(list)
    by_score_band = defaultdict(list)
    by_symbol = defaultdict(list)
    for t in trades:
        by_strategy[t["strategy"]].append(t)
        by_symbol[t["symbol"]].append(t)
        # THE question: does quality_score actually predict anything? If these bands don't
        # separate, the score is decoration and should be said so plainly rather than shipped
        # as though it ranks something.
        s = t["quality_score"]
        band = "80+" if s >= 80 else "60-80" if s >= 60 else "40-60" if s >= 40 else "<40"
        by_score_band[band].append(t)

    return {
        "overall": _stats(trades),
        "by_strategy": {k: _stats(v) for k, v in sorted(by_strategy.items())},
        "by_quality_score_band": {k: _stats(by_score_band[k])
                                  for k in ("80+", "60-80", "40-60", "<40") if k in by_score_band},
        "by_symbol": {k: _stats(v) for k, v in sorted(by_symbol.items())},
    }
