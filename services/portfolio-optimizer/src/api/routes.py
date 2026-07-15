"""Portfolio optimization API."""
from dataclasses import asdict
from datetime import date, timedelta
from typing import Literal

import httpx
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from common.jwt_auth import get_current_username
from pydantic import BaseModel, Field

from common.config import get_settings
from common.logging import get_logger

from ..optimizers import ai_allocation, hierarchical_risk_parity, mean_variance, risk_parity

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
_settings = get_settings()
log = get_logger("portfolio-optimizer")

METHOD = Literal["mean_variance", "risk_parity", "hierarchical_risk_parity", "ai_allocation"]
MIN_ROWS = 30  # minimum trading days required

# T247-PORTFOLIOOPTIMIZER-SKILLMD-SCHEMA: skill.md previously documented an unimplemented
# constraints.max_weight/min_weight + target_return request contract — Pydantic silently
# dropped these unknown fields (no extra="forbid"), so a caller following the docs got a
# request that ran with the hardcoded default max_weight instead of their intended value,
# no error indicating the constraint was never applied. max_weight is now real (every
# optimizer method already accepts a max_weight parameter internally — this just exposes
# it). min_weight and target_return are NOT implemented — no method has lower-bound or
# target-return support today; adding that is new optimizer functionality, not a bug fix, so
# skill.md's mention of those two was removed rather than half-implemented here.
class OptimizeConstraints(BaseModel):
    max_weight: float | None = None


class OptimizeRequest(BaseModel):
    symbols: list[str]
    method: METHOD = "mean_variance"
    lookback_days: int = 365
    # T247-PORTFOLIOOPTIMIZER-DEADSCOREFALLBACK: previously unconstrained. ai_allocation()'s
    # `keep = [s for s in returns.columns if scores.get(s, -1) >= min_score]` relies on -1
    # being lower than any real score to correctly exclude symbols with no fetched score — a
    # caller-supplied min_score <= -1 would let those symbols into `keep` via the -1 default,
    # which then received a fabricated 50.0 "neutral" score instead of being excluded. ge=0
    # keeps the -1 sentinel meaningfully below every valid input.
    min_score: float = Field(60.0, ge=0)
    constraints: OptimizeConstraints | None = None


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


def _fetch_scores(symbols: list[str]) -> tuple[dict[str, float], list[str]]:
    """Fetch K-Scores. Returns (scores, failed_symbols).

    T237-PO1: a transient ranking-engine failure (timeout, 5xx, connection reset) used to
    silently omit that symbol from `scores` with no log line — ai_allocation's
    `scores.get(s, 0)` then defaulted it to 0, always below min_score, so the symbol was
    silently excluded from the optimized portfolio indistinguishably from a genuinely low
    K-Score. Track and surface failures separately, same pattern _fetch_closes already uses
    for dropped_symbols, so callers/operators can tell "scored low" from "score fetch failed".
    """
    scores: dict[str, float] = {}
    failed: list[str] = []
    with httpx.Client(timeout=15) as c:
        for s in symbols:
            try:
                r = c.get(f"{_settings.ranking_engine_url}/rankings/{s}")
                if r.status_code == 200:
                    val = r.json().get("score")
                    scores[s] = float(val) if val is not None else 0.0
                else:
                    failed.append(s)
                    log.warning("portfolio.score_fetch_failed", symbol=s, status=r.status_code)
            except Exception as exc:
                failed.append(s)
                log.warning("portfolio.score_fetch_failed", symbol=s, error=str(exc))
    return scores, failed


@router.post("/optimize")
def optimize(req: OptimizeRequest, _: str = Depends(get_current_username)):
    closes, dropped = _fetch_closes(req.symbols, req.lookback_days)

    # T247-PORTFOLIOOPTIMIZER-MINROWS-OFFBYONE: the actual optimizer input is
    # `returns = closes.pct_change().dropna()`, which always has exactly one fewer row than
    # `closes` (the first row's pct_change is NaN, dropped). Checking `len(closes) < MIN_ROWS`
    # let a request with exactly MIN_ROWS price rows through, but only fed MIN_ROWS-1 rows of
    # returns into the optimizer — one short of the "30 trading days" the error message and
    # MIN_ROWS constant promise. Require MIN_ROWS+1 raw price rows so the resulting returns
    # series actually has MIN_ROWS rows.
    if closes.empty or len(closes) < MIN_ROWS + 1:
        detail = "Insufficient price history — need at least 30 trading days."
        if dropped:
            detail += f" Symbols with no/insufficient data: {', '.join(dropped)}"
        raise HTTPException(400, detail)

    if len(closes.columns) < 2:
        raise HTTPException(400, f"Need at least 2 symbols with sufficient history. Dropped: {', '.join(dropped)}")

    returns = closes.pct_change().dropna()

    max_weight = req.constraints.max_weight if req.constraints else None
    if max_weight is not None and not (0.0 < max_weight <= 1.0):
        raise HTTPException(400, "constraints.max_weight must be in (0, 1]")

    if req.method == "mean_variance":
        out = mean_variance(returns) if max_weight is None else mean_variance(returns, max_weight=max_weight)
    elif req.method == "risk_parity":
        out = risk_parity(returns) if max_weight is None else risk_parity(returns, max_weight=max_weight)
    elif req.method == "hierarchical_risk_parity":
        out = hierarchical_risk_parity(returns) if max_weight is None else hierarchical_risk_parity(returns, max_weight=max_weight)
    else:
        scores, failed_scores = _fetch_scores(list(closes.columns))
        out = (
            ai_allocation(returns, scores, min_score=req.min_score)
            if max_weight is None
            else ai_allocation(returns, scores, min_score=req.min_score, max_weight=max_weight)
        )

    result = asdict(out)
    if dropped:
        result["dropped_symbols"] = dropped
    if req.method == "ai_allocation" and failed_scores:
        result["failed_score_symbols"] = failed_scores
    return result
