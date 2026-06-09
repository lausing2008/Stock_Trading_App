"""WF-2 Paper Portfolio API — read-only views + admin controls."""
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from db import PaperEquityCurve, PaperPortfolio, PaperTrade, get_session
from .auth import get_current_user, get_admin_user
from db.models import User

router = APIRouter(prefix="/paper-portfolio", tags=["paper-portfolio"])


def _get_active_portfolio(session: Session) -> PaperPortfolio:
    p = session.execute(
        select(PaperPortfolio).where(PaperPortfolio.is_active.is_(True)).limit(1)
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="No active paper portfolio found")
    return p


# ── Summary ───────────────────────────────────────────────────────────────────

@router.get("/summary")
def get_summary(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    p = _get_active_portfolio(session)

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

    latest_curve = session.execute(
        select(PaperEquityCurve)
        .where(PaperEquityCurve.portfolio_id == p.id)
        .order_by(desc(PaperEquityCurve.date))
        .limit(1)
    ).scalar_one_or_none()

    return {
        "portfolio_id": p.id,
        "name": p.name,
        "trading_style": p.config.get("trading_style", "GROWTH"),
        "initial_capital": p.initial_capital,
        "current_equity": round(equity, 2),
        "current_cash": round(p.current_cash, 2),
        "open_positions_value": round(open_value, 2),
        "total_return_pct": round((equity / p.initial_capital - 1) * 100, 2),
        "total_realized_pnl": total_realized,
        "total_unrealized_pnl": total_unrealized,
        "open_positions": len(open_trades),
        "closed_trades": len(closed_trades),
        "win_rate_pct": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "spy_close": latest_curve.spy_close if latest_curve else None,
        "qqq_close": latest_curve.qqq_close if latest_curve else None,
        "config": p.config,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


# ── Open positions ────────────────────────────────────────────────────────────

@router.get("/positions")
def get_positions(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    p = _get_active_portfolio(session)
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
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    p = _get_active_portfolio(session)
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
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    p = _get_active_portfolio(session)
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
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Return entry decisions (all trades, open + closed, as decision log)."""
    p = _get_active_portfolio(session)
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
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Merge body keys into the portfolio config (admin only)."""
    p = _get_active_portfolio(session)
    allowed_keys = {
        "max_positions", "max_sector_pct", "risk_per_trade_pct", "max_position_pct",
        "min_confidence", "min_kscore", "min_rr_ratio", "min_entry_score",
        "max_hold_days", "trail_atr_mult", "trail_trigger_pct", "breakeven_trigger_pct",
        "wait_exit_days", "enabled", "paused",
    }
    updated = {k: v for k, v in body.items() if k in allowed_keys}
    p.config = {**p.config, **updated}
    session.commit()
    return {"ok": True, "config": p.config}


# ── Admin: reset ──────────────────────────────────────────────────────────────

@router.post("/reset")
def reset_portfolio(
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Close all open trades at current_price and reset cash to initial_capital."""
    p = _get_active_portfolio(session)
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
    _: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
) -> dict:
    """Update initial_capital and/or current_cash (admin only).

    Body: { initial_capital?: number, current_cash?: number }
    Setting current_cash lets you add/withdraw cash without a full reset.
    """
    p = _get_active_portfolio(session)

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

    p = _get_active_portfolio(session)
    if state == "running":
        p.config = {**p.config, "enabled": True, "paused": False}
    elif state == "paused":
        p.config = {**p.config, "enabled": True, "paused": True}
    else:  # stopped
        p.config = {**p.config, "enabled": False, "paused": False}

    session.commit()
    return {"ok": True, "state": state, "config": p.config}
