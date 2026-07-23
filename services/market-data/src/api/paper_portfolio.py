"""WF-2 Paper Portfolio API — read-only views + admin controls."""
import json
import math
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from db import PaperEquityCurve, PaperPortfolio, PaperTrade, SessionLocal, Signal, SignalHorizon, get_session
from db.models import User, Stock, Price, TimeFrame
from .auth import get_current_user, get_admin_user
from common.config import get_settings
from common.logging import get_logger

log = get_logger("paper_portfolio_api")

router = APIRouter(prefix="/paper-portfolio", tags=["paper-portfolio"])

_settings = get_settings()
_TRADE_PARAMS_PATH = Path(_settings.model_dir) / "trade_params.json"

# Defaults if no tuned params exist yet
_FALLBACK_PARAMS: dict[str, dict] = {
    "SHORT":  {"stop_pct": 0.970, "tp_pct": 1.05, "max_hold_days": 10},
    "SWING":  {"stop_pct": 0.945, "tp_pct": 1.12, "max_hold_days": 20},
    "GROWTH": {"stop_pct": 0.900, "tp_pct": 1.25, "max_hold_days": 60},
    "LONG":   {"stop_pct": 0.880, "tp_pct": 1.35, "max_hold_days": 90},
}

_tune_lock = threading.Lock()
_tune_running: dict[str, bool] = {}  # style → is running

_ENTRY_WEIGHTS_PATH = Path(_settings.model_dir) / "entry_weights.json"
_calibration_lock = threading.Lock()
_calibration_running = False


_MIN_SHARPE_DAYS = 20  # annualizing < 20 days produces meaningless Sharpe/Calmar
_MIN_CAGR_DAYS = 20    # same floor as Sharpe/Sortino/Calmar — see the cagr_pct comment below for why


def _portfolio_risk_metrics(curve_rows: list) -> dict:
    """Compute Sharpe, Sortino, CAGR, max drawdown, Calmar from equity curve rows (ordered by date)."""
    valid_rows = [r for r in curve_rows if r.equity and r.equity > 0]
    equities = [r.equity for r in valid_rows]
    data_days = len(equities)

    if data_days < 2:
        return {"sharpe": None, "sortino": None, "cagr_pct": None,
                "max_drawdown_pct": None, "calmar": None,
                "data_days": data_days, "insufficient_data": True}

    # Max drawdown — valid at any sample size
    peak = equities[0]
    max_dd = 0.0
    for e in equities:
        if e > peak:
            peak = e
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = round(max_dd * 100, 2)

    # CAGR — use actual calendar days when available, else trading-day estimate
    e0, ef = equities[0], equities[-1]
    try:
        cal_days = max((valid_rows[-1].date - valid_rows[0].date).days, 1)
        years = cal_days / 365.25
    except Exception:
        cal_days = data_days
        years = max(data_days, 1) / 252
    # Bug found 2026-07-05 (user report): CAGR had no minimum-period floor, unlike Sharpe/
    # Sortino/Calmar (_MIN_SHARPE_DAYS below). Annualizing any gain over a short window
    # explodes combinatorially — e.g. a 5.9x gain over 16 days annualizes to ~3.5e19%,
    # not a data bug, just what (1+r)^(365/days) does at small `days`. Gate it the same way.
    cagr_pct = (
        round(((ef / e0) ** (1.0 / years) - 1) * 100, 2)
        if e0 > 0 and years > 0 and cal_days >= _MIN_CAGR_DAYS
        else None
    )

    # Sharpe, Sortino, and Calmar require enough data to annualize meaningfully
    if data_days < _MIN_SHARPE_DAYS:
        return {"sharpe": None, "sortino": None, "cagr_pct": cagr_pct,
                "max_drawdown_pct": max_dd_pct, "calmar": None,
                "data_days": data_days, "insufficient_data": True}

    # Daily returns
    daily_returns = [(equities[i] / equities[i - 1]) - 1 for i in range(1, len(equities))]

    n = len(daily_returns)
    mean_r = sum(daily_returns) / n
    variance = sum((r - mean_r) ** 2 for r in daily_returns) / max(n - 1, 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0

    annualised_return = mean_r * 252
    annualised_vol = std_r * math.sqrt(252)
    risk_free = 0.05
    sharpe = round((annualised_return - risk_free) / annualised_vol, 2) if annualised_vol > 0 else None

    # Sortino — downside deviation (returns below 0)
    downside_sq = [min(r, 0.0) ** 2 for r in daily_returns]
    downside_dev = math.sqrt(sum(downside_sq) / max(n, 1)) * math.sqrt(252)
    sortino = round((annualised_return - risk_free) / downside_dev, 2) if downside_dev > 0 else None

    # Calmar = CAGR / max drawdown (use geometric compound rate, not arithmetic mean * 252)
    calmar = round((cagr_pct / 100) / max_dd, 2) if max_dd > 0 and cagr_pct is not None else None

    return {
        "sharpe": sharpe,
        "sortino": sortino,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": max_dd_pct,
        "calmar": calmar,
        "data_days": data_days,
        "insufficient_data": False,
    }


def _compute_alpha_beta(curve_rows: list) -> dict:
    """Compute beta and annualised Jensen's alpha vs SPY from equity curve rows."""
    paired = [
        (r.equity, r.spy_close) for r in curve_rows
        if r.equity and r.spy_close and r.equity > 0 and r.spy_close > 0
    ]
    if len(paired) < 20:
        return {"alpha": None, "beta": None, "info_ratio": None}

    equities = [p[0] for p in paired]
    spys     = [p[1] for p in paired]
    n = len(equities) - 1
    if n < 2:
        return {"alpha": None, "beta": None, "info_ratio": None}

    p_rets = [(equities[i + 1] / equities[i]) - 1 for i in range(n)]
    s_rets = [(spys[i + 1]     / spys[i])     - 1 for i in range(n)]

    mean_p = sum(p_rets) / n
    mean_s = sum(s_rets) / n

    cov   = sum((p_rets[i] - mean_p) * (s_rets[i] - mean_s) for i in range(n)) / max(n - 1, 1)
    var_s = sum((s_rets[i] - mean_s) ** 2                    for i in range(n)) / max(n - 1, 1)

    beta = round(cov / var_s, 3) if var_s > 1e-10 else None

    if beta is None:
        return {"alpha": None, "beta": None, "info_ratio": None}

    # Jensen's alpha: annualised excess return above what beta predicts
    alpha = round((mean_p - beta * mean_s) * 252 * 100, 2)

    # Information ratio: annualised active return / tracking error (benchmark-relative, not beta-adjusted)
    active = [p_rets[i] - s_rets[i] for i in range(n)]
    mean_active = sum(active) / n
    var_active  = sum((r - mean_active) ** 2 for r in active) / max(n - 1, 1)
    te = math.sqrt(var_active * 252) if var_active > 0 else 0
    info_ratio = round((mean_active * 252) / te, 2) if te > 0 else None

    return {"alpha": alpha, "beta": round(beta, 2), "info_ratio": info_ratio}


def _get_portfolio(session: Session, portfolio_id: int | None = None) -> PaperPortfolio:
    if portfolio_id is not None:
        p = session.execute(
            select(PaperPortfolio).where(
                PaperPortfolio.id == portfolio_id,
                PaperPortfolio.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} not found")
        return p
    p = session.execute(
        select(PaperPortfolio).where(PaperPortfolio.is_active.is_(True)).order_by(PaperPortfolio.id).limit(1)
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="No active paper portfolio found")
    return p


# ── Summary ───────────────────────────────────────────────────────────────────

@router.get("/summary")
def get_summary(
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    p = _get_portfolio(session, portfolio_id)

    open_trades = session.execute(
        select(PaperTrade).where(PaperTrade.portfolio_id == p.id, PaperTrade.stage == "open")
    ).scalars().all()

    closed_trades = session.execute(
        select(PaperTrade).where(PaperTrade.portfolio_id == p.id, PaperTrade.stage == "closed")
    ).scalars().all()

    open_value = sum((t.current_price or t.entry_price) * t.shares for t in open_trades)
    equity = p.current_cash + open_value

    wins = [t for t in closed_trades if (t.pnl or 0) > 0]
    losses = [t for t in closed_trades if (t.pnl or 0) < 0]
    win_rate = round(len(wins) / max(len(closed_trades), 1) * 100, 1)
    avg_win  = round(sum(t.pct_return or 0 for t in wins) / max(len(wins), 1), 2)
    avg_loss = round(sum(t.pct_return or 0 for t in losses) / max(len(losses), 1), 2)
    total_realized = round(sum(t.pnl or 0 for t in closed_trades), 2)

    gross_profit = sum(t.pnl for t in wins if t.pnl)
    gross_loss   = abs(sum(t.pnl for t in losses if t.pnl))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

    hold_days_list = [t.hold_days for t in closed_trades if t.hold_days and t.hold_days > 0]
    avg_hold_days = round(sum(hold_days_list) / len(hold_days_list), 1) if hold_days_list else None

    expectancy = round(
        (win_rate / 100) * avg_win + (1 - win_rate / 100) * avg_loss, 2
    ) if closed_trades else None
    total_unrealized = round(
        sum(((t.current_price or t.entry_price) - t.entry_price) * t.shares for t in open_trades), 2
    )

    all_curve = session.execute(
        select(PaperEquityCurve)
        .where(PaperEquityCurve.portfolio_id == p.id)
        .order_by(PaperEquityCurve.date)
    ).scalars().all()

    risk   = _portfolio_risk_metrics(all_curve)
    ab     = _compute_alpha_beta(all_curve)
    latest_curve = all_curve[-1] if all_curve else None

    # Benchmark outperformance: compare portfolio total return to SPY/QQQ since day 1
    total_return_pct = round((equity / p.initial_capital - 1) * 100, 2)
    outperformance_vs_spy: float | None = None
    outperformance_vs_qqq: float | None = None
    outperformance_vs_hsi: float | None = None
    if all_curve:
        first = all_curve[0]
        if first.spy_close and latest_curve and latest_curve.spy_close:
            spy_return = round((latest_curve.spy_close / first.spy_close - 1) * 100, 2)
            outperformance_vs_spy = round(total_return_pct - spy_return, 2)
        if first.qqq_close and latest_curve and latest_curve.qqq_close:
            qqq_return = round((latest_curve.qqq_close / first.qqq_close - 1) * 100, 2)
            outperformance_vs_qqq = round(total_return_pct - qqq_return, 2)
        if first.hsi_close and latest_curve and latest_curve.hsi_close:
            hsi_return = round((latest_curve.hsi_close / first.hsi_close - 1) * 100, 2)
            outperformance_vs_hsi = round(total_return_pct - hsi_return, 2)

    exit_breakdown: dict[str, int] = {}
    for t in closed_trades:
        key = t.exit_reason or "unknown"
        exit_breakdown[key] = exit_breakdown.get(key, 0) + 1

    return {
        "portfolio_id": p.id,
        "name": p.name,
        "trading_style": p.config.get("trading_style", "GROWTH"),
        "initial_capital": p.initial_capital,
        "current_equity": round(equity, 2),
        "current_cash": round(p.current_cash, 2),
        "open_positions_value": round(open_value, 2),
        "total_return_pct": total_return_pct,
        "total_realized_pnl": total_realized,
        "total_unrealized_pnl": total_unrealized,
        "open_positions": len(open_trades),
        "closed_trades": len(closed_trades),
        "win_rate_pct": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": round(abs(avg_loss), 2) if avg_loss else avg_loss,  # return positive magnitude (Kelly endpoint also returns positive)
        "profit_factor": profit_factor,
        "avg_hold_days": avg_hold_days,
        "expectancy_pct": expectancy,
        "sharpe": risk["sharpe"],
        "sortino": risk.get("sortino"),
        "cagr_pct": risk.get("cagr_pct"),
        "max_drawdown_pct": risk["max_drawdown_pct"],
        "calmar": risk["calmar"],
        "data_days": risk.get("data_days", 0),
        "insufficient_data": risk.get("insufficient_data", False),
        "alpha": ab["alpha"],
        "beta": ab["beta"],
        "info_ratio": ab["info_ratio"],
        "outperformance_vs_spy": outperformance_vs_spy,
        "outperformance_vs_qqq": outperformance_vs_qqq,
        "outperformance_vs_hsi": outperformance_vs_hsi,
        "spy_close": latest_curve.spy_close if latest_curve else None,
        "qqq_close": latest_curve.qqq_close if latest_curve else None,
        # Regime engine — current market state (written by engine each cycle)
        "regime_state": p.config.get("regime_state"),
        "regime_vix": p.config.get("regime_vix"),
        "regime_spy": p.config.get("regime_spy"),
        "regime_notes": p.config.get("regime_notes", []),
        "config": p.config,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "exit_breakdown": exit_breakdown,
    }


# ── Open positions ────────────────────────────────────────────────────────────

@router.get("/positions")
def get_positions(
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    p = _get_portfolio(session, portfolio_id)
    trades = session.execute(
        select(PaperTrade)
        .where(PaperTrade.portfolio_id == p.id, PaperTrade.stage == "open")
        .order_by(desc(PaperTrade.entry_date))
    ).scalars().all()

    # Fetch latest signal for each open position using the portfolio's trading style as horizon
    portfolio_style = p.config.get("trading_style", "SWING").upper()
    try:
        sig_horizon = SignalHorizon(portfolio_style)
    except ValueError:
        sig_horizon = SignalHorizon.SWING
    symbols = list({t.symbol for t in trades})
    current_signals: dict[str, str] = {}
    if symbols:
        sig_subq = (
            select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
            .where(Signal.horizon == sig_horizon)
            .group_by(Signal.stock_id)
            .subquery()
        )
        sig_rows = session.execute(
            select(Stock.symbol, Signal.signal)
            .join(sig_subq, Stock.id == sig_subq.c.stock_id)
            .join(Signal, (Signal.stock_id == sig_subq.c.stock_id) & (Signal.ts == sig_subq.c.max_ts) & (Signal.horizon == sig_horizon))
            .where(Stock.symbol.in_(symbols))
        ).all()
        current_signals = {sym: sig.value if hasattr(sig, "value") else str(sig) for sym, sig in sig_rows}

    return [
        {
            "id": t.id,
            "symbol": t.symbol,
            "trading_style": t.trading_style,
            "entry_date": t.entry_date.isoformat() if t.entry_date else None,
            "entry_price": t.entry_price,
            "current_price": t.current_price,
            "shares": round(t.shares, 4),
            "position_value": round((t.current_price or t.entry_price) * t.shares, 2),
            "stop_loss": t.stop_loss,
            "current_stop": t.current_stop,
            "take_profit": t.take_profit,
            "highest_price": t.highest_price,
            "hold_days": int(np.busday_count(t.entry_date, date.today() + timedelta(days=1))) if t.entry_date else t.hold_days,
            "unrealized_pnl": round(((t.current_price or t.entry_price) - t.entry_price) * t.shares, 2),
            "unrealized_pct": round(((t.current_price or t.entry_price) / t.entry_price - 1) * 100, 2),
            "rr_ratio_at_entry": t.rr_ratio_at_entry,
            "entry_score": t.entry_score,
            "confidence_at_entry": t.confidence_at_entry,
            "kscore_at_entry": t.kscore_at_entry,
            "market_regime_at_entry": t.market_regime_at_entry,
            "sector": t.sector,
            "decision_notes": t.entry_decision_notes or [],
            "entry_reasons": t.entry_reasons or {},
            "current_signal": current_signals.get(t.symbol),
        }
        for t in trades
    ]


# ── Manual exit ───────────────────────────────────────────────────────────────

@router.post("/trades/{trade_id}/exit")
def manual_exit_trade(
    trade_id: int,
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Force-close an open paper trade at the current live price."""
    import yfinance as yf
    p = _get_portfolio(session, portfolio_id)
    trade = session.get(PaperTrade, trade_id)
    if not trade or trade.portfolio_id != p.id:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade.stage != "open":
        raise HTTPException(status_code=400, detail="Trade is already closed")

    # Fetch live price
    exit_price: float | None = None
    try:
        info = yf.Ticker(trade.symbol).fast_info
        exit_price = float(info.last_price)
    except Exception:
        pass
    if exit_price is None or exit_price <= 0:
        exit_price = trade.current_price or trade.entry_price

    cfg = p.config or {}
    slippage  = cfg.get("exit_slippage_pct", 0.001)
    commission = cfg.get("commission_per_share", 0.0)
    exit_p    = round(exit_price * (1 - slippage), 4)
    exit_value = round(exit_p * trade.shares, 2)
    exit_commission = round(commission * trade.shares, 4)

    pnl      = round((exit_p - trade.entry_price) * trade.shares, 2)
    pnl_pct  = round((exit_p / trade.entry_price - 1) * 100, 2)

    now = datetime.utcnow()
    trade.stage        = "closed"
    trade.hold_days    = int(np.busday_count(trade.entry_date, now.date() + timedelta(days=1))) if trade.entry_date else 0
    trade.exit_time    = now
    trade.exit_price   = exit_p
    trade.exit_reason  = "manual_exit"
    trade.pnl           = pnl
    trade.pct_return    = pnl_pct
    trade.current_price = exit_p

    # Credit cash back
    p.current_cash = max(0.0, round(p.current_cash + exit_value - exit_commission, 2))

    session.commit()
    log.info("paper.manual_exit", symbol=trade.symbol, exit_price=exit_p,
             pnl=pnl, pnl_pct=pnl_pct, trade_id=trade_id)
    return {
        "symbol": trade.symbol,
        "exit_price": exit_p,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "cash_after": round(p.current_cash, 2),
    }


# ── Closed trades ─────────────────────────────────────────────────────────────

@router.get("/trades")
def get_trades(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    symbol: str | None = Query(None),
    exit_reason: str | None = Query(None),
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    p = _get_portfolio(session, portfolio_id)
    q = (
        select(PaperTrade)
        .where(PaperTrade.portfolio_id == p.id, PaperTrade.stage == "closed")
    )
    if symbol:
        q = q.where(PaperTrade.symbol == symbol.upper())
    if exit_reason:
        q = q.where(PaperTrade.exit_reason == exit_reason)

    total = session.execute(select(func.count()).select_from(q.subquery())).scalar() or 0
    trades = session.execute(
        q.order_by(desc(PaperTrade.exit_time)).offset((page - 1) * limit).limit(limit)
    ).scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
        "items": [
            {
                "id": t.id,
                "symbol": t.symbol,
                "trading_style": t.trading_style,
                "entry_date": t.entry_date.isoformat() if t.entry_date else None,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "shares": round(t.shares, 4),
                "pnl": t.pnl,
                "pct_return": t.pct_return,
                "hold_days": t.hold_days,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "rr_ratio_at_entry": t.rr_ratio_at_entry,
                "entry_score": t.entry_score,
                "confidence_at_entry": t.confidence_at_entry,
                "kscore_at_entry": t.kscore_at_entry,
            }
            for t in trades
        ],
    }


# ── T258-TRADE-POSTMORTEM: per-closed-trade plan-vs-actual review ────────────
# Mechanical only (no LLM) — every field here is derived directly from PaperTrade's own
# stored plan (entry_price/stop_loss/take_profit/entry_time) and actuals (exit_price/
# exit_time/exit_reason), plus a single Price range-query for max-favorable-excursion. This
# was the fit-gap analysis's own explicit v1 scope: the mechanical fields are what the
# self-improvement/calibration-loop discipline documented elsewhere in this repo says to
# trust first, before adding an LLM-generated prose layer on top in a later version.

_MECHANICAL_EXIT_REASONS = {"stop_hit", "breakeven_stop", "target_reached", "time_stop"}


@router.get("/trades/{trade_id}/postmortem")
def get_trade_postmortem(
    trade_id: int,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Plan-vs-actual review for one closed trade — entry/exit adherence to the stored plan,
    exit-reason classification (mechanical vs discretionary), and max favorable excursion
    (the best price reached during the hold vs. the actual exit price)."""
    from ..services.paper_trading_engine import _STYLE_OVERRIDES

    trade = session.get(PaperTrade, trade_id)
    if trade is None:
        raise HTTPException(404, "Trade not found")
    if trade.stage != "closed":
        raise HTTPException(400, "Post-mortem is only available for closed trades")

    expected_hold_days = _STYLE_OVERRIDES.get(trade.trading_style, {}).get("max_hold_days", 60)
    exit_reason = trade.exit_reason or "unknown"
    is_mechanical_exit = exit_reason in _MECHANICAL_EXIT_REASONS

    entry_slippage_pct = None
    if trade.entry_price:
        # Plan-vs-actual entry price is the SAME value today (paper trading fills at the
        # signal's own live_price at entry time — there is no separate "planned entry" that
        # can diverge from the fill) — this field is a placeholder for the real-broker case
        # (T230-PORTFOLIO-BROKER-SYNC), where a synced position's actual fill CAN differ from
        # the paper-simulated entry_price. Left at 0.0 for pure-paper trades rather than
        # omitted, so the API response shape doesn't change between broker and non-broker
        # trades — a future broker-fill-vs-plan comparison can populate this without a
        # breaking schema change.
        entry_slippage_pct = 0.0

    exit_vs_stop_pct = None
    if trade.exit_price and trade.stop_loss:
        exit_vs_stop_pct = round((trade.exit_price - trade.stop_loss) / trade.stop_loss * 100, 2)
    exit_vs_target_pct = None
    if trade.exit_price and trade.take_profit:
        exit_vs_target_pct = round((trade.exit_price - trade.take_profit) / trade.take_profit * 100, 2)

    hold_days_vs_expected = None
    if trade.hold_days is not None:
        hold_days_vs_expected = trade.hold_days - expected_hold_days

    # Max favorable excursion: highest price reached during the hold window, from the SAME
    # daily Price table already used elsewhere in this file — a single indexed range query,
    # not a new data source.
    mfe_price = None
    mfe_vs_exit_pct = None
    if trade.stock_id is not None and trade.entry_time and trade.exit_time:
        highs = session.execute(
            select(func.max(Price.high)).where(
                Price.stock_id == trade.stock_id,
                Price.timeframe == TimeFrame.D1,
                Price.ts >= trade.entry_time,
                Price.ts <= trade.exit_time,
            )
        ).scalar()
        if highs is not None:
            mfe_price = float(highs)
            if trade.exit_price:
                mfe_vs_exit_pct = round((mfe_price - trade.exit_price) / trade.exit_price * 100, 2)

    return {
        "trade_id": trade.id,
        "symbol": trade.symbol,
        "trading_style": trade.trading_style,
        "exit_reason": exit_reason,
        "is_mechanical_exit": is_mechanical_exit,
        "plan_adherence": {
            "entry_slippage_pct": entry_slippage_pct,
            "exit_vs_stop_pct": exit_vs_stop_pct,
            "exit_vs_target_pct": exit_vs_target_pct,
        },
        "hold_window": {
            "actual_hold_days": trade.hold_days,
            "expected_hold_days": expected_hold_days,
            "hold_days_vs_expected": hold_days_vs_expected,
        },
        "max_favorable_excursion": {
            "price": mfe_price,
            "vs_exit_pct": mfe_vs_exit_pct,
        },
        "pnl": trade.pnl,
        "pct_return": trade.pct_return,
    }


# ── Trades CSV export ─────────────────────────────────────────────────────────

@router.get("/trades/csv")
def get_trades_csv(
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    import csv, io
    from fastapi.responses import StreamingResponse
    p = _get_portfolio(session, portfolio_id)
    trades = session.execute(
        select(PaperTrade)
        .where(PaperTrade.portfolio_id == p.id, PaperTrade.stage == "closed")
        .order_by(desc(PaperTrade.exit_time))
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "symbol", "style", "entry_date", "exit_date", "entry_price", "exit_price",
        "shares", "pnl", "pct_return", "hold_days", "exit_reason",
        "stop_loss", "take_profit", "rr_ratio", "entry_score", "confidence",
    ])
    for t in trades:
        writer.writerow([
            t.symbol, t.trading_style,
            t.entry_date.isoformat() if t.entry_date else "",
            t.exit_time.date().isoformat() if t.exit_time else "",
            t.entry_price, t.exit_price,
            round(t.shares, 4), round(t.pnl or 0, 2),
            round(t.pct_return or 0, 4), t.hold_days, t.exit_reason,
            t.stop_loss, t.take_profit, t.rr_ratio_at_entry,
            t.entry_score, t.confidence_at_entry,
        ])
    buf.seek(0)
    filename = f"paper_trades_portfolio_{p.id}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Equity curve ──────────────────────────────────────────────────────────────

@router.get("/equity-curve")
def get_equity_curve(
    days: int = Query(180, ge=7, le=730),
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    p = _get_portfolio(session, portfolio_id)
    cutoff = date.today() - timedelta(days=days)
    rows = session.execute(
        select(PaperEquityCurve)
        .where(PaperEquityCurve.portfolio_id == p.id, PaperEquityCurve.date >= cutoff)
        .order_by(PaperEquityCurve.date)
    ).scalars().all()

    return [
        {
            "date": r.date.isoformat(),
            "equity": round(r.equity, 2),
            "cash": round(r.cash, 2),
            "open_positions_value": round(r.open_positions_value, 2),
            "open_positions_count": r.open_positions_count,
            "spy_close": r.spy_close,
            "qqq_close": r.qqq_close,
            "hsi_close": r.hsi_close,
            "market_regime": r.market_regime,  # PT-A2: for regime shading overlay
        }
        for r in rows
    ]


# ── Decision log ──────────────────────────────────────────────────────────────

@router.get("/decisions")
def get_decisions(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    symbol: str | None = Query(None),
    days_back: int = Query(30, ge=1, le=180),
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Return entry decisions (all trades, open + closed, as decision log)."""
    p = _get_portfolio(session, portfolio_id)
    cutoff = datetime.utcnow() - timedelta(days=days_back)

    q = select(PaperTrade).where(
        PaperTrade.portfolio_id == p.id,
        PaperTrade.entry_time >= cutoff,
    )
    if symbol:
        q = q.where(PaperTrade.symbol == symbol.upper())

    total = session.execute(select(func.count()).select_from(q.subquery())).scalar() or 0
    trades = session.execute(
        q.order_by(desc(PaperTrade.entry_time)).offset((page - 1) * limit).limit(limit)
    ).scalars().all()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, -(-total // limit)),
        "items": [
            {
                "id": t.id,
                "symbol": t.symbol,
                "decision": "ENTER",
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "entry_price": t.entry_price,
                "entry_score": t.entry_score,
                "decision_notes": t.entry_decision_notes or [],
                "confidence_at_entry": t.confidence_at_entry,
                "kscore_at_entry": t.kscore_at_entry,
                "rr_ratio_at_entry": t.rr_ratio_at_entry,
                "market_regime_at_entry": t.market_regime_at_entry,
                "stage": t.stage,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "entry_reasons": t.entry_reasons or {},
                "exit_reasons": t.exit_reasons or {},
                "hold_days": t.hold_days,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "shares": t.shares,
                "pnl": t.pnl,
                "pct_return": t.pct_return,
            }
            for t in trades
        ],
    }


# ── Admin: configure ──────────────────────────────────────────────────────────

@router.post("/configure")
def configure_portfolio(
    body: dict,
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Merge body keys into the portfolio config (admin only).

    All percentage-based params expect decimal fractions (0.01 = 1%, NOT 1 = 1%).
    The endpoint validates ranges and returns 400 if a value is out of bounds.
    """
    p = _get_portfolio(session, portfolio_id)
    # T232-CONFIGGAP: 12 fields the frontend ConfigPanel exposes were missing from this set,
    # so saving them silently no-op'd (updated dict below filters on membership with no error
    # returned to the user) — e.g. "Max Market Pos" appeared to save but the value never
    # persisted. All are real, actively-read _DEFAULT_CONFIG keys in paper_trading_engine.py.
    allowed_keys = {
        "max_positions", "max_sector_pct", "risk_per_trade_pct", "max_position_pct",
        "min_confidence", "min_kscore", "min_rr_ratio", "min_entry_score",
        "max_hold_days", "trail_atr_mult", "trail_trigger_pct", "breakeven_trigger_pct",
        "wait_exit_days", "enabled", "paused",
        "max_loss_per_trade_pct", "max_portfolio_drawdown_pct", "max_daily_loss_pct",
        "max_open_risk_pct", "hold_stall_max_gain", "stop_cooldown_hours",
        # T232-CONFIGGAP additions:
        "max_market_positions", "max_sector_positions", "max_entries_per_day",
        "max_entry_gap_pct", "hold_stall_days", "max_consecutive_losses",
        "max_weekly_loss_pct", "max_open_exposure_pct", "equity_floor_pct",
        "min_ta_score", "min_volume_z", "partial_tp_pct",
        # T203-LLMWIRE: llm_scoring_enabled existed in decision-engine's llm_scorer.py since
        # T203 but was never threaded from portfolio config into _call_decision_engine()'s
        # config_overrides — this was a built-but-dormant feature with no way to turn it on
        # for any real portfolio. See paper_trading_engine.py's config_overrides dict.
        "llm_scoring_enabled", "llm_score_weight", "llm_model",
    }
    # PT-H1: Validate decimal fraction params — reject values that look like % integers
    # (e.g. risk_per_trade_pct=1 meaning "1%" but engine expects 0.01).
    _RANGE_CHECKS: dict[str, tuple[float, float, str]] = {
        "risk_per_trade_pct":   (0.001, 0.05,  "Enter as decimal fraction (e.g. 0.01 for 1%). Max 5%."),
        "max_position_pct":     (0.01,  0.30,  "Enter as decimal fraction (e.g. 0.10 for 10%). Max 30%."),
        "max_loss_per_trade_pct":(0.005, 0.10, "Enter as decimal fraction (e.g. 0.02 for 2%). Max 10%."),
        "max_sector_pct":       (0.05,  0.60,  "Enter as decimal fraction (e.g. 0.30 for 30%). Range 5–60%."),
        "max_portfolio_drawdown_pct":(0.05, 0.50, "Enter as decimal fraction (e.g. 0.20 for 20%)."),
        "max_daily_loss_pct":   (0.005, 0.15,  "Enter as decimal fraction (e.g. 0.04 for 4%)."),
        "trail_trigger_pct":    (0.01,  0.30,  "Enter as decimal fraction (e.g. 0.05 for 5%)."),
        "breakeven_trigger_pct":(0.005, 0.20,  "Enter as decimal fraction (e.g. 0.03 for 3%)."),
        "max_open_risk_pct":    (0.02,  0.50,  "Enter as decimal fraction (e.g. 0.12 for 12%)."),
        "hold_stall_max_gain":  (0.01,  0.30,  "Enter as decimal fraction (e.g. 0.05 for 5%)."),
        "max_entry_gap_pct":    (0.01,  0.20,  "Enter as decimal fraction (e.g. 0.04 for 4%)."),
        "max_weekly_loss_pct":  (0.01,  0.30,  "Enter as decimal fraction (e.g. 0.08 for 8%)."),
        "max_open_exposure_pct":(0.05,  1.00,  "Enter as decimal fraction (e.g. 0.40 for 40%)."),
        "equity_floor_pct":     (0.10,  1.00,  "Enter as decimal fraction (e.g. 0.80 for 80%)."),
        "partial_tp_pct":       (0.01,  0.50,  "Enter as decimal fraction (e.g. 0.10 for 10%)."),
    }
    # A count-based cap set to 0 doesn't mean "block everything" — every gate that reads these
    # keys checks `if x and x > 0:` before enforcing, so 0 (falsy) silently DISABLES the gate
    # instead of blocking all entries. Found 2026-07-03: HK SWING Portfolio had
    # max_entries_per_day=0 from an unvalidated Config Panel edit, which the API accepted with
    # no error — the gate simply never fired rather than trading being blocked, but either
    # outcome is a config mistake, not a value anyone should be able to save unintentionally.
    _MIN_COUNT_CHECKS: dict[str, int] = {
        "max_positions": 1, "max_market_positions": 1, "max_sector_positions": 1,
        "max_entries_per_day": 1, "max_consecutive_losses": 1, "max_hold_days": 1,
        "hold_stall_days": 1, "wait_exit_days": 1,
    }
    # T203-LLMWIRE: caps how many points a single LLM verdict can add/subtract from the
    # overall entry score — scores in this codebase run in a small (roughly 0-10) integer
    # range (see min_score_for_regime()), so an unbounded weight would let one LLM call
    # dominate every other scored dimension combined.
    _RANGE_CHECKS_INT: dict[str, tuple[int, int, str]] = {
        "llm_score_weight": (1, 5, "How many points the LLM verdict adds/subtracts. Keep small relative to the ~0-10 total entry score."),
    }
    errors: list[str] = []
    for key, val in body.items():
        if key in _RANGE_CHECKS and val is not None:
            lo, hi, hint = _RANGE_CHECKS[key]
            try:
                fval = float(val)
            except (TypeError, ValueError):
                errors.append(f"{key}: expected a number")
                continue
            if not (lo <= fval <= hi):
                errors.append(f"{key}={fval}: out of valid range [{lo}, {hi}]. {hint}")
        if key in _MIN_COUNT_CHECKS and val is not None:
            try:
                ival = float(val)
            except (TypeError, ValueError):
                errors.append(f"{key}: expected a number")
                continue
            if ival < _MIN_COUNT_CHECKS[key]:
                errors.append(f"{key}={ival}: must be at least {_MIN_COUNT_CHECKS[key]} (0 silently disables this gate rather than blocking entries — use the paused flag or an override endpoint to actually stop trading)")
        if key in _RANGE_CHECKS_INT and val is not None:
            lo, hi, hint = _RANGE_CHECKS_INT[key]
            try:
                ival = int(val)
            except (TypeError, ValueError):
                errors.append(f"{key}: expected an integer")
                continue
            if not (lo <= ival <= hi):
                errors.append(f"{key}={ival}: out of valid range [{lo}, {hi}]. {hint}")
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    updated = {k: v for k, v in body.items() if k in allowed_keys and v is not None}
    # T232-CONFIGGAP: surface any key the caller sent that isn't recognized, instead of
    # silently dropping it — this is exactly how "Max Market Pos save does nothing" went
    # unnoticed until a user reported it.
    unknown = sorted(k for k in body if k not in allowed_keys and body[k] is not None)
    old_vals = {k: p.config.get(k) for k in updated}
    p.config = {**p.config, **updated}
    session.commit()
    # PT-C10: log config changes so the audit trail is visible in container logs
    if updated:
        log.info("paper.config_updated",
                 changed={k: {"from": old_vals[k], "to": updated[k]} for k in updated})
    if unknown:
        log.warning("paper.config_update_unknown_keys", keys=unknown)
    return {"ok": True, "config": p.config, "ignored_keys": unknown}


# ── Admin: time-boxed regime_risk_off_gate override ────────────────────────────

@router.post("/risk-off-override")
def set_risk_off_override(
    hours: float = Query(..., gt=0, le=24, description="Override duration in hours (max 24)"),
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Temporarily disable the risk_off entry gate for this portfolio.

    T232-HKOVERRIDE: a deliberate, self-expiring override — NOT a permanent config flip.
    While active, regime_risk_off_gate reverts to pre-T226-A behaviour (50% size + score-5
    requirement instead of a full block). Expires automatically; no cron job needed since
    the gate itself checks the expiry timestamp on every evaluation (see
    _regime_risk_off_override_active in paper_trading_engine.py).
    """
    p = _get_portfolio(session, portfolio_id)
    until = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
    p.config = {**p.config, "regime_risk_off_override_until": until}
    session.commit()
    log.warning("paper.risk_off_override_set", portfolio=p.name, hours=hours, until=until)
    return {"ok": True, "override_until": until}


@router.delete("/risk-off-override")
def clear_risk_off_override(
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Cancel an active risk_off gate override before it expires."""
    p = _get_portfolio(session, portfolio_id)
    cfg = dict(p.config)
    cfg.pop("regime_risk_off_override_until", None)
    p.config = cfg
    session.commit()
    log.info("paper.risk_off_override_cleared", portfolio=p.name)
    return {"ok": True}


# ── Admin: reset ──────────────────────────────────────────────────────────────

@router.post("/reset")
def reset_portfolio(
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Close all open trades at current_price and reset cash to initial_capital."""
    p = _get_portfolio(session, portfolio_id)
    open_trades = session.execute(
        select(PaperTrade).where(PaperTrade.portfolio_id == p.id, PaperTrade.stage == "open")
    ).scalars().all()

    now = datetime.utcnow()
    for t in open_trades:
        exit_price = t.current_price or t.entry_price
        t.stage = "closed"
        t.hold_days = int(np.busday_count(t.entry_date, now.date() + timedelta(days=1))) if t.entry_date else 0
        t.exit_time = now
        t.exit_price = exit_price
        t.exit_reason = "manual_reset"
        t.exit_reasons = {"message": "Admin reset — all positions force-closed"}
        t.pnl = round((exit_price - t.entry_price) * t.shares, 2)
        t.pct_return = round((exit_price / t.entry_price - 1) * 100, 4)

    # Snapshot final equity before reset so the equity curve captures the ending value.
    final_equity = round(
        p.current_cash + sum(t.exit_price * t.shares for t in open_trades), 2
    )
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    session.execute(
        pg_insert(PaperEquityCurve)
        .values(portfolio_id=p.id, date=datetime.utcnow().date(), equity=final_equity)
        .on_conflict_do_update(
            index_elements=["portfolio_id", "date"],
            set_={"equity": final_equity},
        )
    )

    p.current_cash = p.initial_capital
    session.commit()

    return {
        "ok": True,
        "positions_closed": len(open_trades),
        "cash_reset_to": p.initial_capital,
    }


# ── Admin: set capital ────────────────────────────────────────────────────────

@router.post("/capital")
def set_capital(
    body: dict,
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Update initial_capital and/or current_cash (admin only).

    Body: { initial_capital?: number, current_cash?: number }
    Setting current_cash lets you add/withdraw cash without a full reset.
    """
    p = _get_portfolio(session, portfolio_id)

    new_initial = body.get("initial_capital")
    new_cash = body.get("current_cash")

    if new_initial is not None:
        val = float(new_initial)
        if val <= 0:
            raise HTTPException(status_code=400, detail="initial_capital must be > 0")
        p.initial_capital = round(val, 2)

    if new_cash is not None:
        val = float(new_cash)
        if val < 0:
            raise HTTPException(status_code=400, detail="current_cash cannot be negative")
        p.current_cash = round(val, 2)

    session.commit()
    return {
        "ok": True,
        "initial_capital": p.initial_capital,
        "current_cash": p.current_cash,
    }


# ── Admin: engine state ───────────────────────────────────────────────────────

@router.post("/engine")
def set_engine_state(
    body: dict,
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Set engine state: { state: 'running' | 'paused' | 'stopped' }

    running — monitor + scan for new entries (full operation)
    paused  — monitor open positions only, no new entries
    stopped — do nothing (engine completely halted)
    """
    state = body.get("state", "").lower()
    if state not in ("running", "paused", "stopped"):
        raise HTTPException(status_code=400, detail="state must be 'running', 'paused', or 'stopped'")

    p = _get_portfolio(session, portfolio_id)
    if state == "running":
        p.config = {**p.config, "enabled": True, "paused": False}
    elif state == "paused":
        p.config = {**p.config, "enabled": True, "paused": True}
    else:  # stopped
        p.config = {**p.config, "enabled": False, "paused": False}

    session.commit()
    return {"ok": True, "state": state, "config": p.config}


@router.get("/attribution")
def get_attribution(
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """PT-A1: Aggregate closed trades by entry_score, confidence, regime, and R:R bands.

    Returns win_rate, avg_return, profit_factor, and count for each bucket so
    traders can identify which entry profiles actually perform.
    """
    p = _get_portfolio(session, portfolio_id)
    trades = session.execute(
        select(PaperTrade).where(
            PaperTrade.portfolio_id == p.id,
            PaperTrade.stage == "closed",
            PaperTrade.pnl.is_not(None),
        )
    ).scalars().all()

    if not trades:
        return {"message": "No closed trades yet", "by_score": [], "by_confidence": [], "by_regime": [], "by_rr": []}

    def _stats(bucket: list) -> dict:
        if not bucket:
            return {"count": 0, "win_rate": None, "avg_return": None, "profit_factor": None}
        wins = [t for t in bucket if (t.pnl or 0) > 0]
        losses = [t for t in bucket if (t.pnl or 0) < 0]
        returns = [t.pct_return for t in bucket if t.pct_return is not None]
        gross_win = sum(t.pnl for t in wins if t.pnl)
        gross_loss = abs(sum(t.pnl for t in losses if t.pnl))
        return {
            "count": len(bucket),
            "win_rate": round(len(wins) / len(bucket) * 100, 1),
            "avg_return": round(sum(returns) / len(returns), 2) if returns else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        }

    # By entry score band
    score_bands = [
        ("≤2", lambda t: (t.entry_score or 0) <= 2),
        ("3", lambda t: (t.entry_score or 0) == 3),
        ("4", lambda t: (t.entry_score or 0) == 4),
        ("5+", lambda t: (t.entry_score or 0) >= 5),
    ]
    by_score = [{"band": label, **_stats([t for t in trades if fn(t)])} for label, fn in score_bands]

    # By confidence band
    conf_bands = [
        ("<55%", lambda t: (t.confidence_at_entry or 0) < 55),
        ("55–65%", lambda t: 55 <= (t.confidence_at_entry or 0) < 65),
        ("65–75%", lambda t: 65 <= (t.confidence_at_entry or 0) < 75),
        ("75%+", lambda t: (t.confidence_at_entry or 0) >= 75),
    ]
    by_confidence = [{"band": label, **_stats([t for t in trades if fn(t)])} for label, fn in conf_bands]

    # By market regime at entry
    regimes = sorted({t.market_regime_at_entry for t in trades if t.market_regime_at_entry})
    by_regime = [{"band": r, **_stats([t for t in trades if t.market_regime_at_entry == r])} for r in regimes]
    unknown_regime = [t for t in trades if not t.market_regime_at_entry]
    if unknown_regime:
        by_regime.append({"band": "unknown", **_stats(unknown_regime)})

    # By R:R band
    rr_bands = [
        ("<1.5", lambda t: (t.rr_ratio_at_entry or 0) < 1.5),
        ("1.5–2.5", lambda t: 1.5 <= (t.rr_ratio_at_entry or 0) < 2.5),
        ("2.5+", lambda t: (t.rr_ratio_at_entry or 0) >= 2.5),
    ]
    by_rr = [{"band": label, **_stats([t for t in trades if fn(t)])} for label, fn in rr_bands]

    # Best entry profile (score + confidence combo with ≥10 trades)
    best_profile = None
    best_wr = -1.0
    for s_label, s_fn in score_bands:
        for c_label, c_fn in conf_bands:
            bucket = [t for t in trades if s_fn(t) and c_fn(t)]
            if len(bucket) >= 10:
                wr = sum(1 for t in bucket if (t.pnl or 0) > 0) / len(bucket)
                if wr > best_wr:
                    best_wr = wr
                    best_profile = {"score_band": s_label, "conf_band": c_label, "win_rate": round(wr * 100, 1), "count": len(bucket)}

    return {
        "total_trades": len(trades),
        "by_score": by_score,
        "by_confidence": by_confidence,
        "by_regime": by_regime,
        "by_rr": by_rr,
        "best_profile": best_profile,
    }


# ── Multi-portfolio: list ─────────────────────────────────────────────────────

@router.patch("/{portfolio_id}/active")
def toggle_portfolio_active(
    portfolio_id: int,
    body: dict,
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Enable or disable a portfolio. Disabled portfolios are skipped by paper_trading_step."""
    portfolio = session.get(PaperPortfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    active = bool(body.get("active", True))
    portfolio.is_active = active
    session.commit()
    log.info("paper.portfolio_toggled", portfolio_id=portfolio_id, name=portfolio.name, is_active=active)
    return {"ok": True, "id": portfolio_id, "is_active": active}


@router.get("/list")
def list_portfolios(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Lightweight list of all portfolios (active and inactive) with summary stats."""
    portfolios = session.execute(
        select(PaperPortfolio).order_by(PaperPortfolio.id)
    ).scalars().all()

    # Read latest entry gate block reason for each portfolio (set by _write_gate_block in engine).
    import json as _pf_json
    _gate_blocks: dict[int, dict] = {}
    # T232-WHYNOTRADE: per-candidate skip tally (set by _write_no_entry_summary) — covers the
    # case where no portfolio-level gate fired but every candidate failed its own check.
    _no_entry_summaries: dict[int, dict] = {}
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _pf_r = _get_pool_redis()
        for p in portfolios:
            raw = _pf_r.get(f"paper:gate_block:{p.id}")
            if raw:
                try:
                    _gate_blocks[p.id] = _pf_json.loads(raw)
                except Exception:
                    pass
            raw_ne = _pf_r.get(f"paper:no_entry_summary:{p.id}")
            if raw_ne:
                try:
                    _no_entry_summaries[p.id] = _pf_json.loads(raw_ne)
                except Exception:
                    pass
    except Exception:
        pass  # Redis unavailable — omit gate_block from response

    result = []
    for p in portfolios:
        open_trades = session.execute(
            select(PaperTrade).where(PaperTrade.portfolio_id == p.id, PaperTrade.stage == "open")
        ).scalars().all()
        closed_trades = session.execute(
            select(PaperTrade).where(PaperTrade.portfolio_id == p.id, PaperTrade.stage == "closed")
        ).scalars().all()

        open_value = sum((t.current_price or t.entry_price) * t.shares for t in open_trades)
        equity = p.current_cash + open_value

        wins = [t for t in closed_trades if (t.pnl or 0) > 0]
        win_rate = round(len(wins) / max(len(closed_trades), 1) * 100, 1)

        all_curve = session.execute(
            select(PaperEquityCurve).where(PaperEquityCurve.portfolio_id == p.id).order_by(PaperEquityCurve.date)
        ).scalars().all()
        risk = _portfolio_risk_metrics(all_curve)

        result.append({
            "id": p.id,
            "name": p.name,
            "trading_style": p.config.get("trading_style", "GROWTH"),
            "market": p.config.get("market", "US"),
            "current_equity": round(equity, 2),
            "initial_capital": p.initial_capital,
            "total_return_pct": round((equity / p.initial_capital - 1) * 100, 2),
            "win_rate_pct": win_rate,
            "open_positions": len(open_trades),
            "closed_trades": len(closed_trades),
            "sharpe": risk["sharpe"],
            "sortino": risk.get("sortino"),
            "cagr_pct": risk.get("cagr_pct"),
            "max_drawdown_pct": risk["max_drawdown_pct"],
            "is_active": p.is_active,
            "is_running": p.is_active and p.config.get("enabled", True) and not p.config.get("paused", False),
            "is_paused": p.is_active and p.config.get("enabled", True) and bool(p.config.get("paused", False)),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "entry_gate_block": _gate_blocks.get(p.id),  # {gate, reason, ts} or None
            "no_entry_summary": _no_entry_summaries.get(p.id),  # {candidates_seen, top_reasons, ts} or None
        })

    return result


# ── Multi-portfolio: create ───────────────────────────────────────────────────

@router.post("/run-step")
def run_paper_trading_step(
    enforce_market_hours: bool = Query(True, description="Set false to test outside market hours"),
    _: User = Depends(get_admin_user),
) -> dict:
    """PT-H5: Manually trigger paper_trading_step() for testing and debugging.

    Useful on weekends or holidays when the scheduler does not fire.
    Rate-limited to one call per minute to avoid hammering yfinance.
    Set enforce_market_hours=false to run outside 9:30–16:00 ET (for testing only).
    """
    import time
    from src.services.paper_trading_engine import paper_trading_step, _DEFAULT_CONFIG
    from db import PaperPortfolio, SessionLocal
    from sqlalchemy import select

    _last_run_key = "_run_step_last_called"
    now_ts = time.time()
    last = getattr(run_paper_trading_step, _last_run_key, 0.0)
    if now_ts - last < 60:
        raise HTTPException(status_code=429, detail="run-step rate limit: wait 60s between calls")
    setattr(run_paper_trading_step, _last_run_key, now_ts)

    if not enforce_market_hours:
        # Temporarily patch the market hours check to always return True
        import src.services.paper_trading_engine as _eng
        _orig = _eng._is_market_hours
        _eng._is_market_hours = lambda *args: True
        try:
            paper_trading_step()
        finally:
            _eng._is_market_hours = _orig
    else:
        paper_trading_step()

    # Return a snapshot of current portfolio state after the step
    with SessionLocal() as session:
        portfolios = session.execute(
            select(PaperPortfolio).where(PaperPortfolio.is_active.is_(True))
        ).scalars().all()
        summary = [
            {
                "id": p.id,
                "name": p.name,
                "current_cash": round(p.current_cash, 2),
                "regime_state": p.config.get("regime_state"),
                "enabled": p.config.get("enabled"),
                "paused": p.config.get("paused"),
            }
            for p in portfolios
        ]

    log.info("paper.run_step_manual", portfolios=len(summary),
             enforce_market_hours=enforce_market_hours)
    return {"ok": True, "portfolios": summary}


@router.post("/create")
def create_portfolio(
    body: dict,
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Create a new paper portfolio (admin only).

    Config is seeded from _DEFAULT_CONFIG + style overrides so all safety params
    (risk_per_trade_pct=0.01, max_position_pct=0.10 etc.) are correct by default.
    """
    from src.services.paper_trading_engine import _DEFAULT_CONFIG, _STYLE_OVERRIDES
    name = str(body.get("name", "Paper Portfolio")).strip() or "Paper Portfolio"
    style = str(body.get("trading_style", "GROWTH")).upper()
    market = str(body.get("market", "US")).upper()
    initial_capital = float(body.get("initial_capital", 100_000))

    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be > 0")
    if style not in ("SWING", "GROWTH", "LONG", "SHORT"):
        raise HTTPException(status_code=400, detail="trading_style must be SWING, GROWTH, LONG, or SHORT")
    if market not in ("US", "HK"):
        raise HTTPException(status_code=400, detail="market must be US or HK")

    # Optional broker connection to link at creation time
    broker_connection_id: int | None = None
    raw_broker_id = body.get("broker_connection_id")
    if raw_broker_id:
        try:
            from db.models import BrokerConnection
            conn = session.get(BrokerConnection, int(raw_broker_id))
            if conn and conn.is_authorized:
                broker_connection_id = conn.id
        except Exception:
            pass

    # PT-H1: Seed full config from engine defaults so new portfolios are always correct
    cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {}), "trading_style": style, "market": market}
    p = PaperPortfolio(
        name=name,
        initial_capital=initial_capital,
        current_cash=initial_capital,
        config=cfg,
        is_active=True,
        broker_connection_id=broker_connection_id,
    )
    session.add(p)
    session.commit()
    session.refresh(p)

    log.info("paper.portfolio_created", portfolio_id=p.id, name=name, style=style,
             capital=initial_capital, broker_connection_id=broker_connection_id)
    return {"ok": True, "portfolio_id": p.id, "name": p.name,
            "broker_connection_id": broker_connection_id}


# ── Multi-portfolio: compare equity curves ────────────────────────────────────

@router.get("/compare")
def compare_portfolios(
    days: int = Query(180, ge=7, le=730),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Return equity curves for all active portfolios for the comparison overlay chart."""
    portfolios = session.execute(
        select(PaperPortfolio).where(PaperPortfolio.is_active.is_(True)).order_by(PaperPortfolio.id)
    ).scalars().all()

    cutoff = date.today() - timedelta(days=days)
    result = []
    for p in portfolios:
        rows = session.execute(
            select(PaperEquityCurve)
            .where(PaperEquityCurve.portfolio_id == p.id, PaperEquityCurve.date >= cutoff)
            .order_by(PaperEquityCurve.date)
        ).scalars().all()
        first_equity = rows[0].equity if rows and rows[0].equity and rows[0].equity > 0 else None
        result.append({
            "portfolio_id": p.id,
            "name": p.name,
            "trading_style": p.config.get("trading_style", "GROWTH"),
            "initial_capital": p.initial_capital,
            "curve": [
                {
                    "date": r.date.isoformat(),
                    "equity": round(r.equity, 2),
                    "indexed": round(r.equity / first_equity * 100, 4) if first_equity and r.equity else None,
                    "spy_close": r.spy_close,
                    "market_regime": r.market_regime,
                }
                for r in rows
            ],
        })
    return result


# ── AL-4: Trade parameter optimisation (Optuna) ───────────────────────────────

def _load_trade_params() -> dict:
    """Load tuned params from disk, fall back to hardcoded defaults."""
    try:
        if _TRADE_PARAMS_PATH.exists():
            return json.loads(_TRADE_PARAMS_PATH.read_text())
    except Exception:
        pass
    return {}


def _simulate_trade_sharpe(
    trades: list,
    price_map: dict,  # symbol → sorted list of (date, close)
    stop_pct: float,
    tp_pct: float,
    max_hold_days: int,
) -> float | None:
    """Simulate exit outcomes for closed trades using given params. Returns annualised Sharpe."""
    returns = []
    for t in trades:
        prices = price_map.get(t.symbol, [])
        if not prices:
            continue
        entry_date = t.entry_date
        entry_price = t.entry_price
        stop_level = entry_price * stop_pct
        tp_level   = entry_price * tp_pct
        exit_date  = getattr(t, "exit_date", None)  # actual close date; prevents lookahead
        # Walk forward from entry_date
        exit_return: float | None = None
        days_held = 0
        for d, close in prices:
            if d < entry_date:
                continue
            if d == entry_date:
                continue  # skip entry day itself
            # Never simulate past the actual close date — prices beyond exit_date are future
            # data relative to when the trade was open, introducing lookahead bias.
            if exit_date and d > exit_date:
                break
            days_held += 1
            if close <= stop_level:
                exit_return = (stop_level / entry_price) - 1
                break
            if close >= tp_level:
                exit_return = (tp_level / entry_price) - 1
                break
            if days_held >= max_hold_days:
                exit_return = (close / entry_price) - 1
                break
        if exit_return is None and prices:
            # Use last available price
            exit_return = (prices[-1][1] / entry_price) - 1
        if exit_return is not None:
            returns.append(exit_return)

    if len(returns) < 5:
        return None

    n = len(returns)
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0
    if std_r == 0:
        return None
    # Rough annualisation: assume mean hold = max_hold_days/2 trading days
    ann_factor = math.sqrt(252 / max(max_hold_days / 2, 1))
    return (mean_r / std_r) * ann_factor


def _run_optuna_for_style(style: str, n_trials: int, portfolio_id: int | None = None) -> dict:
    """Run Optuna to tune stop_pct, tp_pct, max_hold_days for one style.

    Uses closed paper trades of the given style (filtered to portfolio_id when provided).
    Runs inline — call from a background thread.
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        return {"error": "optuna not installed in market-data — run pip install optuna"}

    from db import SessionLocal

    with SessionLocal() as session:
        q = select(PaperTrade).where(
            PaperTrade.stage == "closed",
            PaperTrade.trading_style == style,
            PaperTrade.entry_price.is_not(None),
            PaperTrade.entry_date.is_not(None),
        )
        if portfolio_id is not None:
            q = q.where(PaperTrade.portfolio_id == portfolio_id)
        trades = session.execute(q).scalars().all()

        if len(trades) < 10:
            return {"error": f"Not enough closed {style} trades ({len(trades)}); need ≥ 10"}

        # Pre-fetch price history for all unique symbols
        symbols = list({t.symbol for t in trades})
        max_hold = 120  # enough for any style
        cutoff = min(t.entry_date for t in trades) - timedelta(days=1)
        price_map: dict[str, list] = {}
        for sym in symbols:
            stock = session.execute(
                select(Stock).where(Stock.symbol == sym)
            ).scalar_one_or_none()
            if not stock:
                continue
            rows = session.execute(
                select(Price.ts, Price.close)
                .where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1, Price.ts >= cutoff)
                .order_by(Price.ts)
            ).all()
            price_map[sym] = [(r.ts.date(), r.close) for r in rows]

        # Snapshot trades list (detach from session)
        trade_snapshots = [(t.symbol, t.entry_date, t.entry_price,
                        t.exit_time.date() if t.exit_time else None) for t in trades]

    class _TradeProxy:
        def __init__(self, symbol, entry_date, entry_price, exit_date=None):
            self.symbol = symbol
            self.entry_date = entry_date
            self.entry_price = entry_price
            self.exit_date = exit_date  # actual close date; caps lookahead in simulation

    trade_objs = [_TradeProxy(s, d, p, ex) for s, d, p, ex in trade_snapshots]

    fallback = _FALLBACK_PARAMS.get(style, _FALLBACK_PARAMS["SWING"])

    def objective(trial: "optuna.Trial") -> float:
        stop_pct     = trial.suggest_float("stop_pct",     0.88, 0.98)
        tp_pct       = trial.suggest_float("tp_pct",       1.04, 1.40)
        max_hold_days = trial.suggest_int("max_hold_days",  5,    90)
        sharpe = _simulate_trade_sharpe(trade_objs, price_map, stop_pct, tp_pct, max_hold_days)
        return sharpe if sharpe is not None else -999.0

    study = optuna.create_study(direction="maximize")
    # Seed with current fallback params so search starts near a known good point
    study.enqueue_trial({
        "stop_pct": fallback["stop_pct"],
        "tp_pct": fallback["tp_pct"],
        "max_hold_days": fallback["max_hold_days"],
    })
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best = study.best_params
    best_sharpe = round(study.best_value, 3)
    return {
        "style": style,
        "n_trials": n_trials,
        "n_trades": len(trade_objs),
        "best_stop_pct": round(best["stop_pct"], 4),
        "best_tp_pct": round(best["tp_pct"], 4),
        "best_max_hold_days": int(best["max_hold_days"]),
        "best_sharpe": best_sharpe,
        "tuned_at": datetime.utcnow().isoformat(),
    }


def _tune_and_save(style: str, n_trials: int, portfolio_id: int | None = None) -> None:
    """Background task: run Optuna for style, merge results into trade_params.json."""
    try:
        result = _run_optuna_for_style(style, n_trials, portfolio_id=portfolio_id)
        if "error" not in result:
            current = _load_trade_params()
            current[style] = result
            _TRADE_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _TRADE_PARAMS_PATH.write_text(json.dumps(current, indent=2))
            log.info("paper.tune_params.saved", style=style, sharpe=result.get("best_sharpe"))
        else:
            log.warning("paper.tune_params.failed", style=style, error=result["error"])
    except Exception as exc:
        log.exception("paper.tune_params.exception", style=style, exc=str(exc))
    finally:
        _tune_running.pop(style, None)


@router.get("/trade-params")
def get_trade_params(
    _: User = Depends(get_current_user),
) -> dict:
    """Return current tuned trade parameters per style (or fallback defaults if not yet tuned)."""
    saved = _load_trade_params()
    result = {}
    for style, fallback in _FALLBACK_PARAMS.items():
        if style in saved:
            result[style] = {
                **saved[style],
                "is_tuned": True,
                "is_running": _tune_running.get(style, False),
            }
        else:
            result[style] = {
                **fallback,
                "is_tuned": False,
                "is_running": _tune_running.get(style, False),
                "note": "Using default params — run Optuna to tune",
            }
    return result


@router.post("/tune-params")
def tune_trade_params(
    background_tasks: BackgroundTasks,
    style: str = Query("SWING"),
    n_trials: int = Query(80, ge=20, le=300),
    portfolio_id: int | None = Query(None),
    _: User = Depends(get_admin_user),
) -> dict:
    """Start Optuna tuning for stop_pct / tp_pct / max_hold_days for one trading style.

    Runs in the background. Poll GET /trade-params to see when is_running=False.
    When portfolio_id is provided, uses only that portfolio's closed trades.
    """
    style = style.upper()
    if style not in _FALLBACK_PARAMS:
        raise HTTPException(400, f"style must be one of: {list(_FALLBACK_PARAMS)}")
    with _tune_lock:
        if _tune_running.get(style):
            return {"status": "already_running", "style": style}
        _tune_running[style] = True
    background_tasks.add_task(_tune_and_save, style, n_trials, portfolio_id)
    return {"status": "started", "style": style, "n_trials": n_trials, "portfolio_id": portfolio_id}


# ── PT-3: Entry score calibration — logistic regression on closed paper trades ──

_MIN_CALIBRATION_TRADES = 100


def calibrate_entry_weights() -> dict:
    """Fit logistic regression on closed paper trades to learn entry factor weights.

    Features: rr_ratio_at_entry, confidence_at_entry, entry_score, kscore_at_entry
    Target: pnl > 0 (win = 1, loss = 0)

    Returns a dict with weights and metadata, or {"error": ...} if insufficient data.
    Saves to entry_weights.json only if the fit beats the current fallback rule
    (score >= min_entry_score) on a held-out validation slice — see AUD232-001.

    AUD232-001: previously fit on the FULL closed-trade dataset with no held-out
    validation at all — the same class of problem (parameter calibration from historical
    trade/outcome data, feeding back into live decision-making) that signal-engine's
    routes.py hardened against in T232-OC3/T234-ML-WEIGHT-NO-VALIDATION-GATE/
    T234-SIG-INSAMPLE-GATE-TUNING, all three of which exist specifically because an
    unvalidated in-sample fit was found to apply upward-biased flukes as if they were real
    signal. Now uses the same 70/30 chronological split + EV-lift-over-baseline gate.
    """
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        return {"error": "scikit-learn not installed in market-data"}

    from ..services.paper_trading_engine import _DEFAULT_CONFIG

    # AUD-CALIBRATION-SCORESCALE: T232-DL-DUALSCORER-DEBT's 2026-07-17 fix added 3 new
    # scoring layers to _should_enter() (pre-regime warning, regime-as-score, K-Score ±1),
    # shifting entry_score's scale/distribution for every trade scored from that date
    # forward. entry_score is persisted verbatim and this fit previously mixed pre- and
    # post-change trades under one w_score coefficient with no distinction — a real, if
    # bounded and self-correcting (next fit after enough post-change trades accumulate),
    # calibration-drift risk. Exclude pre-change trades so every fit going forward trains on
    # a single, internally-consistent score scale. This cutoff is retired once _MIN_CALIBRATION_TRADES
    # worth of post-change trades exist on their own (the WHERE clause naturally excludes
    # nothing extra once all trades in the table postdate the cutoff).
    _SCORE_SCALE_CUTOFF = date(2026, 7, 17)

    with SessionLocal() as session:
        rows = session.execute(
            select(
                PaperTrade.rr_ratio_at_entry,
                PaperTrade.confidence_at_entry,
                PaperTrade.entry_score,
                PaperTrade.kscore_at_entry,
                PaperTrade.pnl,
                PaperTrade.entry_date,
            ).where(
                PaperTrade.stage == "closed",
                PaperTrade.pnl.is_not(None),
                PaperTrade.rr_ratio_at_entry.is_not(None),
                PaperTrade.confidence_at_entry.is_not(None),
                PaperTrade.entry_score.is_not(None),
                PaperTrade.entry_date >= _SCORE_SCALE_CUTOFF,
            ).order_by(PaperTrade.entry_date)
        ).all()

    if len(rows) < _MIN_CALIBRATION_TRADES:
        return {"error": f"Need ≥{_MIN_CALIBRATION_TRADES} closed trades; have {len(rows)}"}

    # 70/30 chronological split (same convention as signal-engine's calibrate_ml_weight) —
    # rows are already ordered by entry_date via the query above.
    split = max(1, int(len(rows) * 0.7))
    train_rows, val_rows = rows[:split], rows[split:]
    _MIN_VAL_TRADES = 20
    if len(val_rows) < _MIN_VAL_TRADES:
        return {"error": f"Need ≥{_MIN_VAL_TRADES} validation-slice trades after a 70/30 split; have {len(val_rows)}"}

    def _to_xy(rset):
        X = np.array([
            [
                float(r.rr_ratio_at_entry),
                float(r.confidence_at_entry),
                float(r.entry_score),
                float(r.kscore_at_entry) if r.kscore_at_entry is not None else 50.0,
            ]
            for r in rset
        ])
        y = np.array([1 if float(r.pnl) > 0 else 0 for r in rset])
        return X, y

    X_train_raw, y_train = _to_xy(train_rows)
    win_rate = float(y_train.mean())
    # Threshold calibration: use 52% floor to avoid over-filtering in choppy markets
    threshold = max(0.50, min(0.60, win_rate + 0.02))

    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train_raw)

    model = LogisticRegression(max_iter=500, class_weight="balanced", random_state=42)
    model.fit(X_train, y_train)

    # Un-scale coefficients back to raw-feature space for use without scaler at runtime
    coef = model.coef_[0]
    intercept = float(model.intercept_[0])
    means = scaler.mean_
    stds = scaler.scale_

    # raw_logit = intercept_raw + sum(coef_raw[i] * x_raw[i])
    # where coef_raw[i] = coef[i] / stds[i]  and  intercept_raw adjusts for means
    coef_raw = coef / stds
    intercept_raw = intercept - float(np.sum(coef * means / stds))

    # ── Validation: does the calibrated rule beat the current fallback rule
    # (score >= min_entry_score, i.e. no calibration at all) on trades the fit never saw? ──
    import math as _math
    _min_entry_score = _DEFAULT_CONFIG.get("min_entry_score", 4)
    X_val_raw, y_val = _to_xy(val_rows)

    def _mean_pnl_where(mask, rset):
        selected = [float(r.pnl) for r, m in zip(rset, mask) if m]
        return (sum(selected) / len(selected), len(selected)) if selected else (None, 0)

    cal_logits = intercept_raw + X_val_raw @ coef_raw
    cal_probs = 1.0 / (1.0 + np.exp(-cal_logits))
    candidate_mask = cal_probs >= threshold
    baseline_mask = X_val_raw[:, 2] >= _min_entry_score  # column 2 = entry_score

    candidate_ev, candidate_n = _mean_pnl_where(candidate_mask, val_rows)
    baseline_ev, baseline_n = _mean_pnl_where(baseline_mask, val_rows)

    if candidate_ev is None or baseline_ev is None or candidate_ev <= baseline_ev:
        log.info(
            "paper.entry_weights_calibration_rejected",
            n_trades=len(rows), val_n=len(val_rows),
            candidate_ev=round(candidate_ev, 2) if candidate_ev is not None else None,
            candidate_n=candidate_n,
            baseline_ev=round(baseline_ev, 2) if baseline_ev is not None else None,
            baseline_n=baseline_n,
            reason="candidate did not beat the min_entry_score baseline on the validation slice",
        )
        return {
            "error": "candidate weights did not beat the current fallback rule on the validation slice",
            "candidate_ev": round(candidate_ev, 2) if candidate_ev is not None else None,
            "baseline_ev": round(baseline_ev, 2) if baseline_ev is not None else None,
            "val_n": len(val_rows),
        }

    result = {
        "intercept":    float(intercept_raw),
        "w_rr":         float(coef_raw[0]),
        "w_confidence": float(coef_raw[1]),
        "w_score":      float(coef_raw[2]),
        "w_kscore":     float(coef_raw[3]),
        "threshold":    threshold,
        "win_rate":     win_rate,
        "n_trades":     len(rows),
        "validation_n": len(val_rows),
        "validation_ev": round(candidate_ev, 2),
        "baseline_validation_ev": round(baseline_ev, 2),
        "calibrated_at": datetime.utcnow().isoformat(),
    }

    _ENTRY_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ENTRY_WEIGHTS_PATH.write_text(json.dumps(result, indent=2))
    log.info("paper.entry_weights_saved", n_trades=len(rows), win_rate=round(win_rate, 3), threshold=round(threshold, 3),
             validation_ev=round(candidate_ev, 2), baseline_validation_ev=round(baseline_ev, 2))

    # Signal engine to reload weights on next call
    try:
        from .paper_trading_engine import reload_entry_weights  # type: ignore
        reload_entry_weights()
    except Exception:
        pass

    return result


def _calibrate_and_save() -> None:
    global _calibration_running
    try:
        result = calibrate_entry_weights()
        if "error" in result:
            log.warning("paper.entry_calibration_failed", error=result["error"])
        else:
            log.info("paper.entry_calibration_done", n_trades=result["n_trades"])
    except Exception as exc:
        log.exception("paper.entry_calibration_exception", exc=str(exc))
    finally:
        _calibration_running = False


@router.get("/entry_factors")
def get_entry_factors(
    _: User = Depends(get_current_user),
) -> dict:
    """Return calibrated entry factor weights (or status if not yet calibrated)."""
    if not _ENTRY_WEIGHTS_PATH.exists():
        return {
            "status": "not_calibrated",
            "note": f"Need ≥{_MIN_CALIBRATION_TRADES} closed trades. POST /calibrate-entry to run.",
            "is_running": _calibration_running,
        }
    try:
        data = json.loads(_ENTRY_WEIGHTS_PATH.read_text())
        data["status"] = "calibrated"
        data["is_running"] = _calibration_running
        return data
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.post("/calibrate-entry")
def trigger_entry_calibration(
    background_tasks: BackgroundTasks,
    _: User = Depends(get_admin_user),
) -> dict:
    """Fit logistic regression on closed paper trades to calibrate entry factor weights.

    Runs in the background. Check GET /entry_factors for results.
    """
    global _calibration_running
    with _calibration_lock:
        if _calibration_running:
            return {"status": "already_running"}
        _calibration_running = True
    background_tasks.add_task(_calibrate_and_save)
    return {"status": "started", "min_trades": _MIN_CALIBRATION_TRADES}


# ── SELFIMPROVE-NEVER-CALIBRATED-PARAMS: min_rr_ratio calibration ────────────
# min_rr_ratio (2.0) and regime_min_rr_ratio (3.0) — the R:R hard-reject floor
# _should_enter() uses whenever a portfolio hasn't explicitly set its own value — were
# permanently hardcoded literals with no feedback loop from real trade outcomes at all.
# Same 70/30 chronological split + validation-beats-baseline gate as calibrate_entry_weights()
# above and signal-engine's calibrate_ta_weights/calibrate_ml_weight — proven pattern, not a
# new design. Writes a validated replacement DEFAULT (not a hard override — an explicit
# portfolio.config value always wins, exactly as it does today against the 2.0/3.0 literals).

_MIN_RR_OVERRIDE_PATH = Path(_settings.model_dir) / "min_rr_calibration.json"
_MIN_RR_MIN_TRADES = 100  # same floor as calibrate_entry_weights — real R:R spread needs volume
_MIN_RR_MIN_VAL_TRADES = 20
_MIN_RR_CANDIDATES = [1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0]
_MIN_RR_MIN_CANDIDATE_N = 15  # a candidate threshold needs enough qualifying trades to trust its EV


def calibrate_min_rr_ratio() -> dict:
    """Sweep candidate min_rr_ratio floors against real closed-trade R:R/PnL data.

    For each candidate threshold T, "qualifying" trades are those whose rr_ratio_at_entry >= T
    (i.e. trades _should_enter() would have allowed through under that floor) — mean pnl over
    those trades is that threshold's EV. Picks the train-slice EV-maximizing threshold (subject
    to _MIN_RR_MIN_CANDIDATE_N, so a threshold surviving on 3 lucky trades can't win), then only
    applies it if it ALSO beats the CURRENT default threshold's own validation-slice EV — the
    same held-out-data discipline as calibrate_entry_weights(), not an in-sample pick.
    """
    from ..services.paper_trading_engine import _default_min_rr_ratio, reload_min_rr_override

    with SessionLocal() as session:
        rows = session.execute(
            select(PaperTrade.rr_ratio_at_entry, PaperTrade.pnl, PaperTrade.entry_date)
            .where(
                PaperTrade.stage == "closed",
                PaperTrade.pnl.is_not(None),
                PaperTrade.rr_ratio_at_entry.is_not(None),
            ).order_by(PaperTrade.entry_date)
        ).all()

    if len(rows) < _MIN_RR_MIN_TRADES:
        return {"error": f"Need >={_MIN_RR_MIN_TRADES} closed trades with rr_ratio_at_entry; have {len(rows)}"}

    split = max(1, int(len(rows) * 0.7))
    train_rows, val_rows = rows[:split], rows[split:]
    if len(val_rows) < _MIN_RR_MIN_VAL_TRADES:
        return {"error": f"Need >={_MIN_RR_MIN_VAL_TRADES} validation-slice trades after a 70/30 split; have {len(val_rows)}"}

    def _ev_at(threshold, rset):
        qualifying = [float(r.pnl) for r in rset if float(r.rr_ratio_at_entry) >= threshold]
        if len(qualifying) < _MIN_RR_MIN_CANDIDATE_N:
            return None, len(qualifying)
        return sum(qualifying) / len(qualifying), len(qualifying)

    curve = []
    best_ev = None
    best_threshold = None
    for t in _MIN_RR_CANDIDATES:
        train_ev, train_n = _ev_at(t, train_rows)
        curve.append({"threshold": t, "train_ev": round(train_ev, 4) if train_ev is not None else None, "train_n": train_n})
        if train_ev is not None and (best_ev is None or train_ev > best_ev):
            best_ev = train_ev
            best_threshold = t

    if best_threshold is None:
        return {"error": "no candidate threshold has enough qualifying train-slice trades", "curve": curve}

    # Current default (whatever calibration has already applied, or the original 2.0 literal)
    # is the baseline this candidate must beat on validation — never compared against an
    # arbitrary fixed number, always against whatever is ACTUALLY live right now.
    baseline_threshold = _default_min_rr_ratio("neutral")
    candidate_ev, candidate_n = _ev_at(best_threshold, val_rows)
    baseline_ev, baseline_n = _ev_at(baseline_threshold, val_rows)

    if candidate_ev is None or baseline_ev is None or candidate_ev <= baseline_ev:
        log.info(
            "paper.min_rr_calibration_rejected",
            n_trades=len(rows), val_n=len(val_rows),
            candidate_threshold=best_threshold, candidate_ev=round(candidate_ev, 4) if candidate_ev is not None else None,
            baseline_threshold=baseline_threshold, baseline_ev=round(baseline_ev, 4) if baseline_ev is not None else None,
            reason="candidate did not beat the current default threshold's own validation-slice EV",
        )
        return {
            "error": "candidate threshold did not beat the current default on the validation slice",
            "candidate_threshold": best_threshold,
            "candidate_validation_ev": round(candidate_ev, 4) if candidate_ev is not None else None,
            "baseline_threshold": baseline_threshold,
            "baseline_validation_ev": round(baseline_ev, 4) if baseline_ev is not None else None,
            "val_n": len(val_rows),
            "curve": curve,
        }

    result = {
        "min_rr_ratio": best_threshold,
        # T190's regime-stiffened floor keeps its own +50% relative bump over the calibrated
        # base rather than a second independent sweep — no real per-regime R:R/PnL volume
        # exists yet to calibrate choppy/risk_off separately from neutral.
        "regime_min_rr_ratio": round(best_threshold * 1.5, 2),
        "n_trades": len(rows),
        "validation_n": len(val_rows),
        "candidate_validation_ev": round(candidate_ev, 4),
        "baseline_threshold": baseline_threshold,
        "baseline_validation_ev": round(baseline_ev, 4),
        "curve": curve,
        "calibrated_at": datetime.utcnow().isoformat(),
    }

    _MIN_RR_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MIN_RR_OVERRIDE_PATH.write_text(json.dumps(result, indent=2))
    log.info("paper.min_rr_calibration_applied", threshold=best_threshold,
             validation_ev=round(candidate_ev, 4), baseline_validation_ev=round(baseline_ev, 4),
             n_trades=len(rows))

    try:
        from db import TuneHistory
        with SessionLocal() as session:
            import uuid as _uuid
            session.add(TuneHistory(
                run_id=str(_uuid.uuid4()), parameter_class="entry_gate", parameter_name="min_rr_ratio",
                style="ALL", market="ALL",
                old_value={"min_rr_ratio": baseline_threshold},
                new_value={"min_rr_ratio": best_threshold, "regime_min_rr_ratio": result["regime_min_rr_ratio"]},
                train_window_start=train_rows[0].entry_date, train_window_end=train_rows[-1].entry_date,
                validation_window_start=val_rows[0].entry_date, validation_window_end=val_rows[-1].entry_date,
                train_ev_pct=round(best_ev, 4), validation_ev_pct=round(candidate_ev, 4),
                baseline_validation_ev_pct=round(baseline_ev, 4), validation_n=candidate_n,
                promoted=True, gate_failures=[], triggered_by="manual",
            ))
            session.commit()
    except Exception as exc:
        log.warning("paper.min_rr_calibration_tune_history_failed", error=str(exc))

    reload_min_rr_override()
    return result


_min_rr_calibration_lock = threading.Lock()
_min_rr_calibration_running = False


def _calibrate_min_rr_and_save() -> None:
    global _min_rr_calibration_running
    try:
        result = calibrate_min_rr_ratio()
        if "error" in result:
            log.warning("paper.min_rr_calibration_failed", error=result["error"])
        else:
            log.info("paper.min_rr_calibration_done", n_trades=result["n_trades"])
    except Exception as exc:
        log.exception("paper.min_rr_calibration_exception", exc=str(exc))
    finally:
        _min_rr_calibration_running = False


@router.get("/min_rr_calibration")
def get_min_rr_calibration(
    _: User = Depends(get_current_user),
) -> dict:
    """Return the calibrated min_rr_ratio/regime_min_rr_ratio defaults (or status if none yet)."""
    if not _MIN_RR_OVERRIDE_PATH.exists():
        return {
            "status": "not_calibrated",
            "note": f"Need >={_MIN_RR_MIN_TRADES} closed trades. POST /calibrate-min-rr to run.",
            "is_running": _min_rr_calibration_running,
        }
    try:
        data = json.loads(_MIN_RR_OVERRIDE_PATH.read_text())
        data["status"] = "calibrated"
        data["is_running"] = _min_rr_calibration_running
        return data
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.post("/calibrate-min-rr")
def trigger_min_rr_calibration(
    background_tasks: BackgroundTasks,
    _: User = Depends(get_admin_user),
) -> dict:
    """Sweep candidate min_rr_ratio floors against closed paper trades.

    Runs in the background. Check GET /min_rr_calibration for results.
    """
    global _min_rr_calibration_running
    with _min_rr_calibration_lock:
        if _min_rr_calibration_running:
            return {"status": "already_running"}
        _min_rr_calibration_running = True
    background_tasks.add_task(_calibrate_min_rr_and_save)
    return {"status": "started", "min_trades": _MIN_RR_MIN_TRADES}


# ── Decision Engine shadow audit ──────────────────────────────────────────────

@router.get("/de-divergences")
def get_de_divergences(
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(get_current_user),
) -> dict:
    """Return recent Decision Engine shadow divergences and agreements from Redis."""
    try:
        from common.redis_client import get_redis as _get_pool_redis
        rc = _get_pool_redis()
        raw_div = rc.lrange("de:divergences", 0, limit - 1)
        raw_agr = rc.lrange("de:agreements", 0, limit - 1)
        total_div = rc.llen("de:divergences")
        total_agr = rc.llen("de:agreements")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")

    divergences = []
    for raw in raw_div:
        try:
            divergences.append(json.loads(raw))
        except Exception:
            pass

    agreements = []
    for raw in raw_agr:
        try:
            agreements.append(json.loads(raw))
        except Exception:
            pass

    total = total_div + total_agr
    agreement_rate = round(total_agr / total * 100, 1) if total else None

    return {
        "total_divergences": total_div,
        "total_agreements": total_agr,
        "agreement_rate_pct": agreement_rate,
        "divergences": divergences,
        "agreements": agreements,
    }


# ── T241-P6: position-scaling shadow-mode comparison report ─────────────────

@router.get("/position-scaling-shadow")
def get_position_scaling_shadow(
    limit: int = Query(100, ge=1, le=500),
    _: User = Depends(get_current_user),
) -> dict:
    """Per the T241 design doc's Phase 6 acceptance criteria: "a running shadow-mode report
    you can review weekly before deciding whether to let the new pipeline start controlling
    paper trades for real." Reads ps:shadow:pending (verdicts still within their holding
    window) and ps:shadow:resolved (verdicts scheduler.py has checked against the real
    subsequent price) from Redis — same pattern as /de-divergences above.
    """
    try:
        from common.redis_client import get_redis as _get_pool_redis
        rc = _get_pool_redis()
        raw_pending = rc.lrange("ps:shadow:pending", 0, limit - 1)
        raw_resolved = rc.lrange("ps:shadow:resolved", 0, limit - 1)
        total_pending = rc.llen("ps:shadow:pending")
        total_resolved = rc.llen("ps:shadow:resolved")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")

    pending = []
    for raw in raw_pending:
        try:
            pending.append(json.loads(raw))
        except Exception:
            pass

    resolved = []
    for raw in raw_resolved:
        try:
            resolved.append(json.loads(raw))
        except Exception:
            pass

    n_correct = sum(1 for r in resolved if r.get("outcome_correct"))
    hit_rate = round(n_correct / len(resolved) * 100, 1) if resolved else None

    # Would-act vs. would-not-act breakdown — lets a reviewer see whether the model's
    # "act" calls specifically are earning their keep, not just the aggregate hit rate
    # (which a model that mostly predicts "don't act" could inflate trivially).
    would_act_resolved = [r for r in resolved if r.get("would_act")]
    would_act_hit_rate = (
        round(sum(1 for r in would_act_resolved if r.get("outcome_correct")) / len(would_act_resolved) * 100, 1)
        if would_act_resolved else None
    )

    return {
        "total_pending": total_pending,
        "total_resolved": total_resolved,
        "hit_rate_pct": hit_rate,
        "would_act_count": len(would_act_resolved),
        "would_act_hit_rate_pct": would_act_hit_rate,
        "pending": pending,
        "resolved": resolved,
    }


@router.get("/kelly")
def kelly_sizing(
    style: str = Query("SWING", description="Trading style: SWING|GROWTH|LONG|SHORT"),
    lookback_days: int = Query(90, description="Days of closed trade history to use"),
    _user: str = Depends(get_current_user),
):
    """Compute Kelly Criterion position sizing from closed paper trade history.

    Returns kelly_f (full Kelly), quarter_kelly (recommended sizing fraction),
    and summary statistics. Uses the last `lookback_days` of closed trades.

    Kelly formula: f* = (p×b - q) / b
      p = win rate, q = 1-p, b = avg_win_pct / avg_loss_pct
    Position sizing: use quarter-Kelly (0.25×f*) to account for model uncertainty.
    """
    cutoff = datetime.combine(date.today() - timedelta(days=lookback_days), time.min)
    with SessionLocal() as session:
        trades = session.execute(
            select(PaperTrade)
            .where(
                PaperTrade.stage == "closed",
                PaperTrade.trading_style == style.upper(),
                PaperTrade.exit_time >= cutoff,
                PaperTrade.pct_return.isnot(None),
            )
            .order_by(desc(PaperTrade.exit_time))
        ).scalars().all()

    if len(trades) < 10:
        return {
            "style": style.upper(),
            "trades_count": len(trades),
            "kelly_f": None,
            "quarter_kelly": None,
            "recommended_risk_pct": 1.0,
            "win_rate": None,
            "avg_win_pct": None,
            "avg_loss_pct": None,
            "note": f"Need ≥10 closed trades; only {len(trades)} found in last {lookback_days} days",
        }

    wins = [t.pct_return for t in trades if t.pct_return and t.pct_return > 0]
    losses = [abs(t.pct_return) for t in trades if t.pct_return and t.pct_return < 0]
    # Denominator must be decisive trades (wins + losses), not all trades.
    # Breakeven trades (pct_return==0.0) are excluded from both lists but were
    # included in `trades`, which would understate p and deflate kelly_f.
    decisive = len(wins) + len(losses)
    p = len(wins) / decisive if decisive > 0 else 0.5
    q = 1.0 - p
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.01

    b = avg_win / avg_loss if avg_loss > 0 else 1.0
    kelly_f = (p * b - q) / b if b > 0 else 0.0
    kelly_f = max(0.0, min(kelly_f, 1.0))
    quarter_kelly = kelly_f * 0.25

    # Map quarter-Kelly to a practical risk % (base 1%, scaled by quarter-Kelly bands)
    if quarter_kelly >= 0.08:
        recommended_risk_pct = 3.0
    elif quarter_kelly >= 0.05:
        recommended_risk_pct = 2.0
    else:
        recommended_risk_pct = 1.0

    return {
        "style": style.upper(),
        "trades_count": len(trades),
        "lookback_days": lookback_days,
        "kelly_f": round(kelly_f, 4),
        "quarter_kelly": round(quarter_kelly, 4),
        "recommended_risk_pct": recommended_risk_pct,
        "win_rate": round(p, 4),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "reward_risk_ratio": round(b, 2),
    }


# ── T233-SELFIMPROVE-PHASE2 (Phase 2a): gate-threshold backtest harness ────────
# See docs/DESIGN_BACKTEST_HARNESS_PHASE2_2026-07-06.md for full scope/rationale.
# Manually-triggered research tool — NOT wired to any promotion gate or config write.
# Reports what a candidate min_entry_score would have done on held-out historical data;
# a human reads the result and decides whether to change portfolio.config by hand.

@router.get("/backtest/min-entry-score")
def backtest_min_entry_score(
    style: str = Query(..., description="SHORT | SWING | LONG | GROWTH"),
    market: str = Query("US", description="US | HK"),
    window_days: int = Query(60, ge=14, le=365, description="Lookback window in calendar days"),
    _: User = Depends(get_admin_user),
) -> dict:
    """Walk-forward backtest of candidate min_entry_score values via the real _should_enter().

    Searches candidates on the older 70% of the window (train), only reports a candidate
    as beating baseline if it ALSO wins on the newer 30% (validation) the search never saw.
    Research tool only — does not write to portfolio.config or any promotion history table.
    """
    from ..backtest.gate_harness import walk_forward_min_entry_score
    from ..services.paper_trading_engine import _DEFAULT_CONFIG, _STYLE_OVERRIDES

    style = style.upper()
    if style not in ("SHORT", "SWING", "LONG", "GROWTH"):
        raise HTTPException(status_code=400, detail=f"Unknown style: {style}")
    market = market.upper()
    if market not in ("US", "HK"):
        raise HTTPException(status_code=400, detail=f"Unknown market: {market}")

    base_cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}
    window_end = date.today()
    window_start = window_end - timedelta(days=window_days)

    with SessionLocal() as session:
        return walk_forward_min_entry_score(session, style, market, base_cfg, window_start, window_end)


# ── T233-SELFIMPROVE-PHASE2b: min_kscore / min_ta_score / min_volume_z ──────────
# See gate_harness.py's own module docstring (search "Phase 2b") for the full re-scoping
# rationale — these three pre-filter gates live in _scan_for_entries' candidate loop, not
# inside _should_enter(), but are each a pure per-signal comparison (no open-position/equity
# state), so they're layered onto the same per-signal replay Phase 2a already uses rather than
# needing the full bar-by-bar equity-curve engine originally envisioned for this phase.

@router.get("/backtest/extended-gate")
def backtest_extended_gate(
    style: str = Query(..., description="SHORT | SWING | LONG | GROWTH"),
    market: str = Query("US", description="US | HK"),
    param: str = Query(..., description="min_kscore | min_ta_score | min_volume_z"),
    window_days: int = Query(60, ge=14, le=365, description="Lookback window in calendar days"),
    _: User = Depends(get_admin_user),
) -> dict:
    """Walk-forward backtest of a candidate min_kscore/min_ta_score/min_volume_z value,
    replaying the real _should_enter() PLUS the three pre-filter gates _scan_for_entries
    applies before ever calling it. Research tool only — does not write to portfolio.config
    or any promotion history table.
    """
    from ..backtest.gate_harness import walk_forward_extended_gate
    from ..services.paper_trading_engine import _DEFAULT_CONFIG, _STYLE_OVERRIDES, _HK_MARKET_OVERRIDES

    style = style.upper()
    if style not in ("SHORT", "SWING", "LONG", "GROWTH"):
        raise HTTPException(status_code=400, detail=f"Unknown style: {style}")
    market = market.upper()
    if market not in ("US", "HK"):
        raise HTTPException(status_code=400, detail=f"Unknown market: {market}")
    if param not in ("min_kscore", "min_ta_score", "min_volume_z"):
        raise HTTPException(status_code=400, detail=f"Unknown param: {param}")

    base_cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}
    if market == "HK":
        base_cfg = {**base_cfg, **_HK_MARKET_OVERRIDES}
    window_end = date.today()
    window_start = window_end - timedelta(days=window_days)

    current = base_cfg.get(param, 0.0)
    # Candidate grid — only ever TIGHTER than the current value (see gate_harness.py's own
    # docstring: a stored-outcome replay can only evaluate tightening an existing gate, never
    # a genuinely looser one, since it re-filters signals that already fired under the CURRENT
    # threshold rather than regenerating them against a different one).
    if param == "min_kscore":
        candidates = sorted({v for v in (current, current + 2, current + 5, current + 8, current + 12) if v <= 100})
    elif param == "min_ta_score":
        candidates = sorted({v for v in (current, current + 0.05, current + 0.10, current + 0.15) if v <= 1.0})
    else:  # min_volume_z
        candidates = sorted({v for v in (current, current + 0.25, current + 0.5, current + 1.0)})

    with SessionLocal() as session:
        return walk_forward_extended_gate(
            session, style, market, base_cfg, window_start, window_end, param, candidates,
        )


# ── T233-SELFIMPROVE-PHASE3: promotion gate + tune history ─────────────────────
# See docs/DESIGN_PROMOTION_GATE_PHASE3_2026-07-05.md for full scope/rationale.
# Still manually-triggered and does NOT write to portfolio.config — records every
# attempted tune (promoted or not) to tune_history so "what changed and did it help"
# never requires reconstructing state from container logs across services.

@router.post("/backtest/min-entry-score/promote")
def promote_min_entry_score(
    style: str = Query(..., description="SHORT | SWING | LONG | GROWTH"),
    market: str = Query("US", description="US | HK"),
    window_days: int = Query(60, ge=14, le=365, description="Lookback window in calendar days"),
    max_worst_trade_regression_pct: float = Query(10.0, ge=0, description="Reject if candidate's worst trade is this many pp worse than baseline's"),
    _: User = Depends(get_admin_user),
) -> dict:
    """Run the min_entry_score backtest, apply the Phase 3 promotion gate, and record the
    attempt (promoted or not) to tune_history. Does NOT apply the candidate to portfolio.config —
    a human still decides whether to hand-edit the live config based on this result.
    """
    from ..backtest.promotion_gate import evaluate_and_record
    from ..services.paper_trading_engine import _DEFAULT_CONFIG, _STYLE_OVERRIDES

    style = style.upper()
    if style not in ("SHORT", "SWING", "LONG", "GROWTH"):
        raise HTTPException(status_code=400, detail=f"Unknown style: {style}")
    market = market.upper()
    if market not in ("US", "HK"):
        raise HTTPException(status_code=400, detail=f"Unknown market: {market}")

    base_cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}
    window_end = date.today()
    window_start = window_end - timedelta(days=window_days)

    with SessionLocal() as session:
        return evaluate_and_record(
            session, style, market, base_cfg, window_start, window_end,
            max_worst_trade_regression_pct=max_worst_trade_regression_pct,
        )


@router.get("/tune-history")
def get_tune_history(
    style: str | None = Query(None, description="Filter to SHORT | SWING | LONG | GROWTH"),
    market: str | None = Query(None, description="Filter to US | HK"),
    limit: int = Query(50, ge=1, le=500),
    _: User = Depends(get_admin_user),
) -> dict:
    """Browse the tune_history table — every attempted tune, promoted or rejected, with the
    full before/after backtest numbers. Directly answers "what changed, when, and did it help"
    without reconstructing state from container logs across services.
    """
    from db import TuneHistory

    with SessionLocal() as session:
        q = select(TuneHistory).order_by(desc(TuneHistory.ts)).limit(limit)
        if style:
            q = q.where(TuneHistory.style == style.upper())
        if market:
            q = q.where(TuneHistory.market == market.upper())
        rows = session.execute(q).scalars().all()
        return {
            "count": len(rows),
            "rows": [
                {
                    "id": r.id, "run_id": r.run_id, "ts": r.ts.isoformat(),
                    "parameter_class": r.parameter_class, "parameter_name": r.parameter_name,
                    "style": r.style, "market": r.market,
                    "old_value": r.old_value, "new_value": r.new_value,
                    "train_window": [str(r.train_window_start), str(r.train_window_end)],
                    "validation_window": [str(r.validation_window_start), str(r.validation_window_end)],
                    "train_ev_pct": r.train_ev_pct, "validation_ev_pct": r.validation_ev_pct,
                    "baseline_validation_ev_pct": r.baseline_validation_ev_pct,
                    "validation_n": r.validation_n,
                    "approx_worst_trade_pct": r.approx_worst_trade_pct,
                    "baseline_worst_trade_pct": r.baseline_worst_trade_pct,
                    "promoted": r.promoted, "gate_failures": r.gate_failures,
                    "triggered_by": r.triggered_by,
                }
                for r in rows
            ],
        }
