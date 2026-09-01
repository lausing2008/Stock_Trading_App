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

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Market, Signal, SignalHorizon, SignalOutcome, SignalType, Stock

from .gate_harness import (
    _HORIZON_BUCKET,
    _HORIZON_RESOLUTION_LAG_DAYS,
    _MIN_PROMOTION_EV_LIFT_PCT,
    _MIN_PROMOTION_LIFT_SD_RATIO,
    _historical_atr,
)

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
    # T234-CONFIG-UNJUSTIFIED-THRESHOLDS: paper_trading_engine.py's real circuit breaker
    # (PA-D2) computes peak = max(equity-curve peak, CURRENT equity) and suspends new entries
    # once (peak - current) / peak exceeds this fraction — reproduced here as a genuine gate
    # inside the day-stepping loop below, not a post-hoc filter on the finished equity curve
    # (a post-hoc filter can't know which entries the breaker would ACTUALLY have blocked,
    # since blocking an early entry changes every later day's cash/position state too).
    "max_portfolio_drawdown_pct": 0.20,
    # T234-CONFIG-UNJUSTIFIED-THRESHOLDS item #23: mirrors _open_paper_trade()'s real PT-B5
    # aggregate-open-risk check (paper_trading_engine.py) — sum((entry_price - stop) * shares)
    # across every currently-open position, plus this candidate's own not-yet-opened risk
    # contribution, must not exceed this fraction of equity. Same "must be a real, state-
    # dependent gate inside the loop, not a post-hoc filter" reasoning as the drawdown breaker
    # above — blocking one entry changes every later day's open-risk total too.
    "max_open_risk_pct": 0.12,
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
    n_skipped_drawdown_breaker: int = 0  # blocked specifically by the drawdown circuit breaker
    n_skipped_open_risk_cap: int = 0     # blocked specifically by the aggregate open-risk cap
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
    fabricated Sharpe of 0.0 would misleadingly read as "genuinely flat," not "unmeasurable".

    AUD-PORTFOLIOBACKTEST-VAREPS: a bare `std == 0` guard is the same float-noise-explosion
    bug class AUD292-SHARPE-VAREPS already found and fixed elsewhere in this codebase — a
    day-over-day equity[i]/equity[i-1]-1 return series (exactly what this function's own
    caller builds) can produce a `std` that is pure floating-point noise (~1e-17, not exactly
    0.0) during a run of near-identical daily returns, which a bare `== 0` lets through and
    explodes the resulting Sharpe toward an enormous, meaningless value (reproduced directly:
    ~2.4e+14 from a real, non-degenerate 24-step equity-curve fixture). Same _VAR_EPS
    threshold convention as the already-fixed sibling."""
    _VAR_EPS = 1e-9
    if len(daily_returns) < 2:
        return None
    arr = np.array(daily_returns, dtype=float)
    std = arr.std(ddof=1)
    if std <= _VAR_EPS:
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
    n_skipped_drawdown_breaker = 0
    n_skipped_open_risk_cap = 0
    running_peak = cfg["initial_capital"]  # PA-D2: peak = max(curve peak, current equity), updated every day

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
        # 0. Update the running peak BEFORE today's entries/exits are processed — mirrors
        # PA-D2's own "peak = max(equity-curve peak, current equity)" read at the top of each
        # paper_trading_step() cycle, using yesterday's closing equity as "current" for today's
        # gating decision (today's own equity isn't known yet until step 3 below runs).
        running_peak = max(running_peak, _mark_to_market())

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
            equity = _mark_to_market()
            drawdown = (running_peak - equity) / running_peak if running_peak > 0 else 0.0
            if drawdown > cfg["max_portfolio_drawdown_pct"]:
                n_skipped_drawdown_breaker += 1
                continue
            if len(open_positions) >= cfg["max_positions"]:
                n_skipped_no_room += 1
                continue
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
            # T234-CONFIG-UNJUSTIFIED-THRESHOLDS item #23: mirrors _open_paper_trade()'s real
            # PT-B5 aggregate-open-risk check exactly — sum((entry - stop) * shares) across
            # every still-open position (open_positions already reflects today's exits, since
            # step 1 above processed them first), plus this candidate's own new_trade_risk.
            max_open_risk = cfg.get("max_open_risk_pct")
            if max_open_risk and equity > 0:
                open_risk = sum(
                    p["stop_distance"] * p["shares"] for p in open_positions
                )
                new_trade_risk = stop_distance * shares
                if (open_risk + new_trade_risk) / equity > max_open_risk:
                    n_skipped_open_risk_cap += 1
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
    result.n_skipped_drawdown_breaker = n_skipped_drawdown_breaker
    result.n_skipped_open_risk_cap = n_skipped_open_risk_cap
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


# T234-CONFIG-UNJUSTIFIED-THRESHOLDS: max_portfolio_drawdown_pct (0.20) was flagged as one of
# the highest-leverage never-empirically-validated constants in the codebase — "the master
# circuit breaker for the whole portfolio." Unlike gate_harness.py's per-signal sweeps
# (min_entry_score, min_kscore, etc. — filters on WHICH signals get admitted, replayed via
# replay_should_enter()), the drawdown breaker only ever gates NEW ENTRIES once the running
# portfolio is already underwater — testing a candidate value means re-running the WHOLE
# day-stepped simulation with that threshold (a post-hoc filter on an already-finished equity
# curve can't know what the breaker would ACTUALLY have blocked, since blocking one entry
# changes every later day's cash/position state too). This reuses run_portfolio_backtest()
# itself as the "replay" primitive, one full call per candidate, rather than a lighter
# per-signal function — a materially more expensive sweep than gate_harness.py's, but the only
# honest way to test a portfolio-level state-dependent gate.
_DRAWDOWN_SWEEP_CANDIDATES = [0.10, 0.15, 0.20, 0.25, 0.30]


def _drawdown_sweep_resolvable_window_end(window_end: date, style: str) -> date:
    """Same BUG233-BACKTESTHARNESS-EMPTYVALIDATION fix as gate_harness.py's own
    _resolvable_window_end() — pull window_end back by the style's own outcome-resolution lag
    so the validation slice's SignalOutcome rows have actually resolved by the time it's
    replayed, reusing the SAME lag table (not a second, independently-guessed one)."""
    return window_end - timedelta(days=_HORIZON_RESOLUTION_LAG_DAYS.get(style.upper(), 14))


def _passes_return_promotion_margin(
    candidate_total_return_pct: float | None,
    baseline_total_return_pct: float | None,
    combined_trade_returns: list[float],
) -> bool:
    """Reuses the SAME lift-margin discipline gate_harness.py's own _passes_promotion_margin()
    established (BUG233-BACKTESTHARNESS-COINFLIP: a bare "any positive difference" comparison
    is a near-coin-flip at realistic sample sizes) — but on total_return_pct (a portfolio-level
    pct, already comparable in scale to the per-trade pct returns _passes_promotion_margin was
    designed around) with the SD computed directly from the combined candidate+baseline
    trade-level returns, since there's no BacktestResult object here to read a pre-computed SD
    off of. Requires BOTH values to be genuinely measurable, a minimum absolute lift
    (_MIN_PROMOTION_EV_LIFT_PCT), AND that lift to be a meaningful fraction
    (_MIN_PROMOTION_LIFT_SD_RATIO) of the combined trades' own return dispersion — two
    independent guards, not one, so a candidate can clear the absolute floor by a wide margin
    and still correctly fail here if the underlying trades are too dispersed for that lift to
    be distinguishable from noise."""
    if candidate_total_return_pct is None or baseline_total_return_pct is None:
        return False
    lift = candidate_total_return_pct - baseline_total_return_pct
    if lift < _MIN_PROMOTION_EV_LIFT_PCT:
        return False
    if len(combined_trade_returns) < 2:
        return False
    mean = sum(combined_trade_returns) / len(combined_trade_returns)
    variance = sum((r - mean) ** 2 for r in combined_trade_returns) / (len(combined_trade_returns) - 1)
    sd_pct = (variance ** 0.5) * 100
    if sd_pct <= 0:
        return True  # zero dispersion means the lift (already >= the absolute floor) is real
    return lift >= _MIN_PROMOTION_LIFT_SD_RATIO * sd_pct


def sweep_max_portfolio_drawdown_pct(
    session: Session,
    symbols: list[str],
    style: str,
    market: str,
    window_start: date,
    window_end: date,
    candidates: list[float] | None = None,
    base_cfg_overrides: dict | None = None,
) -> dict:
    """Walk-forward search over candidate max_portfolio_drawdown_pct values — same chronological
    70/30 train/validation split and promotion-margin discipline as gate_harness.py's own
    walk_forward_extended_gate()/walk_forward_min_entry_score(), reusing its EXACT promotion
    margin constants (not a second, independently-tuned threshold) so this stays no more
    permissive than the sibling sweeps this repo already trusts.

    Promotion metric is total_return_pct (the module's own headline portfolio stat) — NOT
    max_drawdown_pct alone, since a breaker tuned purely to minimize drawdown trivially wins by
    being maximally strict (fewer entries, less exposure, less drawdown, but also less return);
    the whole point of a circuit breaker is a return/risk TRADE-OFF, so the promotion criterion
    has to weigh the return side, with max_drawdown_pct reported alongside for context on what
    that return was bought/sold for.
    """
    style = style.upper()
    base_cfg = {**_DEFAULT_CFG, **(base_cfg_overrides or {})}
    current_value = base_cfg.get("max_portfolio_drawdown_pct", 0.20)
    candidates = candidates if candidates is not None else sorted(set(_DRAWDOWN_SWEEP_CANDIDATES + [current_value]))

    resolvable_end = _drawdown_sweep_resolvable_window_end(window_end, style)
    if resolvable_end <= window_start:
        return {
            "style": style, "market": market.upper(),
            "skipped_reason": (
                f"window too short to leave any resolvable validation slice after accounting "
                f"for {style}'s {_HORIZON_RESOLUTION_LAG_DAYS.get(style, 14)}-day outcome "
                f"resolution lag (requested window ends {window_end}, resolvable end is "
                f"{resolvable_end}, window starts {window_start})"
            ),
        }

    total_days = (resolvable_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    if val_start > resolvable_end:
        return {
            "style": style, "market": market.upper(),
            "skipped_reason": f"window too short to split ({total_days} resolvable days)",
        }

    baseline_val = run_portfolio_backtest(
        session, symbols, style, market, val_start, resolvable_end,
        cfg_overrides={**base_cfg, "max_portfolio_drawdown_pct": current_value},
    )

    train_results = []
    for cand in candidates:
        cand_result = run_portfolio_backtest(
            session, symbols, style, market, window_start, train_end,
            cfg_overrides={**base_cfg, "max_portfolio_drawdown_pct": cand},
        )
        train_results.append((cand, cand_result))

    best_cand, best_train = None, None
    for cand, res in train_results:
        if res.skipped_reason is not None or res.total_return_pct is None:
            continue
        if best_train is None or res.total_return_pct > best_train.total_return_pct:
            best_cand, best_train = cand, res

    if best_cand is None:
        return {
            "style": style, "market": market.upper(), "param": "max_portfolio_drawdown_pct",
            "current_value": current_value,
            "skipped_reason": "no candidate produced any admitted trades on the train slice",
            "baseline_validation": asdict(baseline_val),
        }

    best_val = run_portfolio_backtest(
        session, symbols, style, market, val_start, resolvable_end,
        cfg_overrides={**base_cfg, "max_portfolio_drawdown_pct": best_cand},
    )

    promoted = False
    if best_val.skipped_reason is None and baseline_val.skipped_reason is None:
        combined_returns = [t.pct_return for t in best_val.trades] + [t.pct_return for t in baseline_val.trades]
        promoted = _passes_return_promotion_margin(
            best_val.total_return_pct, baseline_val.total_return_pct, combined_returns,
        )

    return {
        "style": style, "market": market.upper(), "param": "max_portfolio_drawdown_pct",
        "current_value": current_value,
        "candidate_value": best_cand,
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(resolvable_end)],
        "train_result": asdict(best_train),
        "candidate_validation": asdict(best_val),
        "baseline_validation": asdict(baseline_val),
        "promoted": promoted,
        "note": (
            "promoted=True means the candidate's total_return_pct beat the CURRENT LIVE "
            f"max_portfolio_drawdown_pct's own validation-slice return by at least "
            f"{_MIN_PROMOTION_EV_LIFT_PCT}pp AND by at least {_MIN_PROMOTION_LIFT_SD_RATIO}x "
            "the combined slices' own trade-return dispersion (same margin discipline as "
            "gate_harness.py's BUG233-BACKTESTHARNESS-COINFLIP fix — a bare 'any positive "
            "difference' comparison is a near-coin-flip at realistic sample sizes). Compare "
            "candidate_validation.max_drawdown_pct against baseline_validation.max_drawdown_pct "
            "to see what risk change bought that return — a promoted candidate with materially "
            "WORSE max_drawdown_pct is trading safety for return and should be reviewed by a "
            "human before ever being applied, not auto-applied. This is a research signal, NOT "
            "an automatic config change — see this module's own top-of-file docstring for what "
            "run_portfolio_backtest() does and does not model (no decision-engine/_should_"
            "enter() replay, no aggregate open-risk cap, no correlation cap, no commission/"
            "slippage)."
        ),
    }


# IF-13 (Kelly-consumption half): GET /paper-portfolio/kelly computes quarter-Kelly + a
# recommended_risk_pct from real closed-trade history, but nothing ever consumed it for real
# sizing — risk_per_trade_pct (the actual live sizing input, _DEFAULT_CFG above) stayed a
# static 0.01/0.007 US/HK constant. Wiring Kelly's recommendation directly into real capital
# sizing with no validation would repeat exactly the class of risk this repo's own audit
# history has flagged and fixed elsewhere (AUD283-MLWEIGHT-RATCHET, gate_harness.py's own
# promotion-margin discipline) — an unvalidated parameter directly affecting real capital. This
# reuses the SAME walk-forward sweep machinery as sweep_max_portfolio_drawdown_pct() above,
# swapping the candidate parameter to risk_per_trade_pct, with candidates drawn from Kelly's own
# real recommended_risk_pct bands (1%/2%/3%, see kelly_sizing() in paper_portfolio.py) rather
# than an independently-guessed grid — so a "promoted" result genuinely means "a Kelly-derived
# risk level beat the current live default on held-out data," not just "some arbitrary
# percentage happened to test well."
_RISK_PER_TRADE_SWEEP_CANDIDATES = [0.01, 0.02, 0.03]  # Kelly's own recommended_risk_pct bands


def sweep_risk_per_trade_pct(
    session: Session,
    symbols: list[str],
    style: str,
    market: str,
    window_start: date,
    window_end: date,
    candidates: list[float] | None = None,
    base_cfg_overrides: dict | None = None,
) -> dict:
    """Walk-forward search over candidate risk_per_trade_pct values — same chronological 70/30
    train/validation split and promotion-margin discipline as sweep_max_portfolio_drawdown_pct()
    immediately above (itself matching gate_harness.py's own established convention), reusing
    the EXACT SAME promotion margin constants rather than a second, independently-tuned one.

    Promotion metric is total_return_pct, same reasoning as the drawdown sweep: a risk level
    tuned purely to minimize drawdown trivially wins by sizing every position down to nothing;
    the real trade-off is return-per-unit-of-risk, so max_drawdown_pct is reported alongside for
    context on what a promoted return was bought with, never hidden.
    """
    style = style.upper()
    base_cfg = {**_DEFAULT_CFG, **(base_cfg_overrides or {})}
    current_value = base_cfg.get("risk_per_trade_pct", 0.01)
    candidates = candidates if candidates is not None else sorted(set(_RISK_PER_TRADE_SWEEP_CANDIDATES + [current_value]))

    resolvable_end = _drawdown_sweep_resolvable_window_end(window_end, style)
    if resolvable_end <= window_start:
        return {
            "style": style, "market": market.upper(),
            "skipped_reason": (
                f"window too short to leave any resolvable validation slice after accounting "
                f"for {style}'s {_HORIZON_RESOLUTION_LAG_DAYS.get(style, 14)}-day outcome "
                f"resolution lag (requested window ends {window_end}, resolvable end is "
                f"{resolvable_end}, window starts {window_start})"
            ),
        }

    total_days = (resolvable_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    if val_start > resolvable_end:
        return {
            "style": style, "market": market.upper(),
            "skipped_reason": f"window too short to split ({total_days} resolvable days)",
        }

    baseline_val = run_portfolio_backtest(
        session, symbols, style, market, val_start, resolvable_end,
        cfg_overrides={**base_cfg, "risk_per_trade_pct": current_value},
    )

    train_results = []
    for cand in candidates:
        cand_result = run_portfolio_backtest(
            session, symbols, style, market, window_start, train_end,
            cfg_overrides={**base_cfg, "risk_per_trade_pct": cand},
        )
        train_results.append((cand, cand_result))

    best_cand, best_train = None, None
    for cand, res in train_results:
        if res.skipped_reason is not None or res.total_return_pct is None:
            continue
        if best_train is None or res.total_return_pct > best_train.total_return_pct:
            best_cand, best_train = cand, res

    if best_cand is None:
        return {
            "style": style, "market": market.upper(), "param": "risk_per_trade_pct",
            "current_value": current_value,
            "skipped_reason": "no candidate produced any admitted trades on the train slice",
            "baseline_validation": asdict(baseline_val),
        }

    best_val = run_portfolio_backtest(
        session, symbols, style, market, val_start, resolvable_end,
        cfg_overrides={**base_cfg, "risk_per_trade_pct": best_cand},
    )

    promoted = False
    if best_val.skipped_reason is None and baseline_val.skipped_reason is None:
        combined_returns = [t.pct_return for t in best_val.trades] + [t.pct_return for t in baseline_val.trades]
        promoted = _passes_return_promotion_margin(
            best_val.total_return_pct, baseline_val.total_return_pct, combined_returns,
        )

    return {
        "style": style, "market": market.upper(), "param": "risk_per_trade_pct",
        "current_value": current_value,
        "candidate_value": best_cand,
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(resolvable_end)],
        "train_result": asdict(best_train),
        "candidate_validation": asdict(best_val),
        "baseline_validation": asdict(baseline_val),
        "promoted": promoted,
        "note": (
            "promoted=True means a Kelly-derived risk_per_trade_pct candidate beat the CURRENT "
            f"LIVE value's own validation-slice total_return_pct by at least "
            f"{_MIN_PROMOTION_EV_LIFT_PCT}pp AND by at least {_MIN_PROMOTION_LIFT_SD_RATIO}x "
            "the combined slices' own trade-return dispersion — the same margin discipline as "
            "sweep_max_portfolio_drawdown_pct() and gate_harness.py's own promotion gate (a bare "
            "'any positive difference' comparison is a near-coin-flip at realistic sample "
            "sizes). Compare candidate_validation.max_drawdown_pct against baseline_validation."
            "max_drawdown_pct to see what risk change bought that return — a promoted larger "
            "risk_per_trade_pct with materially worse max_drawdown_pct is trading safety for "
            "return and should be reviewed by a human before being applied by hand, not treated "
            "as an automatic config change. This is a research signal, NOT an automatic capital-"
            "sizing change — see this module's own top-of-file docstring for what run_portfolio_"
            "backtest() does and does not model."
        ),
    }


# T234-CONFIG-UNJUSTIFIED-THRESHOLDS item #23: max_open_risk_pct (0.12, PT-B5 in
# paper_trading_engine.py) — "the aggregate open-risk cap across all positions," never
# empirically validated, same class of portfolio-wide circuit breaker as
# max_portfolio_drawdown_pct above (now already swept). Reuses the identical walk-forward
# machinery — chronological 70/30 split, EXACT SAME promotion-margin constants, same
# total_return_pct promotion metric with max_drawdown_pct reported alongside for the
# risk/return trade-off this is fundamentally about.
_OPEN_RISK_SWEEP_CANDIDATES = [0.06, 0.08, 0.12, 0.16, 0.20]


def sweep_max_open_risk_pct(
    session: Session,
    symbols: list[str],
    style: str,
    market: str,
    window_start: date,
    window_end: date,
    candidates: list[float] | None = None,
    base_cfg_overrides: dict | None = None,
) -> dict:
    """Walk-forward search over candidate max_open_risk_pct values — same chronological 70/30
    train/validation split and promotion-margin discipline as sweep_max_portfolio_drawdown_pct()/
    sweep_risk_per_trade_pct() above, reusing the EXACT SAME promotion margin constants (not a
    third, independently-tuned threshold).

    Promotion metric is total_return_pct, same reasoning as the sibling sweeps: a cap tuned
    purely to minimize open risk trivially wins by admitting almost nothing; the real trade-off
    is return bought per unit of aggregate risk carried, so max_drawdown_pct is reported
    alongside for context on what a promoted return cost in risk, never hidden.
    """
    style = style.upper()
    base_cfg = {**_DEFAULT_CFG, **(base_cfg_overrides or {})}
    current_value = base_cfg.get("max_open_risk_pct", 0.12)
    candidates = candidates if candidates is not None else sorted(set(_OPEN_RISK_SWEEP_CANDIDATES + [current_value]))

    resolvable_end = _drawdown_sweep_resolvable_window_end(window_end, style)
    if resolvable_end <= window_start:
        return {
            "style": style, "market": market.upper(),
            "skipped_reason": (
                f"window too short to leave any resolvable validation slice after accounting "
                f"for {style}'s {_HORIZON_RESOLUTION_LAG_DAYS.get(style, 14)}-day outcome "
                f"resolution lag (requested window ends {window_end}, resolvable end is "
                f"{resolvable_end}, window starts {window_start})"
            ),
        }

    total_days = (resolvable_end - window_start).days
    split_days = max(1, int(total_days * 0.7))
    train_end = window_start + timedelta(days=split_days)
    val_start = train_end + timedelta(days=1)

    if val_start > resolvable_end:
        return {
            "style": style, "market": market.upper(),
            "skipped_reason": f"window too short to split ({total_days} resolvable days)",
        }

    baseline_val = run_portfolio_backtest(
        session, symbols, style, market, val_start, resolvable_end,
        cfg_overrides={**base_cfg, "max_open_risk_pct": current_value},
    )

    train_results = []
    for cand in candidates:
        cand_result = run_portfolio_backtest(
            session, symbols, style, market, window_start, train_end,
            cfg_overrides={**base_cfg, "max_open_risk_pct": cand},
        )
        train_results.append((cand, cand_result))

    best_cand, best_train = None, None
    for cand, res in train_results:
        if res.skipped_reason is not None or res.total_return_pct is None:
            continue
        if best_train is None or res.total_return_pct > best_train.total_return_pct:
            best_cand, best_train = cand, res

    if best_cand is None:
        return {
            "style": style, "market": market.upper(), "param": "max_open_risk_pct",
            "current_value": current_value,
            "skipped_reason": "no candidate produced any admitted trades on the train slice",
            "baseline_validation": asdict(baseline_val),
        }

    best_val = run_portfolio_backtest(
        session, symbols, style, market, val_start, resolvable_end,
        cfg_overrides={**base_cfg, "max_open_risk_pct": best_cand},
    )

    promoted = False
    if best_val.skipped_reason is None and baseline_val.skipped_reason is None:
        combined_returns = [t.pct_return for t in best_val.trades] + [t.pct_return for t in baseline_val.trades]
        promoted = _passes_return_promotion_margin(
            best_val.total_return_pct, baseline_val.total_return_pct, combined_returns,
        )

    return {
        "style": style, "market": market.upper(), "param": "max_open_risk_pct",
        "current_value": current_value,
        "candidate_value": best_cand,
        "train_window": [str(window_start), str(train_end)],
        "validation_window": [str(val_start), str(resolvable_end)],
        "train_result": asdict(best_train),
        "candidate_validation": asdict(best_val),
        "baseline_validation": asdict(baseline_val),
        "promoted": promoted,
        "note": (
            "promoted=True means the candidate's total_return_pct beat the CURRENT LIVE "
            f"max_open_risk_pct's own validation-slice return by at least "
            f"{_MIN_PROMOTION_EV_LIFT_PCT}pp AND by at least {_MIN_PROMOTION_LIFT_SD_RATIO}x "
            "the combined slices' own trade-return dispersion (same margin discipline as "
            "gate_harness.py's BUG233-BACKTESTHARNESS-COINFLIP fix). Compare candidate_"
            "validation.max_drawdown_pct against baseline_validation.max_drawdown_pct to see "
            "what risk change bought that return — a promoted candidate with materially WORSE "
            "max_drawdown_pct is trading safety for return and should be reviewed by a human "
            "before ever being applied, not auto-applied. This is a research signal, NOT an "
            "automatic config change — see this module's own top-of-file docstring for what "
            "run_portfolio_backtest() does and does not model (in particular: the open-risk "
            "computation here uses each position's own fixed stop_distance from entry-time "
            "sizing, since this simulator has no trailing-stop mechanism or intraday live-price "
            "series — the real live PT-B5 check uses the CURRENT live price minus the CURRENT, "
            "possibly-trailed stop, which can differ from this approximation once a real "
            "position's stop has moved)."
        ),
    }
