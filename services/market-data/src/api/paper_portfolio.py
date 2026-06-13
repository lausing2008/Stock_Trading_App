"""WF-2 Paper Portfolio API — read-only views + admin controls."""
import json
import math
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from db import PaperEquityCurve, PaperPortfolio, PaperTrade, SessionLocal, get_session
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


_MIN_SHARPE_DAYS = 20  # annualizing < 20 days produces meaningless Sharpe/Calmar


def _portfolio_risk_metrics(curve_rows: list) -> dict:
    """Compute Sharpe, max drawdown, Calmar from equity curve rows (ordered by date)."""
    equities = [r.equity for r in curve_rows if r.equity and r.equity > 0]
    data_days = len(equities)

    if data_days < 2:
        return {"sharpe": None, "max_drawdown_pct": None, "calmar": None,
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

    # Sharpe and Calmar require enough data to annualize meaningfully
    if data_days < _MIN_SHARPE_DAYS:
        return {"sharpe": None, "max_drawdown_pct": max_dd_pct, "calmar": None,
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

    # Calmar = annualised return / max drawdown
    calmar = round(annualised_return / max_dd, 2) if max_dd > 0 else None

    return {
        "sharpe": sharpe,
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

    # Information ratio: annualised active return / tracking error
    active = [p_rets[i] - beta * s_rets[i] for i in range(n)]
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
    losses = [t for t in closed_trades if (t.pnl or 0) <= 0]
    win_rate = round(len(wins) / max(len(closed_trades), 1) * 100, 1)
    avg_win  = round(sum(t.pct_return or 0 for t in wins) / max(len(wins), 1), 2)
    avg_loss = round(sum(t.pct_return or 0 for t in losses) / max(len(losses), 1), 2)
    total_realized = round(sum(t.pnl or 0 for t in closed_trades), 2)
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
    if all_curve:
        first = all_curve[0]
        if first.spy_close and latest_curve and latest_curve.spy_close:
            spy_return = round((latest_curve.spy_close / first.spy_close - 1) * 100, 2)
            outperformance_vs_spy = round(total_return_pct - spy_return, 2)
        if first.qqq_close and latest_curve and latest_curve.qqq_close:
            qqq_return = round((latest_curve.qqq_close / first.qqq_close - 1) * 100, 2)
            outperformance_vs_qqq = round(total_return_pct - qqq_return, 2)

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
        "avg_loss_pct": avg_loss,
        "sharpe": risk["sharpe"],
        "max_drawdown_pct": risk["max_drawdown_pct"],
        "calmar": risk["calmar"],
        "data_days": risk.get("data_days", 0),
        "insufficient_data": risk.get("insufficient_data", False),
        "alpha": ab["alpha"],
        "beta": ab["beta"],
        "info_ratio": ab["info_ratio"],
        "outperformance_vs_spy": outperformance_vs_spy,
        "outperformance_vs_qqq": outperformance_vs_qqq,
        "spy_close": latest_curve.spy_close if latest_curve else None,
        "qqq_close": latest_curve.qqq_close if latest_curve else None,
        # Regime engine — current market state (written by engine each cycle)
        "regime_state": p.config.get("regime_state"),
        "regime_vix": p.config.get("regime_vix"),
        "regime_spy": p.config.get("regime_spy"),
        "regime_notes": p.config.get("regime_notes", []),
        "config": p.config,
        "created_at": p.created_at.isoformat() if p.created_at else None,
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
            "hold_days": t.hold_days,
            "unrealized_pnl": round(((t.current_price or t.entry_price) - t.entry_price) * t.shares, 2),
            "unrealized_pct": round(((t.current_price or t.entry_price) / t.entry_price - 1) * 100, 2),
            "rr_ratio_at_entry": t.rr_ratio_at_entry,
            "entry_score": t.entry_score,
            "confidence_at_entry": t.confidence_at_entry,
            "kscore_at_entry": t.kscore_at_entry,
            "market_regime_at_entry": t.market_regime_at_entry,
        }
        for t in trades
    ]


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
    decision: str | None = Query(None),   # ENTER | WAIT | SKIP
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
                "exit_reason": t.exit_reason,
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
    """Merge body keys into the portfolio config (admin only)."""
    p = _get_portfolio(session, portfolio_id)
    allowed_keys = {
        "max_positions", "max_sector_pct", "risk_per_trade_pct", "max_position_pct",
        "min_confidence", "min_kscore", "min_rr_ratio", "min_entry_score",
        "max_hold_days", "trail_atr_mult", "trail_trigger_pct", "breakeven_trigger_pct",
        "wait_exit_days", "enabled", "paused",
    }
    updated = {k: v for k, v in body.items() if k in allowed_keys and v is not None}
    old_vals = {k: p.config.get(k) for k in updated}
    p.config = {**p.config, **updated}
    session.commit()
    # PT-C10: log config changes so the audit trail is visible in container logs
    if updated:
        log.info("paper.config_updated",
                 changed={k: {"from": old_vals[k], "to": updated[k]} for k in updated})
    return {"ok": True, "config": p.config}


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
        t.exit_time = now
        t.exit_price = exit_price
        t.exit_reason = "manual_reset"
        t.exit_reasons = {"message": "Admin reset — all positions force-closed"}
        t.pnl = round((exit_price - t.entry_price) * t.shares, 2)
        t.pct_return = round((exit_price / t.entry_price - 1) * 100, 4)

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
        losses = [t for t in bucket if (t.pnl or 0) <= 0]
        returns = [t.pct_return for t in bucket if t.pct_return is not None]
        gross_win = sum(t.pnl for t in wins if t.pnl)
        gross_loss = abs(sum(t.pnl for t in losses if t.pnl))
        return {
            "count": len(bucket),
            "win_rate": round(len(wins) / len(bucket) * 100, 1),
            "avg_return": round(sum(returns) / len(returns) * 100, 2) if returns else None,
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

@router.get("/list")
def list_portfolios(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Lightweight list of all active portfolios with summary stats."""
    portfolios = session.execute(
        select(PaperPortfolio).where(PaperPortfolio.is_active.is_(True)).order_by(PaperPortfolio.id)
    ).scalars().all()

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
            "current_equity": round(equity, 2),
            "initial_capital": p.initial_capital,
            "total_return_pct": round((equity / p.initial_capital - 1) * 100, 2),
            "win_rate_pct": win_rate,
            "open_positions": len(open_trades),
            "closed_trades": len(closed_trades),
            "sharpe": risk["sharpe"],
            "max_drawdown_pct": risk["max_drawdown_pct"],
            "is_running": p.config.get("enabled", True) and not p.config.get("paused", False),
            "is_paused": p.config.get("enabled", True) and bool(p.config.get("paused", False)),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })

    return result


# ── Multi-portfolio: create ───────────────────────────────────────────────────

@router.post("/create")
def create_portfolio(
    body: dict,
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Create a new paper portfolio (admin only)."""
    name = str(body.get("name", "Paper Portfolio")).strip() or "Paper Portfolio"
    style = str(body.get("trading_style", "GROWTH")).upper()
    initial_capital = float(body.get("initial_capital", 100_000))

    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital must be > 0")
    if style not in ("SWING", "GROWTH", "LONG", "SHORT"):
        raise HTTPException(status_code=400, detail="trading_style must be SWING, GROWTH, LONG, or SHORT")

    p = PaperPortfolio(
        name=name,
        initial_capital=initial_capital,
        current_cash=initial_capital,
        config={"trading_style": style, "enabled": True, "paused": False},
        is_active=True,
    )
    session.add(p)
    session.commit()
    session.refresh(p)

    log.info("paper.portfolio_created", portfolio_id=p.id, name=name, style=style, capital=initial_capital)
    return {"ok": True, "portfolio_id": p.id, "name": p.name}


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
        result.append({
            "portfolio_id": p.id,
            "name": p.name,
            "trading_style": p.config.get("trading_style", "GROWTH"),
            "initial_capital": p.initial_capital,
            "curve": [
                {
                    "date": r.date.isoformat(),
                    "equity": round(r.equity, 2),
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
        # Walk forward from entry_date
        exit_return: float | None = None
        days_held = 0
        for d, close in prices:
            if d < entry_date:
                continue
            if d == entry_date:
                continue  # skip entry day itself
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


def _run_optuna_for_style(style: str, n_trials: int) -> dict:
    """Run Optuna to tune stop_pct, tp_pct, max_hold_days for one style.

    Uses all closed paper trades of the given style as the dataset.
    Runs inline — call from a background thread.
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        return {"error": "optuna not installed in market-data — run pip install optuna"}

    from db import SessionLocal

    with SessionLocal() as session:
        trades = session.execute(
            select(PaperTrade).where(
                PaperTrade.stage == "closed",
                PaperTrade.trading_style == style,
                PaperTrade.entry_price.is_not(None),
                PaperTrade.entry_date.is_not(None),
            )
        ).scalars().all()

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
        trade_snapshots = [(t.symbol, t.entry_date, t.entry_price) for t in trades]

    class _TradeProxy:
        def __init__(self, symbol, entry_date, entry_price):
            self.symbol = symbol
            self.entry_date = entry_date
            self.entry_price = entry_price

    trade_objs = [_TradeProxy(s, d, p) for s, d, p in trade_snapshots]

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


def _tune_and_save(style: str, n_trials: int) -> None:
    """Background task: run Optuna for style, merge results into trade_params.json."""
    try:
        result = _run_optuna_for_style(style, n_trials)
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
    _: User = Depends(get_admin_user),
) -> dict:
    """Start Optuna tuning for stop_pct / tp_pct / max_hold_days for one trading style.

    Runs in the background. Poll GET /trade-params to see when is_running=False.
    Uses all closed paper trades of the given style as the optimization dataset.
    """
    style = style.upper()
    if style not in _FALLBACK_PARAMS:
        raise HTTPException(400, f"style must be one of: {list(_FALLBACK_PARAMS)}")
    with _tune_lock:
        if _tune_running.get(style):
            return {"status": "already_running", "style": style}
        _tune_running[style] = True
    background_tasks.add_task(_tune_and_save, style, n_trials)
    return {"status": "started", "style": style, "n_trials": n_trials}
