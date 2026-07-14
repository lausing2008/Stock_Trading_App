"""Strategy CRUD + backtest endpoint — user-scoped via JWT."""
from dataclasses import asdict
from datetime import date, timedelta

import httpx
import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.config import get_settings
from common.jwt_auth import get_current_username
from db import Backtest, Strategy, TimeFrame, get_session

from ..backtest import BacktestEngine

router = APIRouter(tags=["strategy"])
_settings = get_settings()


class StrategyIn(BaseModel):
    name: str
    rule_dsl: dict         # must contain "entry" and optional "exit"
    description: str | None = None


class BacktestIn(BaseModel):
    strategy_id: int | None = None   # load an existing saved strategy
    rule_dsl: dict | None = None     # ad-hoc rules — auto-saved under `name`
    name: str | None = None          # display name when rule_dsl is provided
    symbol: str
    start: date | None = None
    end: date | None = None


@router.post("/strategies")
def create_strategy(
    body: StrategyIn,
    username: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    if "entry" not in body.rule_dsl:
        raise HTTPException(400, "rule_dsl must contain 'entry' rule")
    strat = Strategy(name=body.name, rule_dsl=body.rule_dsl, description=body.description, owner=username)
    session.add(strat)
    session.commit()
    session.refresh(strat)
    return {"id": strat.id, "name": strat.name}


@router.get("/strategies")
def list_strategies(
    username: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    rows = list(session.execute(
        select(Strategy).where(Strategy.owner == username)
    ).scalars())
    return [{"id": r.id, "name": r.name, "description": r.description} for r in rows]


@router.get("/strategies/{sid}")
def get_strategy(
    sid: int,
    username: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    s = session.get(Strategy, sid)
    if not s:
        raise HTTPException(404, "Not found")
    if s.owner != username:
        raise HTTPException(403, "Not your strategy")
    return {"id": s.id, "name": s.name, "rule_dsl": s.rule_dsl, "description": s.description}


@router.delete("/strategies/{sid}")
def delete_strategy(
    sid: int,
    username: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    s = session.get(Strategy, sid)
    if not s:
        raise HTTPException(404, "Not found")
    if s.owner != username:
        raise HTTPException(403, "Not your strategy")
    # Delete backtests first — the SQLAlchemy relationship lacks cascade="delete-orphan"
    # so without this SQLAlchemy tries to NULL the FK, hitting the NOT NULL constraint.
    session.query(Backtest).filter(Backtest.strategy_id == sid).delete()
    session.delete(s)
    session.commit()
    return {"status": "deleted", "id": sid}


def _fetch_prices_df(symbol: str, start: date, end: date) -> pd.DataFrame:
    url = f"{_settings.market_data_url}/stocks/{symbol}/prices"
    with httpx.Client(timeout=30) as c:
        r = c.get(
            url,
            params={"timeframe": "1d", "start": start.isoformat(), "end": end.isoformat(), "limit": 10000},
        )
        r.raise_for_status()
    df = pd.DataFrame(r.json())
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["ts"])
    return df


@router.post("/backtest")
def backtest(
    body: BacktestIn,
    username: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    # Resolve rule DSL and strategy row.
    if body.strategy_id is not None:
        strat = session.get(Strategy, body.strategy_id)
        if not strat:
            raise HTTPException(404, "Strategy not found")
        if strat.owner != username:
            raise HTTPException(403, "Not your strategy")
        rule_dsl = strat.rule_dsl
    elif body.rule_dsl is not None:
        if "entry" not in body.rule_dsl:
            raise HTTPException(400, "rule_dsl must contain 'entry' rule")
        rule_dsl = body.rule_dsl
        strat = Strategy(
            name=body.name or f"Run — {body.symbol}",
            rule_dsl=rule_dsl,
            owner=username,
        )
        session.add(strat)
        session.flush()  # populate strat.id before using it below
    else:
        raise HTTPException(400, "Provide either strategy_id or rule_dsl")

    start = body.start or (date.today() - timedelta(days=365 * 3))
    end = body.end or date.today()
    df = _fetch_prices_df(body.symbol, start, end)
    if df.empty:
        raise HTTPException(404, f"No data for {body.symbol}")

    engine = BacktestEngine()
    try:
        result = engine.run(df, rule_dsl.get("entry"), rule_dsl.get("exit"))
    except ValueError as exc:
        raise HTTPException(422, f"Invalid rule_dsl: {exc}") from exc

    # Fetch SPY benchmark for the same date range
    spy_df = _fetch_prices_df("SPY", start, end)
    if not spy_df.empty and len(spy_df) > 1:
        spy_close = spy_df.set_index("ts")["close"].sort_index()
        # Align to strategy dates
        spy_rets = spy_close.pct_change().fillna(0)
        spy_eq = (1 + spy_rets).cumprod()
        spy_total = float(spy_eq.iloc[-1] - 1)
        # T247-STRATEGYENGINE-CAGR-OVERFLOW: same overflow risk as BacktestEngine.run()'s own
        # cagr computation (a same-calendar-day SPY range floors years to ~0, and
        # equity**(1/years) overflows to inf) — same fix: floor at 1 trading day, guard the
        # result with np.isfinite.
        spy_years = max((spy_df["ts"].iloc[-1] - spy_df["ts"].iloc[0]).days / 365.25, 1 / 365.25)
        spy_cagr_raw = spy_eq.iloc[-1] ** (1 / spy_years) - 1 if spy_eq.iloc[-1] > 0 else -1.0
        spy_cagr = float(spy_cagr_raw) if np.isfinite(spy_cagr_raw) else None
        result.benchmark_cagr = round(spy_cagr, 4) if spy_cagr is not None else None
        result.benchmark_total_return = round(spy_total, 4)
        result.alpha = (
            round(result.cagr - spy_cagr, 4)
            if (result.cagr is not None and spy_cagr is not None)
            else None
        )
        result.benchmark_equity_curve = [
            {"ts": str(t), "equity": round(float(e), 6)}
            for t, e in zip(spy_df["ts"], spy_eq, strict=False)
        ][-500:]

    bt = Backtest(
        strategy_id=strat.id,
        universe=[body.symbol],
        start=start,
        end=end,
        timeframe=TimeFrame.D1,
        sharpe=result.sharpe,
        max_drawdown=result.max_drawdown,
        win_rate=result.win_rate,
        cagr=result.cagr,
        profit_factor=result.profit_factor,
        total_return=result.total_return,
        equity_curve={
            "data": result.equity_curve[-500:],
            "sortino": result.sortino,
            "calmar": result.calmar,
            "benchmark_cagr": result.benchmark_cagr,
            "alpha": result.alpha,
            "benchmark_equity_curve": result.benchmark_equity_curve,
        },
        trades={"data": result.trades},
    )
    session.add(bt)
    session.commit()
    session.refresh(bt)
    return {"backtest_id": bt.id, **asdict(result)}


@router.get("/backtests")
def list_backtests(
    username: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        select(Backtest, Strategy)
        .join(Strategy, Backtest.strategy_id == Strategy.id)
        .where(Strategy.owner == username)
        .order_by(Backtest.created_at.desc())
    ).all()
    return [
        {
            "id": bt.id,
            "name": strat.name,
            "symbol": bt.universe[0] if bt.universe else "",
            "start": bt.start.isoformat(),
            "end": bt.end.isoformat(),
            "total_return": bt.total_return,
            "cagr": bt.cagr,
            "sharpe": bt.sharpe,
            "sortino": (bt.equity_curve or {}).get("sortino"),
            "calmar": (bt.equity_curve or {}).get("calmar"),
            "max_drawdown": bt.max_drawdown,
            "win_rate": bt.win_rate,
            "profit_factor": bt.profit_factor,
            "n_trades": len(bt.trades.get("data", [])) if bt.trades else 0,
            "created_at": bt.created_at.isoformat() if bt.created_at else None,
        }
        for bt, strat in rows
    ]


@router.get("/backtests/{bid}")
def get_backtest(
    bid: int,
    username: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    bt = session.get(Backtest, bid)
    if not bt:
        raise HTTPException(404, "Not found")
    strat = session.get(Strategy, bt.strategy_id)
    if not strat or strat.owner != username:
        raise HTTPException(403, "Not your backtest")
    return {
        "id": bt.id,
        "name": strat.name,
        "rule_dsl": strat.rule_dsl,
        "symbol": bt.universe[0] if bt.universe else "",
        "start": bt.start.isoformat(),
        "end": bt.end.isoformat(),
        "total_return": bt.total_return,
        "cagr": bt.cagr,
        "sharpe": bt.sharpe,
        "sortino": (bt.equity_curve or {}).get("sortino"),
        "calmar": (bt.equity_curve or {}).get("calmar"),
        "max_drawdown": bt.max_drawdown,
        "win_rate": bt.win_rate,
        "profit_factor": bt.profit_factor,
        "equity_curve": bt.equity_curve.get("data", []) if bt.equity_curve else [],
        "trades": bt.trades.get("data", []) if bt.trades else [],
        "n_trades": len(bt.trades.get("data", [])) if bt.trades else 0,
        "created_at": bt.created_at.isoformat() if bt.created_at else None,
    }


@router.delete("/backtests/{bid}")
def delete_backtest(
    bid: int,
    username: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    bt = session.get(Backtest, bid)
    if not bt:
        raise HTTPException(404, "Not found")
    strat = session.get(Strategy, bt.strategy_id)
    if not strat or strat.owner != username:
        raise HTTPException(403, "Not your backtest")
    session.delete(bt)
    session.flush()   # apply delete before the count so orphan check sees the updated state
    remaining = session.query(Backtest).filter(Backtest.strategy_id == strat.id).count()
    if remaining == 0:
        session.delete(strat)
    session.commit()
    return {"status": "deleted", "id": bid}
