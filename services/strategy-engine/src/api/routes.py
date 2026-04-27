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
    strategy_id: int
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
    strat = session.get(Strategy, body.strategy_id)
    if not strat:
        raise HTTPException(404, "Strategy not found")
    if strat.owner != username:
        raise HTTPException(403, "Not your strategy")

    start = body.start or (date.today() - timedelta(days=365 * 3))
    end = body.end or date.today()
    df = _fetch_prices_df(body.symbol, start, end)
    if df.empty:
        raise HTTPException(404, f"No data for {body.symbol}")

    engine = BacktestEngine()
    entry = strat.rule_dsl.get("entry")
    exit_rule = strat.rule_dsl.get("exit")
    result = engine.run(df, entry, exit_rule)

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
        equity_curve={"data": result.equity_curve[-500:]},
        trades={"data": result.trades},
    )
    session.add(bt)
    session.commit()
    session.refresh(bt)
    return {"backtest_id": bt.id, **asdict(result)}
