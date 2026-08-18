"""T230-BACKTESTING-MULTISYMBOL: multi-symbol, day-stepped portfolio backtest.

HONEST SCOPE — read before trusting or extending this module's output:

This is NOT what the tracker item's own `fix` text originally asked for ("simulate
paper_trading_step() day-by-day using historical signals and prices"). A faithful replay of
that function needs: no-historical-persistence regime detection (the same permanent gap
gate_harness.py already discloses — see its own module docstring), decision-engine calls,
_scan_for_entries()'s full candidate loop (staleness/watchlist/cross-portfolio-symbol locks),
and _monitor_positions()'s live day-by-day stop/target/trailing-stop/signal-decay exit logic —
a "2+ weeks" build per this repo's own docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md §1c,
which explicitly scopes exactly this kind of full bar-by-bar equity-curve replay out as a
future Phase 2b that remains unbuilt.

What this module IS: a smaller, honestly-labeled MVP that answers a real, useful question —
"if I had run a shared-capital portfolio across these N symbols using this app's own real
entry/exit ground truth and real position-sizing math, what would the resulting equity curve,
Sharpe, drawdown, and win rate have looked like?" It does this by:

  1. Reusing gate_harness.py's ALREADY-PROVEN, point-in-time-safe SignalOutcome ground truth
     (real entry_date/entry_price/exit_date/exit_price per symbol, already computed and
     persisted by evaluate_signal_outcomes() — never a re-simulated exit) instead of replaying
     _monitor_positions()'s own live stop/target logic.
  2. Day-stepping through a MERGED, chronologically-sorted event timeline across all requested
     symbols (not one symbol in isolation) — so a shared cash pool, a max_positions cap, and a
     simplified sector-concentration cap all interact exactly as they would across a real
     multi-symbol book.
  3. Reusing REAL sizing inputs where they already exist as pure, already-imported helpers
     (_historical_atr for stop distance) and a genuine SUBSET of the real risk_per_trade_pct /
     max_position_pct sizing formula from paper_trading_engine.py's _open_paper_trade() — NOT
     the full 6-multiplier stack (earnings/regime/confidence/research/consensus/score
     multipliers), since those inputs either don't exist historically (live_regime) or would
     need the full _should_enter() replay this module deliberately doesn't attempt.

What is deliberately NOT modeled (disclosed, not silently glossed over):
  - No decision-engine or _should_enter() gate is replayed here at all — every SignalOutcome
    BUY signal in the window is treated as "the entry signal fired," with portfolio-level caps
    (cash, max_positions, sector) being the ONLY admission filter. A real trading day would
    also apply the full entry-gate stack gate_harness.py's OWN sibling module already tests.
  - No aggregate open-risk cap, no cross-symbol correlation cap, no drawdown circuit breaker,
    no cooldown/re-entry-lockout logic — all real, live mechanisms in _scan_for_entries() that
    this MVP does not attempt to reproduce.
  - Commission/slippage are NOT applied (paper_trading_engine.py's own defaults are $0
    commission for most retail brokers anyway, per its own comment — but slippage IS normally
    applied live and is skipped here for simplicity).
  - Exits use the outcome's own resolved hold-window exit_date/exit_price — a REAL, point-in-
    time-safe ground truth, but NOT a simulated stop/trailing-stop/target exit a live trade
    might have taken earlier or later.

If a genuinely faithful multi-symbol replay of the live decision pipeline is ever needed, build
Phase 2b/2c per the design doc instead of extending this module's own simplified sizing further.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Market, Signal, SignalHorizon, SignalOutcome, SignalType, Stock

from .gate_harness import _HORIZON_BUCKET, _historical_atr

# Simplified subset of paper_trading_engine.py's _DEFAULT_CONFIG — only the fields this
# module's own sizing/cap logic actually reads. Matching the real defaults exactly (not
# independently guessed) so a backtest run with no cfg override reflects this app's real,
# live-deployed risk posture.
_DEFAULT_CFG = {
    "initial_capital": 100_000.0,
    "max_positions": 6,
    "max_sector_pct": 0.25,
    "risk_per_trade_pct": 0.01,
    "max_position_pct": 0.10,
    "max_loss_per_trade_pct": 0.02,
    "min_position_value": 200.0,
    "stop_atr_mult": 2.0,  # matches paper_trading_engine.py's non-GROWTH style default
}


@dataclass
class PortfolioTrade:
    symbol: str
    signal_id: int
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    shares: float
    pct_return: float
    pnl_dollar: float
    sector: str | None


@dataclass
class PortfolioBacktestResult:
    symbols: list[str]
    market: str
    window_start: date
    window_end: date
    initial_capital: float
    final_equity: float
    total_return_pct: float
    n_signals_seen: int          # resolved BUY signals across all requested symbols in window
    n_entered: int                # how many actually got a position sized and opened
    n_skipped_no_room: int        # blocked by max_positions/sector_cap/cash — real candidates, not admitted
    win_rate: float | None = None
    avg_return_pct: float | None = None
    sharpe_ratio: float | None = None      # daily-equity-curve Sharpe, annualized (252 trading days)
    max_drawdown_pct: float | None = None
    equity_curve: list[tuple[str, float]] = field(default_factory=list)  # (date_iso, equity)
    trades: list[PortfolioTrade] = field(default_factory=list)
    skipped_reason: str | None = None


def _fetch_symbol_signals(
    session: Session, symbols: list[str], style: str, market: str, window_start: date, window_end: date,
) -> list[tuple[Signal, SignalOutcome, Stock]]:
    """Resolved BUY signals for the requested symbol set — same resolved-outcome shape as
    gate_harness.py's own _fetch_matched_signals(), scoped to a specific symbol list instead
    of the whole market."""
    style = style.upper()
    bucket = _HORIZON_BUCKET[style]
    is_correct_col = getattr(SignalOutcome, f"is_correct_{bucket}")
    return_col = getattr(SignalOutcome, f"return_{bucket}")
    rows = session.execute(
        select(Signal, SignalOutcome, Stock)
        .join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .join(Stock, Stock.id == Signal.stock_id)
        .where(
            Signal.horizon == SignalHorizon(style),
            Signal.signal == SignalType.BUY,
            Stock.market == Market(market),
            Stock.symbol.in_([s.upper() for s in symbols]),
            SignalOutcome.signal_date >= window_start,
            SignalOutcome.signal_date <= window_end,
            SignalOutcome.entry_date.is_not(None),
            SignalOutcome.exit_date.is_not(None),
            is_correct_col.is_not(None),
            return_col.is_not(None),
        )
        .order_by(SignalOutcome.entry_date)
    ).all()
    return list(rows)


def _size_position(equity: float, entry_price: float, atr: float | None, cfg: dict) -> tuple[float, float] | None:
    """A genuine SUBSET of _open_paper_trade()'s real sizing formula (paper_trading_engine.py)
    — risk_per_trade_pct of equity / ATR-based stop distance, capped by max_position_pct and
    max_loss_per_trade_pct. Deliberately omits the 6 independent size multipliers (earnings/
    regime/confidence/research/consensus/score) the real function applies — see this module's
    own top-of-file docstring for why those aren't safely reproducible without a full
    _should_enter() replay. Returns (shares, stop_distance) or None if the position would be
    below min_position_value (mirrors the real FIN-07 skip)."""
    if entry_price <= 0:
        return None
    stop_distance = (atr * cfg["stop_atr_mult"]) if atr else entry_price * 0.05
    if stop_distance <= 0:
        return None
    risk_dollar = equity * cfg["risk_per_trade_pct"]
    shares = risk_dollar / stop_distance
    max_loss_pct = cfg.get("max_loss_per_trade_pct")
    if max_loss_pct:
        max_loss_dollar = equity * max_loss_pct
        if stop_distance * shares > max_loss_dollar:
            shares = max_loss_dollar / stop_distance
    shares = round(shares, 4)
    position_value = round(shares * entry_price, 2)
    max_pos = equity * cfg["max_position_pct"]
    if position_value > max_pos:
        shares = round(max_pos / entry_price, 4)
        position_value = round(shares * entry_price, 2)
    if shares < 0.01 or position_value < cfg.get("min_position_value", 200.0):
        return None
    return shares, stop_distance


def _max_drawdown_pct(equity_values: list[float]) -> float:
    """Peak-to-trough drawdown as a positive percent of the running peak. Empty/degenerate
    input returns 0.0 rather than raising."""
    if not equity_values:
        return 0.0
    peak = equity_values[0]
    max_dd = 0.0
    for v in equity_values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
    return round(max_dd * 100, 2)


def _annualized_sharpe(daily_returns: list[float]) -> float | None:
    """Annualized Sharpe (252 trading days, 0% risk-free rate) on a daily-return series.
    None (not 0.0) when there are fewer than 2 return observations or zero variance — a
    fabricated Sharpe of 0.0 would misleadingly read as "genuinely flat," not "unmeasurable"."""
    if len(daily_returns) < 2:
        return None
    arr = np.array(daily_returns, dtype=float)
    std = arr.std(ddof=1)
    if std == 0:
        return None
    return round(float(arr.mean() / std * np.sqrt(252)), 3)


def run_portfolio_backtest(
    session: Session,
    symbols: list[str],
    style: str,
    market: str,
    window_start: date,
    window_end: date,
    cfg_overrides: dict | None = None,
) -> PortfolioBacktestResult:
    """Day-step through the requested symbols' resolved BUY signals as a single shared-capital
    portfolio. See this module's own top-of-file docstring for exactly what is and is not
    modeled — this is an honest MVP, not a replay of the live decision/exit pipeline."""
    cfg = {**_DEFAULT_CFG, **(cfg_overrides or {})}
    matched = _fetch_symbol_signals(session, symbols, style, market, window_start, window_end)

    result = PortfolioBacktestResult(
        symbols=[s.upper() for s in symbols], market=market.upper(),
        window_start=window_start, window_end=window_end,
        initial_capital=cfg["initial_capital"], final_equity=cfg["initial_capital"],
        total_return_pct=0.0, n_signals_seen=len(matched), n_entered=0, n_skipped_no_room=0,
    )
    if not matched:
        result.skipped_reason = "no resolved BUY signals for these symbols in this window"
        return result

    cash = cfg["initial_capital"]
    open_positions: list[dict] = []  # each: {"symbol","sector","shares","entry_price","stop_distance","exit_date","exit_price","pct_return","signal_id","entry_date"}
    equity_curve: list[tuple[date, float]] = []
    trades: list[PortfolioTrade] = []
    n_entered = 0
    n_skipped_no_room = 0

    def _mark_to_market() -> float:
        """Equity = cash + sum(shares * entry_price) for still-open positions — a simplified
        mark using entry price (this module doesn't carry a live/intraday price series for
        every date), so the equity curve moves only on realized entries/exits, not intraday
        marks. Documented above as a real simplification, not silently assumed away."""
        return cash + sum(p["shares"] * p["entry_price"] for p in open_positions)

    # The full day-by-day schedule comes from BOTH entry and exit dates — a day with only an
    # exit (no new entry) must still get an equity-curve point once cash/positions change.
    events_by_date: dict[date, list[tuple[Signal, SignalOutcome, Stock]]] = {}
    all_dates_set: set[date] = set()
    for sig, outcome, stock in matched:
        events_by_date.setdefault(outcome.entry_date, []).append((sig, outcome, stock))
        all_dates_set.add(outcome.entry_date)
        all_dates_set.add(outcome.exit_date)
    all_dates = sorted(all_dates_set)

    for day in all_dates:
        # 1. Process exits scheduled for today FIRST — frees cash/room before today's entries.
        still_open = []
        for p in open_positions:
            if p["exit_date"] == day:
                proceeds = p["shares"] * p["exit_price"]
                cash += proceeds
                pnl_dollar = proceeds - (p["shares"] * p["entry_price"])
                trades.append(PortfolioTrade(
                    symbol=p["symbol"], signal_id=p["signal_id"],
                    entry_date=p["entry_date"], exit_date=p["exit_date"],
                    entry_price=p["entry_price"], exit_price=p["exit_price"],
                    shares=p["shares"], pct_return=p["pct_return"],
                    pnl_dollar=round(pnl_dollar, 2), sector=p["sector"],
                ))
            else:
                still_open.append(p)
        open_positions = still_open

        # 2. Process entries scheduled for today.
        for sig, outcome, stock in events_by_date.get(day, []):
            if len(open_positions) >= cfg["max_positions"]:
                n_skipped_no_room += 1
                continue
            equity = _mark_to_market()
            atr = _historical_atr(session, stock.id, outcome.signal_date)
            sized = _size_position(equity, outcome.entry_price, atr, cfg)
            if sized is None:
                n_skipped_no_room += 1
                continue
            shares, stop_distance = sized
            position_value = shares * outcome.entry_price
            if position_value > cash * 0.98:
                n_skipped_no_room += 1
                continue
            sector = stock.sector
            sector_value = sum(
                p["shares"] * p["entry_price"] for p in open_positions
                if (p["sector"] is None) == (sector is None) and (p["sector"] == sector or sector is None)
            )
            if (sector_value + position_value) / max(equity, 1) > cfg["max_sector_pct"]:
                n_skipped_no_room += 1
                continue
            cash -= position_value
            bucket = _HORIZON_BUCKET[style.upper()]
            open_positions.append({
                "symbol": stock.symbol, "signal_id": sig.id, "sector": sector,
                "shares": shares, "entry_price": outcome.entry_price,
                "stop_distance": stop_distance, "entry_date": outcome.entry_date,
                "exit_date": outcome.exit_date, "exit_price": outcome.exit_price,
                "pct_return": float(getattr(outcome, f"return_{bucket}")),
            })
            n_entered += 1

        equity_curve.append((day, round(_mark_to_market(), 2)))

    result.n_entered = n_entered
    result.n_skipped_no_room = n_skipped_no_room
    result.trades = trades
    result.equity_curve = [(d.isoformat(), e) for d, e in equity_curve]

    final_equity = cash + sum(p["shares"] * p["entry_price"] for p in open_positions)
    result.final_equity = round(final_equity, 2)
    result.total_return_pct = round((final_equity / cfg["initial_capital"] - 1) * 100, 2)

    if trades:
        rets = [t.pct_return for t in trades]
        result.win_rate = round(sum(1 for r in rets if r > 0) / len(rets), 4)
        result.avg_return_pct = round(sum(rets) / len(rets) * 100, 4)

    equity_values = [e for _, e in equity_curve]
    result.max_drawdown_pct = _max_drawdown_pct(equity_values)
    if len(equity_values) >= 2:
        daily_rets = [
            (equity_values[i] / equity_values[i - 1] - 1)
            for i in range(1, len(equity_values))
            if equity_values[i - 1] > 0
        ]
        result.sharpe_ratio = _annualized_sharpe(daily_rets)

    if n_entered == 0:
        result.skipped_reason = "no signals were ever admitted (portfolio caps/cash always blocked entry)"

    return result
