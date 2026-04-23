"""Portfolio optimization API."""
from dataclasses import asdict
from datetime import date, timedelta
from typing import Literal

import httpx
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from common.config import get_settings

from ..optimizers import ai_allocation, hierarchical_risk_parity, mean_variance, risk_parity

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
_settings = get_settings()

METHOD = Literal["mean_variance", "risk_parity", "hierarchical_risk_parity", "ai_allocation"]


class OptimizeRequest(BaseModel):
    symbols: list[str]
    method: METHOD = "mean_variance"
    lookback_days: int = 365
    min_score: float = 60.0


def _fetch_closes(symbols: list[str], lookback_days: int) -> pd.DataFrame:
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    series = {}
    with httpx.Client(timeout=30) as c:
        for s in symbols:
            r = c.get(f"{_settings.market_data_url}/stocks/{s}/prices", params={"start": start, "limit": 5000})
            if r.status_code != 200:
                continue
            data = r.json()
            if not data:
                continue
            df = pd.DataFrame(data)
            df["ts"] = pd.to_datetime(df["ts"])
            series[s] = df.set_index("ts")["close"]
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).dropna(how="all").ffill().dropna()


def _fetch_scores(symbols: list[str]) -> dict[str, float]:
    scores = {}
    with httpx.Client(timeout=15) as c:
        for s in symbols:
            try:
                r = c.get(f"{_settings.ranking_engine_url}/rankings/{s}")
                if r.status_code == 200:
                    scores[s] = float(r.json().get("score", 0))
            except Exception:
                continue
    return scores


@router.post("/optimize")
def optimize(req: OptimizeRequest):
    closes = _fetch_closes(req.symbols, req.lookback_days)
    if closes.empty or len(closes) < 30:
        raise HTTPException(400, "Insufficient price history — need at least 30 trading days for all symbols")
    returns = closes.pct_change().dropna()

    if req.method == "mean_variance":
        out = mean_variance(returns)
    elif req.method == "risk_parity":
        out = risk_parity(returns)
    elif req.method == "hierarchical_risk_parity":
        out = hierarchical_risk_parity(returns)
    else:
        scores = _fetch_scores(req.symbols)
        out = ai_allocation(returns, scores, min_score=req.min_score)

    return asdict(out)
