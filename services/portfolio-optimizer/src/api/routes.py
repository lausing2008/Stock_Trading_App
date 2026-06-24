"""Portfolio optimization API."""
from dataclasses import asdict
from datetime import date, timedelta
from typing import Literal

import httpx
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from common.jwt_auth import get_current_username
from pydantic import BaseModel

from common.config import get_settings

from ..optimizers import ai_allocation, hierarchical_risk_parity, mean_variance, risk_parity

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
_settings = get_settings()

METHOD = Literal["mean_variance", "risk_parity", "hierarchical_risk_parity", "ai_allocation"]
MIN_ROWS = 30  # minimum trading days required


class OptimizeRequest(BaseModel):
    symbols: list[str]
    method: METHOD = "mean_variance"
    lookback_days: int = 365
    min_score: float = 60.0


def _fetch_closes(symbols: list[str], lookback_days: int) -> tuple[pd.DataFrame, list[str]]:
    """Fetch closing prices. Returns (DataFrame, dropped_symbols)."""
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
        return pd.DataFrame(), list(symbols)

    merged = pd.DataFrame(series).sort_index()

    # Drop symbols that have fewer than MIN_ROWS non-null values in the lookback window
    counts = merged.notna().sum()
    good = counts[counts >= MIN_ROWS].index.tolist()
    dropped = [s for s in symbols if s not in good]

    if not good:
        return pd.DataFrame(), dropped

    # Forward-fill gaps (weekends/holidays).
    filled = merged[good].ffill()
    # Drop symbols that still have NaNs after ffill — these have leading NaNs (started trading
    # partway through the lookback window). Leaving them in would cause dropna() to remove those
    # leading rows for ALL symbols, silently shortening everyone's history.
    good2 = [c for c in filled.columns if not filled[c].isna().any()]
    newly_dropped = [s for s in good if s not in good2]
    result = filled[good2].dropna()
    return result, dropped + newly_dropped


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
def optimize(req: OptimizeRequest, _: str = Depends(get_current_username)):
    closes, dropped = _fetch_closes(req.symbols, req.lookback_days)

    if closes.empty or len(closes) < MIN_ROWS:
        detail = "Insufficient price history — need at least 30 trading days."
        if dropped:
            detail += f" Symbols with no/insufficient data: {', '.join(dropped)}"
        raise HTTPException(400, detail)

    if len(closes.columns) < 2:
        raise HTTPException(400, f"Need at least 2 symbols with sufficient history. Dropped: {', '.join(dropped)}")

    returns = closes.pct_change().dropna()

    if req.method == "mean_variance":
        out = mean_variance(returns)
    elif req.method == "risk_parity":
        out = risk_parity(returns)
    elif req.method == "hierarchical_risk_parity":
        out = hierarchical_risk_parity(returns)
    else:
        scores = _fetch_scores(list(closes.columns))
        out = ai_allocation(returns, scores, min_score=req.min_score)

    result = asdict(out)
    if dropped:
        result["dropped_symbols"] = dropped
    return result
