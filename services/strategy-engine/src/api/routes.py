"""Strategy CRUD + backtest endpoint — user-scoped via JWT."""
from dataclasses import asdict
from datetime import date, timedelta

import httpx
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
    strategy_id: int | None = None   # provide this OR rule_dsl, not both
    rule_dsl: dict | None = None     # run ad-hoc without saving to DB
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
    # Resolve the rule DSL — from a saved strategy or supplied ad-hoc.
    if body.strategy_id is not None:
        strat = session.get(Strategy, body.strategy_id)
        if not strat:
            raise HTTPException(404, "Strategy not found")
        if strat.owner != username:
            raise HTTPException(403, "Not your strategy")
        rule_dsl = strat.rule_dsl
        strat_id = strat.id
    elif body.rule_dsl is not None:
        if "entry" not in body.rule_dsl:
            raise HTTPException(400, "rule_dsl must contain 'entry' rule")
        rule_dsl = body.rule_dsl
        strat_id = None
    else:
        raise HTTPException(400, "Provide either strategy_id or rule_dsl")

    start = body.start or (date.today() - timedelta(days=365 * 3))
    end = body.end or date.today()
    df = _fetch_prices_df(body.symbol, start, end)
    if df.empty:
        raise HTTPException(404, f"No data for {body.symbol}")

    engine = BacktestEngine()
    result = engine.run(df, rule_dsl.get("entry"), rule_dsl.get("exit"))

    # Only persist when running against a saved strategy.
    if strat_id is not None:
        bt = Backtest(
            strategy_id=strat_id,
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
            equity_curve={"data": result.equity_curve[-500:]},
            trades={"data": result.trades},
        )
        session.add(bt)
        session.commit()
        session.refresh(bt)
        return {"backtest_id": bt.id, **asdict(result)}

    return {"backtest_id": None, **asdict(result)}
